# Copyright (c) 2019-2021 Almar Klein - This code is subject to the MIT license
"""
The itemdb library allows you to store and retrieve Python dicts in a
database on the local filesystem, in an easy, fast, and reliable way.

Based on the rock-solid and ACID compliant SQLite, but with easy and
explicit transactions using a ``with`` statement. It provides a simple
object-based API, with the flexibility to store (JSON-compatible) items
with arbitrary fields, and add indices when needed.
"""

import os
import sys
import json
import queue
import asyncio
import sqlite3
import threading


__version__ = "1.1.1"
version_info = tuple(map(int, __version__.split(".")))

__all__ = ["ItemDB", "AsyncItemDB", "asyncify"]

json_encode = json.JSONEncoder(ensure_ascii=True).encode
json_decode = json.JSONDecoder().decode

is_py36 = sys.version_info < (3, 7)


# Notes:
#
# * Setting isolation_level to None turns on autocommit mode. We need to do
#   this to prevent Python from issuing BEGIN before DML statements.
# * Using a connection object as a context manager auto-commits/rollbacks a
#   transaction.
# * We should close cursor objects as soon as possible, because they can hold
#   back waiting writers. That's why we dont have an iterator.
# * MongoDB's approach of db.tablename.push() looks nice, but I don't like
#   the "magical" side of it, especially since the db does not know its tables.
#   Also it makes the code more complex, introduces an extra class, and
#   increases the risk of preventing a db from closing (by holding a table).


def asyncify(func):
    """Wrap a normal function into an awaitable co-routine. Can be used
    as a decorator.

    The original function will be executed in a separate thread. This
    allows async code to execute io-bound code (like querying a sqlite
    database) without stalling.

    Note that the code in func must be thread-safe. It's probably best to
    isolate the io-bound parts of your code and only wrap these.
    """

    def threaded_func(loop, future, args, kwargs):
        try:
            result = func(*args, **kwargs)
        except BaseException as e:
            loop.call_soon_threadsafe(future.set_exception, e)
        else:
            loop.call_soon_threadsafe(future.set_result, result)

    async def asyncified_func(*args, **kwargs):
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        threading.Thread(
            name="asyncify " + func.__name__,
            target=threaded_func,
            args=(loop, future, args, kwargs),
        ).start()
        return await future

    asyncified_func.__name__ = "asyncified_" + func.__name__
    return asyncified_func


class ItemDB:
    """A transactional database for storage and retrieval of dict items.

    The items in the database can be any JSON serializable dictionary.
    Indices can be defined for specific fields to enable fast selection
    of items based on these fields. Indices can be marked as unique to
    make a field mandatory and *identify* items based on that field.

    Transactions are done by using the ``with`` statement, and are mandatory
    for all operations that write to the database.
    """

    def __init__(self, filename):
        self._mtime = -1
        if os.path.isfile(filename):
            self._mtime = os.path.getmtime(filename)
        self._conn = sqlite3.connect(
            filename, timeout=60, isolation_level=None, check_same_thread=False
        )
        self._cur = None
        self._indices_per_table = {}

    @property
    def mtime(self):
        """The time that the database file was last modified, as a Unix timestamp.
        Is -1 if the file did not exist, or if the filename is not represented
        on the filesystem.
        """
        return self._mtime

    def __enter__(self):
        if self._cur is not None:
            raise IOError("Already in a transaction")
        self._cur = self._conn.cursor()
        self._cur.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, type, value, traceback):
        self._cur.close()
        self._cur = None
        if value:
            self._conn.rollback()
            self._indices_per_table.clear()  # we cannot trust this cache anymore
        else:
            self._conn.commit()

    def __del__(self):
        self._conn.close()

    def close(self):
        """Close the database connection. This will be automatically
        called when the instance is deleted. But since it can be held
        e.g. in a traceback, consider using ``with closing(db):``.
        """
        self._conn.close()

    def get_table_names(self):
        """Return a (sorted) list of table names present in the database."""
        cur = self._conn.cursor()
        try:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            table_names = {x[0] for x in cur}
        finally:
            cur.close()
        return list(sorted(table_names))

    def get_indices(self, table_name):
        """Get a set of indices for the given table. Names prefixed with "!"
        represent fields that are required and unique. Raises KeyError if the
        table does not exist.
        """
        # Use cached?
        try:
            return self._indices_per_table[table_name]
        except KeyError:
            pass
        except TypeError:
            raise TypeError(f"Table name must be str, not {table_name}.")

        # Check table name
        if not isinstance(table_name, str):
            raise TypeError(f"Table name must be str, not {table_name}")
        elif not table_name.isidentifier():
            raise ValueError(f"Table name must be an identifier, not '{table_name}'")

        # Get columns for the table (cid, name, type, notnull, default, pk)
        cur = self._conn.cursor()
        try:
            cur.execute(f"PRAGMA table_info('{table_name}');")
            found_indices = {(x[3] * "!" + x[1]) for x in cur}  # includes !_ob
        finally:
            cur.close()

        # Cache and return - or fail
        if found_indices:
            found_indices.difference_update({"!_ob", "_ob"})
            self._indices_per_table[table_name] = found_indices
            return found_indices
        else:
            raise KeyError(f"Table {table_name} not present, maybe use ensure_table()?")

    def ensure_table(self, table_name, *indices):
        """Ensure that the given table exists and has the given indices.

        If an index name is prefixed with "!", it indicates a field that is
        mandatory and unique. Note that new unique indices cannot be added
        when the table already exist.

        This method returns as quickly as possible when the table
        already exists and has the appropriate indices. Returns the
        ItemDB object, so calls to this method can be stacked.

        Although this call may modify the database, one does not need
        to call this in a transaction.
        """

        if not all(isinstance(x, str) for x in indices):
            raise TypeError("Indices must be str")

        # Select missing indices
        try:
            missing_indices = set(indices).difference(self.get_indices(table_name))
        except KeyError:
            missing_indices = {"--table--"}

        # Do we need to do some work? Allow being used under a context and not
        if missing_indices:
            if self._cur:
                self._ensure_table_helper1(table_name, indices, missing_indices)
            else:
                with self:
                    self._ensure_table_helper1(table_name, indices, missing_indices)

        return self  # allow stacking this function

    def _ensure_table_helper1(self, table_name, indices, missing_indices):
        # Make sure the table is complete
        self._ensure_table_helper2(table_name, indices)
        self._indices_per_table.pop(table_name, None)  # let it refresh
        # Update values that already had a value for the just added columns/indices
        items = [
            item
            for item in self.select_all(table_name)
            if any(x.lstrip("!") in item for x in missing_indices)
        ]
        self.put(table_name, *items)

    def _ensure_table_helper2(self, table_name, indices):
        """Slow version to ensure table."""

        cur = self._cur

        # Check the column names
        for fieldname in indices:
            key = fieldname.lstrip("!")
            if not key.isidentifier():
                raise ValueError("Column names must be identifiers.")
            elif key == "_ob":
                raise IndexError("Column names cannot be '_ob' (name is reserved).")

        # Ensure the table.
        # If there is one unique key, make it the primary key and omit rowid.
        # This results in smaller and faster databases.
        text = f"CREATE TABLE IF NOT EXISTS {table_name} (_ob TEXT NOT NULL"
        unique_keys = sorted(x.lstrip("!") for x in indices if x.startswith("!"))
        if len(unique_keys) == 1:
            text += f", {unique_keys[0]} NOT NULL PRIMARY KEY) WITHOUT ROWID;"
        else:
            for key in unique_keys:
                text += f", {key} NOT NULL UNIQUE"
            text += ");"
        cur.execute(text)

        # Ensure the columns and indices
        cur.execute(f"PRAGMA table_info('{table_name}');")
        found_indices = {(x[3] * "!" + x[1]) for x in cur}

        for fieldname in sorted(indices):
            key = fieldname.lstrip("!")
            if fieldname not in found_indices:
                if fieldname.startswith("!"):
                    raise IndexError(
                        f"Cannot add unique index {fieldname!r} after the table has been created."
                    )
                elif fieldname in {x.lstrip("!") for x in found_indices}:
                    raise IndexError(f"Given index {fieldname!r} should be unique.")
                cur.execute(f"ALTER TABLE {table_name} ADD {key};")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table_name}_{key} ON {table_name} ({key})"
            )

    def delete_table(self, table_name):
        """Delete the table with the given name.
        This method must be called within a transaction.

        Warning: this deletes the whole table, including all of its items.

        Can raise KeyError if an invalid table is given, or IOError if not
        used within a transaction
        """
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        cur = self._cur
        if cur is None:
            raise IOError("Can only use delete_table() within a transaction.")
        self._indices_per_table.pop(table_name, None)
        self._cur.execute(f"DROP TABLE {table_name}")

    def rename_table(self, table_name, new_table_name):
        """Rename a table. This method must be called within a transaction.

        Can raise KeyError if an invalid table is given, or IOError if not
        used within a transaction
        """
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        if not (isinstance(new_table_name, str) and new_table_name.isidentifier()):
            raise TypeError(f"Table name must be a str identifier, not '{table_name}'")
        cur = self._cur
        if cur is None:
            raise IOError("Can only use rename_table() within a transaction.")
        self._indices_per_table.pop(table_name, None)
        self._cur.execute(f"ALTER TABLE {table_name} RENAME TO {new_table_name}")

    def count_all(self, table_name):
        """Get the total number of items in the given table."""
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        cur = self._conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table_name}")
            return cur.fetchone()[0]
        finally:
            cur.close()

    def count(self, table_name, query, *args):
        """Get the number of items in the given table that match the given query.

        Examples::

            # Count the persons older than 20
            db.count("persons", "age > 20")
            # Use parameters for variables (to avoid SQL injection)
            db.count("persons", "age > ?", min_age)
            # Use AND and OR for more precise queries
            db.count("persons", "age > ? AND age < ?", min_age, max_age)

        See select() for details on queries.

        Can raise KeyError if an invalid table is given, IndexError if an
        invalid field is used in the query, or sqlite3.OperationalError for
        an invalid query.
        """
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        cur = self._conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE {query}", args)
            return cur.fetchone()[0]
        except sqlite3.OperationalError as err:
            if "no such column" in str(err).lower():
                raise IndexError(str(err))
            raise err
        finally:
            cur.close()

    def select_all(self, table_name):
        """Get all items in the given table. See select() for details."""
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        cur = self._conn.cursor()
        try:
            cur.execute(f"SELECT _ob FROM {table_name}")
            return [json_decode(x[0]) for x in cur]
        finally:
            cur.close()

    def select(self, table_name, query, *args):
        """Get the items in the given table that match the given query.

        The query follows SQLite syntax and can only include indexed
        fields. If needed, use ensure_table() to add indices. The query
        is always fast (which is why this method is called select, and
        not search).

        Examples::

            # Select the persons older than 20
            db.select("persons", "age > 20")
            # Use parameters for variables (to avoid SQL injection)
            db.select("persons", "age > ?", min_age)
            # Use AND and OR for more precise queries
            db.select("persons", "age > ? AND age < ?", min_age, max_age)

        There is no method to filter items bases on non-indexed fields,
        because this is easy using a list comprehension, e.g.::

            items = db.select_all("persons")
            items = [i for i in items if i["age"] > 20]

        Can raise KeyError if an invalid table is given, IndexError if an
        invalid field is used in the query, or sqlite3.OperationalError for
        an invalid query.
        """
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        # It is tempting to make this a generator, but also dangerous because
        # the cursor might not be closed if the generator is stored somewhere
        # and not run through the end.
        cur = self._conn.cursor()
        try:
            cur.execute(f"SELECT _ob FROM {table_name} WHERE {query}", args)
            return [json_decode(x[0]) for x in cur]
        except sqlite3.OperationalError as err:
            if "no such column" in str(err).lower():
                raise IndexError(str(err))
            raise err
        finally:
            cur.close()

    def select_one(self, table_name, query, *args):
        """Get the first item in the given table that match the given query.
        Returns None if there was no match. See select() for details.
        """
        items = self.select(table_name, query, *args)
        return items[0] if items else None

    def put(self, table_name, *items):
        """Put one or more items into the given table.
        This method must be called within a transaction.

        Can raise KeyError if an invalid table is given, IOError if not
        used within a transaction, TypeError if an item is not a (JSON
        serializable) dict, or IndexError if an item does not have a
        required field.
        """
        cur = self._cur
        if cur is None:
            raise IOError("Can only use put() within a transaction.")

        # Get indices - fail with KeyError for invalid table name
        indices = self.get_indices(table_name)

        for item in items:
            if not isinstance(item, dict):
                raise TypeError("Expecing each item to be a dict")

            row_keys = "_ob"
            row_plac = "?"
            row_vals = [json_encode(item)]  # Can raise TypeError
            for fieldname in indices:
                key = fieldname.lstrip("!")
                if key in item:
                    row_keys += ", " + key
                    row_plac += ", ?"
                    row_vals.append(item[key])
                elif fieldname.startswith("!"):
                    raise IndexError(f"Item does not have required field {key!r}")

            cur.execute(
                f"INSERT OR REPLACE INTO {table_name} ({row_keys}) VALUES ({row_plac})",
                row_vals,
            )

    def put_one(self, table_name, **item):
        """Put an item into the given table using kwargs.
        This method must be called within a transaction.
        """
        self.put(table_name, item)

    def delete(self, table_name, query, *args):
        """Delete items from the given table.
        This method must be called within a transaction.

        Examples::

            # Delete the persons older than 20
            db.delete("persons", "age > 20")
            # Use parameters for variables (to avoid SQL injection)
            db.delete("persons", "age > ?", min_age)
            # Use AND and OR for more precise queries
            db.delete("persons", "age > ? AND age < ?", min_age, max_age)

        See select() for details on queries.

        Can raise KeyError if an invalid table is given, IOError if not
        used within a transaction, IndexError if an invalid field is
        used in the query, or sqlite3.OperationalError for an invalid
        query.
        """
        self.get_indices(table_name)  # Fail with KeyError for invalid table name
        cur = self._cur
        if cur is None:
            raise IOError("Can only use delete() within a transaction.")
        try:
            cur.execute(f"DELETE FROM {table_name} WHERE {query}", args)
        except sqlite3.OperationalError as err:
            if "no such column" in str(err).lower():
                raise IndexError(str(err))
            raise err
        finally:
            cur.close()


class AsyncItemDB:
    """An async version of ItemDB. The API is exactly the same, except
    that all methods are async, and one must use `async with` instead
    of the normal `with`.
    """

    async def __new__(cls, filename):
        self = super().__new__(cls)
        self._queue = queue.Queue()
        self._thread = Thread4AsyncItemDB(self._queue)
        self._thread.start()
        self.db = self._thread.db = await self._handle(ItemDB, filename)
        return self

    @property
    def mtime(self):
        return self.db.mtime

    async def _handle(self, function, *args, **kwargs):
        future = asyncio.get_event_loop().create_future()
        self._queue.put_nowait((future, function, args, kwargs))
        return await future

    async def __aenter__(self):
        return await self._handle(self.db.__enter__)

    async def __aexit__(self, type, value, traceback):
        return await self._handle(self.db.__exit__, type, value, traceback)

    def __del__(self):
        future = asyncio.get_event_loop().create_future()
        self._queue.put_nowait((future, self.db.close, (), {}))
        self._queue.put_nowait((None, None, None, None))

    async def close(self):
        future = asyncio.get_event_loop().create_future()
        self._queue.put_nowait((future, self.db.close, (), {}))
        self._queue.put_nowait((None, None, None, None))
        return await future

    async def get_table_names(self, *args, **kwargs):
        return await self._handle(self.db.get_table_names, *args, **kwargs)

    async def get_indices(self, *args, **kwargs):
        return await self._handle(self.db.get_indices, *args, **kwargs)

    async def ensure_table(self, *args, **kwargs):
        return await self._handle(self.db.ensure_table, *args, **kwargs)

    async def delete_table(self, *args, **kwargs):
        return await self._handle(self.db.delete_table, *args, **kwargs)

    async def rename_table(self, *args, **kwargs):
        return await self._handle(self.db.rename_table, *args, **kwargs)

    async def count_all(self, *args, **kwargs):
        return await self._handle(self.db.count_all, *args, **kwargs)

    async def count(self, *args, **kwargs):
        return await self._handle(self.db.count, *args, **kwargs)

    async def select_all(self, *args, **kwargs):
        return await self._handle(self.db.select_all, *args, **kwargs)

    async def select(self, *args, **kwargs):
        return await self._handle(self.db.select, *args, **kwargs)

    async def select_one(self, *args, **kwargs):
        return await self._handle(self.db.select_one, *args, **kwargs)

    async def put(self, *args, **kwargs):
        return await self._handle(self.db.put, *args, **kwargs)

    async def put_one(self, *args, **kwargs):
        return await self._handle(self.db.put_one, *args, **kwargs)

    async def delete(self, *args, **kwargs):
        return await self._handle(self.db.delete, *args, **kwargs)


class Thread4AsyncItemDB(threading.Thread):
    """Thread that does the work for the AsyncItemDB."""

    _count = 0

    def __init__(self, queue):
        Thread4AsyncItemDB._count += 1
        super().__init__(name=f"AsyncItemDB_{Thread4AsyncItemDB._count}")
        self.daemon = True
        self._queue = queue
        self.db = None

    def run(self) -> None:

        while True:
            # Continues running until all queue items are processed,
            # even after closed (so we can finalize all futures)
            future, function, args, kwargs = self._queue.get()
            if future is None:
                break

            try:
                result = function(*args, **kwargs)

                def set_result(fut, result):
                    if not fut.done():
                        fut.set_result(result)

                loop = future._loop if is_py36 else future.get_loop()
                loop.call_soon_threadsafe(set_result, future, result)

            except BaseException as e:

                def set_exception(fut, e):
                    if not fut.done():
                        fut.set_exception(e)

                loop = future._loop if is_py36 else future.get_loop()
                loop.call_soon_threadsafe(set_exception, future, e)

Guide
=====


Introduction
------------

The itemdb library allows you to store and retrieve Python dicts in a
database on the local filesystem, in an easy, fast, and reliable way.

To be more precise: it is an `ACID compliant
<https://en.wikipedia.org/wiki/ACID>`_ transactional database for
storage and retrieval of JSON-compatible dict items.
That sounds very technical; let's break it down:

* ACID means it has desirable database features of atomicity,
  consistency, isolation and durability. We'll get back to these when
  we talk about transactions.
* The itemdb API focuses on making transactions easy and explicit.
* JSON is used to serialize the dict items, so the values in the dicts
  are limited to: ``None``, ``bool``, ``int``, ``float``, ``str``,
  ``list``, ``dict``.

In practice, itemdb uses the rock-solid `SQLite <https://sqlite.org>`_, and
provides an object-based API that requires no knowledge of SQL.

You can use itemdb in a wide variety of applications. This includes
web-servers, though once your traffic scales up, you may want to
consider something like PostgreSQL or perhasps a hosted db.


Opening a database
------------------

In itemdb (like SQLite) each database is represented as a file. One can also
use ``":memory:"`` to create an in-memory database for testing/demo purposes.

.. code-block:: python

    db = ItemDB(filename)
    db = ItemDB(":memory:")


Creating tables and indices
---------------------------

Each database consists of tables, and the tables contain the items. A
"table" is what is also called "table" in SQL databases, a "collection"
in MongoDB, and an "object store" in IndexedDB.

You can create a table using ``ensure_table()``. It is safe to call this
before every time that you use the database, because it returns fast if the
table already exist:

.. code-block:: python

    db.ensure_table("some_table_name")

In the same call we can also specify indices. Indicices represent fields in the
items that are indexed, so that they can be used to retrieve items fast,
using ``select()``, ``count()`` and ``delete()``.

Indices can also be prefixed with a "!", marking the field as mandatory and unique,
making it possible to identify items.

.. code-block:: python

    db.ensure_table("persons", "!name", "age")

We can now ``select()`` items based on the ``name`` and ``age`` fields, and no
two items can have the same value for ``name``.

.. note::

    No new fields can be marked unique once the table has been created.

.. note::

    In the examples below we mark the "name" field as unique, but
    strictly speaking this is wrong, because different persons can have
    the same name. Another form of ID would be more appropriate in real
    use-cases.


Add some items
--------------

An "item" is what is called a "row" in SQL databases, a "document"
in MongoDB, and an "object" in IndexedDB. Let's add some to our table!

.. code-block:: python

    with db:
        db.put_one("persons", name="Jane", age=22)
        db.put_one("persons", name="John", age=18, fav_number=7)
        db.put("persons", {"name": "Guido"}, {"name": "Anne", "age": 42})

You can see how we use ``with db`` here. This is because itemdb requires using
a transaction when making changes to the database. Everything inside
the with statement is a single transaction. More on that later.

You can also see that with ``put_one()`` we can use keyword arguments to specify fields,
while with ``put()`` we can specify multiple items, each items a dict.

The dictionary can contain as many fields as you want, including sub-dicts and lists.
Although the ``age`` field is indexed, it is not mandatory (you can
select items with missing age using ``db.select("persons", "age is NULL")``).

Since the ``name`` field is unique, if we ``put`` an item with an existing name,
it will simply update it:

.. code-block:: python

    # John had his birthday and changed his favourite number
    with db:
        db.put_one("persons", name="John", age=19, fav_number=8)


Make some queries
-----------------

Use e.g. ``count()``, ``select()`` to query the database:

.. code-block:: python

    >>> db.count_all("persons")
    4

    >>> db.select("persons", "age > ?", 20)
    [{'name': 'Jane', 'age': 22}, {'name': 'Anne', 'age': 42}]

    >>> select_name = "John"
    >>> db.select_one("persons", "name = ?", select_name)
    {'name': 'John', 'age': 19, 'fav_number': 8}


Transactions
------------

Transactions are an important concept in databases. In ACID databases (like itemdb) it has a number of features:

* A transaction is atomic (either the whole transaction is applied, or
  the whole transaction is not applied)
* A transaction is applied in isolation, even when multiple processes
  are interacting with the database at the same time. This means that
  when a transaction is in progress, another process/thread that wants
  to apply a transaction that "intersects" with the ongoing operation,
  it will wait. (This even works for multiple Docker containers
  operating on the same SQLite database.)
* The remaining elements of ACID (consistency and durability) mean that
  the database always remains in a healthy state. Even on a power outage
  or if the system crashes halfway a transaction.

In itemdb, transactions are easy, using a context manager. Let's have a look at some examples:

.. code-block:: python

    # Increasing a value is recommended to do in a transaction.
    with db:
        player = db.select("players", "name == ?", player_name)
        player["position"] += 2
        db.put("players", player)

    # The below has no effect: the transaction fails and is rolled back
    with db:
        db.put_one("persons", name="John", age=21, fav_number=8)
        raise RuntimeError()


Database maintenance
--------------------

Sometimes, you may want to add unique keys to a table or remove existing indices.
This is possible by copying the items to a new table and then replacing the new
table with the old. By doing this inside a transaction, it can be done safely:

.. code-block:: python

    with db:
        db.ensure_table("persons2", "!id", "name", "age")
        for i, person in enumerate(db.select_all("persons")):
            # Make sure each person has an id, e.g.:
            person["id"] = i
            db.put("persons2", person)
        db.delete_table("persons")
        db.rename_table("persons2", "persons")

At the time of writing, itemdb does not provide an API for
`backups <https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup>`_ or
`vacuuming <https://www.sqlite.org/lang_vacuum.html>`_,
but it's just SQLite under the hood, so you can use the common methods.


Going Async
-----------

The API of ``ItemDB`` is synchronous. It operates with the filesystem, so
it can benefit from async use a lot.

There are two ways to make your code async. The first is by using the
``AsyncItemDB`` class. It has the exact same API as ``ItemDB``, but all its
methods are async. Note that you must also use ``async with``.

The second approach is to asyncify a synchronous function. The idea of
this approach is to do all itemdb operations inside a function and then
wrap that function if you want to use it in an async environment.
Consider the following example of a web server:

.. code-block:: python

    @itemdb.asycify
    def push_items(filename, items):
        db = ItemDB(filename)
        db.ensure_table("my_table", "!id", "mtime")

        with db:
            ...
            db.put("my_table", items)

    async def my_request_handler(request):
        ...
        # Because we decorated the function with asyncify,
        # we can now await it, while the db interaction
        # occurs in a separate thread.
        await push_items(filename, items)
        ...

Of the two mentioned approaches, the asyncify-approach is slightly
more efficient, because it makes use of a thread pool, and only switches
to a thread for the duration of the function you've asyncified. However,
using ``AsyncItemDB`` probably makes your code easier to read and
maintain, which is probably worth more.

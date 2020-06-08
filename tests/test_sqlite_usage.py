"""
Tests for how to use sqlite3 to get proper locks.

Basically, you want to set isolation_level to None (which is not the default),
and use "BEGIN IMMEDIATE" to get a write lock.

Reading:

* https://docs.python.org/3.7/library/sqlite3.html#controlling-transactions
* https://www.sqlite.org/lang_transaction.html
* https://stackoverflow.com/questions/15856976/transactions-with-python-sqlite3

"""

import os
import time
import random
import sqlite3
import tempfile
import threading


tempdir = tempfile.gettempdir()
n_threads = 40
n_writes = 10


def create_db(dbname):
    con = sqlite3.connect(dbname)
    with con:
        con.execute(
            """
        CREATE TABLE items (
            st REAL NOT NULL,
            mt REAL NOT NULL,
            id INTEGER NOT NULL,
            item TEXT NOT NULL
            );
        """
        )


def write_to_db(dbname, safe=True):
    for i in range(n_writes):
        id = i
        mt = mt = random.randint(100, 200)
        st = time.time()

        con = sqlite3.connect(dbname, timeout=60, isolation_level=None)
        # con.isolation_level = None  # also works
        with con:
            cur = con.cursor()
            if safe:
                cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT mt FROM items WHERE id is ?", (id,))
            mts = [x[0] for x in cur]
            if not len(mts) or max(mts) <= mt:
                cur.execute(
                    "INSERT INTO items (st, mt, id, item) VALUES (?, ?, ?, ?)",
                    (st, float(mt), id, ""),
                )


def xxtest_sqlite_nonlocking():
    # NOTE: this test makes sure that NOT using SQLite the right way
    # causes errors; we're deliberately making it fail. A problem is that
    # the test can still pass on fast computers, like a fast CI :/

    filename = os.path.join(tempdir, "sqlite_test1.sqlite")

    # Open file and empty it
    if os.path.isfile(filename):
        os.remove(filename)
    create_db(filename)

    # Prepare, start, and join threads
    t0 = time.perf_counter()
    threads = [
        threading.Thread(target=write_to_db, args=(filename, False))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t1 = time.perf_counter()

    # Evaluate the result
    con = sqlite3.connect(filename)
    items = [item for item in con.execute("SELECT id, mt FROM items")]

    print(f"{t1-t0} s, for {n_threads*n_writes} writes, saving {len(items)} items")

    assert 10 <= len(items) <= 120
    fails = 0
    mts = {}
    for item in items:
        if item[1] < mts.get(item[0], -9999):
            fails += 1
        mts[item[0]] = item[1]
    assert fails > 0
    return items


def test_sqlite_locking():
    filename = os.path.join(tempdir, "sqlite_test1.sqlite")

    # Open file and empty it
    if os.path.isfile(filename):
        os.remove(filename)
    create_db(filename)

    # Prepare, start, and join threads
    t0 = time.perf_counter()
    threads = [
        threading.Thread(target=write_to_db, args=(filename,)) for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    t1 = time.perf_counter()

    # Evaluate the result
    con = sqlite3.connect(filename)
    items = [item for item in con.execute("SELECT id, mt FROM items")]

    print(f"{t1-t0} s, for {n_threads*n_writes} writes, saving {len(items)} items")

    assert 10 <= len(items) <= 100
    # assert len(items) == n_threads * n_writes
    mts = {}
    for item in items:
        assert item[1] >= mts.get(item[0], -9999)
        mts[item[0]] = item[1]

    return items


if __name__ == "__main__":
    items = xxtest_sqlite_nonlocking()
    items = test_sqlite_locking()

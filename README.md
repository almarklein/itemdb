[![PyPI Version](https://img.shields.io/pypi/v/itemdb.svg)](https://pypi.python.org/pypi/itemdb/)
![CI](https://github.com/almarklein/itemdb/workflows/CI/badge.svg)
[![Documentation Status](https://readthedocs.org/projects/itemdb/badge/?version=latest)](https://itemdb.readthedocs.io/en/latest/?badge=latest)

# itemdb


The itemdb library allows you to store and retrieve Python dicts in a
database on the local filesystem, in an easy, fast, and reliable way.

Based on the rock-solid and ACID compliant SQLite, but with easy and
explicit transactions using a ``with`` statement. It provides a simple
object-based API, with the flexibility to store (JSON-compatible) items
with arbitrary fields, and add indices when needed.

This lib was originally part of the [TimeTurtle time tracker](https://timeturtle.app)
and is also used in [MyPaaS](https://github.com/almarklein/mypaas).


## Installation

```
pip install itemdb
```


## Quick usage example

```py
import itemdb

# Open the database and make sure there is a table with appopriate indices
db = itemdb.ItemDB(":memory:")
db.ensure_table("persons", "!name", "age")

# Add some items to the db
with db:
    db.put_one("persons", name="Jane", age=22)
    db.put_one("persons", name="John", age=18, fav_number=7)
    db.put("persons", {"name": "Guido"}, {"name": "Anne", "age": 42})

# Update an item
with db:
    db.put_one("persons", name="John", age=19, fav_number=8)

# Query items
db.count_all("persons")  # -> 4
db.select("persons", "age > 20")  # -> list of 2 items
```

See the [guide](https://itemdb.readthedocs.io/en/latest/guide.html) for details.


## Async

The `AsyncItemDB` class provides the same API, but async:

```py
import itemdb

db = await itemdb.AsyncItemDB(":memory:")
await db.ensure_table("persons", "!name", "age")

async with db:
    await db.put_one("persons", name="Jane", age=22)

...
```

Alternatively, a decorator is provided to turn a normal function into an async one
(running in a separate thread).

```py

@asycify
def your_db_interaction_logic(...):
    db = ItemDB(filename)
    ...

async def your_async_code(...):
    await your_db_interaction_logic(...)
```


## License

MIT


## Developers

* Run `black .` to autoformat.
* Run `flake8 . --max-line-length=99` to lint.
* Run `pytest .` to run unit tests.

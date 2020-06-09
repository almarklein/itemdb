""" Tests related to db management and tables.
"""

import os
import tempfile

from pytest import raises

from testutils import run_tests
from itemdb import ItemDB


def get_fresh_filename():
    filename = os.path.join(tempfile.gettempdir(), "test.db")
    if os.path.isfile(filename):
        os.remove(filename)
    return filename


def test_create_tables():

    # Empty database, zero tables

    db = ItemDB(":memory:")

    assert db.get_table_names() == []  # no tables

    # Two tables

    db = ItemDB(":memory:").ensure_table("foo", "key").ensure_table("bar")

    assert db.get_table_names() == ["bar", "foo"]
    assert db.count_all("foo") == 0
    assert db.count_all("bar") == 0


def test_table_fails():

    db = ItemDB(":memory:")
    for name in [(), 4, b"", [], {}]:
        with raises(TypeError):  # not a str
            db.ensure_table(name)

    db = ItemDB(":memory:")
    for name in ["foo bar", "foo-bar", "33", "foo!", "!foo"]:
        with raises(ValueError):  # not an identifier
            db.ensure_table(name)


def test_delete_table():

    db = ItemDB(":memory:")
    db.ensure_table("persons", "!name")
    db.ensure_table("animals", "!name")
    with db:
        db.put_one("persons", name="Jan", age=30)
        db.put_one("persons", name="Henk", age=42)
        db.put_one("animals", name="Takkie", age=30)
        db.put_one("animals", name="Siepe", age=42)

    assert db.count_all("persons") == 2
    assert db.count_all("animals") == 2

    with db:
        db.delete_table("persons")

    with raises(KeyError):
        db.count_all("persons")
    db.ensure_table("persons", "!name")
    assert db.count_all("persons") == 0
    assert db.count_all("animals") == 2

    with db:
        db.delete_table("animals")

    with raises(KeyError):
        db.count_all("animals")
    db.ensure_table("animals", "!name")
    assert db.count_all("persons") == 0
    assert db.count_all("animals") == 0

    # Need a transaction context
    with raises(IOError):
        db.delete_table("persons")
    # This works
    with db:
        db.delete_table("persons")
    # But this not because the table is gone
    with raises(KeyError):
        with db:
            db.delete_table("persons")


def test_rename_table():

    db = ItemDB(":memory:")
    db.ensure_table("persons", "!name")
    with db:
        db.put_one("persons", name="Jan", age=30)
        db.put_one("persons", name="Henk", age=42)
        db.put_one("persons", name="Takkie", age=30)
        db.put_one("persons", name="Siepe", age=42)

    assert db.count_all("persons") == 4
    with raises(KeyError):
        db.count_all("clients")

    # Fails
    with raises(IOError):  # Need a transaction context
        db.rename_table("persons", "clients")
    with raises(TypeError):  # not a str
        with db:
            db.rename_table("persons", 3)
    with raises(TypeError):  # not an identifier
        with db:
            db.rename_table("persons", "foo bar")

    with db:
        db.rename_table("persons", "clients")

    assert db.count_all("clients") == 4
    with raises(KeyError):
        db.count_all("persons")


def test_change_unique_key():

    db = ItemDB(":memory:")
    db.ensure_table("persons", "!name")
    with db:
        db.put_one("persons", name="Jan", age=30)
        db.put_one("persons", name="Henk", age=42)

    assert db.count_all("persons") == 2

    # Add a new person, who also happens to be named "Jan"
    with db:
        db.put_one("persons", name="Jan", age=72)
    # Sorry, Jan
    assert db.count_all("persons") == 2

    # Let's fix this, we need a separate id, so we need to re-index.
    # We cannot simply do this on an existing table. So we need some steps.
    try:
        with db:
            db.ensure_table("persons2")
            for i, person in enumerate(db.select_all("persons")):
                person["id"] = i
                db.put("persons2", person)
            db.delete_table("persons")
            raise RuntimeError("Oop! Something goes wrong in the process")
            db.rename_table("persons2", "persons")
    except RuntimeError:
        pass

    # Our little operation failed, but we did it in a transaction, so its fine!
    assert db.count_all("persons") == 2

    # Try again
    with db:
        db.ensure_table("persons2")
        for i, person in enumerate(db.select_all("persons")):
            person["id"] = i
            db.put("persons2", person)
        db.delete_table("persons")
        db.rename_table("persons2", "persons")

    # Now we're good
    assert db.count_all("persons") == 2
    with db:
        db.put_one("persons", name="Jan", age=72, id=3)
    assert db.count_all("persons") == 3


if __name__ == "__main__":
    run_tests(globals())

import gc
import time
import asyncio
import threading

from pytest import raises

from testutils import run_tests
from itemdb import asyncify, ItemDB, AsyncItemDB


side_effect = [0]


def plain_func(x):
    time.sleep(1)  # emulate io
    side_effect[0] += 10
    return x + 5


def plain_func_that_errors(x):
    raise ValueError(x)


def swait(co):
    """Sync-wait for the given coroutine, and return the result."""
    return asyncio.get_event_loop().run_until_complete(co)


def swait_multiple(cos):
    """Sync-wait for the given coroutines."""
    # asyncio.get_event_loop().run_until_complete(asyncio.wait(cos))  # stopped working
    asyncio.get_event_loop().run_until_complete(asyncio.gather(*cos))


def test_asyncify1():
    side_effect[0] = 0

    # Test the plain func
    t0 = time.perf_counter()
    assert plain_func(3) == 8
    t1 = time.perf_counter()
    assert (t1 - t0) > 0.99
    assert side_effect[0] == 10

    # Get that it rerturns a co
    co = asyncify(plain_func)(3)
    assert asyncio.iscoroutine(co)

    # Run it
    assert swait(co) == 8
    assert side_effect[0] == 20

    # Run a func that errors
    with raises(ValueError) as err:
        swait(asyncify(plain_func_that_errors)(3))
    assert err.value.args[0] == 3


def test_asyncif2():
    side_effect[0] = 0

    # Test that the decorator produces a co
    func = asyncify(plain_func)
    assert callable(func)
    assert "plain_func" in func.__name__
    co = func()
    assert asyncio.iscoroutine(co)
    co.close()  # avoid "never awaited" warning

    # Run it multiple times
    t0 = time.perf_counter()
    swait_multiple([func(3) for _ in range(5)])
    assert side_effect[0] == 50
    t1 = time.perf_counter()
    assert (t1 - t0) < 2


def test_AsyncItemDB_methods():
    methods1 = set(ItemDB.__dict__.keys())
    methods2 = set(AsyncItemDB.__dict__.keys())
    for name in methods1:
        if name.startswith("_"):
            continue
        assert name in methods2, f"{name} is missing"


def test_AsyncItemDB_threads():
    time.sleep(0.1)
    assert threading.active_count() < 20

    dbs1 = swait(_test_AsyncItemDB_threads())
    assert threading.active_count() > 100

    dbs2 = swait(_test_AsyncItemDB_threads())
    time.sleep(0.1)
    assert threading.active_count() > 200

    time.sleep(0.1)
    assert threading.active_count() > 200
    dbs1 = None  # noqa
    gc.collect()
    time.sleep(0.1)
    assert threading.active_count() > 100
    assert threading.active_count() < 200

    dbs2 = None  # noqa
    gc.collect()
    time.sleep(0.1)
    assert threading.active_count() < 20


async def _test_AsyncItemDB_threads():
    dbs = []
    for _ in range(100):
        dbs.append(await AsyncItemDB(":memory:"))
    return dbs


def test_AsyncItemDB():
    swait(_test_AsyncItemDB())


async def _test_AsyncItemDB():
    db = await AsyncItemDB(":memory:")
    assert await db.get_table_names() == []  # no tables

    await db.ensure_table("foo", "key")
    await db.ensure_table("bar")
    assert await db.count_all("foo") == 0
    assert await db.count_all("bar") == 0
    assert await db.get_indices("foo") == {"key"}

    db = await AsyncItemDB(":memory:")
    await db.ensure_table("items", "!id", "mt")

    with raises(IOError):  # Put needs to be used under a context
        await db.put("items", dict(id=1, mt=100))

    with raises(TypeError):  # Normal with not allowed
        with db:
            pass

    # ----- Adding items

    async with db:
        await db.put("items", dict(id=1, mt=100))
        await db.put("items", dict(id=2, mt=100, value=42))
        await db.put("items", dict(id=3, value=42))
    assert len(await db.select_all("items")) == 3
    assert await db.count_all("items") == 3
    assert len(await db.select("items", "mt == 100")) == 2
    assert await db.count("items", "mt == 100") == 2
    with raises(IndexError):  # No index for value
        await db.select("items", "value == 42")

    assert (await db.select_one("items", "id == 3"))["value"] == 42
    async with db:
        await db.delete("items", "id == 3")
    assert (await db.select_one("items", "id == 3")) is None

    assert db.mtime == -1
    await db.close()

    # ----- Transactions

    db = await AsyncItemDB(":memory:")
    await db.ensure_table("items", "!id", "mt")
    async with db:
        await db.put_one("items", id=1, mt=100)
        await db.put_one("items", id=2, mt=100)
    assert await db.count_all("items") == 2
    with raises(RuntimeError):
        async with db:
            await db.put_one("items", id=3, mt=100)
            await db.put_one("items", id=4, mt=100)
        raise RuntimeError("Transaction has been comitted")
    assert await db.count_all("items") == 4
    with raises(RuntimeError):
        async with db:
            await db.put_one("items", id=5, mt=100)
            await db.put_one("items", id=6, mt=100)
            raise RuntimeError("Abort transaction!")
    assert await db.count_all("items") == 4

    # ----- rename and delete table

    db = await AsyncItemDB(":memory:")
    await db.ensure_table("persons", "!name")
    async with db:
        await db.put_one("persons", name="Jan", age=30)
        await db.put_one("persons", name="Henk", age=42)
    assert await db.count_all("persons") == 2

    async with db:
        await db.rename_table("persons", "clients")

    assert await db.count_all("clients") == 2
    with raises(KeyError):
        await db.count_all("persons")

    async with db:
        await db.delete_table("clients")

    with raises(KeyError):
        await db.count_all("clients")


if __name__ == "__main__":
    run_tests(globals())

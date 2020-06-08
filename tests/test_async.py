import time
import asyncio

from pytest import raises

from testutils import run_tests
from itemdb import asyncify


side_effect = [0]


def plain_func(x):
    time.sleep(1)  # emulate io
    side_effect[0] += 10
    return x + 5


def plain_func_that_errors(x):
    raise ValueError(x)


def swait(co):
    """ Sync-wait for the given coroutine, and return the result. """
    return asyncio.get_event_loop().run_until_complete(co)


def swait_multiple(cos):
    """ Sync-wait for the given coroutines. """
    asyncio.get_event_loop().run_until_complete(asyncio.wait(cos))


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
    swait_multiple([func(3) for i in range(5)])
    assert side_effect[0] == 50
    t1 = time.perf_counter()
    assert (t1 - t0) < 2


if __name__ == "__main__":
    run_tests(globals())

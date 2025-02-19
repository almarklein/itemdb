import time
import asyncio
import threading
import concurrent

import itemdb


# %% More theoretical test, without using an actual ItemDB

executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=None, thread_name_prefix="itemdb"
)


async def run_in_thread(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def thread_func():
        result = func(*args, **kwargs)
        loop.call_soon_threadsafe(future.set_result, result)

    t = threading.Thread(target=thread_func)
    t.start()
    return await future


class FakeItemDB:
    def work(self):
        pass  # time.sleep(0)

    async def work_async0(self):
        # Not actually async
        return self.work()

    async def work_async1(self):
        # Using a thread
        return await run_in_thread(self.work)

    async def work_async2(self):
        # Using a thread pool
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, self.work)


async def do_some_work(method_name):
    n = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.5:
        db = FakeItemDB()
        await getattr(db, method_name)()
        n += 1
    etime = time.perf_counter() - t0
    return etime / n


def check_speed_of_async():
    # Try out multiple ways to implement async. The overhead for the threading
    # is in favor of the thread pool, but it's really tiny, in the order of 100us.
    # Plus starting 40 threads might be overkill on many cases too. Creating
    # a thread per db might make more sense ...
    # Example run:
    # work_async0 1.1372491800444893e-06
    # work_async1 0.0002502583791895948
    # work_async2 0.00015008997599039617

    loop = asyncio.new_event_loop()
    for i in range(3):
        method_name = f"work_async{i}"
        t = loop.run_until_complete(do_some_work(method_name))
        print(method_name, t)


# %% Practical test, comparing asyncify vs AsyncItemDB


async def do_work_using_asyncify():
    def work():
        db = itemdb.ItemDB(":memory:")
        db.ensure_table("items")
        with db:
            db.put_one("items", id=1, name="foo")
            db.put_one("items", id=1, name="bar")

    await itemdb.asyncify(work)()


async def do_work_using_asyncitemdb():
    async def work():
        db = await itemdb.AsyncItemDB(":memory:")
        await db.ensure_table("items")
        async with db:
            await db.put_one("items", id=1, name="foo")
            await db.put_one("items", id=1, name="bar")

    await work()


def check_speed_of_async_itemdb():
    co = _check_speed_of_async_itemdb()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(co)


async def _check_speed_of_async_itemdb():
    # The AsyncItemDB approach is less efficient than using asyncify.
    # The amount differs a lot between runs, but ranges from 20-30% with the
    # current measurement. Using a threadpool with AsyncItemDB might reduce
    # this discrepancy. Note that aiosqlite also uses a new thread per connection.

    n = 500

    time.sleep(0.1)
    t0 = time.perf_counter()

    for _i in range(n):
        await do_work_using_asyncify()

    t1 = time.perf_counter()
    print(f"with asyncify:    {(t1 - t0) / n:0.9f} s")

    time.sleep(0.1)
    t0 = time.perf_counter()

    for _i in range(n):
        await do_work_using_asyncitemdb()

    t1 = time.perf_counter()
    print(f"with AsyncItemDB: {(t1 - t0) / n:0.9f} s")


if __name__ == "__main__":
    check_speed_of_async()
    check_speed_of_async_itemdb()

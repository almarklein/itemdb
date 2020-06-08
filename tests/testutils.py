import os
import sys

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TEST_DIR)

# This makes it possible to run the tests as scripts
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def run_tests(scope):
    """ Run all test functions in the given scope.
    """
    for func in list(scope.values()):
        if callable(func) and func.__name__.startswith("test_"):
            print(f"Running {func.__name__} ...")
            func()
    print("Done")

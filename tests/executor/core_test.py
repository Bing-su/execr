import pytest
from execr.executor.core import binary


def test_binary():
    try:
        binary()
    except Exception as e:
        pytest.fail(f"binary() raised an exception: {e}")

import pytest

from energy import store


@pytest.fixture
def db():
    conn = store.connect(":memory:")
    yield conn
    conn.close()

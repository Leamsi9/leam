import pytest
from shared_coding_fixtures import SHARED_THREAD


@pytest.fixture(autouse=True)
def synthetic_shared_coding_installation(monkeypatch):
    """Existing configured-session tests use a synthetic installation binding."""
    monkeypatch.setenv("LEAM_SHARED_CODEX_THREAD", SHARED_THREAD)

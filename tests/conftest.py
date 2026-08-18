"""Shared fixtures.

Every test runs against a THROWAWAY ledger in a temp directory. That has to be
set before any alphadesk import, because config.py resolves DATA_DIR at import
time — get this wrong and a test run writes into the developer's real
~/.alphadesk.
"""

import os
import tempfile

import pytest

os.environ.setdefault("ALPHADESK_DATA", tempfile.mkdtemp(prefix="alphadesk-test-"))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A fresh, isolated ledger per test."""
    monkeypatch.setenv("ALPHADESK_DATA", str(tmp_path))
    import importlib

    from alphadesk import config
    importlib.reload(config)
    from alphadesk.ledger import store as store_mod
    importlib.reload(store_mod)
    store_mod.init()
    return store_mod


@pytest.fixture()
def client(store):
    """TestClient over the real app, wired to the throwaway ledger."""
    from fastapi.testclient import TestClient

    from alphadesk.app import dashboard
    return TestClient(dashboard.app)


@pytest.fixture(autouse=True)
def _reset_providers():
    """Provider selection is cached for the process; clear it between tests so
    one test's NEWS_PROVIDER can't leak into the next."""
    from alphadesk.providers import registry
    registry.reset_cache()
    yield
    registry.reset_cache()

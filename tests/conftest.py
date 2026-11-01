"""Load the plugin (its directory name has a hyphen) and share fixtures."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_plugin() -> None:
    if "hermes_todo" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "hermes_todo", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["hermes_todo"] = pkg
    spec.loader.exec_module(pkg)


_load_plugin()

from hermes_todo import db, tools, transitions  # noqa: E402

REPO = "/repos/main"


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    yield db.Store(conn)
    conn.close()


@pytest.fixture
def wired(store, monkeypatch):
    """The tools, pointed at an in-memory store and one repo's list."""
    monkeypatch.setattr(tools, "_STORE", store)
    monkeypatch.setattr(tools, "list_dir", lambda: REPO)
    yield store

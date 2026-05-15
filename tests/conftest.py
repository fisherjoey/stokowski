"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from stokowski.storage import StateStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> StateStore:
    """A fresh StateStore backed by a tmp_path SQLite file."""
    store = StateStore(tmp_path / "state.db")
    yield store
    store.close()


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

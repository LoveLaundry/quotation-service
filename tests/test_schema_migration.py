"""The startup migration must add multi-tenancy columns to existing tables."""

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.quotation_service import main
from src.quotation_service.config import DatabaseType
from src.quotation_service import database


def _columns(engine, table):
    with engine.connect() as conn:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def test_migration_adds_laundry_id_to_legacy_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE quotations (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE garment_tags (id INTEGER PRIMARY KEY)"))

    monkeypatch.setattr(main, "DB_TYPE", DatabaseType.SQLITE)
    monkeypatch.setattr(database, "engine", engine)

    main._migrate_quotation_schema()

    assert "laundry_id" in _columns(engine, "quotations")
    assert "laundry_id" in _columns(engine, "garment_tags")
    assert "status_history" in _columns(engine, "quotations")

    # Idempotent: a second run must not raise.
    main._migrate_quotation_schema()
    assert "laundry_id" in _columns(engine, "quotations")

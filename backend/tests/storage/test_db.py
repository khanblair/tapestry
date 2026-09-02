"""Tests for tapestry.storage.db -- get_connection/init_schema round-trip.

get_connection is part of the cross-package interface contract
(core/events.py imports it directly), so these tests exercise it the way a
real caller would: get a connection, confirm the schema exists, write/read
through it, confirm repeated calls return the cached singleton.
"""

from __future__ import annotations

import sqlite3

import pytest

from tapestry.storage.db import get_connection, init_schema


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_tapestry.sqlite")


class TestGetConnection:
    def test_returns_sqlite_connection_with_row_factory(self, db_path):
        conn = get_connection(db_path)

        assert isinstance(conn, sqlite3.Connection)
        assert conn.row_factory is sqlite3.Row

    def test_schema_applied_automatically_on_first_call(self, db_path):
        conn = get_connection(db_path)

        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "conversations" in tables
        assert "events" in tables

    def test_repeated_calls_with_same_path_return_same_connection(self, db_path):
        conn1 = get_connection(db_path)
        conn2 = get_connection(db_path)

        assert conn1 is conn2

    def test_reads_env_var_when_path_not_given(self, tmp_path, monkeypatch):
        env_path = str(tmp_path / "env_tapestry.sqlite")
        monkeypatch.setenv("TAPESTRY_DB_PATH", env_path)

        conn = get_connection()

        assert conn is get_connection(env_path)


class TestInitSchema:
    def test_idempotent(self, db_path):
        conn = get_connection(db_path)

        # Calling init_schema again must not raise (CREATE ... IF NOT EXISTS).
        init_schema(conn)
        init_schema(conn)

    def test_conversation_and_event_round_trip(self, db_path):
        conn = get_connection(db_path)

        conn.execute(
            "INSERT INTO conversations (id, kind, name, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("conv-1", "dm", "Ada & human", "2026-09-02T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO events "
            "(id, conversation_id, type, timestamp, actor, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "evt-1",
                "conv-1",
                "message.created",
                "2026-09-02T00:00:01Z",
                "ada",
                '{"text": "hello"}',
            ),
        )
        conn.commit()

        conversation = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", ("conv-1",)
        ).fetchone()
        event = conn.execute(
            "SELECT * FROM events WHERE conversation_id = ?", ("conv-1",)
        ).fetchone()

        assert conversation["kind"] == "dm"
        assert conversation["name"] == "Ada & human"
        assert event["type"] == "message.created"
        assert event["actor"] == "ada"
        assert event["payload_json"] == '{"text": "hello"}'

    def test_events_indexed_on_conversation_id(self, db_path):
        conn = get_connection(db_path)

        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='events'"
            ).fetchall()
        }
        assert "idx_events_conversation_id" in indexes

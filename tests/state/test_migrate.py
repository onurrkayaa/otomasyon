import psycopg
import pytest

from kernel.state import db, migrate


def test_migrations_idempotent():
    """İkinci koşu hiçbir şey uygulamamalı."""
    assert migrate.apply_migrations() == []


def test_expected_tables_exist():
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        ).fetchall()
    names = {r[0] for r in rows}
    for expected in ("runs", "steps", "events", "tool_calls", "job_queue"):
        assert expected in names


def test_events_table_rejects_update():
    """Kural 5 veritabanı seviyesinde uygulanıyor mu."""
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key) VALUES"
            " ('11111111-1111-1111-1111-111111111111', 't1', 'wf', 'h1',"
            " 'pending', 1.0, 10, 'k1')"
        )
        conn.execute(
            "INSERT INTO events (run_id, seq, type) VALUES"
            " ('11111111-1111-1111-1111-111111111111', 1, 'run_created')"
        )

    with pytest.raises(psycopg.errors.RaiseException):
        with db.tx() as conn:
            conn.execute("UPDATE events SET type = 'degistirildi'")

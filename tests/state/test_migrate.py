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


def test_migrations_serialize_on_an_advisory_lock():
    """Küçük 3: aynı anda başlayan iki deploy'da biri CREATE TABLE ile patlar."""
    import threading

    done = threading.Event()
    errors: list[Exception] = []

    def _apply():
        try:
            migrate.apply_migrations()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=_apply)
    with db.tx() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)", (migrate.MIGRATION_LOCK_ID,)
        )
        thread.start()
        assert not done.wait(1.0), "göç koşucusu advisory lock'u beklemeliydi"

    thread.join(timeout=10)
    assert done.is_set()
    assert errors == []

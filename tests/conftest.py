from __future__ import annotations

import os

import pytest

TEST_DSN = os.environ.get(
    "OTOMASYON_TEST_DSN", "postgresql://localhost/otomasyon_test"
)

TABLES = [
    "side_effects", "job_queue", "tool_calls",
    "events", "steps", "runs",
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    os.environ["OTOMASYON_DB_DSN"] = TEST_DSN
    from kernel.state import db, migrate

    db.reset_pool()
    with db.tx() as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db.reset_pool()
    migrate.apply_migrations()
    yield
    db.reset_pool()


@pytest.fixture(autouse=True)
def _clean():
    from kernel.state import db

    with db.tx() as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    yield

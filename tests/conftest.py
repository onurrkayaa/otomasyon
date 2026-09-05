from __future__ import annotations

import os

import pytest

TEST_DSN = os.environ.get(
    "OTOMASYON_TEST_DSN", "postgresql://localhost/otomasyon_test"
)

def require_test_database(dsn: str) -> None:
    """Şema DROP ediliyor: yanlış DSN ile koşulan suite üretim şemasını siler."""
    name = dsn.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in name.lower():
        pytest.exit(
            f"OTOMASYON_TEST_DSN bir test veritabanına işaret etmiyor: {name!r}."
            " Suite 'DROP SCHEMA public CASCADE' çalıştırır; iptal edildi.",
            returncode=2,
        )


TABLES = [
    "side_effects", "job_queue", "tool_calls",
    "events", "steps", "runs",
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    require_test_database(TEST_DSN)
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

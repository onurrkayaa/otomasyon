"""Idempotent göç koşucusu.

Alembic kasıtlı olarak kullanılmıyor: M1'de tek bir ileri-yönlü göç var ve
Alembic bir bağımlılık + konfig dizini + revizyon grafiği getiriyor. İhtiyaç
doğduğunda (M2'de şema büyüyünce) geçilir.
"""
from __future__ import annotations

from pathlib import Path

from kernel.state import db

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations() -> list[str]:
    """Uygulanmamış göçleri sırayla uygular. Uygulananların adını döner."""
    applied: list[str] = []
    with db.tx() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        done = {r[0] for r in rows}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    for name in apply_migrations():
        print(f"uygulandı: {name}")

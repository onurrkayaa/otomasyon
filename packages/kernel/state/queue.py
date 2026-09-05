"""Postgres tabanlı kiralı iş kuyruğu.

Dayanıklılık modeli: bir iş "kira" ile alınır. Worker çökerse kira dolar ve iş
başka bir worker tarafından alınabilir hale gelir. Harici bir kuyruk servisi
YOK (K3): tek altyapı bağımlılığı Postgres.
"""
from __future__ import annotations

from uuid import UUID

import psycopg
from pydantic import BaseModel


class Job(BaseModel):
    id: int
    run_id: UUID
    node_id: str
    attempts: int


def enqueue(
    conn: psycopg.Connection, run_id: UUID, node_id: str, delay_seconds: int = 0
) -> int:
    row = conn.execute(
        "INSERT INTO job_queue (run_id, node_id, available_at)"
        " VALUES (%s, %s, now() + make_interval(secs => %s)) RETURNING id",
        (run_id, node_id, delay_seconds),
    ).fetchone()
    assert row is not None
    return int(row[0])


def claim(
    conn: psycopg.Connection, worker_id: str, lease_seconds: int = 30
) -> Job | None:
    row = conn.execute(
        "UPDATE job_queue SET"
        "   locked_by = %s,"
        "   locked_until = now() + make_interval(secs => %s),"
        "   attempts = attempts + 1"
        " WHERE id = ("
        "   SELECT id FROM job_queue"
        "   WHERE available_at <= now()"
        "     AND (locked_until IS NULL OR locked_until < now())"
        "   ORDER BY available_at"
        "   FOR UPDATE SKIP LOCKED"
        "   LIMIT 1"
        " )"
        " RETURNING id, run_id, node_id, attempts",
        (worker_id, lease_seconds),
    ).fetchone()
    if row is None:
        return None
    return Job(id=row[0], run_id=row[1], node_id=row[2], attempts=row[3])


def complete(conn: psycopg.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM job_queue WHERE id = %s", (job_id,))


def release(conn: psycopg.Connection, job_id: int, delay_seconds: int) -> None:
    """İşi kuyruğa geri koyar; kilidi bırakır ve erişimi geciktirir."""
    conn.execute(
        "UPDATE job_queue SET locked_by = NULL, locked_until = NULL,"
        " available_at = now() + make_interval(secs => %s) WHERE id = %s",
        (delay_seconds, job_id),
    )

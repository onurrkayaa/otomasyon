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
    worker_id: str


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
        " RETURNING id, run_id, node_id, attempts, locked_by",
        (worker_id, lease_seconds),
    ).fetchone()
    if row is None:
        return None
    return Job(
        id=row[0],
        run_id=row[1],
        node_id=row[2],
        attempts=row[3],
        worker_id=row[4],
    )


def complete(conn: psycopg.Connection, job_id: int, worker_id: str) -> bool:
    """İşi kuyruktan çıkarır. Yalnız kirası HÂLÂ GEÇERLİ olan sahip silebilir.

    False = kira kaybedilmiş: iş başkası tarafından devralınmış, bu worker'ın
    ilerlemesi geçersizdir. Çağıran bu durumda hiçbir sonuç yazmamalıdır
    (`_mark`'taki sahiplik korumasının kuyruk katmanındaki muadili).
    """
    cur = conn.execute(
        "DELETE FROM job_queue WHERE id = %s AND locked_by = %s"
        " AND locked_until > now()",
        (job_id, worker_id),
    )
    return cur.rowcount > 0


def release(
    conn: psycopg.Connection, job_id: int, worker_id: str, delay_seconds: int
) -> bool:
    """İşi kuyruğa geri koyar; kilidi bırakır ve erişimi geciktirir.

    False = kira kaybedilmiş (bkz. `complete`): yeni sahibin kilidi
    düşürülmez, bu worker'ın ilerlemesi geçersizdir.
    """
    cur = conn.execute(
        "UPDATE job_queue SET locked_by = NULL, locked_until = NULL,"
        " available_at = now() + make_interval(secs => %s)"
        " WHERE id = %s AND locked_by = %s AND locked_until > now()",
        (delay_seconds, job_id, worker_id),
    )
    return cur.rowcount > 0

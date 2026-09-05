"""Ekleme-yalnız olay kaydı (Kural 5).

seq numarası run başına ardışıktır. Yarış koşulunu önlemek için run satırı
FOR UPDATE ile kilitlenir: events tablosunda henüz satır yokken oradan kilit
alınamaz, ama run satırı her zaman vardır.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel


class Event(BaseModel):
    seq: int
    type: str
    payload: dict
    created_at: datetime


def append(conn: psycopg.Connection, run_id: UUID, type: str, payload: dict) -> int:
    conn.execute("SELECT id FROM runs WHERE id = %s FOR UPDATE", (run_id,))
    row = conn.execute(
        "INSERT INTO events (run_id, seq, type, payload)"
        " SELECT %s, coalesce(max(seq), 0) + 1, %s, %s FROM events WHERE run_id = %s"
        " RETURNING seq",
        (run_id, type, Jsonb(payload), run_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def read(conn: psycopg.Connection, run_id: UUID) -> list[Event]:
    rows = conn.execute(
        "SELECT seq, type, payload, created_at FROM events"
        " WHERE run_id = %s ORDER BY seq",
        (run_id,),
    ).fetchall()
    return [
        Event(seq=r[0], type=r[1], payload=r[2], created_at=r[3]) for r in rows
    ]

"""İki aşamalı kiralı rezervasyon (§5.1 / K15).

  1. REZERVE ET — tool_calls satırı KENDİ transaction'ında commit edilir.
     Dış transaction'a bağlanırsa rollback'te rezervasyon buharlaşır ve
     çift yazma koruması ortadan kalkar. UNIQUE ihlali = başkası üstlendi.
  2. ÇAĞIR    — dış sistem çağrısı.
  3. TAMAMLA  — status='completed' + yanıt kaydı.

Kirası dolmuş bir rezervasyon ASLA kör tekrar edilmez: önce reconcile,
sonra external_idempotency, ikisi de yoksa 'uncertain' → insan kuyruğu.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from kernel.state import db
from kernel.tools.base import Tool, ToolOutcome, ToolResult


def execute_tool(
    tool: Tool,
    run_id: UUID,
    step_id: UUID,
    payload: dict,
    lease_seconds: int = 60,
) -> ToolResult:
    key = tool.idempotency_key(run_id, payload)

    if _reserve(tool.name, step_id, key, payload, lease_seconds):
        return _call_and_complete(tool, key, payload)
    return _handle_existing(tool, key, payload, lease_seconds)


# --- 1. aşama -----------------------------------------------------------------

def _reserve(
    tool_name: str, step_id: UUID, key: str, payload: dict, lease_seconds: int
) -> bool:
    """True = rezervasyon bizim. False = anahtar zaten alınmış."""
    try:
        with db.independent_tx() as conn:
            conn.execute(
                "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
                " request, status, lease_expires_at) VALUES"
                " (%s, %s, %s, %s, %s, 'reserved', now() + make_interval(secs => %s))",
                (uuid4(), step_id, tool_name, key, Jsonb(payload), lease_seconds),
            )
        return True
    except psycopg.errors.UniqueViolation:
        return False


# --- 2. ve 3. aşama -----------------------------------------------------------

def _call_and_complete(tool: Tool, key: str, payload: dict) -> ToolResult:
    try:
        response = tool.execute(payload)
    except Exception as exc:  # noqa: BLE001 — her dış hata kaydedilir
        _mark(key, "failed", {"error": str(exc)})
        return ToolResult(outcome=ToolOutcome.FAILED, error=str(exc))
    _mark(key, "completed", response)
    return ToolResult(outcome=ToolOutcome.COMPLETED, response=response)


def _mark(key: str, status: str, response: dict) -> None:
    with db.independent_tx() as conn:
        conn.execute(
            "UPDATE tool_calls SET status = %s, response = %s, completed_at = now()"
            " WHERE idempotency_key = %s",
            (status, Jsonb(response), key),
        )


# --- Anahtar zaten alınmışsa --------------------------------------------------

def _handle_existing(
    tool: Tool, key: str, payload: dict, lease_seconds: int
) -> ToolResult:
    with db.independent_tx() as conn:
        row = conn.execute(
            "SELECT status, response, (lease_expires_at < now()) AS expired"
            " FROM tool_calls WHERE idempotency_key = %s",
            (key,),
        ).fetchone()

    if row is None:
        # Yarış: satır az önce silinmiş olabilir. İşi ertele, baştan denensin.
        return ToolResult(outcome=ToolOutcome.DEFERRED, retry_after_seconds=1)

    status, response, expired = row[0], row[1], row[2]

    if status == "completed":
        return ToolResult(outcome=ToolOutcome.COMPLETED, response=response)

    if status == "uncertain":
        return ToolResult(
            outcome=ToolOutcome.UNCERTAIN,
            error="önceki çağrının sonucu belirsiz; insan incelemesi gerekli",
        )

    if status == "failed":
        if _retake_failed(key, lease_seconds):
            return _call_and_complete(tool, key, payload)
        return ToolResult(outcome=ToolOutcome.DEFERRED, retry_after_seconds=1)

    # status == 'reserved'
    if not expired:
        # Başka bir worker çalışıyor. Bloke OLMA — worker'ı meşgul tutar.
        return ToolResult(
            outcome=ToolOutcome.DEFERRED, retry_after_seconds=lease_seconds
        )

    return _recover_expired(tool, key, payload, lease_seconds)


def _retake_failed(key: str, lease_seconds: int) -> bool:
    """Başarısız bir çağrıyı atomik olarak yeniden rezerve eder."""
    with db.independent_tx() as conn:
        row = conn.execute(
            "UPDATE tool_calls SET status = 'reserved', response = NULL,"
            " completed_at = NULL,"
            " lease_expires_at = now() + make_interval(secs => %s)"
            " WHERE idempotency_key = %s AND status = 'failed' RETURNING id",
            (lease_seconds, key),
        ).fetchone()
    return row is not None


def _recover_expired(
    tool: Tool, key: str, payload: dict, lease_seconds: int
) -> ToolResult:
    """Kirası dolmuş rezervasyon — çağrı dış sisteme ulaştı mı bilinmiyor."""
    with db.independent_tx() as conn:
        row = conn.execute(
            "UPDATE tool_calls"
            " SET lease_expires_at = now() + make_interval(secs => %s)"
            " WHERE idempotency_key = %s AND status = 'reserved'"
            "   AND lease_expires_at < now() RETURNING id",
            (lease_seconds, key),
        ).fetchone()
    if row is None:
        # Başkası devraldı.
        return ToolResult(
            outcome=ToolOutcome.DEFERRED, retry_after_seconds=lease_seconds
        )

    if tool.supports_reconcile:
        found = tool.reconcile(payload)
        if found is not None:
            _mark(key, "completed", found)
            return ToolResult(outcome=ToolOutcome.COMPLETED, response=found)
        return _call_and_complete(tool, key, payload)

    if tool.external_idempotency:
        return _call_and_complete(tool, key, payload)

    _mark(key, "uncertain", {"reason": "kira doldu, doğrulama yolu yok"})
    return ToolResult(
        outcome=ToolOutcome.UNCERTAIN,
        error="kira doldu ve reconcile/external_idempotency yok; kör tekrar yapılmadı",
    )

"""Tek adım yürütücü.

Bir adımın yürütülmesi bir steps satırı açar, düğüm tipine göre dağıtır ve
sonuca göre akışı ilerletir. Yeniden denemede YENİ bir steps satırı açılır
(attempt artar); çökmüş denemenin satırı 'running' olarak kalır ve denetim
izinin parçasıdır.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from kernel import config
from kernel.gateway.gateway import BudgetExceeded, Gateway, RefusalError
from kernel.gateway.types import LLMRequest
from kernel.orchestrator import flow
from kernel.state import db, events, queue
from kernel.tools import execution, registry
from kernel.tools.base import ToolOutcome

# Ham istisna metni SİLİNEMEYEN olay kaydına yazılmaz (Kural 5): sızan bir
# anahtar ya da müşteri verisi bir daha temizlenemez. Olaya yalnız sınıf adı ve
# kırpılmış özet gider; ham metin steps.error kolonunda kalır (o UPDATE edilebilir).
ERROR_SUMMARY_LIMIT = 200


def error_payload(error_class: str, error: str) -> dict:
    return {
        "error_class": error_class,
        "error_summary": error[:ERROR_SUMMARY_LIMIT],
    }


# Katman 1: ürün sabiti. Bayt bayt sabit tutulur — §6.2 önbellek noktası 1.
SYSTEM_LAYER1 = (
    "Sen kurumsal bir belge işleme otomasyonunun parçasısın. "
    "Girdiyi tek cümlede özetle. Yorum ekleme, soru sorma."
)


def execute_step(job: queue.Job, gateway: Gateway) -> str:
    node = flow.M1_FLOW[job.node_id]
    step_id = uuid4()

    with db.tx() as conn:
        run = conn.execute(
            "SELECT input, tenant_id FROM runs WHERE id = %s", (job.run_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, attempt, status, input)"
            " VALUES (%s, %s, %s, %s, 'running', %s)",
            (step_id, job.run_id, node.id, job.attempts, Jsonb(run[0])),
        )
        events.append(
            conn,
            job.run_id,
            "step_started",
            {"node_id": node.id, "attempt": job.attempts, "step_id": str(step_id)},
        )

    payload, tenant_id = run[0], run[1]

    if node.type == "llm_task":
        return _run_llm_task(job, node, step_id, payload, tenant_id, gateway)
    return _run_tool(job, node, step_id, payload)


def _run_llm_task(
    job: queue.Job,
    node: flow.Node,
    step_id: UUID,
    payload: dict,
    tenant_id: str,
    gateway: Gateway,
) -> str:
    assert node.tier is not None
    request = LLMRequest(
        tier=node.tier,
        system_layer1=SYSTEM_LAYER1,
        # Katman 2: kiracıya özel, çalışmalar arası sabit.
        system_layer2=f"Kiracı: {tenant_id}.",
        # Katman 3: adımın beyan edilmiş girdisi (Kural 3).
        user_content=str(payload.get("text", "")),
    )
    try:
        response = gateway.complete(request, job.run_id, step_id)
    except BudgetExceeded as exc:
        return _fail_run(job, step_id, "budget_exceeded", str(exc))
    except RefusalError as exc:
        return _fail_run(job, step_id, "failed", str(exc))

    _finish_step(job, step_id, {"ozet": response.text})
    return _advance(job, node)


def _run_tool(
    job: queue.Job, node: flow.Node, step_id: UUID, payload: dict
) -> str:
    assert node.tool is not None
    result = execution.execute_tool(
        registry.get(node.tool),
        job.run_id,
        step_id,
        payload,
        lease_seconds=config.TOOL_LEASE_SECONDS,
    )

    if result.outcome is ToolOutcome.COMPLETED:
        _finish_step(job, step_id, result.response or {})
        return _advance(job, node)

    if result.outcome is ToolOutcome.DEFERRED:
        with db.tx() as conn:
            conn.execute(
                "UPDATE steps SET status = 'failed', ended_at = now(),"
                " error = 'ertelendi' WHERE id = %s",
                (step_id,),
            )
            queue.release(conn, job.id, result.retry_after_seconds)
            events.append(
                conn, job.run_id, "step_deferred", {"node_id": node.id}
            )
        return "deferred"

    if result.outcome is ToolOutcome.UNCERTAIN:
        return _fail_run(job, step_id, "uncertain", result.error or "belirsiz")

    return _fail_run(job, step_id, "failed", result.error or "araç hatası")


# --- ortak ---

def _finish_step(job: queue.Job, step_id: UUID, output: dict) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE steps SET status = 'completed', output = %s, ended_at = now()"
            " WHERE id = %s",
            (Jsonb(output), step_id),
        )
        conn.execute(
            "UPDATE runs SET step_count = step_count + 1, updated_at = now()"
            " WHERE id = %s",
            (job.run_id,),
        )
        events.append(
            conn, job.run_id, "step_completed", {"step_id": str(step_id)}
        )


def _advance(job: queue.Job, node: flow.Node) -> str:
    with db.tx() as conn:
        queue.complete(conn, job.id)
        if node.next is None:
            conn.execute(
                "UPDATE runs SET status = 'completed', updated_at = now()"
                " WHERE id = %s",
                (job.run_id,),
            )
            events.append(conn, job.run_id, "run_completed", {})
            return "run_completed"
        queue.enqueue(conn, job.run_id, node.next)
        return "advanced"


def _fail_run(job: queue.Job, step_id: UUID, run_status: str, error: str) -> str:
    with db.tx() as conn:
        conn.execute(
            "UPDATE steps SET status = 'failed', error = %s, ended_at = now()"
            " WHERE id = %s",
            (error, step_id),
        )
        conn.execute(
            "UPDATE runs SET status = %s, updated_at = now() WHERE id = %s",
            (run_status, job.run_id),
        )
        queue.complete(conn, job.id)
        events.append(
            conn, job.run_id, "run_" + run_status, {"error": error}
        )
    return "run_" + run_status

import uuid

import pytest
from psycopg.types.json import Jsonb

from kernel.state import db
from kernel.tools import execution, registry
from kernel.tools.base import ToolOutcome
from tests.fakes import NoReconcileTool, SlowWriterTool


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    registry.register(SlowWriterTool())
    registry.register(NoReconcileTool())
    yield
    registry.clear()


def _make_run_and_step() -> tuple[uuid.UUID, uuid.UUID]:
    run_id, step_id = uuid.uuid4(), uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'running', 1.0, 10, %s)",
            (run_id, str(run_id)),
        )
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, status)"
            " VALUES (%s, %s, 'kaydet', 'running')",
            (step_id, run_id),
        )
    return run_id, step_id


def _orphan_reservation(step_id: uuid.UUID, key: str, payload: dict) -> None:
    """Çökmüş bir worker'ın bıraktığı, kirası dolmuş rezervasyonu simüle eder."""
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at) VALUES"
            " (%s, %s, 'test.slow_writer', %s, %s, 'reserved',"
            "  now() - interval '1 second')",
            (uuid.uuid4(), step_id, key, Jsonb(payload)),
        )


def _count(key: str) -> int:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT count(*) FROM side_effects WHERE key = %s", (key,)
        ).fetchone()
    return int(row[0])


def test_live_lease_defers_instead_of_blocking():
    """Kirası geçerli rezervasyon: worker bloke olmaz, iş ertelenir."""
    run_id, step_id = _make_run_and_step()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at) VALUES"
            " (%s, %s, 'test.slow_writer', %s, '{}'::jsonb, 'reserved',"
            "  now() + interval '60 seconds')",
            (uuid.uuid4(), step_id, f"{run_id}:c"),
        )

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "c"}
    )
    assert result.outcome is ToolOutcome.DEFERRED
    assert result.retry_after_seconds > 0
    assert _count("c") == 0


def test_expired_lease_with_reconcile_finding_write_does_not_rewrite():
    """Yazma gerçekleşmişti: reconcile bulur, TEKRAR YAZILMAZ."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:d"
    _orphan_reservation(step_id, key, {"key": "d"})
    # Çökmeden önce yazma gerçekleşmişti:
    with db.tx() as conn:
        conn.execute("INSERT INTO side_effects (key) VALUES ('d')")

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "d"}
    )

    assert result.outcome is ToolOutcome.COMPLETED
    assert _count("d") == 1, "yan etki tekrarlanmamalı"


def test_expired_lease_with_reconcile_finding_nothing_reexecutes():
    """Yazma gerçekleşmemişti: reconcile bulamaz, güvenle tekrar çalıştırılır."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:e"
    _orphan_reservation(step_id, key, {"key": "e"})

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "e"}
    )

    assert result.outcome is ToolOutcome.COMPLETED
    assert _count("e") == 1


def test_expired_lease_without_reconcile_becomes_uncertain():
    """Doğrulama yolu yok → kör tekrar YOK, insan kuyruğu."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:f"
    _orphan_reservation(step_id, key, {"key": "f"})

    result = execution.execute_tool(
        registry.get("test.no_reconcile"), run_id, step_id, {"key": "f"}
    )

    assert result.outcome is ToolOutcome.UNCERTAIN
    assert _count("f") == 0, "belirsiz durumda kör yazma yapılmamalı"

    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s", (key,)
        ).fetchone()
    assert row[0] == "uncertain"


def test_uncertain_stays_uncertain_on_retry():
    """Belirsiz bir çağrı otomatik olarak tekrar denenmez."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:g"
    _orphan_reservation(step_id, key, {"key": "g"})
    execution.execute_tool(
        registry.get("test.no_reconcile"), run_id, step_id, {"key": "g"}
    )

    again = execution.execute_tool(
        registry.get("test.no_reconcile"), run_id, step_id, {"key": "g"}
    )
    assert again.outcome is ToolOutcome.UNCERTAIN
    assert _count("g") == 0


def test_reconcile_exception_becomes_uncertain():
    """K1: reconcile'ın kendisi patlarsa istisna yukarı kaçmaz, sonuç 'uncertain'."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:h"
    _orphan_reservation(step_id, key, {"key": "h"})

    class ExplodingReconcile(SlowWriterTool):
        def reconcile(self, payload: dict) -> dict | None:
            raise ConnectionError("dış sisteme ulaşılamadı")

    result = execution.execute_tool(
        ExplodingReconcile(), run_id, step_id, {"key": "h"}
    )

    assert result.outcome is ToolOutcome.UNCERTAIN
    assert _count("h") == 0, "reconcile patlayınca kör tekrar yapılmamalı"
    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s", (key,)
        ).fetchone()
    assert row[0] == "uncertain"

import uuid

import pytest

from kernel.state import db
from kernel.tools import execution, registry
from kernel.tools.base import ToolOutcome
from tests.fakes import AlwaysFailsTool, SlowWriterTool


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    registry.register(SlowWriterTool())
    registry.register(AlwaysFailsTool())
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


def _side_effect_count(key: str) -> int:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT count(*) FROM side_effects WHERE key = %s", (key,)
        ).fetchone()
    return int(row[0])


def test_happy_path_writes_once_and_completes():
    run_id, step_id = _make_run_and_step()
    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "a"}
    )
    assert result.outcome is ToolOutcome.COMPLETED
    assert result.response == {"written": "a"}
    assert _side_effect_count("a") == 1

    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s",
            (f"{run_id}:a",),
        ).fetchone()
    assert row[0] == "completed"


def test_second_call_with_same_key_does_not_execute_again():
    """Idempotency'nin asıl kazancı: kaydedilmiş yanıt döner, çağrı yapılmaz."""
    run_id, step_id = _make_run_and_step()
    first = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "a"}
    )
    second = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "a"}
    )

    assert first.outcome is ToolOutcome.COMPLETED
    assert second.outcome is ToolOutcome.COMPLETED
    assert second.response == {"written": "a"}
    assert _side_effect_count("a") == 1, "ikinci çağrı yan etkiyi tekrarlamamalı"


def test_reservation_is_created_before_execution():
    """K15: rezervasyon satırı dış çağrıdan ÖNCE var olmalı."""
    run_id, step_id = _make_run_and_step()
    seen: list[str | None] = []

    class Peeking(SlowWriterTool):
        def execute(self, payload: dict) -> dict:
            with db.independent_tx() as conn:
                row = conn.execute(
                    "SELECT status FROM tool_calls WHERE idempotency_key = %s",
                    (f"{run_id}:{payload['key']}",),
                ).fetchone()
            seen.append(row[0] if row else None)
            return super().execute(payload)

    execution.execute_tool(Peeking(), run_id, step_id, {"key": "b"})
    assert seen == ["reserved"], "dış çağrı sırasında rezervasyon commit'li olmalı"


def test_failure_marks_failed_and_allows_retry():
    run_id, step_id = _make_run_and_step()
    first = execution.execute_tool(
        registry.get("test.always_fails"), run_id, step_id, {}
    )
    assert first.outcome is ToolOutcome.FAILED
    assert "dış sistem patladı" in (first.error or "")

    second = execution.execute_tool(
        registry.get("test.always_fails"), run_id, step_id, {}
    )
    assert second.outcome is ToolOutcome.FAILED, "başarısız iş yeniden denenebilmeli"

import pytest

from kernel.gateway.gateway import Gateway
from kernel.gateway.transport import OfflineTransport
from kernel.orchestrator import runner
from kernel.state import db, events
from kernel.tools import registry
from tests.fakes import register_tools


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    register_tools()
    yield
    registry.clear()


def _drain(worker_id: str = "w1", limit: int = 10) -> None:
    gw = Gateway(OfflineTransport())
    for _ in range(limit):
        if not runner.run_once(worker_id, gw):
            return


def test_full_run_completes_with_single_side_effect():
    run_id = runner.start_run("t1", {"text": "merhaba", "key": "k1"})
    _drain()

    with db.tx() as conn:
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        effects = conn.execute(
            "SELECT count(*) FROM side_effects WHERE key = 'k1'"
        ).fetchone()[0]
        types = [e.type for e in events.read(conn, run_id)]

    assert status == "completed"
    assert effects == 1
    assert types[0] == "run_created"
    assert types[-1] == "run_completed"
    assert "llm_call_completed" in types


def test_both_steps_recorded():
    run_id = runner.start_run("t1", {"text": "x", "key": "k2"})
    _drain()
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT node_id, status FROM steps WHERE run_id = %s ORDER BY started_at",
            (run_id,),
        ).fetchall()
    assert rows == [("ozetle", "completed"), ("kaydet", "completed")]


def test_uncertain_tool_stops_the_run():
    """reconcile sunmayan araçta kira dolarsa run 'uncertain' olur."""
    import uuid as _uuid

    from kernel.orchestrator import flow

    original = flow.M1_FLOW["kaydet"].tool
    flow.M1_FLOW["kaydet"].tool = "test.no_reconcile"
    try:
        run_id = runner.start_run("t1", {"text": "x", "key": "k3"})
        # Çökmüş worker'ın bıraktığı, kirası dolmuş rezervasyonu simüle et.
        with db.tx() as conn:
            step_id = _uuid.uuid4()
            conn.execute(
                "INSERT INTO steps (id, run_id, node_id, attempt, status)"
                " VALUES (%s, %s, 'kaydet', 0, 'failed')",
                (step_id, run_id),
            )
            conn.execute(
                "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
                " request, status, lease_expires_at) VALUES"
                " (%s, %s, 'test.no_reconcile', %s, '{}'::jsonb, 'reserved',"
                "  now() - interval '1 second')",
                (_uuid.uuid4(), step_id, f"{run_id}:k3"),
            )
        _drain()
        with db.tx() as conn:
            status = conn.execute(
                "SELECT status FROM runs WHERE id = %s", (run_id,)
            ).fetchone()[0]
            effects = conn.execute(
                "SELECT count(*) FROM side_effects WHERE key = 'k3'"
            ).fetchone()[0]
        assert status == "uncertain"
        assert effects == 0
    finally:
        flow.M1_FLOW["kaydet"].tool = original

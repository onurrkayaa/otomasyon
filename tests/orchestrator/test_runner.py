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


def test_step_exception_does_not_escape_run_once(monkeypatch):
    """K1: uygulama istisnası worker'ı öldürmemeli, iş kuyruğa geri dönmeli."""
    from kernel.orchestrator import flow

    monkeypatch.setitem(
        flow.M1_FLOW,
        "kaydet",
        flow.Node(id="kaydet", type="tool", tool="kayitli.olmayan.arac"),
    )
    run_id = runner.start_run("t1", {"text": "x", "key": "k4"})
    gw = Gateway(OfflineTransport())

    assert runner.run_once("w1", gw) is True, "ozetle adımı"
    assert runner.run_once("w1", gw) is True, "patlayan adım istisna sızdırmamalı"

    with db.tx() as conn:
        jobs = conn.execute(
            "SELECT locked_by, available_at > now() FROM job_queue WHERE run_id = %s",
            (run_id,),
        ).fetchall()
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]

    assert len(jobs) == 1, "iş kuyrukta kalmalı"
    assert jobs[0][0] is None, "kilit bırakılmalı"
    assert jobs[0][1] is True, "geri çekilme gecikmesi uygulanmalı"
    assert status == "running", "tavan aşılmadan run sonlandırılmamalı"


def test_run_is_dead_lettered_after_max_attempts(monkeypatch):
    """K1: tavana ulaşan iş kuyruktan çıkar, run 'uncertain' olur."""
    from kernel import config
    from kernel.orchestrator import flow

    monkeypatch.setitem(
        flow.M1_FLOW,
        "kaydet",
        flow.Node(id="kaydet", type="tool", tool="kayitli.olmayan.arac"),
    )
    monkeypatch.setattr(config, "MAX_JOB_ATTEMPTS", 1)

    run_id = runner.start_run("t1", {"text": "x", "key": "k5"})
    gw = Gateway(OfflineTransport())
    runner.run_once("w1", gw)
    runner.run_once("w1", gw)

    with db.tx() as conn:
        left = conn.execute(
            "SELECT count(*) FROM job_queue WHERE run_id = %s", (run_id,)
        ).fetchone()[0]
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        recorded = events.read(conn, run_id)

    assert left == 0, "ölü mektup işi kuyrukta bırakmamalı"
    assert status == "uncertain"
    uncertain = [e for e in recorded if e.type == "run_uncertain"]
    assert len(uncertain) == 1
    assert uncertain[0].payload["error_class"] == "KeyError"
    assert "error" not in uncertain[0].payload, "ham metin olay kaydına yazılmamalı"

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
                (_uuid.uuid4(), step_id, f"{run_id}:kaydet:k3"),
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


def test_lost_lease_cancels_the_advance():
    """K2: kirasını kaybeden worker akışı ilerletemez — sonraki düğüm için
    İKİ iş oluşmaz, runs durumu bir kez yazılır."""
    from kernel.orchestrator import step
    from kernel.state import queue

    run_id = runner.start_run("t1", {"text": "x", "key": "k6"})
    gw = Gateway(OfflineTransport())

    with db.tx() as conn:
        job_a = queue.claim(conn, "wA", 30)
    assert job_a is not None
    with db.tx() as conn:
        conn.execute(
            "UPDATE job_queue SET locked_until = now() - interval '1 second'"
            " WHERE id = %s",
            (job_a.id,),
        )
    with db.tx() as conn:
        job_b = queue.claim(conn, "wB", 300)
    assert job_b is not None and job_b.id == job_a.id

    assert step.execute_step(job_a, gw) == "lease_lost"

    with db.tx() as conn:
        jobs = conn.execute(
            "SELECT node_id, locked_by FROM job_queue WHERE run_id = %s", (run_id,)
        ).fetchall()
    assert jobs == [("ozetle", "wB")], "kaybeden worker sonraki düğümü kuyruğa koymamalı"
    # 'run_completed' bu dalda (next="kaydet") zaten hiç yazılmaz; o iddia
    # test_lost_lease_cancels_the_completion'da (next=None dalı) sınanıyor.

    # Gerçek sahip ilerletince kuyrukta yine TEK iş olur.
    assert step.execute_step(job_b, gw) == "advanced"
    with db.tx() as conn:
        jobs = conn.execute(
            "SELECT node_id FROM job_queue WHERE run_id = %s", (run_id,)
        ).fetchall()
    assert jobs == [("kaydet",)]


def test_lost_lease_cancels_the_completion():
    """K2: kirasını kaybeden worker run'ı TAMAMLAYAMAZ. 'kaydet' düğümünde
    next=None, yani bu _advance'in run'ı bitiren dalıdır — burada
    'run_completed' tam bir kez yazılmalı ve kaybedenin altında yazılmamalı."""
    from kernel.orchestrator import step
    from kernel.state import queue

    run_id = runner.start_run("t1", {"text": "x", "key": "k16"})
    gw = Gateway(OfflineTransport())
    assert runner.run_once("w1", gw) is True  # ozetle tamamlanır, kaydet kuyruğa girer

    with db.tx() as conn:
        job_a = queue.claim(conn, "wA", 30)
    assert job_a is not None and job_a.node_id == "kaydet"
    with db.tx() as conn:
        conn.execute(
            "UPDATE job_queue SET locked_until = now() - interval '1 second'"
            " WHERE id = %s",
            (job_a.id,),
        )
    with db.tx() as conn:
        job_b = queue.claim(conn, "wB", 300)
    assert job_b is not None and job_b.id == job_a.id

    assert step.execute_step(job_a, gw) == "lease_lost"

    with db.tx() as conn:
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        types = [e.type for e in events.read(conn, run_id)]
    assert "run_completed" not in types, "kaybeden worker run'ı tamamlamamalı"
    assert status == "running", "kaybeden worker altında run 'running' kalmalı"

    assert step.execute_step(job_b, gw) == "run_completed"

    with db.tx() as conn:
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        types = [e.type for e in events.read(conn, run_id)]
    completed = [t for t in types if t == "run_completed"]
    assert len(completed) == 1, "devralan worker run_completed'i tam bir kez yazmalı"
    assert status == "completed"


def test_lost_lease_cancels_the_deferred_branch():
    """Madde 1: DEFERRED dalı da sahiplik korumalı olmalı — kirasını kaybeden
    worker adım bütçesini tüketmemeli, 'step_deferred' yazmamalı."""
    import uuid as _uuid

    from kernel.orchestrator import step
    from kernel.state import queue

    run_id = runner.start_run("t1", {"text": "x", "key": "k14"})
    gw = Gateway(OfflineTransport())
    assert runner.run_once("w1", gw) is True  # ozetle tamamlanır, kaydet kuyruğa girer

    # kaydet için kirası GEÇERLİ başka bir rezervasyon → DEFERRED'e zorla.
    with db.tx() as conn:
        held_step_id = _uuid.uuid4()
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, attempt, status)"
            " VALUES (%s, %s, 'kaydet', 0, 'running')",
            (held_step_id, run_id),
        )
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at) VALUES"
            " (%s, %s, 'test.slow_writer', %s, '{}'::jsonb, 'reserved',"
            "  now() + interval '600 seconds')",
            (_uuid.uuid4(), held_step_id, f"{run_id}:kaydet:k14"),
        )

    with db.tx() as conn:
        job_a = queue.claim(conn, "wA", 30)
    assert job_a is not None and job_a.node_id == "kaydet"

    # Kirayı geçmişe çekip başkasına kaptır.
    with db.tx() as conn:
        conn.execute(
            "UPDATE job_queue SET locked_until = now() - interval '1 second'"
            " WHERE id = %s",
            (job_a.id,),
        )
    with db.tx() as conn:
        job_b = queue.claim(conn, "wB", 300)
    assert job_b is not None and job_b.id == job_a.id

    with db.tx() as conn:
        step_count_before = conn.execute(
            "SELECT step_count FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]

    assert step.execute_step(job_a, gw) == "lease_lost"

    with db.tx() as conn:
        step_count_after = conn.execute(
            "SELECT step_count FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        types = [e.type for e in events.read(conn, run_id)]

    assert step_count_after == step_count_before, (
        "kirasını kaybeden worker DEFERRED dalında adım bütçesini tüketmemeli"
    )
    assert "step_deferred" not in types


def test_run_failed_event_carries_no_raw_error_text(monkeypatch):
    """Ö2: silinemeyen olay kaydına ham istisna metni yazılmaz (Kural 5)."""
    from kernel.orchestrator import flow
    from tests.fakes import AlwaysFailsTool

    secret = "sizan-sir-" + "x" * 500

    class LeakyTool(AlwaysFailsTool):
        name = "test.leaky"

        def execute(self, payload: dict) -> dict:
            raise RuntimeError(secret)

    registry.register(LeakyTool())
    monkeypatch.setitem(
        flow.M1_FLOW, "kaydet", flow.Node(id="kaydet", type="tool", tool="test.leaky")
    )

    run_id = runner.start_run("t1", {"text": "x", "key": "k7"})
    _drain()

    with db.tx() as conn:
        failed = [e for e in events.read(conn, run_id) if e.type == "run_failed"]
        errors = conn.execute(
            "SELECT error FROM steps WHERE run_id = %s AND node_id = 'kaydet'",
            (run_id,),
        ).fetchall()

    assert len(failed) == 1
    payload = failed[0].payload
    assert "error" not in payload, "ham metin olay kaydında olmamalı"
    assert payload["error_class"]
    assert len(payload["error_summary"]) == 200, "özet 200 karaktere kırpılmalı"
    assert secret not in payload["error_summary"]
    assert secret in errors[0][0], "ham metin steps.error'da kalmalı (temizlenebilir)"


def test_max_steps_stops_the_run():
    """Ö3: adım tavanına ulaşan run ikinci adıma geçemez."""
    run_id = runner.start_run("t1", {"text": "x", "key": "k8"})
    with db.tx() as conn:
        conn.execute("UPDATE runs SET max_steps = 1 WHERE id = %s", (run_id,))

    _drain()

    with db.tx() as conn:
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        left = conn.execute(
            "SELECT count(*) FROM job_queue WHERE run_id = %s", (run_id,)
        ).fetchone()[0]
        nodes = conn.execute(
            "SELECT node_id FROM steps WHERE run_id = %s", (run_id,)
        ).fetchall()
        failed = [e for e in events.read(conn, run_id) if e.type == "run_failed"]

    assert status == "failed"
    assert left == 0, "tavana ulaşan run kuyrukta iş bırakmamalı"
    assert nodes == [("ozetle",)], "ikinci düğüm için steps satırı açılmamalı"
    assert failed and failed[0].payload["error_class"] == "MaxStepsExceeded"


def test_deferral_counts_against_max_steps():
    """Ö3: DEFERRED dalı da tavana sayılır — kaçak erteleme döngüsü olamaz."""
    import uuid as _uuid

    run_id = runner.start_run("t1", {"text": "x", "key": "k9"})
    gw = Gateway(OfflineTransport())
    runner.run_once("w1", gw)  # ozetle tamamlanır (step_count = 1)

    # Başka bir worker'ın elindeki, kirası GEÇERLİ rezervasyon → DEFERRED.
    with db.tx() as conn:
        step_id = _uuid.uuid4()
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, attempt, status)"
            " VALUES (%s, %s, 'kaydet', 0, 'running')",
            (step_id, run_id),
        )
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at) VALUES"
            " (%s, %s, 'test.slow_writer', %s, '{}'::jsonb, 'reserved',"
            "  now() + interval '600 seconds')",
            (_uuid.uuid4(), step_id, f"{run_id}:kaydet:k9"),
        )

    runner.run_once("w1", gw)  # kaydet ertelenir

    with db.tx() as conn:
        step_count = conn.execute(
            "SELECT step_count FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
    assert step_count == 2, "erteleme adım bütçesinden düşmeli"


def test_same_idempotency_key_returns_the_same_run():
    """Ö4: aynı anahtarla ikinci start_run yeni run YARATMAZ."""
    first = runner.start_run(
        "t1", {"text": "x", "key": "k10"}, idempotency_key="fatura-42"
    )
    second = runner.start_run(
        "t1", {"text": "y", "key": "k11"}, idempotency_key="fatura-42"
    )

    assert first == second
    with db.tx() as conn:
        runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
        jobs = conn.execute("SELECT count(*) FROM job_queue").fetchone()[0]
        created = [
            e for e in events.read(conn, first) if e.type == "run_created"
        ]
    assert runs == 1
    assert jobs == 1, "tekrar edilen istek ikinci işi kuyruğa koymamalı"
    assert len(created) == 1


def test_different_idempotency_keys_create_separate_runs():
    a = runner.start_run("t1", {"text": "x", "key": "k12"}, idempotency_key="a")
    b = runner.start_run("t1", {"text": "x", "key": "k13"}, idempotency_key="b")
    assert a != b


def test_build_gateway_requires_explicit_transport(monkeypatch):
    """Ö6: env yoksa sessizce 'offline'a düşmek üretimde sahte başarı üretir."""
    monkeypatch.delenv("OTOMASYON_TRANSPORT", raising=False)
    with pytest.raises(ValueError):
        runner.build_gateway()

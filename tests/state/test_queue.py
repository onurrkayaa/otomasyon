import threading
import time
import uuid

from kernel.state import db, queue


def _make_run() -> uuid.UUID:
    run_id = uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'pending', 1.0, 10, %s)",
            (run_id, str(run_id)),
        )
    return run_id


def test_claim_returns_enqueued_job():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
    with db.tx() as conn:
        job = queue.claim(conn, "w1")
    assert job is not None
    assert job.node_id == "n1"
    assert job.attempts == 1


def test_claim_returns_none_when_empty():
    with db.tx() as conn:
        assert queue.claim(conn, "w1") is None


def test_locked_job_is_not_reclaimed_before_lease_expiry():
    """Sıralı: kirası dolmamış bir iş üçüncü talebe verilmemeli."""
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        queue.enqueue(conn, run_id, "n2")

    with db.tx() as conn:
        first = queue.claim(conn, "w1")
    with db.tx() as conn:
        second = queue.claim(conn, "w2")
    with db.tx() as conn:
        third = queue.claim(conn, "w3")

    assert first is not None and second is not None
    assert first.id != second.id
    assert third is None, "iki iş vardı, üçüncü talep boş dönmeli"


def test_two_concurrent_workers_get_different_jobs_without_blocking():
    """SKIP LOCKED: eşzamanlı ikinci claim birinciyi beklemeden farklı iş almalı.

    Birinci worker'ın transaction'ı açık tutulurken (kilit hâlâ elinde) ikinci
    worker claim çağırır. Bloklayan bir FOR UPDATE olsaydı ikinci çağrı ilk
    transaction commit olana kadar (HOLD_SECONDS) beklerdi. SKIP LOCKED ile
    çok daha hızlı dönmeli ve farklı bir işi almalı.
    """
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        queue.enqueue(conn, run_id, "n2")

    HOLD_SECONDS = 3
    first_job: list[queue.Job] = []
    holder_ready = threading.Event()

    def holder():
        with db.tx() as conn:
            job = queue.claim(conn, "w1")
            assert job is not None
            first_job.append(job)
            holder_ready.set()
            time.sleep(HOLD_SECONDS)

    thread = threading.Thread(target=holder)
    thread.start()
    holder_ready.wait(timeout=5)

    started = time.monotonic()
    with db.tx() as conn:
        second = queue.claim(conn, "w2")
    elapsed = time.monotonic() - started

    thread.join()

    assert second is not None
    assert first_job and first_job[0].id != second.id
    assert elapsed < 1.0, (
        "ikinci claim, birinci worker'ın kirası dolana kadar beklemiş "
        f"olabilir (SKIP LOCKED bekleniyordu): {elapsed:.2f}s"
    )


def test_expired_lease_is_reclaimable():
    """Worker çökerse iş kirası dolunca başkası alabilmeli."""
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        job = queue.claim(conn, "w1", lease_seconds=30)
        assert job is not None
        # Kirayı geçmişe çekerek çökmüş worker'ı simüle et.
        conn.execute(
            "UPDATE job_queue SET locked_until = now() - interval '1 second'"
            " WHERE id = %s",
            (job.id,),
        )

    with db.tx() as conn:
        again = queue.claim(conn, "w2")

    assert again is not None
    assert again.id == job.id
    assert again.attempts == 2, "yeniden alım attempts sayacını artırmalı"


def test_release_delays_availability():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        job = queue.claim(conn, "w1")
        assert job is not None
        queue.release(conn, job.id, delay_seconds=60)

    with db.tx() as conn:
        assert queue.claim(conn, "w2") is None, "gecikmeli iş hemen alınamaz"


def test_complete_removes_job():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        job = queue.claim(conn, "w1")
        assert job is not None
        queue.complete(conn, job.id)

    with db.tx() as conn:
        assert queue.claim(conn, "w2") is None

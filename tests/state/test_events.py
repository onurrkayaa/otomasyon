import threading
import uuid

import pytest

from kernel.state import db, events


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


def test_append_returns_increasing_seq():
    run_id = _make_run()
    with db.tx() as conn:
        assert events.append(conn, run_id, "run_created", {}) == 1
        assert events.append(conn, run_id, "step_started", {"node": "a"}) == 2


def test_read_returns_events_in_order():
    run_id = _make_run()
    with db.tx() as conn:
        events.append(conn, run_id, "run_created", {})
        events.append(conn, run_id, "step_started", {"node": "a"})

    with db.tx() as conn:
        got = events.read(conn, run_id)

    assert [e.seq for e in got] == [1, 2]
    assert got[1].payload == {"node": "a"}


def test_concurrent_appends_produce_unique_seqs():
    """İki eşzamanlı yazar aynı seq'i almamalı."""
    run_id = _make_run()
    errors: list[Exception] = []

    def writer():
        try:
            for _ in range(10):
                with db.tx() as conn:
                    events.append(conn, run_id, "tick", {})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with db.tx() as conn:
        got = events.read(conn, run_id)
    assert [e.seq for e in got] == list(range(1, 41))

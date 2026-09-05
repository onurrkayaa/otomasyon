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


def test_mark_does_not_overwrite_lost_ownership():
    """Bulgu 1: bir satır zaten 'uncertain' işaretliyse (başka bir worker
    kirası dolmuş rezervasyonu devralıp insan kuyruğuna almış demektir),
    geç kalan/takılıp uyanan bir worker onu sessizce 'completed' ya da
    'failed' ile EZMEMELİ. `_mark` artık `AND status = 'reserved'` koruması
    taşıyor; satır 'reserved' değilse güncelleme uygulanmaz ve çağıran
    UNCERTAIN döner."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:c"
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at)"
            " VALUES (%s, %s, 'test.slow_writer', %s,"
            " '{}'::jsonb, 'uncertain', now())",
            (uuid.uuid4(), step_id, key),
        )

    result = execution._call_and_complete(
        registry.get("test.slow_writer"), key, {"key": "c"}
    )

    assert result.outcome is ToolOutcome.UNCERTAIN
    assert "sahiplik" in (result.error or "")

    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s", (key,)
        ).fetchone()
    assert row[0] == "uncertain", "sahipliği kaybedilmiş satır ezilmemeli"


def test_reservation_survives_outer_transaction_rollback():
    """Bulgu 2 (K15): rezervasyon bağımsız commit ettiği için, çevresindeki
    bir transaction geri alınsa bile hayatta kalmalı. `execute_tool` bir
    `db.tx()` bloğunun İÇİNDEN çağrılıyor, sonra o dış transaction kasıtlı
    olarak geri alınıyor; satırın AYRI bir bağlantıdan hâlâ görünür ve
    'completed' olduğu doğrulanıyor."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:d"

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with db.tx() as _outer:
            execution.execute_tool(
                registry.get("test.slow_writer"), run_id, step_id, {"key": "d"}
            )
            raise _Boom("dış transaction'ı kasıtlı geri al")

    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s", (key,)
        ).fetchone()
    assert row is not None, (
        "rezervasyon satırı dış transaction rollback'inden bağımsız commit"
        " edilmeli"
    )
    assert row[0] == "completed"


def test_reserve_never_uses_shared_db_tx(monkeypatch):
    """Bulgu 2 (K15) — regresyon kilidi: `execute_tool` hiçbir aşamada
    `db.tx()` çağırmamalı, yalnızca `db.independent_tx()`. Salt davranışsal
    bir rollback testi bunu ayırt edemez: havuz her `pool().connection()`
    çağrısında zaten AYRI bir bağlantı verdiği için (deneyle doğrulandı —
    düzeltme raporuna bakınız), `_reserve` `db.tx()` kullansaydı bile üstteki
    rollback testi yine PASSED olurdu. Bu yüzden burada `db.tx`'i patlayacak
    şekilde monkeypatch'liyoruz: `_reserve` (ya da `_mark`/`_handle_existing`
    merdiveninin herhangi bir basamağı) `db.independent_tx()` yerine
    `db.tx()` kullanırsa bu test PATLAR."""
    run_id, step_id = _make_run_and_step()

    def _forbidden(*args, **kwargs):
        raise AssertionError(
            "execute_tool db.tx() çağırmamalı — K15 independent_tx() zorunlu"
        )

    monkeypatch.setattr(db, "tx", _forbidden)

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "e"}
    )
    assert result.outcome is ToolOutcome.COMPLETED

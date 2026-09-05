import uuid
from decimal import Decimal

import pytest

from kernel.gateway import accounting, tiers
from kernel.gateway.gateway import BudgetExceeded, Gateway, RefusalError
from kernel.gateway.types import LLMRequest, RawResponse, Usage
from kernel.state import db, events


class FakeTransport:
    def __init__(self, response: RawResponse):
        self.response = response
        self.calls: list[dict] = []

    def send(self, **kwargs) -> RawResponse:
        self.calls.append(kwargs)
        return self.response


def _raw(
    stop_reason: str = "end_turn", model: str = "claude-opus-5"
) -> RawResponse:
    return RawResponse(
        text="özet",
        model=model,
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=4000,
        ),
    )


def _make_run_and_step(budget: str = "1.0") -> tuple[uuid.UUID, uuid.UUID]:
    run_id, step_id = uuid.uuid4(), uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'running', %s, 10, %s)",
            (run_id, Decimal(budget), str(run_id)),
        )
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, status)"
            " VALUES (%s, %s, 'ozetle', 'running')",
            (step_id, run_id),
        )
    return run_id, step_id


def test_tier_resolves_to_model_and_effort():
    assert tiers.resolve("standard") == ("claude-opus-5", "high")
    assert tiers.resolve("fast") == ("claude-opus-5", "low")


def test_bulk_tier_is_locked():
    """K9: ucuz kademe eval kanıtı olmadan kullanılamaz."""
    with pytest.raises(KeyError):
        tiers.resolve("bulk")


def test_cost_uses_separate_cache_read_rate():
    usage = Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=4000)
    # 1000*5 + 200*25 + 4000*0.50 = 5000 + 5000 + 2000 = 12000 / 1e6
    assert accounting.cost("claude-opus-5", usage) == Decimal("0.012000")


def test_cost_normalizes_dated_model_id():
    """K3: API takma adı çözülmüş tarihli kimlik döndürür; fiyat önekten bulunur."""
    usage = Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=4000)
    assert accounting.cost("claude-opus-5-20260115", usage) == Decimal("0.012000")


def test_unknown_model_does_not_kill_the_call():
    """K3: fiyatı bilinmeyen model → istisna YOK, sessizlik de YOK."""
    run_id, step_id = _make_run_and_step()
    gw = Gateway(FakeTransport(_raw(model="bilinmeyen-model-9")))

    resp = gw.complete(
        LLMRequest(
            tier="fast", system_layer1="a", system_layer2="b", user_content="c"
        ),
        run_id,
        step_id,
    )

    assert resp.text == "özet"
    assert resp.cost_usd == Decimal(0)

    with db.tx() as conn:
        spent = conn.execute(
            "SELECT spent_usd FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        recorded = events.read(conn, run_id)

    assert spent == Decimal(0), "bilinmeyen fiyatta uydurma maliyet yazılmamalı"
    unknown = [e for e in recorded if e.type == "llm_call_pricing_unknown"]
    assert len(unknown) == 1
    assert unknown[0].payload["model"] == "bilinmeyen-model-9"
    assert unknown[0].payload["step_id"] == str(step_id)
    assert unknown[0].payload["stop_reason"] == "end_turn"
    assert "llm_call_completed" in [e.type for e in recorded]


def test_complete_records_usage_cost_and_event():
    run_id, step_id = _make_run_and_step()
    gw = Gateway(FakeTransport(_raw()))

    resp = gw.complete(
        LLMRequest(
            tier="fast",
            system_layer1="sabit",
            system_layer2="kiracı",
            user_content="girdi",
        ),
        run_id,
        step_id,
    )

    assert resp.text == "özet"
    assert resp.cost_usd == Decimal("0.012000")

    with db.tx() as conn:
        row = conn.execute(
            "SELECT model, tokens_in, tokens_out, cache_read_tokens, cost_usd"
            " FROM steps WHERE id = %s",
            (step_id,),
        ).fetchone()
        spent = conn.execute(
            "SELECT spent_usd FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        recorded = [e.type for e in events.read(conn, run_id)]

    assert row == ("claude-opus-5", 1000, 200, 4000, Decimal("0.012000"))
    assert spent == Decimal("0.012000")
    assert "llm_call_completed" in recorded


def test_refusal_raises():
    """§6.5: stop_reason='refusal' sessizce geçilmez."""
    run_id, step_id = _make_run_and_step()
    gw = Gateway(FakeTransport(_raw(stop_reason="refusal")))
    with pytest.raises(RefusalError):
        gw.complete(
            LLMRequest(
                tier="fast", system_layer1="a", system_layer2="b", user_content="c"
            ),
            run_id,
            step_id,
        )


def test_budget_exhausted_rejects_before_calling_model():
    run_id, step_id = _make_run_and_step(budget="0.000001")
    transport = FakeTransport(_raw())
    gw = Gateway(transport)

    with pytest.raises(BudgetExceeded):
        gw.complete(
            LLMRequest(
                tier="fast", system_layer1="a", system_layer2="b", user_content="c"
            ),
            run_id,
            step_id,
        )
    assert transport.calls == [], "bütçe aşımında model ÇAĞRILMAMALI"


def test_layers_are_passed_separately_for_caching():
    """§6.2: katmanlar ayrı gönderilmeli ki önbellek noktaları kurulabilsin."""
    run_id, step_id = _make_run_and_step()
    transport = FakeTransport(_raw())
    Gateway(transport).complete(
        LLMRequest(
            tier="standard",
            system_layer1="URUN_SABITI",
            system_layer2="KIRACI",
            user_content="GIRDI",
        ),
        run_id,
        step_id,
    )
    call = transport.calls[0]
    assert call["system_layer1"] == "URUN_SABITI"
    assert call["system_layer2"] == "KIRACI"
    assert call["user_content"] == "GIRDI"
    assert call["effort"] == "high"


def test_only_transport_module_imports_anthropic():
    """§6.1: anthropic SDK'sının tek import noktası transport.py'dir."""
    import pathlib

    root = pathlib.Path(__file__).parents[2] / "packages" / "kernel"
    py_files = list(root.rglob("*.py"))
    assert len(py_files) > 5, f"tekel testi hiçbir şey taramadı: {root}"
    # Muafiyet TAM göreli yola verilir: kernel/** altına konan ikinci bir
    # transport.py yalnız adıyla muafiyet kazanmamalı.
    offenders = [
        p.relative_to(root).as_posix()
        for p in py_files
        if p.relative_to(root).as_posix() != "gateway/transport.py"
        and "anthropic" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"anthropic'i doğrudan import eden modüller: {offenders}"

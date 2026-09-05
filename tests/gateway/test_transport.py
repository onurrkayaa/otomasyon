"""AnthropicTransport'un GÖNDERDİĞİ kwargs'ın kilidi (§6.1 / K9).

Gateway testleri katmanların Transport'a ayrı geçirildiğini doğrular; asıl
kural bir katman aşağıdadır. Burada gerçek SDK kullanılmaz: `anthropic`
paketi ne import edilir ne de kurulu olması gerekir — client enjekte edilir.
"""
from __future__ import annotations

from kernel.gateway.transport import AnthropicTransport

BETA = "server-side-fallback-2026-07-01"


class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Usage:
    input_tokens = 11
    output_tokens = 7
    cache_read_input_tokens = 3
    cache_creation_input_tokens = 2


class _Response:
    content = [_Block("yanit")]
    model = "claude-opus-5-20260115"
    stop_reason = "end_turn"
    usage = _Usage()


class _Messages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()


class _Beta:
    def __init__(self, messages: _Messages):
        self.messages = messages


class FakeClient:
    def __init__(self) -> None:
        self.messages = _Messages()
        self.beta = _Beta(self.messages)


def _send(effort: str = "low") -> dict:
    client = FakeClient()
    AnthropicTransport(client=client).send(
        model="claude-opus-5",
        effort=effort,
        system_layer1="URUN_SABITI",
        system_layer2="KIRACI",
        user_content="GIRDI",
        max_tokens=16000,
    )
    assert len(client.messages.calls) == 1
    return client.messages.calls[0]


def test_thinking_is_adaptive_never_disabled():
    """§6.1'in sessiz arıza modu: thinking ASLA disabled gönderilmez."""
    assert _send()["thinking"] == {"type": "adaptive"}


def test_effort_is_sent_via_output_config():
    """K9: kademe (model, effort) çiftidir; effort output_config ile gider."""
    assert _send(effort="xhigh")["output_config"] == {"effort": "xhigh"}


def test_budget_tokens_is_never_sent():
    """Opus 5 budget_tokens ile 400 döner; anahtar hiçbir yerde olmamalı."""
    call = _send()
    assert "budget_tokens" not in call
    assert "budget_tokens" not in call["thinking"]


def test_both_system_layers_carry_cache_control():
    """§6.2: iki önbellek noktası da işaretlenmiş olmalı."""
    system = _send()["system"]
    assert len(system) == 2
    assert [b["text"] for b in system] == ["URUN_SABITI", "KIRACI"]
    assert all(b.get("cache_control") for b in system)


def test_server_side_fallback_beta_is_requested():
    """§6.5: sunucu tarafı geri düşüş varsayılan açık."""
    call = _send()
    assert BETA in call["betas"]
    assert call["fallbacks"] == "default"


def test_model_and_usage_are_read_from_the_response():
    client = FakeClient()
    raw = AnthropicTransport(client=client).send(
        model="claude-opus-5",
        effort="low",
        system_layer1="a",
        system_layer2="b",
        user_content="c",
        max_tokens=100,
    )
    assert raw.text == "yanit"
    assert raw.model == "claude-opus-5-20260115"
    assert raw.usage.input_tokens == 11
    assert raw.usage.cache_read_input_tokens == 3

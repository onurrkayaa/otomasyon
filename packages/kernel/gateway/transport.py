"""anthropic SDK'sının TEK import noktası (spec §6.1).

Başka hiçbir modül anthropic'i import etmez. Bu dosya değiştirilirken
§6.1'deki iki kuralı ihlal etmemeye dikkat: thinking ASLA disabled
gönderilmez, ve model kimliği yalnız tiers.py'den gelir.
"""
from __future__ import annotations

from typing import Protocol

from kernel.gateway.types import RawResponse, Usage


class Transport(Protocol):
    def send(
        self,
        *,
        model: str,
        effort: str,
        system_layer1: str,
        system_layer2: str,
        user_content: str,
        max_tokens: int,
    ) -> RawResponse: ...


class AnthropicTransport:
    def __init__(self, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client

    def send(
        self,
        *,
        model: str,
        effort: str,
        system_layer1: str,
        system_layer2: str,
        user_content: str,
        max_tokens: int,
    ) -> RawResponse:
        resp = self._client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                # Önbellek noktası 1 — ürün sabiti, tüm kiracılarda ortak.
                {
                    "type": "text",
                    "text": system_layer1,
                    "cache_control": {"type": "ephemeral"},
                },
                # Önbellek noktası 2 — kiracıya özel, çalışmalar arası sabit.
                {
                    "type": "text",
                    "text": system_layer2,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": user_content}],
            # ASLA {"type": "disabled"} — §6.1'deki sessiz arıza modu.
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            # §6.5: sunucu tarafı geri düşüş varsayılan açık.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        text = "".join(
            block.text for block in resp.content if block.type == "text"
        )
        return RawResponse(
            text=text,
            model=resp.model,
            stop_reason=resp.stop_reason,
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_input_tokens=getattr(
                    resp.usage, "cache_read_input_tokens", 0
                ) or 0,
                cache_creation_input_tokens=getattr(
                    resp.usage, "cache_creation_input_tokens", 0
                ) or 0,
            ),
        )


class OfflineTransport:
    """Ağa çıkmadan sabit yanıt döndürür.

    Test iskelesi değildir: API anahtarı olmadan yerel geliştirme, kaos
    testleri ve M5'teki gölge modunun temelidir. Kullanım GERÇEKTEN sıfırdır,
    bu yüzden usage da sıfırdır: uydurma bir token sayısı runs.spent_usd'ye ve
    llm_call_completed olayına gerçek maliyetmiş gibi girerdi.
    """

    def __init__(self, text: str = "offline-yanit"):
        self._text = text

    def send(
        self,
        *,
        model: str,
        effort: str,
        system_layer1: str,
        system_layer2: str,
        user_content: str,
        max_tokens: int,
    ) -> RawResponse:
        return RawResponse(
            text=self._text,
            model=model,
            stop_reason="end_turn",
            usage=Usage(input_tokens=0, output_tokens=0),
        )

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLMRequest(BaseModel):
    """Üç katmanlı prompt (§6.2).

    system_layer1 — ürün sabiti; TÜM kiracılarda bayt bayt aynı. Önbellek 1.
    system_layer2 — kiracıya özel; çalışmalar arası sabit. Önbellek 2.
    user_content  — adımın beyan edilmiş girdisi. Önbelleklenmez.
    """

    tier: str
    system_layer1: str
    system_layer2: str
    user_content: str
    max_tokens: int = 16000


class RawResponse(BaseModel):
    text: str
    model: str
    usage: Usage
    stop_reason: str


class LLMResponse(BaseModel):
    text: str
    model: str
    usage: Usage
    cost_usd: Decimal
    stop_reason: str

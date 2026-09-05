"""LLM Ağ Geçidi — tek çıkış kapısı (§6.1).

Hiçbir ajan doğrudan model çağırmaz. Muhasebe, bütçe, reddedilme kontrolü
ve olay kaydı burada toplanır; dağıtılırsa hiçbiri güvenilir olmaz.

M1 kapsamı: muhasebe + kaba bütçe ön kontrolü + reddedilme + olay kaydı.
Kiracı günlük tavanı ve p95 sapma alarmı M3/M5'te gelir.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from kernel.gateway import accounting, tiers
from kernel.gateway.transport import Transport
from kernel.gateway.types import LLMRequest, LLMResponse
from kernel.state import db, events

# Kaba ön tahmin: 4 karakter ≈ 1 token, güvenli tarafta yuvarlanır (K8).
CHARS_PER_TOKEN = 4


class RefusalError(RuntimeError):
    """Model isteği reddetti (HTTP 200 + stop_reason='refusal')."""


class BudgetExceeded(RuntimeError):
    """Çalışma bütçesi bu çağrıyı karşılamıyor."""


class Gateway:
    def __init__(self, transport: Transport):
        self._transport = transport

    def complete(
        self, req: LLMRequest, run_id: UUID, step_id: UUID
    ) -> LLMResponse:
        model, effort = tiers.resolve(req.tier)
        self._check_budget(model, req, run_id)

        raw = self._transport.send(
            model=model,
            effort=effort,
            system_layer1=req.system_layer1,
            system_layer2=req.system_layer2,
            user_content=req.user_content,
            max_tokens=req.max_tokens,
        )

        cost = accounting.cost(raw.model, raw.usage)
        self._record(raw, cost, run_id, step_id)

        if raw.stop_reason == "refusal":
            raise RefusalError(
                f"model isteği reddetti (model={raw.model}); maliyet kaydedildi"
            )

        return LLMResponse(
            text=raw.text,
            model=raw.model,
            usage=raw.usage,
            cost_usd=cost,
            stop_reason=raw.stop_reason,
        )

    # --- iç ---

    def _estimate(self, model: str, req: LLMRequest) -> Decimal:
        chars = len(req.system_layer1) + len(req.system_layer2) + len(req.user_content)
        price = accounting.PRICES[model]
        est_in = Decimal(chars // CHARS_PER_TOKEN + 1) * price.inp
        est_out = Decimal(req.max_tokens) * price.out
        return ((est_in + est_out) / accounting.MILLION).quantize(Decimal("0.000001"))

    def _check_budget(self, model: str, req: LLMRequest, run_id: UUID) -> None:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT budget_usd, spent_usd FROM runs WHERE id = %s FOR UPDATE",
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"run bulunamadı: {run_id}")
        budget, spent = row[0], row[1]
        if spent + self._estimate(model, req) > budget:
            raise BudgetExceeded(
                f"tahmini maliyet bütçeyi aşıyor (harcanan={spent}, bütçe={budget})"
            )

    def _record(self, raw, cost: Decimal, run_id: UUID, step_id: UUID) -> None:
        with db.tx() as conn:
            conn.execute(
                "UPDATE steps SET model = %s, tokens_in = %s, tokens_out = %s,"
                " cache_read_tokens = %s, cost_usd = %s WHERE id = %s",
                (
                    raw.model,
                    raw.usage.input_tokens,
                    raw.usage.output_tokens,
                    raw.usage.cache_read_input_tokens,
                    cost,
                    step_id,
                ),
            )
            conn.execute(
                "UPDATE runs SET spent_usd = spent_usd + %s, updated_at = now()"
                " WHERE id = %s",
                (cost, run_id),
            )
            events.append(
                conn,
                run_id,
                "llm_call_completed",
                {
                    "step_id": str(step_id),
                    "model": raw.model,
                    "stop_reason": raw.stop_reason,
                    "cost_usd": str(cost),
                    "cache_read_input_tokens": raw.usage.cache_read_input_tokens,
                },
            )

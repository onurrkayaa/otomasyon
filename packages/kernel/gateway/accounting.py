"""Token → USD maliyet hesabı.

Fiyatlar $/1M token. Önbellek okuma (0.1×) ve yazma (1.25×) çarpanları
spec §13 uyarınca uygulama anında canlı fiyat kaynağından DOĞRULANACAKTIR;
buradaki değerler o doğrulamaya kadar geçerli varsayımdır.
"""
from __future__ import annotations

from decimal import Decimal

from kernel.gateway.types import Usage

MILLION = Decimal(1_000_000)


class Price:
    def __init__(self, inp: str, out: str, cache_read: str, cache_write: str):
        self.inp = Decimal(inp)
        self.out = Decimal(out)
        self.cache_read = Decimal(cache_read)
        self.cache_write = Decimal(cache_write)


PRICES: dict[str, Price] = {
    "claude-opus-5": Price("5.00", "25.00", "0.50", "6.25"),
    "claude-haiku-4-5": Price("1.00", "5.00", "0.10", "1.25"),
}


def price_for(model: str) -> Price | None:
    """Yanıtın bildirdiği model kimliği için fiyat. Bulunamazsa None.

    Arama önek normalizasyonuyla yapılır: API takma adı çözülmüş tarihli bir
    kimlik döndürür (claude-opus-5-20260115 → claude-opus-5). En uzun eşleşen
    önek kazanır.
    """
    matches = [k for k in PRICES if model.startswith(k)]
    if not matches:
        return None
    return PRICES[max(matches, key=len)]


def cost(model: str, usage: Usage) -> Decimal:
    """Bilinmeyen model → Decimal(0). Bilinçli takas: sunucu tarafı geri düşüş
    (§6.5) yanıt modelinin farklı olmasını NORMAL kılar; burada istisna atmak
    maliyeti, olayı ve yanıtı birlikte kaybettirir ve worker'ı öldürür.
    Maliyeti bilmemek, muhasebenin sessizce kaybolmasından iyidir — ama sessiz
    olmaması gerekir: çağıran `price_for(...) is None` ile durumu ayırt eder
    ve o çağrı için bütçe zorlanamaz.
    """
    p = price_for(model)
    if p is None:
        return Decimal(0)
    total = (
        p.inp * usage.input_tokens
        + p.out * usage.output_tokens
        + p.cache_read * usage.cache_read_input_tokens
        + p.cache_write * usage.cache_creation_input_tokens
    )
    return (total / MILLION).quantize(Decimal("0.000001"))

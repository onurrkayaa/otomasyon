"""model_tier → (model, effort) eşlemesi (§6.1).

Kademe bir model adı DEĞİL, bir (model, effort) çiftidir. İlk maliyet
kaldıracı ucuz modele geçmek değil, aynı modelde effort düşürmektir:
önbellek modele özeldir ve model basamağı önbellek yeniden kullanımını
kaybettirir.

'bulk' kademesi burada YOK — eval kanıtı olmadan kilitli (K9). M2'de
derleyici eval raporu doğrulamasıyla birlikte açılır.
"""
from __future__ import annotations

TIERS: dict[str, tuple[str, str]] = {
    "deep": ("claude-opus-5", "xhigh"),
    "standard": ("claude-opus-5", "high"),
    "fast": ("claude-opus-5", "low"),
}


def resolve(name: str) -> tuple[str, str]:
    if name not in TIERS:
        raise KeyError(
            f"tanımsız model_tier: {name!r}. Geçerli: {sorted(TIERS)}"
        )
    return TIERS[name]

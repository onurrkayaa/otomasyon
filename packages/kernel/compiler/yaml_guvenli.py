"""Kaynak metni SIZDIRMAYAN YAML yükleme.

PyYAML'ın hata metni, hatanın etrafındaki kaynak satırı olduğu gibi içerir
(`Mark.__str__` → `get_snippet()`). Konfig dosyalarında o satır bir parola
olabilir — üstelik K14'ün engellemeye çalıştığı senaryoda TAM OLARAK odur:
tırnaksız iki nokta içeren düz metin bir sır hem YAML'ı bozar hem hata
mesajına düşer. Derleme çıktısı CI loglarına gittiği için bu, sırrın
loglanması demektir.

Bu yüzden hata mesajı yalnız KONUMDAN ve PyYAML'ın kendi problem/context
metninden kurulur; ham istisna hiçbir zaman string'e çevrilmez.
"""
from __future__ import annotations

import yaml


def hata_metni(exc: yaml.YAMLError) -> str:
    """Kaynak metin içermeyen, konum taşıyan hata açıklaması."""
    problem = getattr(exc, "problem", None)
    context = getattr(exc, "context", None)
    mark = getattr(exc, "problem_mark", None)

    parcalar = [p for p in (context, problem) if p]
    if mark is not None:
        # DİKKAT: str(mark) snippet'i içerir. Yalnız sayısal alanlar okunur.
        parcalar.append(f"satır {mark.line + 1}, sütun {mark.column + 1}")
    if not parcalar:
        # Bilinmeyen bir YAMLError alt tipi: ham metni GÖMMEK yerine sus.
        return f"ayrıştırılamadı ({type(exc).__name__})"
    return " — ".join(parcalar)


def safe_load(source: str) -> object:
    """`yaml.safe_load`'un aynısı; istisnayı çağıran ele alır.

    Çağıran, istisnayı `hata_metni()` ile mesaja çevirmek ZORUNDADIR;
    f-string'e doğrudan istisna koymak sızıntıdır.
    """
    return yaml.safe_load(source)

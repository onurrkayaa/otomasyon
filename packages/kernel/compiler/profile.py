"""Dağıtım profili: kademe eşlemesi, bütçeler, maskeleme, sır REFERANSLARI.

K14: hiçbir sır düz metin tutulmaz; yalnız "env: VAR_NAME" referansı geçerlidir.
Sır alanı olmak alan ADINDAN türer (SECRET_FIELD_SUFFIXES) — konnektör başına
şema yazmadan işleyen, kaçırması zor bir işaretleme. Bir konnektör ek alanları
`secret_fields:` ile sır ilan edebilir.

Çözülmüş DEĞER bu modülden dışarı yalnız `resolve_secrets()` ile çıkar ve
worker sürecinde kalır. `Profile` nesnesi yalnız referansı taşır; derlenmiş
artefakta ve veritabanına giden şey budur.
"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kernel.compiler.errors import (
    E_SEMA,
    E_SIR,
    E_SIR_ORTAM_YOK,
    W_MASKELEME_KAPALI,
    CompileError,
    CompileFailed,
    CompileReport,
)

ENV_ONEK = "env:"
# Adı bunlardan biriyle biten her konnektör alanı sır sayılır.
SECRET_FIELD_SUFFIXES = (
    "password", "secret", "token", "api_key", "apikey", "private_key", "passphrase",
)
# Maskeleme bloğu hiç yoksa etkin olan yerleşik küme (KK5).
VARSAYILAN_DESENLER = ("tckn", "vkn", "iban", "eposta", "telefon")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TierSpec(Strict):
    model: str
    effort: str | None = None


class Budgets(Strict):
    daily_usd: Decimal = Field(default=Decimal("0"), ge=0)


class MaskingPolicy(Strict):
    enabled: bool = True
    patterns: list[str] = list(VARSAYILAN_DESENLER)


class Profile(Strict):
    tenant_id: str
    tiers: dict[str, TierSpec]
    budgets: Budgets = Budgets()
    masking: MaskingPolicy = MaskingPolicy()
    connectors: dict[str, dict] = {}
    secret_fields: dict[str, list[str]] = {}


def is_secret_field(connector: str, field: str, profile_secret_fields: dict) -> bool:
    if field in profile_secret_fields.get(connector, []):
        return True
    return field.lower().endswith(SECRET_FIELD_SUFFIXES)


def _sir_denetimi(pr: Profile) -> list[CompileError]:
    """Sır alanı env: referansı değilse hata. Mesaj DEĞERİ asla içermez."""
    out: list[CompileError] = []
    for ad, konf in pr.connectors.items():
        for alan, deger in konf.items():
            if not is_secret_field(ad, alan, pr.secret_fields):
                continue
            if not (isinstance(deger, str) and deger.startswith(ENV_ONEK)):
                out.append(
                    CompileError(
                        code=E_SIR,
                        message=(
                            f"{ad}.{alan} bir sır alanı ve düz metin taşıyor;"
                            f' yalnız "env: VAR_NAME" biçimi kabul edilir (K14)'
                        ),
                    )
                )
    return out


def load_profile(path: Path) -> Profile:
    try:
        ham = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CompileFailed(
            CompileReport(errors=[CompileError(code=E_SEMA, message=f"profil ayrıştırılamadı: {exc}")])
        ) from exc
    try:
        pr = Profile.model_validate(ham)
    except ValidationError as exc:
        raise CompileFailed(
            CompileReport(
                errors=[
                    CompileError(
                        code=E_SEMA,
                        message=f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}",
                    )
                    for e in exc.errors()
                ]
            )
        ) from exc
    hatalar = _sir_denetimi(pr)
    if hatalar:
        raise CompileFailed(CompileReport(errors=hatalar))
    return pr


def profile_warnings(pr: Profile) -> list[CompileError]:
    """Derleme raporuna eklenen profil uyarıları (KK5)."""
    if not pr.masking.enabled:
        return [
            CompileError(
                code=W_MASKELEME_KAPALI,
                message=(
                    f"{pr.tenant_id}: PII maskelemesi AÇIKÇA kapatılmış."
                    " Kurumsal veri maskelenmeden dışarı çıkacak (§6.5)."
                ),
            )
        ]
    return []


def env_adi(deger: str) -> str:
    return deger[len(ENV_ONEK):].strip()


def resolve_secrets(pr: Profile, env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """Referansları GERÇEK değerlere çözer. Yalnız worker sürecinde çağrılır.

    Eksik ortam değişkeni derleme/başlangıç hatasıdır: gece yarısı ilk çağrıda
    değil, konfig yüklenirken patlar.
    """
    kaynak = os.environ if env is None else env
    hatalar: list[CompileError] = []
    cozulmus: dict[str, dict[str, str]] = {}
    for ad, konf in pr.connectors.items():
        cozulmus[ad] = {}
        for alan, deger in konf.items():
            if isinstance(deger, str) and deger.startswith(ENV_ONEK):
                var = env_adi(deger)
                if var not in kaynak:
                    hatalar.append(
                        CompileError(
                            code=E_SIR_ORTAM_YOK,
                            message=f"{ad}.{alan} → ortam değişkeni tanımlı değil: {var}",
                        )
                    )
                    continue
                cozulmus[ad][alan] = kaynak[var]
            else:
                cozulmus[ad][alan] = deger
    if hatalar:
        raise CompileFailed(CompileReport(errors=hatalar))
    return cozulmus

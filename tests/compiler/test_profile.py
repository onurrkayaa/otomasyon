from pathlib import Path

import pytest

from kernel.compiler import profile as P
from kernel.compiler.errors import CompileFailed

GECERLI = """
tenant_id: acme
tiers:
  deep: {model: claude-opus-5, effort: xhigh}
  standard: {model: claude-opus-5, effort: high}
  fast: {model: claude-opus-5, effort: low}
budgets: {daily_usd: 50.00}
masking:
  enabled: true
  patterns: [tckn, vkn, iban, eposta, telefon]
connectors:
  erp:
    endpoint: https://sap.acme.internal
    username: "env: ACME_SAP_USER"
    password: "env: ACME_SAP_PASSWORD"
"""


def yaz(tmp_path: Path, metin: str) -> Path:
    p = tmp_path / "profile.yaml"
    p.write_text(metin, encoding="utf-8")
    return p


def test_gecerli_profil_yuklenir(tmp_path):
    pr = P.load_profile(yaz(tmp_path, GECERLI))
    assert pr.tenant_id == "acme"
    assert pr.tiers["fast"].effort == "low"
    assert pr.masking.enabled is True
    assert pr.connectors["erp"]["password"] == "env: ACME_SAP_PASSWORD"


def test_duz_metin_sir_reddedilir(tmp_path):
    """§9.1: derleyici sır alanında env: öneki görmezse konfigi YÜKLEMEZ."""
    bozuk = GECERLI.replace('"env: ACME_SAP_PASSWORD"', '"Hunter2!"')
    with pytest.raises(CompileFailed) as exc:
        P.load_profile(yaz(tmp_path, bozuk))
    assert exc.value.report.codes() == ["E_SIR"]
    assert "Hunter2" not in str(exc.value), "hata mesajı sırrı SIZDIRMAMALI"
    assert "erp.password" in str(exc.value)


def test_env_referansi_cozulur(tmp_path):
    pr = P.load_profile(yaz(tmp_path, GECERLI))
    cozulmus = P.resolve_secrets(
        pr, {"ACME_SAP_USER": "svc_acme", "ACME_SAP_PASSWORD": "s3cr3t"}
    )
    assert cozulmus["erp"]["password"] == "s3cr3t"
    assert cozulmus["erp"]["endpoint"] == "https://sap.acme.internal"


def test_eksik_ortam_degiskeni_yuklemede_patlar(tmp_path):
    """Gece yarısı ilk çağrıda değil, konfig yüklenirken."""
    pr = P.load_profile(yaz(tmp_path, GECERLI))
    with pytest.raises(CompileFailed) as exc:
        P.resolve_secrets(pr, {"ACME_SAP_USER": "svc_acme"})
    assert exc.value.report.codes() == ["E_SIR_ORTAM_YOK"]
    assert "ACME_SAP_PASSWORD" in str(exc.value)


def test_profil_nesnesi_cozulmus_sir_TASIMAZ(tmp_path):
    """Profile veritabanına ve derlenmiş artefakta gider; değer taşımamalı."""
    pr = P.load_profile(yaz(tmp_path, GECERLI))
    P.resolve_secrets(pr, {"ACME_SAP_USER": "svc_acme", "ACME_SAP_PASSWORD": "s3cr3t"})
    assert "s3cr3t" not in pr.model_dump_json()


def test_ozel_sir_alani_isaretlenebilir(tmp_path):
    metin = GECERLI + "\nsecret_fields:\n  erp: [endpoint]\n"
    with pytest.raises(CompileFailed) as exc:
        P.load_profile(yaz(tmp_path, metin))
    assert exc.value.report.codes() == ["E_SIR"]


def test_ozel_sir_alani_harf_duyarsiz_eslesir(tmp_path):
    """`secret_fields: {erp: [Endpoint]}` gerçek alan adı `endpoint` ile eşleşmeli.

    Harf farkıyla sessizce kaçan bir eşleşme, işaretlenmemiş bir sır demektir.
    """
    metin = GECERLI + "\nsecret_fields:\n  erp: [Endpoint]\n"
    with pytest.raises(CompileFailed) as exc:
        P.load_profile(yaz(tmp_path, metin))
    assert exc.value.report.codes() == ["E_SIR"]


def test_maskeleme_kapatilirsa_uyari_uretilir(tmp_path):
    """KK5: sessizce kapanmaz."""
    metin = GECERLI.replace("enabled: true", "enabled: false")
    pr = P.load_profile(yaz(tmp_path, metin))
    uyarilar = P.profile_warnings(pr)
    assert [u.code for u in uyarilar] == ["W_MASKELEME_KAPALI"]


def test_maskeleme_blogu_yoksa_varsayilan_ACIK(tmp_path):
    metin = "\n".join(
        s for s in GECERLI.splitlines()
        if not s.startswith(("masking:", "  enabled:", "  patterns:"))
    )
    pr = P.load_profile(yaz(tmp_path, metin))
    assert pr.masking.enabled is True
    assert set(pr.masking.patterns) == set(P.VARSAYILAN_DESENLER)


def test_bozuk_yaml_hatasi_SIRRI_SIZDIRMAZ(tmp_path):
    """K14: sözdizimini BOZAN bir düz metin sır, hata mesajına düşmemeli.

    Mevcut test paketi yalnız sözdizimsel olarak geçerli ama semantik olarak
    yanlış bir sırrı ("Hunter2!") kapsıyordu. Sözdizimini bozan sır, K14'ün
    hedeflediği senaryonun ta kendisi: derleme çıktısı CI loglarına gider.
    """
    sir = "Hunter2Secret: evet boyle"
    bozuk = GECERLI.replace('"env: ACME_SAP_PASSWORD"', f'"{sir}')
    with pytest.raises(CompileFailed) as exc:
        P.load_profile(yaz(tmp_path, bozuk))
    assert "Hunter2Secret" not in str(exc.value)
    assert exc.value.report.codes() == ["E_SEMA"]
    assert "satır" in str(exc.value), "konum bilgisi korunmalı, yalnız içerik atılmalı"

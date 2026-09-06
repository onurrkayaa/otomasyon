from pathlib import Path

import pytest

from kernel.compiler import loader
from kernel.compiler.errors import CompileFailed

GECERLI = """
apiVersion: v1
name: mini
description: iki dugumlu akis
trigger: {type: http}
limits: {max_steps: 10, max_usd_per_run: 0.50, max_wallclock: 1h}
defaults: {model_tier: standard, retry: {attempts: 2}}
graph:
  - id: ozetle
    type: llm_task
    model_tier: fast
    outputs: {ozet: Ozet}
  - id: kaydet
    type: tool
    depends_on: [ozetle]
    tool: test.slow_writer
    inputs: [ozetle.ozet]
    idempotency: ["{{ run.id }}", "{{ node.id }}"]
    compensation: test.slow_writer_geri_al
"""


def yaz(tmp_path: Path, metin: str) -> Path:
    p = tmp_path / "akis.yaml"
    p.write_text(metin, encoding="utf-8")
    return p


def test_gecerli_yaml_tipli_spece_donusur(tmp_path):
    yuklu = loader.load_workflow(yaz(tmp_path, GECERLI))
    assert yuklu.spec.name == "mini"
    assert [n.id for n in yuklu.spec.graph] == ["ozetle", "kaydet"]
    assert yuklu.spec.graph[1].type == "tool"
    assert yuklu.spec.graph[1].idempotency == ["{{ run.id }}", "{{ node.id }}"]
    assert yuklu.spec.defaults.retry.attempts == 2


def test_beyan_edilmemis_alan_reddedilir(tmp_path):
    """extra='forbid': yazım hatası sessizce yok sayılan alan üretmemeli."""
    bozuk = GECERLI.replace("max_steps: 10", "max_stepss: 10")
    with pytest.raises(CompileFailed) as exc:
        loader.load_workflow(yaz(tmp_path, bozuk))
    assert exc.value.report.codes() == ["E_SEMA", "E_SEMA"]
    assert "max_stepss" in str(exc.value)


def test_bilinmeyen_dugum_tipi_reddedilir(tmp_path):
    bozuk = GECERLI.replace("type: llm_task", "type: sihirli_dugum")
    with pytest.raises(CompileFailed) as exc:
        loader.load_workflow(yaz(tmp_path, bozuk))
    assert "E_SEMA" in exc.value.report.codes()


def test_bozuk_yaml_kod_ile_reddedilir(tmp_path):
    with pytest.raises(CompileFailed) as exc:
        loader.load_workflow(yaz(tmp_path, "graph: [{id: a\n  bozuk"))
    assert exc.value.report.codes() == ["E_SEMA"]


def test_tum_hatalar_tek_raporda_toplanir(tmp_path):
    """Derleyici ilk hatada durmaz: iki ayrı bozukluk tek turda görünmeli."""
    bozuk = GECERLI.replace("name: mini", "name: 9Yanlis").replace(
        "max_steps: 10", "max_steps: 0"
    )
    with pytest.raises(CompileFailed) as exc:
        loader.load_workflow(yaz(tmp_path, bozuk))
    assert len(exc.value.report.errors) >= 2


def test_version_hash_icerige_bagli(tmp_path):
    a = loader.load_workflow(yaz(tmp_path, GECERLI)).version_hash
    b = loader.load_workflow(yaz(tmp_path, GECERLI + "\n")).version_hash
    assert a != b
    assert len(a) == 64


def test_bozuk_yaml_hatasi_KAYNAK_METNI_SIZDIRMAZ(tmp_path):
    """Aynı kusur yükleyicide de vardı; iki çağrı yeri tek yardımcıya bağlı."""
    hassas = "GIZLI_MUSTERI_VERISI: bozuk"
    with pytest.raises(CompileFailed) as exc:
        loader.load_workflow(yaz(tmp_path, f'apiVersion: v1\nname: x\nnot: "{hassas}\n'))
    assert "GIZLI_MUSTERI_VERISI" not in str(exc.value)
    assert exc.value.report.codes() == ["E_SEMA"]

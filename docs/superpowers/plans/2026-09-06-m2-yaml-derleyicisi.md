# M2 — YAML Derleyicisi Implementasyon Planı

> **Ajan işçiler için:** ZORUNLU ALT BECERİ: Bu planı görev görev uygulamak için
> `superpowers:subagent-driven-development` (önerilen) veya `superpowers:executing-plans`
> kullanın. Adımlar takip için checkbox (`- [ ]`) söz dizimindedir.

**Hedef:** Müşteriye giden şeyin kod değil konfig paketi olmasını sağlayan derleyici (K1): YAML → doğrulanmış graf → çalışan run. Bozuk YAML deploy'dan önce reddedilir, çalışma başına maliyet tavanı derleme anında hesaplanır, ve M1'in çekirdeği sabit Python akışı yerine derlenmiş grafı yürütür.

**Mimari:** Derleyici saf bir fonksiyondur: `(workflow.yaml, profile.yaml, types.py) → CompiledGraph | CompileReport`. LLM çağırmaz, ağa çıkmaz, maliyeti sıfırdır. Doğrulama üç katmanda yürür — şema (Pydantic `extra="forbid"`), graf yapısı (asiklik, kaçış kapısı, idempotency/telafi), ve tip/bağlam izolasyonu (`inputs` yalnız beyan edilmiş ata çıktılarına erişir). Derlenen graf `workflows` tablosuna içerik hash'iyle yazılır; run başlarken o satıra sabitlenir, böylece çalışan bir işin ortasında YAML değişse bile iş eski tanımla biter. Orkestratör beş düğüm tipini derlenmiş graf üzerinden dağıtır. Ağ geçidine iki koruma eklenir: kiralı bütçe rezervasyonu (M1'in TOCTOU açığını kapatır) ve tek noktalı PII maskeleme (§6.5).

**Teknoloji:** Python 3.13 · PostgreSQL 17 · psycopg 3 (senkron) · Pydantic v2 · PyYAML · anthropic SDK · pytest

**Spec:** `docs/superpowers/specs/2026-09-05-multi-agent-omurga-design.md`

**Önceki dilim:** `docs/superpowers/plans/2026-09-05-m1-yuruyen-iskelet.md` (tamamlandı, `master`'a birleştirildi)

**Kabul kriteri (spec §11):** Bozuk YAML'lar — her şema zorunluluğu için bir vaka — reddedilir; referans akış derlenir; tavan maliyet raporlanır.

---

## Global Kısıtlar

Bu bölüm her görevin gereksinimlerine örtük olarak dahildir. Değerler spec'ten birebir alınmıştır.

- **Dil:** Python 3.13 (K6). **Altyapı:** yalnız PostgreSQL + yerel nesne deposu (K3); yönetilen servis bağımlılığı yasak.
- **Kural 1:** Ajanlar birbirini doğrudan çağırmaz; tüm geçişler orkestratör üzerinden ve YAML'da beyan edilmiş kenarlardan.
- **Kural 2:** Paylaşılan mutable sözlük yok; her düğüm girdi/çıktısını beyan eder.
- **Kural 3:** Bağlam izolasyonu — bir düğüm `inputs`'ta beyan etmediği alanı **göremez**. Erişim denemesi hatadır.
- **Kural 4:** Yan etki yalnız `Tool` içinde; her yan etkili araç idempotency anahtarı + telafi beyan eder.
- **Kural 5:** `events` ekleme-yalnızdır; UPDATE veritabanı tetikleyicisiyle reddedilir. **Olay kaydına yazılan hiçbir şey geri alınamaz** — bu yüzden olaya giden her serbest metin maskelenmiş olmalıdır.
- **K8 Tavan Maliyet:** Derleyici en pahalı yolu yürüyerek çalışma başına üst sınır hesaplar. Tahmin **güvenli tarafta** olmalıdır; kesin olması gerekmez, aşılmaması gerekir.
- **K9:** `bulk` kademesi eval kanıtı olmadan **kilitli**. `bulk` isteyen düğüm için geçerli eval raporu yoksa derleme **hata** verir.
- **K14 Sır Kuralı:** Konfigde düz metin sır yok; yalnız `env: VAR_NAME` referansı. Çözülmüş sır değeri **derlenmiş artefakta ve veritabanına asla yazılmaz**.
- **K15:** Yan etki iki aşamalı kiralı rezervasyondan geçer; rezervasyon kendi transaction'ında commit edilir; kirası dolmuş rezervasyon kör tekrar edilmez.
- **K16:** Araç şemaları %100 statik ve kiracıdan bağımsızdır. Kiracıya özel hiçbir alan araç şemasına girmez.
- **Ağ geçidi tekeli:** `anthropic` SDK'sı yalnız `packages/kernel/gateway/transport.py` içinde import edilir. `packages/kernel/**/*.py` altında başka hiçbir dosyada düz "anthropic" dizgesi geçmez — yorumda bile. (M1 Görev 6'nın tekel testi bunu her koşuda tarar.)
- **Ağ geçidi kuralı:** `thinking: {"type": "disabled"}` **asla** gönderilmez; ucuzlatma yolu `effort` düşürmektir. `budget_tokens` gönderilmez (Opus 5'te 400 döner).
- **Sert kural (§9):** `customers/**` altında `types.py` dışında `.py` dosyası olamaz. İhlal = build kırılır.
- **M2 kapsam dışı:** telafi zincirlerinin yürütülmesi, onay kararının işlenmesi ve zaman aşımı yönlendirmesi (M3); kaset kayıt/tekrar ve eval koşucusu (M4); OTel ve gölge modu (M5).

---

## Kapsam Kararları

Spec §4'ün YAML örneği iki noktada birden fazla okumaya açık ve §11'in "M2 = derleyici + 5 düğüm tipi" satırı çalışma zamanının ne kadarını kapsadığını söylemiyor. Aşağıdaki kararlar bu belirsizlikleri kapatır; her biri gerekçesi ve yanlışsa maliyetiyle kayıtlıdır.

**KK1 — `depends_on` kontrol kenarıdır, `inputs` veri soyağacıdır.**
Spec §4'ün örneğinde `cikar` düğümü `depends_on: [oku]` yazıyor ama `oku`'dan sonra akış aslında `siniflandir` router'ından geçiyor. İki kenar semantiğini (veri bağımlılığı + kontrol akışı) aynı grafta üst üste bindirmek uygulanamaz bir belirsizlik üretir. Karar: **kontrol akışı yalnız `depends_on`'dan türer**; `inputs` ise herhangi bir **ata** düğümün beyan edilmiş çıktısına erişim hakkıdır (doğrudan ebeveyn olması gerekmez). Referans akışta bu, `cikar.depends_on: [siniflandir]` + `cikar.inputs: [oku.belgeler]` demektir — spec'in niyeti tam olarak budur, yalnız iki alana ayrılmıştır.
*Yanlışsa maliyeti:* referans YAML'da bir alanın yeniden yazılması.

**KK2 — v1 grafı tek etkin dallıdır; örtük paralellik derleme hatasıdır.**
Dallanmayan bir düğümün (`llm_task`, `tool`, `human_approval`) birden fazla çocuğu olamaz; olursa `E_ORTULU_DALLANMA` hatası verilir ve kullanıcı bir `router` ya da `switch` kullanmaya yönlendirilir. Böylece bir run'da **her an tam olarak bir etkin düğüm** olur. Bu değişmez üç şeyi bedavaya verir: run tamamlanma mantığı M1'den olduğu gibi kalır (son düğüm biterse run biter), çoklu gelen kenar asla çift tetiklenmez, ve denetim izi tek bir doğrusal hikâye anlatır. Gerekçe K7'nin gerekçesiyle aynıdır: kısmi hata semantiği kurumsal SLA'yı bulandırır — bu `map` için doğruysa paralel dallar için de doğrudur.
*Yanlışsa maliyeti:* v2'de düğüm-aktivasyon tablosu + run tamamlanma mantığının yeniden yazımı. K7 zaten aynı şekilli ertelemeyi kabul etmiş durumda.

**KK3 — M2 beş düğüm tipini derler, dördünü çalıştırır.**
`llm_task`, `router`, `switch`, `tool` uçtan uca çalışır. `human_approval` derlenir ve çalışma zamanında **yalnız askıya alır**: run `awaiting_approval` durumuna geçer, `approvals` satırı yazılır, iş kuyruktan çıkar, hiçbir süreç ayakta kalmaz. Onay kararının işlenmesi, zaman aşımı yönlendirmesi ve rol tabanlı yetkilendirme M3'ün "İnsan onayı" dilimidir. Gerekçe: derleyici tek başına teslim edilirse onu tüketen hiçbir şey olmaz ve M3 taşar; askıya alma yarısı ~20 satırdır ve referans akışın uçtan uca yürüyebilmesi için gereken tek parçadır.
*Yanlışsa maliyeti:* M3'e taşınacak yirmi satır.

**KK4 — Idempotency anahtarı konfigden gelir, araçtan değil.**
M1'de `Tool.idempotency_key(run_id, node_id, payload)` soyut bir metottu. §4.1(1) çift yazma korumasının **konfig seviyesinde** imkânsız kılınmasını şart koşuyor; anahtar araç kodunda gizliyken derleyici onu doğrulayamaz. Karar: anahtar YAML'daki `idempotency: ["{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]` listesinden derlenir, çalışma zamanında çözülür ve `execute_tool`'a hazır verilir. `Tool.idempotency_key` sözleşmeden **kaldırılır**.
*Yanlışsa maliyeti:* bir metodun geri eklenmesi; M2'de yapmak M3'te yapmaktan ucuz, çünkü çağrı yerleri henüz az.

**KK5 — Maskeleme varsayılan olarak AÇIK; kapatmak açık beyan ister.**
`profile.yaml` maskelemeyi daraltabilir ama sessizce kapatamaz. `masking` bloğu hiç yoksa yerleşik desen kümesi (TCKN, VKN, IBAN, e-posta, telefon) etkindir. Kapatmak `masking: {enabled: false}` yazmayı gerektirir ve derleme raporunda **uyarı** olarak görünür. Gerekçe K14'ün gerekçesiyle aynı: güvenceyi iyi niyete değil derleyiciye bağlamak.
*Yanlışsa maliyeti:* profile.yaml'da bir satır.

**KK6 — Spec §9.1'in YAML örneği olduğu gibi yüklenmez.**
`username: env: ACME_SAP_USER` geçerli YAML değildir (değersiz iç içe eşleme). Doğru biçim tırnaklıdır: `username: "env: ACME_SAP_USER"`. Referans profil ve derleyici bu biçimi kullanır; spec'in örneği açıklayıcıdır, yükleyici değil.
*Yanlışsa maliyeti:* yok — alternatif yok, öteki biçim ayrıştırılamıyor.

---

## Zorunlu Mimari Kurallar (kullanıcı tarafından konuldu)

Bu üç kural tartışmaya kapalıdır ve ilgili görevlerin kabul şartıdır. Uygulayıcı
bunlardan sapamaz; gözden geçiren sapmayı Kritik bulgu sayar.

**ZK1 — Döngü tespiti topolojik sıralamayla yapılır (Görev 3).**
Graf asikliği DFS renklendirmesiyle ya da "derinlik sınırı" gibi bir yaklaşımla
değil, **Kahn topolojik sıralamasıyla** kanıtlanır: sıraya giren düğüm sayısı
toplam düğüm sayısından azsa graf döngülüdür. Gerekçe iki katlı: topolojik sıra
zaten tavan maliyet DP'si (Görev 5) ve doğrulama gezisi için gereklidir, yani
döngü tespiti bedava gelir; ve tek bir mekanizma iki iddiayı birden taşıdığı için
ikisinin birbirinden sapması mümkün değildir.

**ZK2 — PII maskeleme haritası RAM'de değil, veritabanında şifreli ve süreli
tutulur (Görev 6 + 7).**
`MaskSession` haritayı süreç belleğinde tutamaz. Harita `steps.pii_map`
kolonuna **şifreli** (Fernet, anahtar yalnız `OTOMASYON_PII_KEY` ortam
değişkeninden — K14) ve **süreli** (`steps.pii_map_expires_at`) yazılır.
Yazma sırası bağlayıcıdır: harita, taşıma katmanı çağrılmadan ÖNCE commit
edilir; yanıt maskesi çözüldükten sonra kolon `NULL`'lanır; artakalanlar
worker boştayken süpürülür.

Üç gerekçe: (a) çağrının ortasında ölen bir worker'ın haritası kaybolmaz, yani
devralan worker kaydedilmiş bir yanıtı çözebilir — RAM'deki harita ölümle
birlikte gider ve maskeli metin kalıcı olarak anlamsızlaşır; (b) harita
görünmez süreç durumu olmaktan çıkıp TTL'i olan, denetlenebilir bir kayda
dönüşür — KVKK tartışmasında gösterilebilir; (c) şifreleme olmadan bu kolon
kendi başına bir PII deposu olurdu.

Sert sınır: `pii_map` **asla** `events` tablosuna yazılmaz. `events`
ekleme-yalnızdır (Kural 5) ve oraya yazılan bir harita hiçbir zaman
silinemezdi. `steps` UPDATE edilebilir olduğu için temizlenebilir — kolonun
orada olmasının nedeni budur.

**ZK3 — `idempotency` statik olamaz; en az bir run-değişken referans zorunludur
(Görev 1 şeması + Görev 3 denetimi).**
Anahtar parçaları `{{ ... }}` şablon söz dizimiyle yazılır; şablon dışındaki
her parça düz metin sabittir:

```yaml
idempotency: ["erp-yazma", "{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]
```

Geçerli referanslar: `run.id`, `node.id`, `tenant.id` ve `<düğüm>.<alan>[.<altalan>]`
yolları. Derleyici, listede **run'dan run'a değişen** en az bir referans
(`run.id` ya da bir düğüm çıktısı yolu) bulamazsa `E_STATIK_IDEMPOTENCY` verir.

Bu kural gerçek bir sessiz felaketi kapatıyor: `idempotency: [node_id]` her
run'da AYNI anahtarı üretir. İkinci run'ın aracı `tool_calls` satırını
`completed` bulur, dış çağrıyı **hiç yapmaz** ve birinci run'ın yanıtını
döndürür — yani müşterinin ikinci faturası ERP'ye hiç yazılmaz ve sistem bunu
başarı olarak raporlar. `{{ }}` işareti ayrıca "bu bir yol mu yoksa sabit mi"
belirsizliğini ortadan kaldırır ve sabit bir önek yazmayı (teşhis için
yararlı) mümkün kılar.

---

## Dosya Yapısı

| Dosya | Sorumluluk |
|---|---|
| `packages/kernel/compiler/errors.py` | `CompileError`, `CompileReport`, `CompileFailed`, hata kodları |
| `packages/kernel/compiler/schema.py` | §4 YAML sözleşmesinin Pydantic karşılığı (`extra="forbid"`) |
| `packages/kernel/compiler/loader.py` | YAML → `WorkflowSpec` + içerik hash'i |
| `packages/kernel/compiler/profile.py` | `profile.yaml` + sır çözümleme (K14) |
| `packages/kernel/compiler/graph.py` | `CompiledGraph`: kenar haritası, topolojik sıra, yol sayımı |
| `packages/kernel/compiler/validate_graph.py` | §4.1 zorunlulukları + yapısal §4.2 denetimleri |
| `packages/kernel/compiler/validate_types.py` | `types.py` yüklenmesi, tip uyumu, bağlam izolasyonu |
| `packages/kernel/compiler/cost.py` | Tavan maliyet (K8) + rapor üretimi |
| `packages/kernel/compiler/__init__.py` | `compile_workflow()` cephesi |
| `packages/kernel/compiler/__main__.py` | `python -m kernel.compiler` — derleme + rapor CLI'ı |
| `packages/kernel/gateway/masking.py` | PII maskeleme — tek nokta (§6.5) |
| `packages/kernel/state/migrations/002_m2.sql` | `workflows`, `approvals`, `artifacts`, bütçe rezervasyonu kolonları |
| `packages/kernel/state/artifacts.py` | Yerel dosya sistemi nesne deposu |
| `packages/kernel/state/workflows.py` | Derlenmiş grafın kalıcılığı ve run'a sabitlenmesi |
| `packages/kernel/orchestrator/nodes.py` | `router`, `switch`, `human_approval` çalışma zamanları |
| `packages/kernel/orchestrator/channel.py` | Tipli kanal: beyan edilmiş girdilerin toplanması (Kural 3) |
| `customers/acme/workflows/belge_girisi.yaml` | Referans akış (§10) |
| `customers/acme/types.py` | Pydantic modelleri — `customers/` altındaki **tek** `.py` |
| `customers/acme/profile.yaml` | Kademe eşlemesi, bütçeler, maskeleme, sır referansları |
| `tests/compiler/fixtures/bozuk/*.yaml` | Her şema zorunluluğu için bir reddedilme vakası |

**Değişen dosyalar:** `packages/kernel/orchestrator/flow.py` (silinir), `step.py` (derlenmiş grafa geçer), `runner.py` (`start_run` workflow alır), `tools/base.py` (`idempotency_key` kaldırılır), `tools/execution.py` (anahtar dışarıdan gelir), `gateway/gateway.py` (rezervasyon + maskeleme), `gateway/types.py` (`output_schema`), `gateway/transport.py` (yapılandırılmış çıktı), `pyproject.toml` (PyYAML).

---

### Görev 1: Şema modelleri, hata sözleşmesi ve YAML ayrıştırma

Derleyicinin taban katmanı. Bu görevden sonra bir YAML dosyası tipli bir `WorkflowSpec`'e dönüşür ve beyan edilmemiş her alan hataya düşer.

**Dosyalar:**
- Değiştir: `pyproject.toml` (PyYAML bağımlılığı)
- Oluştur: `packages/kernel/compiler/__init__.py` (bu görevde boş)
- Oluştur: `packages/kernel/compiler/errors.py`
- Oluştur: `packages/kernel/compiler/schema.py`
- Oluştur: `packages/kernel/compiler/loader.py`
- Test: `tests/compiler/__init__.py`, `tests/compiler/test_loader.py`

**Arayüzler:**
- Üretir: `CompileError(code, message, node_id=None)`, `CompileReport(errors, warnings, cost=None)`, `CompileFailed(report)`, `WorkflowSpec`, `Node` birleşimi ve beş düğüm modeli, `load_workflow(path) -> LoadedWorkflow(spec, source, version_hash)`
- Tüketir: yok

- [ ] **Adım 1: PyYAML bağımlılığını ekle**

`pyproject.toml` içindeki `dependencies` listesine ekle:

```toml
dependencies = [
    "psycopg[binary,pool]>=3.2",
    "pydantic>=2.9",
    "anthropic>=0.40",
    "pyyaml>=6.0",
]
```

Kur: `.venv/bin/pip install -e '.[dev]'`

- [ ] **Adım 2: Başarısız testi yaz — geçerli YAML ayrıştırılır**

`tests/compiler/__init__.py` dosyasını boş oluştur. `tests/compiler/test_loader.py`:

```python
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
    assert yuklu.spec.graph[1].idempotency == ["run_id", "node_id"]
    assert yuklu.spec.defaults.retry.attempts == 2
```

- [ ] **Adım 3: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_loader.py -v`
Beklenen: FAIL — `ModuleNotFoundError: No module named 'kernel.compiler.loader'`

- [ ] **Adım 4: `errors.py` yaz**

```python
"""Derleme hatalarının sözleşmesi.

Derleyici İLK hatada durmaz: bir müşterinin konfigindeki beş hatayı beş ayrı
deploy turunda öğrenmesi kabul edilemez bir deneyimdir. Tüm bulgular toplanır
ve tek raporda döner. Her hatanın makine-okunur bir KODU vardır; testler ve
kullanıcı arayüzü mesaj metnine değil koda bakar.
"""
from __future__ import annotations

from pydantic import BaseModel

# Şema ve ayrıştırma
E_SEMA = "E_SEMA"                          # beyan edilmemiş alan, tip hatası, bozuk YAML
# Graf yapısı
E_DONGU = "E_DONGU"                        # graf asiklik değil
E_KENAR_HEDEFI = "E_KENAR_HEDEFI"          # var olmayan düğüme kenar
E_COKLU_GIRIS = "E_COKLU_GIRIS"            # birden fazla giriş düğümü
E_ORTULU_DALLANMA = "E_ORTULU_DALLANMA"    # dallanmayan düğümün 2+ çocuğu (KK2)
E_ROTA_DISI_BAGIMLILIK = "E_ROTA_DISI_BAGIMLILIK"  # router'a rota dışından bağımlılık
# §4.1 şema zorunlulukları
E_IDEMPOTENCY = "E_IDEMPOTENCY"            # (1) yan etkili araçta idempotency yok
E_STATIK_IDEMPOTENCY = "E_STATIK_IDEMPOTENCY"  # ZK3: anahtar run'dan run'a değişmiyor
E_IDEMPOTENCY_IFADESI = "E_IDEMPOTENCY_IFADESI"  # ZK3: ayrıştırılamayan {{ }} ifadesi
E_TELAFI = "E_TELAFI"                      # (1) telafi yok
E_KACIS_KAPISI = "E_KACIS_KAPISI"          # (2) router'da kaçış kapısı yok
E_BEYAN_EDILMEMIS_ALAN = "E_BEYAN_EDILMEMIS_ALAN"  # (3) Kural 3 ihlali
E_ARTIFACT = "E_ARTIFACT"                  # (4) büyük içerik gömülü
E_BULK_EVAL = "E_BULK_EVAL"                # (5) bulk kademesi eval raporsuz
E_SIR = "E_SIR"                            # (6) düz metin sır
E_SIR_ORTAM_YOK = "E_SIR_ORTAM_YOK"        # env: referansı var, ortam değişkeni yok
# Tip ve kademe
E_TIP_UYUMSUZ = "E_TIP_UYUMSUZ"
E_BILINMEYEN_TIP = "E_BILINMEYEN_TIP"      # types.py'de olmayan model adı
E_BILINMEYEN_KADEME = "E_BILINMEYEN_KADEME"
E_BILINMEYEN_ARAC = "E_BILINMEYEN_ARAC"
E_SWITCH_IFADESI = "E_SWITCH_IFADESI"      # ayrıştırılamayan case ifadesi
E_SWITCH_DEFAULT = "E_SWITCH_DEFAULT"      # default dalı yok
# Tavan analizi
E_ADIM_TAVANI = "E_ADIM_TAVANI"            # en kötü yol max_steps'i aşıyor
E_BUTCE_TAVANI = "E_BUTCE_TAVANI"          # en kötü yolun maliyeti bütçeyi aşıyor
# Uyarılar
W_RECONCILE = "W_RECONCILE"                # §5.1: reconcile beyanı yok
W_ULASILAMAZ = "W_ULASILAMAZ"              # giriş düğümünden erişilemeyen düğüm
W_EVAL_YOK = "W_EVAL_YOK"                  # router kenarı için altın veri seti yok
W_MASKELEME_KAPALI = "W_MASKELEME_KAPALI"  # KK5: maskeleme açıkça kapatılmış


class CompileError(BaseModel):
    code: str
    message: str
    node_id: str | None = None

    def __str__(self) -> str:
        yer = f" [{self.node_id}]" if self.node_id else ""
        return f"{self.code}{yer}: {self.message}"


class CompileReport(BaseModel):
    errors: list[CompileError] = []
    warnings: list[CompileError] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> list[str]:
        return [e.code for e in self.errors]


class CompileFailed(Exception):
    """Derleme başarısız. Tüm bulgular `report` içindedir."""

    def __init__(self, report: CompileReport):
        self.report = report
        super().__init__("; ".join(str(e) for e in report.errors))
```

- [ ] **Adım 5: `schema.py` yaz**

```python
"""§4 YAML sözleşmesinin Pydantic karşılığı.

Bu dosya izin verilen alanların TEK gerçek kaynağıdır. `extra="forbid"`
sayesinde YAML'daki bir yazım hatası sessizce yok sayılan bir alan üretmez,
derleme hatası üretir — spec §4.1'in "ihlal edilirse YAML yüklenmez" kuralının
mekanik karşılığı.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# Süre biçimi: 30s, 15m, 4h, 2d
SURE = r"^\d+[smhd]$"
KIMLIK = r"^[a-z][a-z0-9_]*$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Retry(Strict):
    attempts: int = Field(default=3, ge=1, le=10)
    backoff: Literal["exponential", "linear"] = "exponential"


class Limits(Strict):
    max_steps: int = Field(ge=1, le=500)
    max_usd_per_run: Decimal = Field(gt=0)
    max_wallclock: str = Field(pattern=SURE)


class Defaults(Strict):
    model_tier: str = "standard"
    retry: Retry = Retry()
    max_tokens: int = Field(default=16000, ge=256, le=64000)


class Trigger(Strict):
    type: Literal["http", "email", "cron", "webhook", "file"]
    mailbox: str | None = None
    schedule: str | None = None
    path: str | None = None


class LowConfidence(Strict):
    threshold: float = Field(gt=0.0, lt=1.0)
    route: str


class ApprovalTimeout(Strict):
    after: str = Field(pattern=SURE)
    route: str


class NodeBase(Strict):
    id: str = Field(pattern=KIMLIK)
    depends_on: list[str] = []
    inputs: list[str] = []


class LLMTaskNode(NodeBase):
    type: Literal["llm_task"]
    model_tier: str | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=64000)
    outputs: dict[str, str] = {}


class RouterNode(NodeBase):
    type: Literal["router"]
    model_tier: str | None = None
    routes: dict[str, str | None] = Field(min_length=2)
    on_low_confidence: LowConfidence | None = None


class SwitchNode(NodeBase):
    type: Literal["switch"]
    on: str
    cases: dict[str, str | None] = Field(min_length=1)


class ToolNode(NodeBase):
    type: Literal["tool"]
    tool: str
    idempotency: list[str] = []
    """Yan etkinin evrensel kimliğini üreten parçalar (ZK3).

    Her parça ya düz metin sabittir ya da `{{ ... }}` şablon referansıdır:

        idempotency: ["erp-yazma", "{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]

    Söz dizimi burada serbesttir; ANLAMSAL denetim Görev 3'tedir — listede
    run'dan run'a değişen en az bir referans bulunmak ZORUNDADIR, aksi halde
    ikinci run'ın yan etkisi sessizce atlanır (E_STATIK_IDEMPOTENCY)."""
    compensation: str | None = None
    batchable: bool = False          # K10: v1'de yok sayılır, şemada rezerve
    outputs: dict[str, str] = {}


class HumanApprovalNode(NodeBase):
    type: Literal["human_approval"]
    assignee_role: str
    context_fields: list[str] = []
    timeout: ApprovalTimeout | None = None


Node = Annotated[
    Union[LLMTaskNode, RouterNode, SwitchNode, ToolNode, HumanApprovalNode],
    Field(discriminator="type"),
]

# Yan etki üretebilen düğüm tipleri. §4.1(1) yalnız bunları bağlar.
YAN_ETKILI_TIPLER = frozenset({"tool"})
# LLM harcayan düğüm tipleri. Tavan maliyet yalnız bunları sayar.
LLM_TIPLERI = frozenset({"llm_task", "router"})
# Dallanan düğüm tipleri. KK2: yalnız bunların birden fazla çocuğu olabilir.
DALLANAN_TIPLER = frozenset({"router", "switch"})


class WorkflowSpec(Strict):
    api_version: Literal["v1"] = Field(alias="apiVersion")
    name: str = Field(pattern=KIMLIK)
    description: str = ""
    trigger: Trigger
    limits: Limits
    defaults: Defaults = Defaults()
    graph: list[Node] = Field(min_length=1)
```

- [ ] **Adım 6: `loader.py` yaz**

```python
"""YAML dosyası → tipli `WorkflowSpec` + içerik hash'i.

version_hash kaynak baytların sha256'sıdır. Spec §5: run başlarken bağlandığı
YAML versiyonu sabitlenir; "workflow versioning" problemi böylece ek altyapı
olmadan çözülür.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from kernel.compiler.errors import E_SEMA, CompileError, CompileFailed, CompileReport
from kernel.compiler.schema import WorkflowSpec


class LoadedWorkflow(BaseModel):
    spec: WorkflowSpec
    source: str
    version_hash: str


def parse_workflow(source: str) -> WorkflowSpec:
    """Metin → spec. Bozuksa CompileFailed fırlatır."""
    try:
        ham = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise CompileFailed(
            CompileReport(errors=[CompileError(code=E_SEMA, message=f"YAML ayrıştırılamadı: {exc}")])
        ) from exc
    if not isinstance(ham, dict):
        raise CompileFailed(
            CompileReport(errors=[CompileError(code=E_SEMA, message="kök öğe eşleme olmalı")])
        )
    try:
        return WorkflowSpec.model_validate(ham)
    except ValidationError as exc:
        raise CompileFailed(CompileReport(errors=_sema_hatalari(exc))) from exc


def _sema_hatalari(exc: ValidationError) -> list[CompileError]:
    """Pydantic hatalarını CompileError'a çevirir; düğüm kimliğini korur."""
    out: list[CompileError] = []
    for e in exc.errors():
        yer = ".".join(str(p) for p in e["loc"])
        out.append(
            CompileError(code=E_SEMA, message=f"{yer}: {e['msg']}", node_id=None)
        )
    return out


def load_workflow(path: Path) -> LoadedWorkflow:
    source = Path(path).read_text(encoding="utf-8")
    return LoadedWorkflow(
        spec=parse_workflow(source),
        source=source,
        version_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
```

- [ ] **Adım 7: Testi koş, geçtiğini gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_loader.py -v`
Beklenen: PASS

- [ ] **Adım 8: Reddedilme testlerini yaz**

`tests/compiler/test_loader.py` sonuna ekle:

```python
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
```

- [ ] **Adım 9: Testleri koş**

Koş: `.venv/bin/python -m pytest tests/compiler -v`
Beklenen: 6 PASS

- [ ] **Adım 10: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: mevcut 69 test + 6 yeni = 75 passed, çıktı temiz.

```bash
git add pyproject.toml packages/kernel/compiler tests/compiler
git commit -m "feat(compiler): YAML şeması, hata sözleşmesi ve yükleyici"
```

---

### Görev 2: Profil ve sır çözümleme (K14)

`profile.yaml` kademe eşlemesini, bütçeleri, maskeleme politikasını ve konnektör sırlarının **referanslarını** taşır. Sır değerleri asla dosyada, asla derlenmiş artefaktta, asla veritabanında.

**Dosyalar:**
- Oluştur: `packages/kernel/compiler/profile.py`
- Test: `tests/compiler/test_profile.py`

**Arayüzler:**
- Tüketir: `CompileError`, `CompileReport`, `CompileFailed`, `E_SIR`, `E_SIR_ORTAM_YOK`, `W_MASKELEME_KAPALI`
- Üretir: `Profile`, `TierSpec`, `MaskingPolicy`, `load_profile(path) -> Profile`, `resolve_secrets(profile, env) -> dict[str, dict[str, str]]`, `SECRET_FIELD_SUFFIXES`

- [ ] **Adım 1: Başarısız testi yaz**

`tests/compiler/test_profile.py`:

```python
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
```

- [ ] **Adım 2: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_profile.py -v`
Beklenen: FAIL — `No module named 'kernel.compiler.profile'`

- [ ] **Adım 3: `profile.py` yaz**

```python
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
```

- [ ] **Adım 4: Testi koş, geçtiğini gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_profile.py -v`
Beklenen: 2 PASS

- [ ] **Adım 5: Kalan sır testlerini yaz**

`tests/compiler/test_profile.py` sonuna ekle:

```python
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
```

- [ ] **Adım 6: Testleri koş ve commit et**

Koş: `.venv/bin/python -m pytest tests/compiler -q -W error`
Beklenen: 14 passed.

```bash
git add packages/kernel/compiler/profile.py tests/compiler/test_profile.py
git commit -m "feat(compiler): profil yükleme ve sır çözümleme (K14)"
```

---

### Görev 3: Graf inşası ve yapısal doğrulama (§4.1 + §4.2)

Grafın kendisi hakkındaki her denetim burada. Bu görevden sonra spec §4.1'in altı zorunluluğundan dördü ve §4.2'nin yapısal denetimleri mekanik olarak uygulanır.

**ZK1 zorunlu:** döngü tespiti Kahn topolojik sıralamasıyla yapılır — `_topolojik()`
sıraya alınan düğüm sayısını toplam düğüm sayısıyla karşılaştırır ve azsa `None`
döner. DFS renklendirmesi ya da derinlik sınırı **kabul edilmez**: aynı sıra
Görev 5'in tavan maliyet DP'sinde de kullanılacak, iki iddia tek mekanizmayı
paylaşmalı.

**ZK3 zorunlu:** `idempotency` listesinin anlamsal denetimi (`{{ }}` ayrıştırma +
"en az bir run-değişken referans") bu görevde `compiler/expr.py` içinde yazılır.

**Dosyalar:**
- Oluştur: `packages/kernel/compiler/graph.py`
- Oluştur: `packages/kernel/compiler/expr.py` (ZK3 — `{{ }}` ayrıştırıcı)
- Oluştur: `packages/kernel/compiler/validate_graph.py`
- Değiştir: `packages/kernel/tools/registry.py` (`names()`, `reconcile_supported()`)
- Test: `tests/compiler/test_graph.py`, `tests/compiler/test_validate_graph.py`

**Arayüzler:**
- Tüketir: `WorkflowSpec`, `CompileError`, hata kodları, `DALLANAN_TIPLER`, `YAN_ETKILI_TIPLER`
- Üretir: `Graph(spec, children, parents, entry, topo)`, `build_graph(spec) -> tuple[Graph | None, list[CompileError]]`, `ValidationContext`, `validate_graph(graph, ctx) -> CompileReport`, `parse_case(key) -> tuple[str, object]`, `registry.names() -> set[str]`, `registry.reconcile_supported() -> set[str]`

- [ ] **Adım 1: Registry'ye iki sorgu ekle**

`packages/kernel/tools/registry.py` sonuna:

```python
def names() -> set[str]:
    """Derleyicinin "referans verilen araç kayıtlı mı" denetimi için."""
    return set(_TOOLS)


def reconcile_supported() -> set[str]:
    """§5.1: reconcile beyan etmeyen araçlarda kira dolması = insan kuyruğu."""
    return {ad for ad, t in _TOOLS.items() if t.supports_reconcile}
```

- [ ] **Adım 2: Başarısız graf testini yaz**

`tests/compiler/test_graph.py`:

```python
import pytest

from kernel.compiler import graph as G
from kernel.compiler.loader import parse_workflow

DUZ = """
apiVersion: v1
name: duz
trigger: {type: http}
limits: {max_steps: 10, max_usd_per_run: 1.00, max_wallclock: 1h}
graph:
  - {id: a, type: llm_task}
  - {id: b, type: llm_task, depends_on: [a]}
  - {id: c, type: llm_task, depends_on: [b]}
"""


def kur(metin: str) -> G.Graph:
    g, hatalar = G.build_graph(parse_workflow(metin))
    assert hatalar == [], hatalar
    assert g is not None
    return g


def test_kenarlar_depends_ondan_turer():
    g = kur(DUZ)
    assert g.entry == "a"
    assert g.children["a"] == ["b"]
    assert g.parents["c"] == ["b"]
    assert g.topo == ["a", "b", "c"]
    assert g.terminals() == ["c"]


def test_atalar_gecisli_hesaplanir():
    """inputs herhangi bir ATA'nın çıktısına erişebilir (KK1)."""
    g = kur(DUZ)
    assert g.ancestors("c") == {"a", "b"}
    assert g.ancestors("a") == set()


def test_dongu_tespit_edilir():
    metin = DUZ.replace("- {id: a, type: llm_task}", "- {id: a, type: llm_task, depends_on: [c]}")
    g, hatalar = G.build_graph(parse_workflow(metin))
    assert g is None
    assert [h.code for h in hatalar] == ["E_DONGU"]


def test_var_olmayan_ebeveyn_hatasi():
    metin = DUZ.replace("depends_on: [a]", "depends_on: [yok]")
    _, hatalar = G.build_graph(parse_workflow(metin))
    assert "E_KENAR_HEDEFI" in [h.code for h in hatalar]


def test_iki_giris_dugumu_reddedilir():
    metin = DUZ + "  - {id: d, type: llm_task}\n"
    _, hatalar = G.build_graph(parse_workflow(metin))
    assert "E_COKLU_GIRIS" in [h.code for h in hatalar]


def test_tekrarlanan_dugum_kimligi_reddedilir():
    metin = DUZ + "  - {id: a, type: llm_task, depends_on: [c]}\n"
    _, hatalar = G.build_graph(parse_workflow(metin))
    assert "E_SEMA" in [h.code for h in hatalar]


def test_yollar_sayilir():
    g = kur(DUZ)
    assert g.paths() == [["a", "b", "c"]]
```

- [ ] **Adım 3: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_graph.py -v`
Beklenen: FAIL — `No module named 'kernel.compiler.graph'`

- [ ] **Adım 4: `graph.py` yaz**

```python
"""Derlenmiş graf: kenar haritaları, topolojik sıra, yol sayımı.

Kenar modeli (KK1): kontrol akışı YALNIZ `depends_on`'dan türer. X'in
`depends_on`'unda P varsa P → X bir kenardır. `inputs` kenar üretmez; o
yalnız bir ata düğümün çıktısına ERİŞİM HAKKIDIR.

Graf asiklik olmak zorundadır; bu modül döngü bulursa graf kurulmaz ve
sonraki hiçbir analiz (tavan maliyet dahil) koşmaz — döngü üzerinde en uzun
yol tanımsızdır.
"""
from __future__ import annotations

from kernel.compiler.errors import (
    E_COKLU_GIRIS,
    E_DONGU,
    E_KENAR_HEDEFI,
    E_SEMA,
    CompileError,
)
from kernel.compiler.schema import Node, WorkflowSpec


class Graph:
    """Kurulmuş, asikliği doğrulanmış graf. Yalnız `build_graph` üretir."""

    def __init__(
        self,
        spec: WorkflowSpec,
        nodes: dict[str, Node],
        children: dict[str, list[str]],
        parents: dict[str, list[str]],
        entry: str,
        topo: list[str],
    ):
        self.spec = spec
        self.nodes = nodes
        self.children = children
        self.parents = parents
        self.entry = entry
        self.topo = topo

    def terminals(self) -> list[str]:
        return [n for n in self.topo if not self.children[n]]

    def ancestors(self, node_id: str) -> set[str]:
        """Geçişli atalar. `inputs` yalnız bu kümeye erişebilir (Kural 3)."""
        out: set[str] = set()
        yigin = list(self.parents[node_id])
        while yigin:
            p = yigin.pop()
            if p in out:
                continue
            out.add(p)
            yigin.extend(self.parents[p])
        return out

    def reachable(self) -> set[str]:
        out: set[str] = set()
        yigin = [self.entry]
        while yigin:
            n = yigin.pop()
            if n in out:
                continue
            out.add(n)
            yigin.extend(self.children[n])
        return out

    def paths(self) -> list[list[str]]:
        """Giriş → uç tüm yollar. Tek etkin dal kuralı (KK2) sayıyı düğüm
        sayısıyla sınırlı tutar; kombinatorik patlama olamaz çünkü yalnız
        router/switch dallanır ve dallar birleşse bile tek dal etkindir."""
        out: list[list[str]] = []

        def yuru(n: str, yol: list[str]) -> None:
            yol = [*yol, n]
            if not self.children[n]:
                out.append(yol)
                return
            for c in self.children[n]:
                yuru(c, yol)

        yuru(self.entry, [])
        return out


def build_graph(spec: WorkflowSpec) -> tuple[Graph | None, list[CompileError]]:
    """Grafı kurar. Kurulamıyorsa (None, hatalar) döner."""
    hatalar: list[CompileError] = []
    nodes: dict[str, Node] = {}
    for n in spec.graph:
        if n.id in nodes:
            hatalar.append(
                CompileError(code=E_SEMA, message=f"tekrarlanan düğüm kimliği: {n.id}", node_id=n.id)
            )
            continue
        nodes[n.id] = n

    children: dict[str, list[str]] = {k: [] for k in nodes}
    parents: dict[str, list[str]] = {k: [] for k in nodes}
    for n in nodes.values():
        for p in n.depends_on:
            if p not in nodes:
                hatalar.append(
                    CompileError(
                        code=E_KENAR_HEDEFI,
                        message=f"depends_on var olmayan düğümü gösteriyor: {p}",
                        node_id=n.id,
                    )
                )
                continue
            children[p].append(n.id)
            parents[n.id].append(p)

    girisler = [k for k, ps in parents.items() if not ps]
    if len(girisler) > 1:
        hatalar.append(
            CompileError(
                code=E_COKLU_GIRIS,
                message=(
                    "birden fazla giriş düğümü: "
                    + ", ".join(sorted(girisler))
                    + " — tetikleyici tek düğüm başlatır"
                ),
            )
        )

    topo = _topolojik(nodes, children, parents)
    if topo is None:
        hatalar.append(
            CompileError(code=E_DONGU, message="graf asiklik değil; döngü içeren düğümler var")
        )
        return None, hatalar
    if not girisler:
        return None, hatalar
    if hatalar and any(h.code == E_KENAR_HEDEFI for h in hatalar):
        return None, hatalar

    return Graph(spec, nodes, children, parents, girisler[0], topo), hatalar


def _topolojik(
    nodes: dict[str, Node], children: dict[str, list[str]], parents: dict[str, list[str]]
) -> list[str] | None:
    derece = {k: len(v) for k, v in parents.items()}
    kuyruk = sorted(k for k, d in derece.items() if d == 0)
    out: list[str] = []
    while kuyruk:
        n = kuyruk.pop(0)
        out.append(n)
        for c in children[n]:
            derece[c] -= 1
            if derece[c] == 0:
                kuyruk.append(c)
        kuyruk.sort()
    return out if len(out) == len(nodes) else None
```

- [ ] **Adım 5: Graf testlerini koş**

Koş: `.venv/bin/python -m pytest tests/compiler/test_graph.py -v`
Beklenen: 7 PASS

- [ ] **Adım 5b: `expr.py` yaz (ZK3)**

```python
"""`{{ ... }}` şablon referanslarının ayrıştırıcısı (ZK3).

Derleme zamanı (validate_graph) ve çalışma zamanı (orchestrator/channel) AYNI
ayrıştırıcıyı kullanır. İki ayrı yorumcu, derleyicinin doğruladığı anahtarla
çalışma zamanının ürettiği anahtarın sessizce farklılaşması demektir — ve
idempotency anahtarında bu, çift yan etki demektir.
"""
from __future__ import annotations

import re

SABLON = re.compile(r"^\s*\{\{\s*(.+?)\s*\}\}\s*$")
# Run boyunca sabit kalan, run'dan run'a DEĞİŞMEYEN referanslar.
SABIT_REFERANSLAR = frozenset({"node.id", "tenant.id"})
# Run'dan run'a değişen yerleşik referans.
RUN_REFERANSI = "run.id"
YOL_DESENI = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def parse_part(parca: str) -> str | None:
    """Düz metin sabit → None. `{{ ref }}` → referans metni.

    Ayrıştırılamayan bir şablon ValueError'dır: `{{ run.id` gibi yarım bir
    ifade sessizce düz metne dönüşürse anahtar sabitleşir ve ZK3 delinir.
    """
    if "{{" in parca or "}}" in parca:
        m = SABLON.match(parca)
        if m is None:
            raise ValueError(f"ayrıştırılamayan idempotency ifadesi: {parca!r}")
        ref = m.group(1)
        if ref != RUN_REFERANSI and ref not in SABIT_REFERANSLAR and not YOL_DESENI.match(ref):
            raise ValueError(
                f"geçersiz referans: {ref!r};"
                f" geçerli: run.id, node.id, tenant.id ya da <düğüm>.<alan>"
            )
        return ref
    return None


def run_degisken_mi(ref: str) -> bool:
    """Bu referans run'dan run'a değişir mi? ZK3'ün asıl sorusu budur."""
    return ref == RUN_REFERANSI or ref not in SABIT_REFERANSLAR
```

- [ ] **Adım 6: Başarısız doğrulama testlerini yaz**

`tests/compiler/test_validate_graph.py`:

```python
import pytest

from kernel.compiler import graph as G
from kernel.compiler import validate_graph as V
from kernel.compiler.loader import parse_workflow

REFERANS = """
apiVersion: v1
name: ref
trigger: {type: http}
limits: {max_steps: 25, max_usd_per_run: 3.00, max_wallclock: 4h}
defaults: {model_tier: standard, retry: {attempts: 3}}
graph:
  - id: oku
    type: tool
    tool: belge.oku
    idempotency: ["{{ run.id }}", "{{ node.id }}"]
    compensation: belge.oku_geri_al
    outputs: {belgeler: BelgeListesi}
  - id: siniflandir
    type: router
    depends_on: [oku]
    model_tier: fast
    inputs: [oku.belgeler]
    routes: {fatura: cikar, dekont: null, belirsiz: insan_kuyrugu}
    on_low_confidence: {threshold: 0.75, route: belirsiz}
  - id: cikar
    type: llm_task
    depends_on: [siniflandir]
    inputs: [oku.belgeler]
    outputs: {fatura: FaturaModeli}
  - id: insan_kuyrugu
    type: human_approval
    depends_on: [siniflandir]
    assignee_role: muhasebe_muduru
"""

CTX = V.ValidationContext(
    known_tools={"belge.oku", "belge.oku_geri_al", "erp.post_invoice", "erp.void_invoice"},
    reconcile_tools={"erp.post_invoice"},
    tiers={"deep", "standard", "fast"},
    eval_dir=None,
)


def dogrula(metin: str, ctx: V.ValidationContext = CTX):
    g, hatalar = G.build_graph(parse_workflow(metin))
    assert g is not None, hatalar
    return V.validate_graph(g, ctx)


def test_referans_graf_temiz_gecer():
    r = dogrula(REFERANS)
    assert r.codes() == []


def test_yan_etkili_aracta_idempotency_zorunlu():
    """§4.1(1) — çift yazma koruması konfig seviyesinde."""
    r = dogrula(REFERANS.replace('    idempotency: ["{{ run.id }}", "{{ node.id }}"]\\n', ''))
    assert "E_IDEMPOTENCY" in r.codes()


def test_yan_etkili_aracta_telafi_zorunlu():
    r = dogrula(REFERANS.replace("    compensation: belge.oku_geri_al\n", ""))
    assert "E_TELAFI" in r.codes()


def test_routerda_kacis_kapisi_zorunlu():
    """§4.1(2) — kutuya girmeye zorlanan model uydurur."""
    r = dogrula(REFERANS.replace("    on_low_confidence: {threshold: 0.75, route: belirsiz}\n", ""))
    assert "E_KACIS_KAPISI" in r.codes()


def test_kacis_kapisi_insan_dugumune_gitmeli():
    """belirsiz rotası otomatik bir düğüme giderse kaçış kapısı DEĞİLDİR."""
    r = dogrula(REFERANS.replace("belirsiz: insan_kuyrugu", "belirsiz: cikar"))
    assert "E_KACIS_KAPISI" in r.codes()


def test_kayitli_olmayan_arac_reddedilir():
    r = dogrula(REFERANS.replace("tool: belge.oku\n", "tool: belge.hayalet\n"))
    assert "E_BILINMEYEN_ARAC" in r.codes()


def test_reconcile_yoksa_UYARI_verilir():
    """§5.1: hata değil — her dış sistem geri-okuma sunmaz."""
    r = dogrula(REFERANS)
    assert [u.code for u in r.warnings] == ["W_RECONCILE"]
    assert r.ok


def test_ortulu_dallanma_reddedilir():
    """KK2: dallanmayan düğümün iki çocuğu olamaz."""
    metin = REFERANS + """  - id: ikinci
    type: llm_task
    depends_on: [oku]
"""
    r = dogrula(metin)
    assert "E_ORTULU_DALLANMA" in r.codes()


def test_router_rotasi_disindan_bagimlilik_reddedilir():
    """Router'a depends_on veren ama hiçbir rotanın hedefi olmayan düğüm
    asla çalışmaz — kenar tek yönlü beyan edilmiştir."""
    metin = REFERANS + """  - id: kacak
    type: llm_task
    depends_on: [siniflandir]
"""
    r = dogrula(metin)
    assert "E_ROTA_DISI_BAGIMLILIK" in r.codes()


def test_bilinmeyen_kademe_reddedilir():
    r = dogrula(REFERANS.replace("model_tier: fast", "model_tier: hizli"))
    assert "E_BILINMEYEN_KADEME" in r.codes()


def test_bulk_kademesi_eval_raporsuz_HATA(tmp_path):
    """K9: kalite düşürmek peşin kanıt ister."""
    ctx = V.ValidationContext(
        known_tools=CTX.known_tools, reconcile_tools=CTX.reconcile_tools,
        tiers={"deep", "standard", "fast", "bulk"}, eval_dir=tmp_path,
    )
    r = dogrula(REFERANS.replace("model_tier: fast", "model_tier: bulk"), ctx)
    assert "E_BULK_EVAL" in r.codes()


def test_switch_default_zorunlu():
    metin = REFERANS + """  - id: esik
    type: switch
    depends_on: [cikar]
    on: cikar.fatura.tutar
    cases: {"> 10000": insan_kuyrugu}
"""
    r = dogrula(metin)
    assert "E_SWITCH_DEFAULT" in r.codes()


@pytest.mark.parametrize(
    "ifade,beklenen",
    [("> 10000", (">", 10000.0)), (">= 1.5", (">=", 1.5)), ("== 'fatura'", ("==", "fatura")),
     ("!= \"x\"", ("!=", "x")), ("< 0", ("<", 0.0))],
)
def test_case_ifadeleri_ayristirilir(ifade, beklenen):
    assert V.parse_case(ifade) == beklenen


def test_statik_idempotency_reddedilir():
    """ZK3: her run'da aynı anahtar → ikinci run'ın yan etkisi SESSİZCE atlanır."""
    r = dogrula(REFERANS.replace(
        'idempotency: ["{{ run.id }}", "{{ node.id }}"]', 'idempotency: ["{{ node.id }}"]'))
    assert "E_STATIK_IDEMPOTENCY" in r.codes()


def test_run_id_iceren_idempotency_gecer():
    r = dogrula(REFERANS.replace(
        'idempotency: ["{{ run.id }}", "{{ node.id }}"]', 'idempotency: ["oku", "{{ run.id }}"]'))
    assert "E_STATIK_IDEMPOTENCY" not in r.codes()


def test_dugum_yolu_iceren_idempotency_gecer():
    r = dogrula(REFERANS.replace(
        'idempotency: ["{{ run.id }}", "{{ node.id }}"]',
        'idempotency: ["{{ cikar.fatura.fatura_no }}"]'))
    assert "E_STATIK_IDEMPOTENCY" not in r.codes()


def test_yarim_sablon_ifadesi_reddedilir():
    """`{{ run.id` sessizce düz metne dönüşürse anahtar sabitleşir."""
    r = dogrula(REFERANS.replace(
        'idempotency: ["{{ run.id }}", "{{ node.id }}"]', 'idempotency: ["{{ run.id"]'))
    assert "E_IDEMPOTENCY_IFADESI" in r.codes()


def test_ayristirilamayan_case_reddedilir():
    metin = REFERANS + """  - id: esik
    type: switch
    depends_on: [cikar]
    on: cikar.fatura.tutar
    cases: {"tutar buyukse": insan_kuyrugu, default: null}
"""
    r = dogrula(metin)
    assert "E_SWITCH_IFADESI" in r.codes()
```

- [ ] **Adım 7: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_validate_graph.py -v`
Beklenen: FAIL — `No module named 'kernel.compiler.validate_graph'`

- [ ] **Adım 8: `validate_graph.py` yaz**

```python
"""§4.1 şema zorunlulukları ve §4.2'nin yapısal denetimleri.

Bu modül LLM çağırmaz, ağa çıkmaz, dosya sistemine yalnız eval raporu için
bakar. Maliyeti sıfırdır ve her YAML commit'inde koşar (§7 Katman 0).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from kernel.compiler.errors import (
    E_BILINMEYEN_ARAC,
    E_BILINMEYEN_KADEME,
    E_BULK_EVAL,
    E_IDEMPOTENCY,
    E_KACIS_KAPISI,
    E_KENAR_HEDEFI,
    E_ORTULU_DALLANMA,
    E_ROTA_DISI_BAGIMLILIK,
    E_SWITCH_DEFAULT,
    E_SWITCH_IFADESI,
    E_TELAFI,
    W_EVAL_YOK,
    W_RECONCILE,
    W_ULASILAMAZ,
    CompileError,
    CompileReport,
)
from kernel.compiler.graph import Graph
from kernel.compiler.schema import DALLANAN_TIPLER

# §7 Katman 3: router kenarı başına en az bu kadar altın örnek.
MIN_EVAL_ORNEK = 20
CASE_DESENI = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$")


@dataclass
class ValidationContext:
    known_tools: set[str] = field(default_factory=set)
    reconcile_tools: set[str] = field(default_factory=set)
    tiers: set[str] = field(default_factory=set)
    eval_dir: Path | None = None


def parse_case(key: str) -> tuple[str, object]:
    """'> 10000' → ('>', 10000.0). Ayrıştırılamazsa ValueError."""
    m = CASE_DESENI.match(key)
    if not m:
        raise ValueError(f"ayrıştırılamayan case ifadesi: {key!r}")
    op, ham = m.group(1), m.group(2)
    if len(ham) >= 2 and ham[0] == ham[-1] and ham[0] in "\"'":
        return op, ham[1:-1]
    try:
        return op, float(ham)
    except ValueError as exc:
        raise ValueError(f"case değeri sayı ya da tırnaklı metin olmalı: {key!r}") from exc


def validate_graph(g: Graph, ctx: ValidationContext) -> CompileReport:
    hatalar: list[CompileError] = []
    uyarilar: list[CompileError] = []
    varsayilan_kademe = g.spec.defaults.model_tier

    for nid in g.topo:
        n = g.nodes[nid]

        # KK2 — örtük dallanma
        if n.type not in DALLANAN_TIPLER and len(g.children[nid]) > 1:
            hatalar.append(
                CompileError(
                    code=E_ORTULU_DALLANMA,
                    message=(
                        f"{n.type} düğümünün {len(g.children[nid])} çocuğu var:"
                        f" {', '.join(sorted(g.children[nid]))}."
                        " Paralel dal v1'de yok; router ya da switch kullanın."
                    ),
                    node_id=nid,
                )
            )

        # Kademe geçerliliği (§6.1) ve K9
        kademe = getattr(n, "model_tier", None) or varsayilan_kademe
        if n.type in {"llm_task", "router"}:
            if kademe not in ctx.tiers:
                hatalar.append(
                    CompileError(
                        code=E_BILINMEYEN_KADEME,
                        message=f"profilde tanımsız model_tier: {kademe!r}",
                        node_id=nid,
                    )
                )
            elif kademe == "bulk" and not _eval_raporu_var(ctx.eval_dir, nid):
                hatalar.append(
                    CompileError(
                        code=E_BULK_EVAL,
                        message=(
                            "bulk kademesi eval kanıtı olmadan kilitlidir (K9);"
                            f" beklenen rapor: evals/{nid}.json"
                        ),
                        node_id=nid,
                    )
                )

        if n.type == "tool":
            hatalar.extend(_arac_denetimi(n, nid, ctx))
            uyarilar.extend(_reconcile_uyarisi(n, nid, ctx))
        elif n.type == "router":
            h, u = _router_denetimi(g, n, nid, ctx)
            hatalar.extend(h)
            uyarilar.extend(u)
        elif n.type == "switch":
            hatalar.extend(_switch_denetimi(g, n, nid))

    ulasilir = g.reachable()
    for nid in g.topo:
        if nid not in ulasilir:
            uyarilar.append(
                CompileError(
                    code=W_ULASILAMAZ,
                    message="giriş düğümünden erişilemiyor; ölü konfig",
                    node_id=nid,
                )
            )

    return CompileReport(errors=hatalar, warnings=uyarilar)


def _arac_denetimi(n, nid: str, ctx: ValidationContext) -> list[CompileError]:
    out: list[CompileError] = []
    if not n.idempotency:
        out.append(
            CompileError(
                code=E_IDEMPOTENCY,
                message="yan etkili araç düğümünde idempotency listesi zorunlu (§4.1/1)",
                node_id=nid,
            )
        )
    else:
        out.extend(_idempotency_denetimi(n, nid))
    if not n.compensation:
        out.append(
            CompileError(
                code=E_TELAFI,
                message="yan etkili araç düğümünde compensation zorunlu (§4.1/1)",
                node_id=nid,
            )
        )
    for arac in (n.tool, n.compensation):
        if arac and arac not in ctx.known_tools:
            out.append(
                CompileError(
                    code=E_BILINMEYEN_ARAC,
                    message=f"araç kaydında yok: {arac}",
                    node_id=nid,
                )
            )
    return out


def _idempotency_denetimi(n, nid: str) -> list[CompileError]:
    """ZK3: anahtar run'dan run'a DEĞİŞMEK zorundadır.

    Sabit bir anahtar (`["{{ node.id }}"]` gibi) her run'da aynı `tool_calls`
    satırını bulur, ikinci run'ın dış çağrısını `completed` sanıp HİÇ YAPMAZ ve
    birincinin yanıtını döndürür. Müşterinin ikinci faturası ERP'ye yazılmaz ve
    sistem bunu başarı olarak raporlar.
    """
    out: list[CompileError] = []
    degisken = False
    for parca in n.idempotency:
        try:
            ref = expr.parse_part(parca)
        except ValueError as exc:
            out.append(CompileError(code=E_IDEMPOTENCY_IFADESI, message=str(exc), node_id=nid))
            continue
        if ref is not None and expr.run_degisken_mi(ref):
            degisken = True
    if not out and not degisken:
        out.append(
            CompileError(
                code=E_STATIK_IDEMPOTENCY,
                message=(
                    "idempotency anahtarı her run'da aynı değeri üretiyor;"
                    " en az bir run-değişken referans zorunlu"
                    ' ("{{ run.id }}" ya da bir düğüm çıktısı yolu) — ZK3'
                ),
                node_id=nid,
            )
        )
    return out


def _reconcile_uyarisi(n, nid: str, ctx: ValidationContext) -> list[CompileError]:
    if n.tool in ctx.known_tools and n.tool not in ctx.reconcile_tools:
        return [
            CompileError(
                code=W_RECONCILE,
                message=(
                    f"{n.tool} reconcile beyan etmiyor: kirası dolan bir rezervasyon"
                    " otomatik olarak insan kuyruğuna düşecek (§5.1)"
                ),
                node_id=nid,
            )
        ]
    return []


def _router_denetimi(g: Graph, n, nid: str, ctx: ValidationContext):
    hatalar: list[CompileError] = []
    uyarilar: list[CompileError] = []
    hedefler = {h for h in n.routes.values() if h is not None}
    for h in hedefler:
        if h not in g.nodes:
            hatalar.append(
                CompileError(code=E_KENAR_HEDEFI, message=f"rota var olmayan düğümü gösteriyor: {h}", node_id=nid)
            )
    hatalar.extend(_cocuk_kume_denetimi(g, nid, hedefler, "routes"))

    if n.on_low_confidence is None:
        hatalar.append(
            CompileError(
                code=E_KACIS_KAPISI,
                message="router'da on_low_confidence zorunlu (§4.1/2)",
                node_id=nid,
            )
        )
    else:
        rota = n.on_low_confidence.route
        hedef = n.routes.get(rota)
        if rota not in n.routes:
            hatalar.append(
                CompileError(code=E_KACIS_KAPISI, message=f"on_low_confidence.route rotalarda yok: {rota}", node_id=nid)
            )
        elif hedef is None or g.nodes[hedef].type != "human_approval":
            hatalar.append(
                CompileError(
                    code=E_KACIS_KAPISI,
                    message=(
                        f"kaçış rotası {rota!r} bir human_approval düğümüne gitmeli;"
                        " otomatik bir düğüme giden rota kaçış kapısı değildir"
                    ),
                    node_id=nid,
                )
            )

    for rota in n.routes:
        if not _eval_ornekleri_yeterli(ctx.eval_dir, nid, rota):
            uyarilar.append(
                CompileError(
                    code=W_EVAL_YOK,
                    message=(
                        f"{rota!r} kenarı için altın veri seti yok ya da"
                        f" {MIN_EVAL_ORNEK} örnekten az (§7 Katman 3)"
                    ),
                    node_id=nid,
                )
            )
    return hatalar, uyarilar


def _switch_denetimi(g: Graph, n, nid: str) -> list[CompileError]:
    hatalar: list[CompileError] = []
    if "default" not in n.cases:
        hatalar.append(
            CompileError(
                code=E_SWITCH_DEFAULT,
                message="switch'te default dalı zorunlu; eşleşmeyen değer run'ı sessizce durdurur",
                node_id=nid,
            )
        )
    for anahtar in n.cases:
        if anahtar == "default":
            continue
        try:
            parse_case(anahtar)
        except ValueError as exc:
            hatalar.append(CompileError(code=E_SWITCH_IFADESI, message=str(exc), node_id=nid))
    hedefler = {h for h in n.cases.values() if h is not None}
    for h in hedefler:
        if h not in g.nodes:
            hatalar.append(
                CompileError(code=E_KENAR_HEDEFI, message=f"case var olmayan düğümü gösteriyor: {h}", node_id=nid)
            )
    hatalar.extend(_cocuk_kume_denetimi(g, nid, hedefler, "cases"))
    return hatalar


def _cocuk_kume_denetimi(g: Graph, nid: str, hedefler: set[str], alan: str) -> list[CompileError]:
    """Dallanan düğümün çocuk kümesi, beyan ettiği hedeflerle BİREBİR aynı olmalı.

    Fazlası: bir düğüm router'a depends_on veriyor ama hiçbir rotanın hedefi
    değil — asla çalışmaz. Eksiği: rota hedefi var ama depends_on eksik —
    kenar tek yönlü beyan edilmiş, çalışma zamanında bağlam izolasyonu bozulur.
    """
    cocuklar = set(g.children[nid]) & set(g.nodes)
    fazla = cocuklar - hedefler
    eksik = {h for h in hedefler if h in g.nodes} - cocuklar
    out: list[CompileError] = []
    if fazla:
        out.append(
            CompileError(
                code=E_ROTA_DISI_BAGIMLILIK,
                message=(
                    f"{', '.join(sorted(fazla))} bu düğüme depends_on veriyor ama"
                    f" {alan} içinde hedef değil; asla çalışmaz"
                ),
                node_id=nid,
            )
        )
    if eksik:
        out.append(
            CompileError(
                code=E_ROTA_DISI_BAGIMLILIK,
                message=(
                    f"{alan} {', '.join(sorted(eksik))} düğümlerini gösteriyor ama"
                    " onların depends_on'unda bu düğüm yok"
                ),
                node_id=nid,
            )
        )
    return out


def _eval_raporu_var(eval_dir: Path | None, node_id: str) -> bool:
    if eval_dir is None:
        return False
    p = eval_dir / f"{node_id}.json"
    if not p.exists():
        return False
    try:
        rapor = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(rapor.get("passed")) and "kalibrasyon" in rapor


def _eval_ornekleri_yeterli(eval_dir: Path | None, node_id: str, rota: str) -> bool:
    if eval_dir is None:
        return False
    p = eval_dir / f"{node_id}.json"
    if not p.exists():
        return False
    try:
        rapor = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return int(rapor.get("ornekler", {}).get(rota, 0)) >= MIN_EVAL_ORNEK
```

- [ ] **Adım 9: Testleri koş**

Koş: `.venv/bin/python -m pytest tests/compiler -v`
Beklenen: tüm testler PASS (14 + 7 + 15 = 36)

- [ ] **Adım 10: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: 105 passed, çıktı temiz.

```bash
git add packages/kernel/compiler packages/kernel/tools/registry.py tests/compiler
git commit -m "feat(compiler): graf inşası ve yapısal doğrulama (§4.1, §4.2)"
```

---

### Görev 4: Tip uyumu ve bağlam izolasyonu doğrulaması

§4.1(3) ve Kural 3'ün derleme zamanı karşılığı. Bir düğüm `inputs`'ta beyan etmediği alana erişemez — ve beyan ettiği alanın **var olduğu** derleme anında kanıtlanır.

**Dosyalar:**
- Oluştur: `packages/kernel/types.py` (çekirdeğin sunduğu beyan edilebilir tipler)
- Oluştur: `packages/kernel/compiler/validate_types.py`
- Test: `tests/compiler/test_validate_types.py`, `tests/compiler/fixtures/types_ornek.py`

**Arayüzler:**
- Tüketir: `Graph`, `CompileError`, hata kodları
- Üretir: `ArtifactRef`, `load_types(path) -> dict[str, type]`, `resolve_type(ad, tipler) -> type | None`, `validate_types(graph, tipler) -> CompileReport`, `alan_yolu_cozumle(...)`

- [ ] **Adım 1: `packages/kernel/types.py` yaz**

```python
"""Çekirdeğin sunduğu, müşteri YAML'ında beyan edilebilir tipler.

`ArtifactRef` §4.1(4)'ün taşıyıcısıdır: büyük içerik adım çıktısına gömülmez,
grafta yalnız referansı dolaşır. Postgres satırları küçük ve olay kaydı
okunabilir kalır; ajan belgeyi ancak açıkça istediğinde yükler.
"""
from __future__ import annotations

from pydantic import BaseModel


class ArtifactRef(BaseModel):
    uri: str
    media_type: str
    bytes: int
    sha256: str


# YAML'da tip adı olarak yazılabilen çekirdek modelleri.
CEKIRDEK_TIPLER: dict[str, type[BaseModel]] = {"ArtifactRef": ArtifactRef}
```

- [ ] **Adım 2: Test tipleri fixture'ını yaz**

`tests/compiler/fixtures/types_ornek.py`:

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from kernel.types import ArtifactRef


class BelgeListesi(BaseModel):
    belgeler: list[ArtifactRef]


class FaturaModeli(BaseModel):
    fatura_no: str
    tutar: Decimal
    tedarikci: str


class GomuluIcerik(BaseModel):
    """§4.1(4) ihlali: ham içerik adım çıktısına gömülüyor."""

    icerik: bytes
```

- [ ] **Adım 3: Başarısız testi yaz**

`tests/compiler/test_validate_types.py`:

```python
from pathlib import Path

from kernel.compiler import graph as G
from kernel.compiler import validate_types as T
from kernel.compiler.loader import parse_workflow

FIXTURES = Path(__file__).parent / "fixtures"

AKIS = """
apiVersion: v1
name: tipli
trigger: {type: http}
limits: {max_steps: 10, max_usd_per_run: 1.00, max_wallclock: 1h}
graph:
  - id: oku
    type: tool
    tool: belge.oku
    idempotency: ["{{ run.id }}", "{{ node.id }}"]
    compensation: belge.oku_geri_al
    outputs: {belgeler: BelgeListesi}
  - id: cikar
    type: llm_task
    depends_on: [oku]
    inputs: [oku.belgeler]
    outputs: {fatura: FaturaModeli}
  - id: yaz
    type: tool
    depends_on: [cikar]
    tool: erp.post_invoice
    inputs: [cikar.fatura]
    idempotency: ["{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]
    compensation: erp.void_invoice
"""


def dogrula(metin: str):
    g, hatalar = G.build_graph(parse_workflow(metin))
    assert g is not None, hatalar
    tipler = T.load_types(FIXTURES / "types_ornek.py")
    return T.validate_types(g, tipler)


def test_tipli_akis_temiz_gecer():
    assert dogrula(AKIS).codes() == []


def test_beyan_edilmemis_alan_reddedilir():
    """Kural 3: bir düğüm inputs'ta beyan etmediği alanı GÖREMEZ."""
    r = dogrula(AKIS.replace("inputs: [cikar.fatura]", "inputs: [oku.gizli]"))
    assert "E_TIP_UYUMSUZ" in r.codes()


def test_ata_olmayan_dugume_erisim_reddedilir():
    metin = AKIS.replace("    inputs: [oku.belgeler]\n", "    inputs: [yaz.sonuc]\n")
    r = dogrula(metin)
    assert "E_BEYAN_EDILMEMIS_ALAN" in r.codes()


def test_bilinmeyen_tip_adi_reddedilir():
    r = dogrula(AKIS.replace("outputs: {fatura: FaturaModeli}", "outputs: {fatura: Hayalet}"))
    assert "E_BILINMEYEN_TIP" in r.codes()


def test_derin_alan_yolu_dogrulanir():
    """{{ cikar.fatura.fatura_no }} yolu gerçekten var mı?"""
    r = dogrula(AKIS.replace("cikar.fatura.fatura_no", "cikar.fatura.yok_boyle_alan"))
    assert "E_TIP_UYUMSUZ" in r.codes()


def test_bytes_alani_artifact_hatasi_verir():
    """§4.1(4): büyük içerik ArtifactRef olarak taşınır, gömülmez."""
    r = dogrula(AKIS.replace("outputs: {fatura: FaturaModeli}", "outputs: {fatura: GomuluIcerik}"))
    assert "E_ARTIFACT" in r.codes()


def test_liste_alanina_yol_erisimi_reddedilir():
    r = dogrula(AKIS.replace("inputs: [oku.belgeler]", "inputs: [oku.belgeler.belgeler.uri]"))
    assert "E_TIP_UYUMSUZ" in r.codes()


def test_switch_on_yolu_da_dogrulanir():
    metin = AKIS + """  - id: esik
    type: switch
    depends_on: [yaz]
    on: cikar.fatura.hayalet_alan
    cases: {"> 1": null, default: null}
"""
    r = dogrula(metin.replace("  - id: yaz", "  - id: yaz").replace(
        "    compensation: erp.void_invoice\n", "    compensation: erp.void_invoice\n"))
    assert "E_TIP_UYUMSUZ" in r.codes()
```

- [ ] **Adım 4: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_validate_types.py -v`
Beklenen: FAIL — `No module named 'kernel.compiler.validate_types'`

- [ ] **Adım 5: `validate_types.py` yaz**

```python
"""Tip uyumu ve bağlam izolasyonu (§4.1/3, §4.1/4, Kural 3).

Denetlenen üç şey:
  1. Her `outputs` tip adı gerçekten var mı ve Pydantic modeli mi.
  2. Her erişim yolu (`inputs`, `on`, `context_fields`, `idempotency`) bir
     ATA düğümün beyan edilmiş çıktısını mı gösteriyor, ve o alan modelde
     gerçekten var mı.
  3. Beyan edilen bir modelde ham `bytes` alanı var mı — varsa büyük içerik
     adım çıktısına gömülüyor demektir (§4.1/4).

`customers/<x>/types.py` dosya YOLUNDAN yüklenir, import yolundan değil:
müşteri dizini bir Python paketi değildir ve olmamalıdır (§9 sert kuralı).
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from pydantic import BaseModel

from kernel.compiler.errors import (
    E_ARTIFACT,
    E_BEYAN_EDILMEMIS_ALAN,
    E_BILINMEYEN_TIP,
    E_TIP_UYUMSUZ,
    CompileError,
    CompileReport,
)
from kernel.compiler.graph import Graph
from kernel.types import CEKIRDEK_TIPLER

LISTE_DESENI = re.compile(r"^(?:list|List)\[(.+)\]$")
# Çalışma zamanında çözülen, düğüm yoluna karşılık gelmeyen anahtar parçaları.
YERLESIK_ANAHTARLAR = frozenset({"run_id", "node_id", "tenant_id"})


def load_types(path: Path) -> dict[str, type[BaseModel]]:
    """types.py dosyasını yükler ve BaseModel alt sınıflarını döner."""
    path = Path(path)
    spec = importlib.util.spec_from_file_location(f"_customer_types_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"types.py yüklenemedi: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tipler = dict(CEKIRDEK_TIPLER)
    for ad in dir(mod):
        deger = getattr(mod, ad)
        if isinstance(deger, type) and issubclass(deger, BaseModel) and deger is not BaseModel:
            tipler[ad] = deger
    return tipler


def resolve_type(ad: str, tipler: dict[str, type[BaseModel]]) -> type[BaseModel] | None:
    """'FaturaModeli' ve 'List[ArtifactRef]' biçimlerini çözer."""
    m = LISTE_DESENI.match(ad.strip())
    if m:
        return resolve_type(m.group(1), tipler)
    return tipler.get(ad.strip())


def _liste_mi(ad: str) -> bool:
    return LISTE_DESENI.match(ad.strip()) is not None


def validate_types(g: Graph, tipler: dict[str, type[BaseModel]]) -> CompileReport:
    hatalar: list[CompileError] = []

    # 1) outputs tip adları
    for nid in g.topo:
        for alan, tip_adi in getattr(g.nodes[nid], "outputs", {}).items():
            model = resolve_type(tip_adi, tipler)
            if model is None:
                hatalar.append(
                    CompileError(
                        code=E_BILINMEYEN_TIP,
                        message=f"{alan}: types.py'de tanımsız tip {tip_adi!r}",
                        node_id=nid,
                    )
                )
                continue
            gomulu = _gomulu_icerik_alanlari(model)
            if gomulu:
                hatalar.append(
                    CompileError(
                        code=E_ARTIFACT,
                        message=(
                            f"{tip_adi}.{gomulu[0]} ham içerik taşıyor;"
                            " büyük içerik ArtifactRef olarak taşınır (§4.1/4)"
                        ),
                        node_id=nid,
                    )
                )

    # 2) erişim yolları
    for nid in g.topo:
        n = g.nodes[nid]
        for yol in _erisim_yollari(n):
            hatalar.extend(_yol_denetimi(g, tipler, nid, yol))

    return CompileReport(errors=hatalar)


def _erisim_yollari(n) -> list[str]:
    yollar = list(n.inputs)
    yollar.extend(getattr(n, "context_fields", []))
    if n.type == "switch":
        yollar.append(n.on)
    if n.type == "tool":
        # ZK3: yalnız düğüm YOLU olan referanslar tip denetimine girer;
        # run.id / node.id / tenant.id yerleşiktir, düz metin sabitler ise
        # hiçbir düğüme bakmaz.
        for parca in n.idempotency:
            try:
                ref = expr.parse_part(parca)
            except ValueError:
                continue  # E_IDEMPOTENCY_IFADESI Görev 3'te raporlanıyor
            if ref is not None and ref not in expr.SABIT_REFERANSLAR and ref != expr.RUN_REFERANSI:
                yollar.append(ref)
    return yollar


def _yol_denetimi(
    g: Graph, tipler: dict[str, type[BaseModel]], nid: str, yol: str
) -> list[CompileError]:
    parcalar = yol.split(".")
    if len(parcalar) < 2:
        return [
            CompileError(
                code=E_TIP_UYUMSUZ,
                message=f"erişim yolu <düğüm>.<alan> biçiminde olmalı: {yol!r}",
                node_id=nid,
            )
        ]
    kaynak, alan, kalan = parcalar[0], parcalar[1], parcalar[2:]

    if kaynak not in g.nodes:
        return [
            CompileError(
                code=E_BEYAN_EDILMEMIS_ALAN,
                message=f"var olmayan düğüme erişim: {kaynak}",
                node_id=nid,
            )
        ]
    if kaynak not in g.ancestors(nid):
        return [
            CompileError(
                code=E_BEYAN_EDILMEMIS_ALAN,
                message=(
                    f"{kaynak} bu düğümün atası değil; yalnız ata çıktılarına"
                    " erişilebilir (Kural 3)"
                ),
                node_id=nid,
            )
        ]

    ciktilar = getattr(g.nodes[kaynak], "outputs", {})
    if alan not in ciktilar:
        return [
            CompileError(
                code=E_TIP_UYUMSUZ,
                message=f"{kaynak} düğümü {alan!r} çıktısını beyan etmiyor",
                node_id=nid,
            )
        ]
    if not kalan:
        return []

    if _liste_mi(ciktilar[alan]):
        return [
            CompileError(
                code=E_TIP_UYUMSUZ,
                message=f"liste alanına derin yol erişimi yok: {yol!r}",
                node_id=nid,
            )
        ]
    model = resolve_type(ciktilar[alan], tipler)
    if model is None:
        return []  # E_BILINMEYEN_TIP zaten yukarıda raporlandı
    return _derin_yol_denetimi(model, kalan, nid, yol)


def _derin_yol_denetimi(model, kalan: list[str], nid: str, yol: str) -> list[CompileError]:
    su_an = model
    for parca in kalan:
        alanlar = getattr(su_an, "model_fields", None)
        if alanlar is None or parca not in alanlar:
            return [
                CompileError(
                    code=E_TIP_UYUMSUZ,
                    message=f"{yol!r}: {getattr(su_an, '__name__', su_an)} içinde {parca!r} alanı yok",
                    node_id=nid,
                )
            ]
        su_an = alanlar[parca].annotation
    return []


def _gomulu_icerik_alanlari(model: type[BaseModel]) -> list[str]:
    """Ham içerik taşıyan alanlar. `bytes` bu mimaride ArtifactRef olmalıdır."""
    return [ad for ad, f in model.model_fields.items() if f.annotation is bytes]
```

- [ ] **Adım 6: Testleri koş**

Koş: `.venv/bin/python -m pytest tests/compiler/test_validate_types.py -v`
Beklenen: 8 PASS

- [ ] **Adım 7: `customers/**` kod sızıntısı CI kuralını ekle**

`tests/test_customers_kod_yok.py`:

```python
"""§9 sert kuralı: customers/ altında types.py dışında .py olamaz.

Bu kural gevşerse ürün altı ay içinde N ayrı danışmanlık projesine dönüşür
(§13 risk tablosu). Denetimi CI'a bağlamak tek çare.
"""
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]


def test_customers_altinda_yalniz_types_py_var():
    kok = KOK / "customers"
    if not kok.exists():
        return
    kacaklar = [
        str(p.relative_to(KOK))
        for p in kok.rglob("*.py")
        if p.name != "types.py"
    ]
    assert kacaklar == [], (
        "customers/ altına mantık sızmış; bu kod ya connectors/'a ya kernel/'e"
        f" ait (§9 sert kuralı): {kacaklar}"
    )
```

- [ ] **Adım 8: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: 114 passed, çıktı temiz.

```bash
git add packages/kernel/types.py packages/kernel/compiler/validate_types.py tests/
git commit -m "feat(compiler): tip uyumu ve bağlam izolasyonu doğrulaması (Kural 3)"
```

---

### Görev 5: Tavan maliyet (K8), derleme cephesi ve rapor CLI'ı

M2'nin kabul kriterinin üçüncü bacağı: "tavan maliyet raporlanır". Bu görevden sonra bir YAML tek komutla derlenir ve çalışma başına maliyet üst sınırı basılır — sabit fiyatlı B2B sözleşme yazabilmenin ön koşulu.

**Dosyalar:**
- Oluştur: `packages/kernel/compiler/cost.py`
- Değiştir: `packages/kernel/compiler/__init__.py` (cephe)
- Oluştur: `packages/kernel/compiler/__main__.py` (CLI)
- Test: `tests/compiler/test_cost.py`, `tests/compiler/test_compile.py`

**Arayüzler:**
- Tüketir: `Graph`, `Profile`, `accounting.price_for`, `resolve_type`
- Üretir: `NodeCost`, `CostReport`, `ceiling_cost(graph, profile, tipler) -> CostReport`, `CompiledWorkflow`, `compile_workflow(...) -> CompiledWorkflow`, `CompiledWorkflow.to_compiled_json() -> dict`

- [ ] **Adım 1: Başarısız maliyet testini yaz**

`tests/compiler/test_cost.py`:

```python
from decimal import Decimal
from pathlib import Path

from kernel.compiler import cost as C
from kernel.compiler import graph as G
from kernel.compiler import profile as P
from kernel.compiler import validate_types as T
from kernel.compiler.loader import parse_workflow

FIXTURES = Path(__file__).parent / "fixtures"

TEK_DUGUM = """
apiVersion: v1
name: tek
trigger: {type: http}
limits: {max_steps: 5, max_usd_per_run: 100.00, max_wallclock: 1h}
defaults: {model_tier: standard, retry: {attempts: 1}, max_tokens: 1000}
graph:
  - {id: a, type: llm_task, outputs: {fatura: FaturaModeli}}
"""

PROFIL = P.Profile(
    tenant_id="t",
    tiers={
        "deep": P.TierSpec(model="claude-opus-5", effort="xhigh"),
        "standard": P.TierSpec(model="claude-opus-5", effort="high"),
        "fast": P.TierSpec(model="claude-opus-5", effort="low"),
    },
)


def hesapla(metin: str, profil: P.Profile = PROFIL) -> C.CostReport:
    g, hatalar = G.build_graph(parse_workflow(metin))
    assert g is not None, hatalar
    tipler = T.load_types(FIXTURES / "types_ornek.py")
    return C.ceiling_cost(g, profil, tipler)


def test_cikti_tokenlari_max_tokensten_gelir():
    """En kötü hâl: model tavanı doldurur. Tahmin GÜVENLİ tarafta olmalı."""
    r = hesapla(TEK_DUGUM)
    (dugum,) = r.nodes
    assert dugum.output_tokens == 1000
    # Opus 5 çıktı $25/1M → 1000 token = $0.025; girdi buna eklenir.
    assert dugum.usd > Decimal("0.025")


def test_yeniden_deneme_maliyeti_carpar():
    """Her deneme tam maliyeti yakar; tavan bunu saymalı."""
    tek = hesapla(TEK_DUGUM).total_usd
    uc = hesapla(TEK_DUGUM.replace("attempts: 1", "attempts: 3")).total_usd
    assert uc == tek * 3


def test_artifact_girdisi_agir_sayilir():
    """Bir belge referansı, üzerinden okunacak İÇERİĞİ temsil eder."""
    metin = """
apiVersion: v1
name: iki
trigger: {type: http}
limits: {max_steps: 5, max_usd_per_run: 100.00, max_wallclock: 1h}
defaults: {model_tier: standard, retry: {attempts: 1}, max_tokens: 1000}
graph:
  - {id: oku, type: tool, tool: belge.oku, idempotency: ["{{ run.id }}", "{{ node.id }}"],
     compensation: belge.oku_geri_al, outputs: {belgeler: BelgeListesi}}
  - {id: a, type: llm_task, depends_on: [oku], inputs: [oku.belgeler],
     outputs: {fatura: FaturaModeli}}
"""
    r = hesapla(metin)
    a = next(n for n in r.nodes if n.node_id == "a")
    assert a.input_tokens >= C.ARTIFACT_TOKEN
    assert r.worst_path == ["oku", "a"]


def test_en_pahali_yol_secilir():
    """İki dal: tavan UCUZ dalın değil PAHALI dalın maliyetidir."""
    metin = """
apiVersion: v1
name: dalli
trigger: {type: http}
limits: {max_steps: 10, max_usd_per_run: 100.00, max_wallclock: 1h}
defaults: {model_tier: fast, retry: {attempts: 1}, max_tokens: 500}
graph:
  - {id: r, type: router, routes: {ucuz: u, pahali: p, belirsiz: h},
     on_low_confidence: {threshold: 0.7, route: belirsiz}}
  - {id: u, type: llm_task, depends_on: [r], max_tokens: 500}
  - {id: p, type: llm_task, depends_on: [r], max_tokens: 40000}
  - {id: h, type: human_approval, depends_on: [r], assignee_role: x}
"""
    r = hesapla(metin)
    assert r.worst_path == ["r", "p"]


def test_arac_ve_onay_dugumleri_llm_maliyeti_uretmez():
    metin = """
apiVersion: v1
name: aracli
trigger: {type: http}
limits: {max_steps: 5, max_usd_per_run: 100.00, max_wallclock: 1h}
defaults: {retry: {attempts: 1}}
graph:
  - {id: oku, type: tool, tool: belge.oku, idempotency: ["{{ run.id }}", "{{ node.id }}"],
     compensation: belge.oku_geri_al}
"""
    assert hesapla(metin).total_usd == Decimal("0")


def test_en_kotu_adim_sayisi_hesaplanir():
    r = hesapla(TEK_DUGUM.replace("attempts: 1", "attempts: 3"))
    assert r.worst_steps == 3
```

- [ ] **Adım 2: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/compiler/test_cost.py -v`
Beklenen: FAIL — `No module named 'kernel.compiler.cost'`

- [ ] **Adım 3: `cost.py` yaz**

```python
"""Tavan Maliyet Prensibi (K8).

Derleyici graftaki en pahalı yolu yürüyerek çalışma başına maliyet ÜST
SINIRINI hesaplar. Tahminler kaba ve GÜVENLİ TARAFTA yapılır: tavanın kesin
olması gerekmez, AŞILMAMASI gerekir. Bu yüzden burada:
  · çıktı her zaman max_tokens kadar sayılır (model tavanı doldurur varsayımı),
  · önbellek indirimi HİÇ sayılmaz (sıfır isabet varsayımı),
  · her düğüm retry.attempts kere tam maliyetle sayılır,
  · bir belge referansı, üzerinden okunacak içeriğin token ağırlığıyla sayılır.

Buradaki token sabitleri spec §13 uyarınca M4'te gerçek ölçümle kalibre
edilecek yer tutuculardır; kalibrasyona kadar hepsi yukarı yuvarlıdır.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from kernel.compiler.graph import Graph
from kernel.compiler.profile import Profile
from kernel.compiler.validate_types import resolve_type
from kernel.gateway import accounting

# Bir ArtifactRef, ajanın okuyacağı belgeyi temsil eder — referansın kendisi
# birkaç yüz bayt, içeriği bir A4 taranmış fatura kadar.
ARTIFACT_TOKEN = 8000
# Serbest metin alanı.
METIN_TOKEN = 2000
# Sayısal/mantıksal/tarih alanı.
SKALER_TOKEN = 8
# Sistem promptu (Katman 1 + 2) için sabit pay; §6.2 minimum önbelleklenebilir
# uzunluğun üstünde tutulur.
SISTEM_TOKEN = 1000
# Liste alanları için varsayılan eleman sayısı çarpanı.
LISTE_CARPANI = 10


class NodeCost(BaseModel):
    node_id: str
    tier: str
    model: str
    input_tokens: int
    output_tokens: int
    attempts: int
    usd: Decimal


class CostReport(BaseModel):
    nodes: list[NodeCost]
    worst_path: list[str]
    worst_steps: int
    total_usd: Decimal

    def render(self) -> str:
        satirlar = [
            f"{'düğüm':<20} {'kademe':<10} {'girdi':>8} {'çıktı':>8} {'deneme':>7} {'USD':>10}",
            "-" * 68,
        ]
        for n in self.nodes:
            satirlar.append(
                f"{n.node_id:<20} {n.tier:<10} {n.input_tokens:>8} "
                f"{n.output_tokens:>8} {n.attempts:>7} {n.usd:>10}"
            )
        satirlar.append("-" * 68)
        satirlar.append(f"en pahalı yol : {' → '.join(self.worst_path)}")
        satirlar.append(f"en kötü adım  : {self.worst_steps}")
        satirlar.append(f"TAVAN MALİYET : ${self.total_usd} / çalışma")
        return "\n".join(satirlar)


def ceiling_cost(g: Graph, profil: Profile, tipler: dict) -> CostReport:
    denemeler = g.spec.defaults.retry.attempts
    varsayilan_tier = g.spec.defaults.model_tier
    varsayilan_max = g.spec.defaults.max_tokens

    maliyetler: dict[str, NodeCost] = {}
    for nid in g.topo:
        n = g.nodes[nid]
        tier = getattr(n, "model_tier", None) or varsayilan_tier
        if n.type not in {"llm_task", "router"} or tier not in profil.tiers:
            maliyetler[nid] = NodeCost(
                node_id=nid, tier=tier if n.type in {"llm_task", "router"} else "-",
                model="-", input_tokens=0, output_tokens=0, attempts=denemeler,
                usd=Decimal("0"),
            )
            continue
        model = profil.tiers[tier].model
        girdi = SISTEM_TOKEN + _girdi_tokenlari(g, n, tipler)
        cikti = getattr(n, "max_tokens", None) or varsayilan_max
        maliyetler[nid] = NodeCost(
            node_id=nid, tier=tier, model=model,
            input_tokens=girdi, output_tokens=cikti, attempts=denemeler,
            usd=_fiyatla(model, girdi, cikti) * denemeler,
        )

    yol, toplam = _en_pahali_yol(g, maliyetler)
    return CostReport(
        nodes=[maliyetler[n] for n in g.topo],
        worst_path=yol,
        worst_steps=len(yol) * denemeler,
        total_usd=toplam,
    )


def _fiyatla(model: str, girdi: int, cikti: int) -> Decimal:
    p = accounting.price_for(model)
    if p is None:
        raise KeyError(
            f"tavan maliyet hesaplanamıyor: {model!r} için fiyat tanımlı değil"
        )
    return ((p.inp * girdi + p.out * cikti) / accounting.MILLION).quantize(
        Decimal("0.000001")
    )


def _girdi_tokenlari(g: Graph, n, tipler: dict) -> int:
    toplam = 0
    for yol in n.inputs:
        parcalar = yol.split(".")
        if len(parcalar) < 2 or parcalar[0] not in g.nodes:
            continue
        ciktilar = getattr(g.nodes[parcalar[0]], "outputs", {})
        tip_adi = ciktilar.get(parcalar[1])
        if tip_adi is None:
            toplam += METIN_TOKEN
            continue
        toplam += _tip_tokenlari(tip_adi, tipler)
    return toplam


def _tip_tokenlari(tip_adi: str, tipler: dict, derinlik: int = 0) -> int:
    if derinlik > 3:
        return METIN_TOKEN
    model = resolve_type(tip_adi, tipler)
    if model is None:
        return METIN_TOKEN
    if model.__name__ == "ArtifactRef":
        return ARTIFACT_TOKEN
    toplam = 0
    for f in model.model_fields.values():
        ann = f.annotation
        ad = getattr(ann, "__name__", str(ann))
        if ad == "ArtifactRef":
            toplam += ARTIFACT_TOKEN
        elif ad == "str":
            toplam += METIN_TOKEN
        elif hasattr(ann, "model_fields"):
            toplam += _tip_tokenlari(ad, tipler, derinlik + 1)
        elif "ArtifactRef" in str(ann):
            toplam += ARTIFACT_TOKEN * LISTE_CARPANI
        elif "str" in str(ann) and "list" in str(ann):
            toplam += METIN_TOKEN * LISTE_CARPANI
        else:
            toplam += SKALER_TOKEN
    return toplam


def _en_pahali_yol(g: Graph, maliyetler: dict[str, NodeCost]) -> tuple[list[str], Decimal]:
    """Topolojik sırada dinamik programlama. Graf asiklik olduğu için güvenli."""
    en_iyi: dict[str, tuple[Decimal, list[str]]] = {}
    for nid in reversed(g.topo):
        kendi = maliyetler[nid].usd
        cocuklar = g.children[nid]
        if not cocuklar:
            en_iyi[nid] = (kendi, [nid])
            continue
        alt, yol = max((en_iyi[c] for c in cocuklar), key=lambda t: t[0])
        en_iyi[nid] = (kendi + alt, [nid, *yol])
    toplam, yol = en_iyi[g.entry]
    return yol, toplam
```

- [ ] **Adım 4: Maliyet testlerini koş**

Koş: `.venv/bin/python -m pytest tests/compiler/test_cost.py -v`
Beklenen: 6 PASS

- [ ] **Adım 5: Cephe testini yaz**

`tests/compiler/test_compile.py`:

```python
import json
from pathlib import Path

import pytest

import kernel.compiler as K
from kernel.compiler.errors import CompileFailed

FIXTURES = Path(__file__).parent / "fixtures"


def test_referans_akis_derlenir(acme_paths):
    d = K.compile_workflow(
        acme_paths.workflow, acme_paths.profile, acme_paths.types,
        known_tools={"belge.oku", "belge.oku_geri_al", "erp.post_invoice", "erp.void_invoice"},
        reconcile_tools={"erp.post_invoice"},
    )
    assert d.report.ok
    assert d.cost.total_usd > 0
    assert len(d.version_hash) == 64


def test_tavan_butceyi_asarsa_derleme_HATA(tmp_path, acme_paths):
    """§4.2: en kötü yolun maliyeti bütçeyi aşıyorsa YAML yüklenmez."""
    metin = acme_paths.workflow.read_text(encoding="utf-8").replace(
        "max_usd_per_run: 3.00", "max_usd_per_run: 0.001"
    )
    p = tmp_path / "akis.yaml"
    p.write_text(metin, encoding="utf-8")
    with pytest.raises(CompileFailed) as exc:
        K.compile_workflow(p, acme_paths.profile, acme_paths.types,
                           known_tools={"belge.oku", "belge.oku_geri_al",
                                        "erp.post_invoice", "erp.void_invoice"})
    assert "E_BUTCE_TAVANI" in exc.value.report.codes()


def test_en_kotu_yol_max_stepsi_asarsa_HATA(tmp_path, acme_paths):
    metin = acme_paths.workflow.read_text(encoding="utf-8").replace(
        "max_steps: 25", "max_steps: 2"
    )
    p = tmp_path / "akis.yaml"
    p.write_text(metin, encoding="utf-8")
    with pytest.raises(CompileFailed) as exc:
        K.compile_workflow(p, acme_paths.profile, acme_paths.types,
                           known_tools={"belge.oku", "belge.oku_geri_al",
                                        "erp.post_invoice", "erp.void_invoice"})
    assert "E_ADIM_TAVANI" in exc.value.report.codes()


def test_derlenmis_artefakt_SIR_ICERMEZ(acme_paths, monkeypatch):
    """K14: çözülmüş sır değeri veritabanına gitmez."""
    monkeypatch.setenv("ACME_SAP_USER", "svc_acme")
    monkeypatch.setenv("ACME_SAP_PASSWORD", "cok-gizli-parola")
    d = K.compile_workflow(
        acme_paths.workflow, acme_paths.profile, acme_paths.types,
        known_tools={"belge.oku", "belge.oku_geri_al", "erp.post_invoice", "erp.void_invoice"},
    )
    metin = json.dumps(d.to_compiled_json())
    assert "cok-gizli-parola" not in metin
    assert "ACME_SAP_PASSWORD" not in metin
```

`tests/conftest.py` sonuna ekle:

```python
from dataclasses import dataclass


@dataclass
class AcmePaths:
    workflow: Path
    profile: Path
    types: Path
    evals: Path


@pytest.fixture
def acme_paths() -> AcmePaths:
    kok = Path(__file__).resolve().parents[1] / "customers" / "acme"
    return AcmePaths(
        workflow=kok / "workflows" / "belge_girisi.yaml",
        profile=kok / "profile.yaml",
        types=kok / "types.py",
        evals=kok / "evals",
    )
```

`tests/conftest.py` başına `from pathlib import Path` ve `import pytest` zaten varsa tekrarlama.

**NOT:** Bu testler Görev 10'da oluşturulacak `customers/acme/` dosyalarına bağlıdır. Bu görevde onları geçici olarak oluşturmayın; testleri `@pytest.mark.skipif(not (KOK/"customers").exists(), reason="referans akış Görev 10'da gelir")` ile işaretleyin ve Görev 10'da işareti kaldırın. Diğer testler bu görevde tam koşar.

- [ ] **Adım 6: `compiler/__init__.py` cephesini yaz**

```python
"""Derleyici cephesi: YAML + profil + tipler → doğrulanmış, fiyatlanmış graf.

Tek giriş noktası. Doğrulama sırası önemlidir: graf kurulamazsa (döngü,
kayıp kenar) sonraki analizler tanımsızdır ve KOŞMAZ; kurulursa yapısal,
tipsel ve maliyet denetimleri BİRLİKTE koşar ve tüm bulgular tek raporda
döner.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kernel.compiler import validate_graph as VG
from kernel.compiler import validate_types as VT
from kernel.compiler.cost import CostReport, ceiling_cost
from kernel.compiler.errors import (
    E_ADIM_TAVANI,
    E_BUTCE_TAVANI,
    CompileError,
    CompileFailed,
    CompileReport,
)
from kernel.compiler.graph import Graph, build_graph
from kernel.compiler.loader import load_workflow
from kernel.compiler.profile import Profile, load_profile, profile_warnings


class CompiledWorkflow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    version_hash: str
    source: str
    graph: Graph
    profile: Profile
    report: CompileReport
    cost: CostReport

    def to_compiled_json(self) -> dict:
        """`workflows.compiled` kolonuna yazılan artefakt.

        Yalnız grafı ve maliyet özetini taşır. Profil BİLEREK dışarıda:
        kademe eşlemesi ve sır referansları worker sürecinde dosyadan okunur,
        veritabanına kopyalanmaz (K14).
        """
        return {
            "name": self.name,
            "version_hash": self.version_hash,
            "entry": self.graph.entry,
            "spec": self.graph.spec.model_dump(mode="json", by_alias=True),
            "children": self.graph.children,
            "cost": {
                "total_usd": str(self.cost.total_usd),
                "worst_path": self.cost.worst_path,
                "worst_steps": self.cost.worst_steps,
            },
        }


def compile_workflow(
    workflow_path: Path,
    profile_path: Path,
    types_path: Path,
    *,
    known_tools: set[str] | None = None,
    reconcile_tools: set[str] | None = None,
    eval_dir: Path | None = None,
) -> CompiledWorkflow:
    from kernel.tools import registry

    yuklu = load_workflow(workflow_path)
    profil = load_profile(profile_path)
    tipler = VT.load_types(types_path)

    g, graf_hatalari = build_graph(yuklu.spec)
    if g is None:
        raise CompileFailed(CompileReport(errors=graf_hatalari))

    ctx = VG.ValidationContext(
        known_tools=registry.names() if known_tools is None else known_tools,
        reconcile_tools=(
            registry.reconcile_supported() if reconcile_tools is None else reconcile_tools
        ),
        tiers=set(profil.tiers),
        eval_dir=eval_dir,
    )
    yapi = VG.validate_graph(g, ctx)
    tip = VT.validate_types(g, tipler)

    hatalar = [*graf_hatalari, *yapi.errors, *tip.errors]
    uyarilar = [*yapi.warnings, *tip.warnings, *profile_warnings(profil)]

    maliyet = ceiling_cost(g, profil, tipler)
    hatalar.extend(_tavan_denetimi(yuklu.spec, maliyet))

    rapor = CompileReport(errors=hatalar, warnings=uyarilar)
    if not rapor.ok:
        raise CompileFailed(rapor)

    return CompiledWorkflow(
        name=yuklu.spec.name,
        version_hash=yuklu.version_hash,
        source=yuklu.source,
        graph=g,
        profile=profil,
        report=rapor,
        cost=maliyet,
    )


def _tavan_denetimi(spec, maliyet: CostReport) -> list[CompileError]:
    out: list[CompileError] = []
    if maliyet.worst_steps > spec.limits.max_steps:
        out.append(
            CompileError(
                code=E_ADIM_TAVANI,
                message=(
                    f"en kötü yol {maliyet.worst_steps} adım sürüyor,"
                    f" max_steps={spec.limits.max_steps}"
                ),
            )
        )
    if maliyet.total_usd > spec.limits.max_usd_per_run:
        out.append(
            CompileError(
                code=E_BUTCE_TAVANI,
                message=(
                    f"tavan maliyet ${maliyet.total_usd} >"
                    f" max_usd_per_run ${spec.limits.max_usd_per_run}"
                    f" (en pahalı yol: {' → '.join(maliyet.worst_path)})"
                ),
            )
        )
    return out
```

`Graph` bir Pydantic modeli olmadığı için `CompiledWorkflow` içinde `arbitrary_types_allowed=True` gerekir; bu bilinçlidir — `Graph` davranış taşıyan bir nesnedir, serileştirilen şey `to_compiled_json()`'dur.

- [ ] **Adım 7: CLI'ı yaz**

`packages/kernel/compiler/__main__.py`:

```python
"""Derleme ve tavan maliyet raporu CLI'ı.

    python -m kernel.compiler customers/acme/workflows/belge_girisi.yaml \
        --profile customers/acme/profile.yaml \
        --types customers/acme/types.py \
        --evals customers/acme/evals

Çıkış kodu: 0 temiz, 1 hata. §7 Katman 0 — her YAML commit'inde koşar.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kernel.compiler import compile_workflow
from kernel.compiler.errors import CompileFailed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kernel.compiler")
    ap.add_argument("workflow", type=Path)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--types", type=Path, required=True)
    ap.add_argument("--evals", type=Path, default=None)
    a = ap.parse_args(argv)

    try:
        d = compile_workflow(a.workflow, a.profile, a.types, eval_dir=a.evals)
    except CompileFailed as exc:
        print(f"DERLEME BAŞARISIZ — {len(exc.report.errors)} hata\n", file=sys.stderr)
        for h in exc.report.errors:
            print(f"  ✗ {h}", file=sys.stderr)
        for u in exc.report.warnings:
            print(f"  ⚠ {u}", file=sys.stderr)
        return 1

    print(f"✓ {d.name} derlendi (hash {d.version_hash[:12]})")
    for u in d.report.warnings:
        print(f"  ⚠ {u}")
    print()
    print(d.cost.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Adım 8: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: 120 passed (dört `test_compile` testi Görev 10'a kadar skip).

```bash
git add packages/kernel/compiler tests/
git commit -m "feat(compiler): tavan maliyet (K8), derleme cephesi ve rapor CLI'ı"
```

---

### Görev 6: Şema göçü 002 ve kiralı bütçe rezervasyonu (TOCTOU kapanışı)

M1'in park edilmiş kritik açığı: bütçe ön kontrolü ile harcama kaydı ayrı transaction'larda. `FOR UPDATE` kilidi çağrı boyunca **tutulmuyor**; iki worker aynı run'da eşzamanlı çağrı yaparsa ikisi de bütçeyi yeterli görür. M1'de tetiklenemezdi çünkü akış sabit ve sıralıydı; M2'de kira süresi dolan bir işi devralan worker aynı run'ın adımını eşzamanlı yürütebilir — açık canlıdır.

Çözüm K15'in para karşılığıdır: **kiralı rezervasyon**. Kontrol ve rezervasyon TEK transaction'da yapılır; harcama kaydı rezervasyonu düşer; çöken worker'ın rezervasyonu kirasıyla birlikte sona erer.

**Dosyalar:**
- Oluştur: `packages/kernel/state/migrations/002_m2.sql`
- Değiştir: `packages/kernel/config.py` (`MAX_DEFERS`)
- Değiştir: `packages/kernel/gateway/gateway.py`
- Değiştir: `packages/kernel/orchestrator/step.py` (DEFERRED dalı: `defer_count`, `status='deferred'`)
- Test: `tests/gateway/test_butce_rezervasyonu.py`, `tests/state/test_migrate.py` (ekleme)

**Arayüzler:**
- Üretir: `workflows`, `approvals`, `artifacts` tabloları; `runs.workflow_id`, `runs.defer_count`; `steps.estimated_usd`, `steps.estimate_expires_at`; `runs.status` içinde `awaiting_approval`; `steps.status` içinde `deferred`
- Değişen: `Gateway._check_budget` → `Gateway._reserve_budget(model, req, run_id, step_id) -> Decimal`

- [ ] **Adım 1: Göç dosyasını yaz**

`packages/kernel/state/migrations/002_m2.sql`:

```sql
-- M2: derleyici artefaktları, insan onayı, nesne deposu ve bütçe rezervasyonu.

-- Derlenmiş graf burada yaşar. Run başlarken bu satıra SABİTLENİR: müşteri
-- çalışan bir işin ortasında YAML'ı düzenlerse, o iş eski tanımla biter (§5).
CREATE TABLE workflows (
    id           uuid PRIMARY KEY,
    tenant_id    text NOT NULL,
    name         text NOT NULL,
    version_hash text NOT NULL,
    yaml_source  text NOT NULL,
    compiled     jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT workflows_version_unique UNIQUE (tenant_id, name, version_hash)
);

ALTER TABLE runs ADD COLUMN workflow_id uuid REFERENCES workflows(id);

-- İnsan onayı RAM tutmaz: satır Postgres'te bekler, hiçbir süreç ayakta kalmaz.
-- 3 gün beklemenin maliyeti bir veritabanı satırıdır (§5).
CREATE TABLE approvals (
    id            uuid PRIMARY KEY,
    run_id        uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_id       text NOT NULL,
    status        text NOT NULL,
    assignee_role text NOT NULL,
    context       jsonb NOT NULL DEFAULT '{}'::jsonb,
    decided_by    text,
    decided_at    timestamptz,
    reason        text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT approvals_status_check CHECK (status IN ('pending','approved','rejected','timeout')),
    CONSTRAINT approvals_bekleyen_tek UNIQUE (run_id, node_id)
);

-- §4.1(4): büyük içerik grafta dolaşmaz, referansı dolaşır.
CREATE TABLE artifacts (
    id         uuid PRIMARY KEY,
    tenant_id  text NOT NULL,
    run_id     uuid REFERENCES runs(id) ON DELETE SET NULL,
    uri        text NOT NULL UNIQUE,
    media_type text NOT NULL,
    bytes      bigint NOT NULL,
    sha256     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- human_approval askıya alma durumu (KK3).
ALTER TABLE runs DROP CONSTRAINT runs_status_check;
ALTER TABLE runs ADD CONSTRAINT runs_status_check CHECK (status IN
    ('pending','running','awaiting_approval','completed','failed',
     'budget_exceeded','uncertain'));

-- M1'de ertelenen adım 'failed' + error='ertelendi' olarak yazılıyordu; denetim
-- izinde gerçek bir hatadan ayırt edilemiyordu. Artık kendi durumu var.
ALTER TABLE steps DROP CONSTRAINT steps_status_check;
ALTER TABLE steps ADD CONSTRAINT steps_status_check CHECK (status IN
    ('running','completed','failed','deferred'));

-- Erteleme adım bütçesini tüketmemeli: uzun bir onay beklemesi run'ı
-- max_steps'e çarptırmamalı. Ayrı sayaç, ayrı tavan.
ALTER TABLE runs ADD COLUMN defer_count int NOT NULL DEFAULT 0;

-- BÜTÇE REZERVASYONU (TOCTOU kapanışı).
-- Bir LLM çağrısından önce tahmini maliyet buraya KİRALI olarak yazılır ve
-- kontrol ile rezervasyon aynı transaction'da yapılır. Çağrı bitince harcama
-- kaydı rezervasyonu düşürür. Worker çökerse rezervasyon kirasıyla sona erer:
-- iş kuyruğu ve K15 ile aynı kurtarma felsefesi, ek temizlik süreci yok.
ALTER TABLE steps ADD COLUMN estimated_usd numeric(12,6);
ALTER TABLE steps ADD COLUMN estimate_expires_at timestamptz;

-- ZK2: PII maskeleme haritası RAM'de DEĞİL, burada — şifreli ve süreli.
-- Fernet ile şifrelenir; anahtar yalnız OTOMASYON_PII_KEY ortam değişkeninden
-- gelir (K14). Şifresiz tutulsaydı bu kolonun kendisi bir PII deposu olurdu.
--
-- Bu kolonun `steps`'te olması BİLİNÇLİDİR: `events` ekleme-yalnızdır (Kural 5)
-- ve oraya yazılan bir harita ASLA silinemezdi. `steps` UPDATE edilebilir,
-- dolayısıyla temizlenebilir.
ALTER TABLE steps ADD COLUMN pii_map bytea;
ALTER TABLE steps ADD COLUMN pii_map_expires_at timestamptz;

CREATE INDEX steps_acik_pii_map ON steps (pii_map_expires_at)
    WHERE pii_map IS NOT NULL;

CREATE INDEX steps_acik_rezervasyon ON steps (run_id)
    WHERE estimate_expires_at IS NOT NULL;
CREATE INDEX approvals_bekleyen ON approvals (status, assignee_role)
    WHERE status = 'pending';
CREATE INDEX runs_workflow ON runs (workflow_id);
```

- [ ] **Adım 2: Göç testini genişlet ve koş**

`tests/state/test_migrate.py` sonuna:

```python
def test_002_gocu_uygulanir(db):
    with db.tx() as conn:
        kolonlar = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'steps'"
            ).fetchall()
        }
        assert {"estimated_usd", "estimate_expires_at",
                "pii_map", "pii_map_expires_at"} <= kolonlar
        tablolar = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public'"
            ).fetchall()
        }
        assert {"workflows", "approvals", "artifacts"} <= tablolar


def test_deferred_ve_awaiting_approval_durumlari_kabul_edilir(db, run_id):
    with db.tx() as conn:
        conn.execute("UPDATE runs SET status = 'awaiting_approval' WHERE id = %s", (run_id,))
```

(Mevcut `db` ve `run_id` fixture adlarını `tests/conftest.py`'deki gerçek adlarla eşleştirin; farklıysa oradaki deseni izleyin.)

Koş: `.venv/bin/python -m pytest tests/state -v`
Beklenen: PASS

- [ ] **Adım 3: Başarısız rezervasyon testini yaz**

`tests/gateway/test_butce_rezervasyonu.py`:

```python
"""TOCTOU kapanışının regresyon kilidi.

Bu testler M1'de PASS ETMEZDİ: kontrol ile harcama ayrı transaction'lardaydı
ve kilit çağrı boyunca tutulmuyordu.
"""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from kernel.gateway.gateway import BudgetExceeded, Gateway
from kernel.gateway.types import LLMRequest
from kernel.state import db


def istek() -> LLMRequest:
    return LLMRequest(
        tier="fast", system_layer1="s1", system_layer2="s2",
        user_content="metin", max_tokens=10000,
    )


def test_rezervasyon_kontrolle_AYNI_transactionda(dar_butceli_run, yavas_gateway):
    """İki eşzamanlı çağrı: tam olarak biri geçmeli.

    Bütçe iki çağrıyı değil bir çağrıyı karşılıyor. M1'in kodunda ikisi de
    bütçeyi yeterli görüp geçerdi — para iki kez harcanırdı.
    """
    run_id, step_a, step_b = dar_butceli_run
    sonuclar: list[str] = []
    kilit = threading.Lock()

    def cagir(step_id):
        try:
            yavas_gateway.complete(istek(), run_id, step_id)
            with kilit:
                sonuclar.append("ok")
        except BudgetExceeded:
            with kilit:
                sonuclar.append("butce")
        except Exception as exc:  # noqa: BLE001
            with kilit:
                sonuclar.append(f"beklenmedik:{exc!r}")

    t1 = threading.Thread(target=cagir, args=(step_a,))
    t2 = threading.Thread(target=cagir, args=(step_b,))
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)
    assert not t1.is_alive() and not t2.is_alive(), "thread asıldı"

    assert sorted(sonuclar) == ["butce", "ok"], sonuclar


def test_harcama_kaydi_rezervasyonu_duser(dar_butceli_run, offline_gateway):
    run_id, step_a, _ = dar_butceli_run
    offline_gateway.complete(istek(), run_id, step_a)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT estimated_usd, estimate_expires_at FROM steps WHERE id = %s",
            (step_a,),
        ).fetchone()
    assert row == (None, None), "tamamlanan adımın rezervasyonu düşmeli"


def test_kirasi_dolan_rezervasyon_butceyi_bloke_ETMEZ(dar_butceli_run, offline_gateway):
    """Çöken worker'ın rezervasyonu run'ı sonsuza dek kilitlemez."""
    run_id, step_a, step_b = dar_butceli_run
    with db.tx() as conn:
        conn.execute(
            "UPDATE steps SET estimated_usd = 999, estimate_expires_at = now() -"
            " interval '1 second' WHERE id = %s",
            (step_a,),
        )
    offline_gateway.complete(istek(), run_id, step_b)  # patlamamalı


def test_acik_rezervasyon_butceyi_bloke_EDER(dar_butceli_run, offline_gateway):
    run_id, step_a, step_b = dar_butceli_run
    with db.tx() as conn:
        conn.execute(
            "UPDATE steps SET estimated_usd = 999, estimate_expires_at = now() +"
            " interval '1 hour' WHERE id = %s",
            (step_a,),
        )
    with pytest.raises(BudgetExceeded):
        offline_gateway.complete(istek(), run_id, step_b)
```

Fixture'lar (`tests/conftest.py`'ye ekle): `dar_butceli_run` bir run + iki `running` steps satırı açar ve `budget_usd` değerini **tek** bir `fast` çağrısını karşılayacak, ikisini karşılamayacak biçimde ayarlar (`Decimal("0.30")` ile başlayın ve `Gateway._estimate` değerine göre kalibre edin — test kalibrasyonu iddiayı zayıflatmadan yapılmalıdır: iki katı asla yetmemeli). `yavas_gateway`, `OfflineTransport.send` içinde 0.5 sn uyuyan bir taşıma katmanıyla kurulur; böylece iki thread rezervasyon penceresinde gerçekten çakışır. `offline_gateway` sıradan `OfflineTransport`'tur.

- [ ] **Adım 4: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/gateway/test_butce_rezervasyonu.py -v`
Beklenen: FAIL — `test_rezervasyon_kontrolle_AYNI_transactionda` iki "ok" görür (mevcut TOCTOU), diğerleri `estimated_usd` kolonu kullanılmadığı için düşer.

- [ ] **Adım 5: `gateway.py`'yi değiştir**

`_check_budget`'ı sil, yerine:

```python
    def _reserve_budget(
        self, model: str, req: LLMRequest, run_id: UUID, step_id: UUID
    ) -> Decimal:
        """Kontrol ve rezervasyon TEK transaction'da (TOCTOU kapanışı).

        `FOR UPDATE` run satırını kilitler; ikinci worker bu kilidi bekler ve
        kilit serbest kaldığında READ COMMITTED altında TAZE bir anlık görüntü
        okur — yani birincinin commit ettiği rezervasyonu görür. Sıra bu yüzden
        önemlidir: önce kilit, sonra rezervasyon toplamı.

        Rezervasyon KİRALIDIR. Worker çağrının ortasında çökerse rezervasyon
        kirasıyla sona erer ve bütçe kendiliğinden serbest kalır; ayrı bir
        temizlik süreci yoktur. Kira JOB_LEASE_SECONDS'tır: işin kirası dolmadan
        rezervasyonu düşmez, yani aynı iş için iki eşzamanlı rezervasyon olamaz.
        """
        tahmin = self._estimate(model, req)
        with db.tx() as conn:
            row = conn.execute(
                "SELECT budget_usd, spent_usd FROM runs WHERE id = %s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"run bulunamadı: {run_id}")
            butce, harcanan = row[0], row[1]
            rezerve = conn.execute(
                "SELECT coalesce(sum(estimated_usd), 0) FROM steps"
                " WHERE run_id = %s AND estimate_expires_at > now()",
                (run_id,),
            ).fetchone()[0]
            if harcanan + rezerve + tahmin > butce:
                raise BudgetExceeded(
                    f"tahmini maliyet bütçeyi aşıyor (harcanan={harcanan},"
                    f" rezerve={rezerve}, tahmin={tahmin}, bütçe={butce})"
                )
            conn.execute(
                "UPDATE steps SET estimated_usd = %s,"
                " estimate_expires_at = now() + make_interval(secs => %s)"
                " WHERE id = %s",
                (tahmin, config.JOB_LEASE_SECONDS, step_id),
            )
        return tahmin
```

`complete()` içinde `self._check_budget(model, req, run_id)` çağrısını
`self._reserve_budget(model, req, run_id, step_id)` ile değiştir.

`_estimate` içinde `accounting.PRICES[model]` yerine:

```python
        price = accounting.price_for(model)
        if price is None:
            raise KeyError(
                f"kademe bilinmeyen bir modele eşleniyor, bütçe zorlanamaz: {model!r}"
            )
```

`_record` içindeki `UPDATE steps` ifadesine rezervasyonun düşürülmesini ekle:

```python
                "UPDATE steps SET model = %s, tokens_in = %s, tokens_out = %s,"
                " cache_read_tokens = %s, cost_usd = %s,"
                " estimated_usd = NULL, estimate_expires_at = NULL"
                " WHERE id = %s",
```

Dosya başına `from kernel import config` import'unu ekle.

- [ ] **Adım 6: `step.py`'nin DEFERRED dalını düzelt**

`_run_tool` içindeki DEFERRED bloğunda iki UPDATE'i değiştir:

```python
            conn.execute(
                "UPDATE steps SET status = 'deferred', ended_at = now()"
                " WHERE id = %s",
                (step_id,),
            )
            conn.execute(
                "UPDATE runs SET defer_count = defer_count + 1, updated_at = now()"
                " WHERE id = %s",
                (job.run_id,),
            )
```

`execute_step` içindeki tavan kontrolüne erteleme tavanını ekle:

```python
    with db.tx() as conn:
        run = conn.execute(
            "SELECT input, tenant_id, step_count, max_steps, defer_count FROM runs"
            " WHERE id = %s",
            (job.run_id,),
        ).fetchone()

    if run[2] >= run[3]:
        return _fail_run(job, None, "failed", "MaxStepsExceeded",
                         f"adım tavanı aşıldı: step_count={run[2]}, max_steps={run[3]}")
    # Erteleme artık adım bütçesini tüketmiyor; kendi tavanı var. Aksi halde
    # süresiz erteleme sessiz bir sonsuz döngüdür.
    if run[4] >= config.MAX_DEFERS:
        return _fail_run(job, None, "uncertain", "MaxDefersExceeded",
                         f"erteleme tavanı aşıldı: defer_count={run[4]}")
```

`packages/kernel/config.py`'ye ekle:

```python
# Bir run'ın kaç kez ertelenebileceğinin tavanı. Erteleme adım bütçesini
# tüketmez (ayrı sayaç), ama sonsuz da olamaz: tavana ulaşan run insan
# incelemesine gider.
MAX_DEFERS = int(os.environ.get("OTOMASYON_MAX_DEFERS", "50"))
```

M1'in `test_runner.py` içindeki `step_deferred` ve `step_count` iddialarını yeni davranışa göre güncelle: ertelenen adımda `step_count` **artmaz**, `defer_count` artar, `steps.status` `'deferred'` olur.

- [ ] **Adım 7: Testleri koş**

Koş: `.venv/bin/python -m pytest tests/gateway tests/orchestrator tests/state -v`
Beklenen: tümü PASS. `test_rezervasyon_kontrolle_AYNI_transactionda` artık `["butce", "ok"]` görür.

- [ ] **Adım 8: Çürütme deneyi (kanıt adımı, commit edilmez)**

`_reserve_budget` içindeki `FOR UPDATE`'i geçici olarak kaldır ve
`test_rezervasyon_kontrolle_AYNI_transactionda`'yı koş. Testin **düştüğünü**
(iki "ok") gör, sonra değişikliği geri al ve `git status --porcelain`'in boş
olduğunu doğrula. Rapor dosyasına deneyin çıktısını yaz: testin boş olmadığının
kanıtı budur.

- [ ] **Adım 9: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: 126 passed.

```bash
git add packages/kernel/state/migrations/002_m2.sql packages/kernel/config.py \
        packages/kernel/gateway/gateway.py packages/kernel/orchestrator/step.py tests/
git commit -m "fix(gateway): kiralı bütçe rezervasyonu — TOCTOU kapatıldı (§6.4)"
```

---

### Görev 7: Ağ geçidinde PII maskeleme (§6.5)

M1'in ikinci park edilmiş maddesi. Spec §6.5: *"PII maskeleme tek noktada. Kurumsal veri dışarı çıkmadan maskelenir; KVKK tartışmasında gösterilecek tek nokta ağ geçididir."* İkinci bir yer daha var ve M1'de o da açıktı: **olay kaydı**. Kural 5 gereği `events` satırı asla silinemez — oraya sızan bir TCKN sonsuza kadar orada kalır. Maskeleme bu yüzden iki yüzeyi birden kapatır.

**ZK2 zorunlu:** maskeleme haritası süreç belleğinde tutulamaz. `steps.pii_map`
kolonuna Fernet ile şifreli ve TTL'li yazılır; taşıma katmanı çağrılmadan ÖNCE
commit edilir, yanıt çözüldükten sonra `NULL`'lanır, artakalanlar worker
boştayken süpürülür. Ayrıntılı gerekçe: "Zorunlu Mimari Kurallar" bölümü.

**Dosyalar:**
- Oluştur: `packages/kernel/gateway/masking.py`
- Oluştur: `packages/kernel/state/pii_map.py` (ZK2 — şifreli kalıcılık + süpürme)
- Değiştir: `pyproject.toml` (`cryptography>=43`)
- Değiştir: `packages/kernel/gateway/gateway.py` (maskele → gönder → maskeyi çöz)
- Değiştir: `packages/kernel/orchestrator/step.py` (`error_payload` olay kaydına maskeli yazar)
- Değiştir: `packages/kernel/orchestrator/runner.py` (profil → varsayılan maskeleyici)
- Test: `tests/gateway/test_masking.py`

**Arayüzler:**
- Üretir: `DESENLER`, `Masker`, `MaskSession`, `set_default(masker)`, `scrub(text) -> str`, `default_masker() -> Masker`
- Değişen: `Gateway.__init__(transport, masker=None)`

- [ ] **Adım 1: Başarısız testi yaz**

`tests/gateway/test_masking.py`:

```python
from kernel.gateway import masking as M

TCKN = "12345678950"
IBAN = "TR330006100519786457841326"
EPOSTA = "ahmet.yilmaz@acme.com.tr"
TELEFON = "+90 532 123 45 67"
VKN = "1234567890"


def test_desenler_maskelenir():
    m = M.Masker(M.VARSAYILAN_DESENLER)
    s = m.session()
    metin = f"Sayın {EPOSTA}, TCKN {TCKN}, IBAN {IBAN}, tel {TELEFON}, VKN {VKN}."
    maskeli = s.mask(metin)
    for gizli in (TCKN, IBAN, EPOSTA, VKN):
        assert gizli not in maskeli, f"{gizli} sızdı: {maskeli}"
    assert "532 123 45 67" not in maskeli
    assert "[TCKN_1]" in maskeli and "[IBAN_1]" in maskeli


def test_maske_geri_cozulur():
    """Model maskeli metinle çalışır; yanıtı gerçek değerlerle geri döner."""
    m = M.Masker(M.VARSAYILAN_DESENLER)
    s = m.session()
    maskeli = s.mask(f"Fatura sahibi {EPOSTA}, TCKN {TCKN}")
    yanit = maskeli.replace("Fatura sahibi", "Alıcı")
    assert s.unmask(yanit) == f"Alıcı {EPOSTA}, TCKN {TCKN}"


def test_ayni_deger_ayni_yer_tutucuyu_alir():
    m = M.Masker(M.VARSAYILAN_DESENLER)
    s = m.session()
    maskeli = s.mask(f"{TCKN} ve yine {TCKN}")
    assert maskeli.count("[TCKN_1]") == 2
    assert "[TCKN_2]" not in maskeli


def test_PII_ICERMEYEN_metin_BAYT_BAYT_degismez():
    """§6.2: Katman 1 ürün sabitidir. Maskeleme onu değiştirirse önbellek ölür."""
    m = M.Masker(M.VARSAYILAN_DESENLER)
    katman1 = "Sen kurumsal bir belge işleme otomasyonunun parçasısın."
    for _ in range(3):
        s = m.session()
        s.mask(katman1)
        s.mask("kiracı: acme")
        s.mask(f"TCKN {TCKN}")
        assert s.mask(katman1) == katman1


def test_yer_tutucu_numaralari_katmanlar_arasi_KARARLI():
    """Katman 1 ve 2'nin maskeli hâli, Katman 3'ün içeriğinden bağımsız olmalı."""
    m = M.Masker(M.VARSAYILAN_DESENLER)
    a, b = m.session(), m.session()
    assert a.mask("sabit sistem promptu") == b.mask("sabit sistem promptu")
    a.mask(f"TCKN {TCKN}")
    assert a.mask("kiracı: acme") == b.mask("kiracı: acme")


def test_scrub_geri_donulemez_ve_olay_kaydi_icin():
    """Olay kaydı SİLİNEMEZ (Kural 5); oraya giden metin geri döndürülemez
    biçimde temizlenir — harita tutmanın anlamı yok, tutmak risk."""
    M.set_default(M.Masker(M.VARSAYILAN_DESENLER))
    assert TCKN not in M.scrub(f"hata: {TCKN} bulunamadı")
    assert "[TCKN_1]" in M.scrub(f"hata: {TCKN} bulunamadı")


def test_desen_kumesi_daraltilabilir():
    m = M.Masker(["iban"])
    s = m.session()
    metin = f"{IBAN} {TCKN}"
    maskeli = s.mask(metin)
    assert IBAN not in maskeli
    assert TCKN in maskeli, "istenmeyen desen maskelenmemeli"


def test_bilinmeyen_desen_adi_hata():
    import pytest
    with pytest.raises(KeyError):
        M.Masker(["yok_boyle_desen"])
```

- [ ] **Adım 2: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/gateway/test_masking.py -v`
Beklenen: FAIL — `No module named 'kernel.gateway.masking'`

- [ ] **Adım 3: `masking.py` yaz**

```python
"""PII maskeleme — TEK nokta (§6.5).

Kurumsal veri iki yüzeyden dışarı çıkar ve ikisi de burada kapatılır:
  1. Sağlayıcıya giden prompt. KVKK/ZDR tartışmasında gösterilecek nokta budur.
  2. `events` olay kaydı. Kural 5 gereği o satırlar ASLA silinemez; oraya
     sızan bir kimlik numarası kalıcıdır. Bu yüzden olay kaydına giden metin
     `scrub()`'dan geçer ve geri döndürülemez — harita tutmak riski taşımaktır.

Prompt yolunda maskeleme GERİ DÖNDÜRÜLEBİLİRDİR: model `[TCKN_1]` görür,
yanıtında onu kullanır, ağ geçidi gerçek değeri geri koyar. Aksi halde
"faturadaki TCKN'yi çıkar" gibi bir görev maskeleme yüzünden imkânsızlaşırdı.

Önbellek güvenliği (§6.2): yer tutucu sayaçları oturum başına ve tip başına
ilerler. PII içermeyen bir metin bayt bayt DEĞİŞMEDEN döner — Katman 1'in
önbellek öneki bozulmaz.
"""
from __future__ import annotations

import re

# Sıra ÖNEMLİ: geniş desen dar deseni yutmasın. E-posta önce (içinde rakam
# olabilir), sonra IBAN (uzun), sonra TCKN (11 hane), sonra VKN (10 hane).
DESENLER: dict[str, re.Pattern[str]] = {
    "eposta": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "iban": re.compile(r"\bTR\d{24}\b", re.IGNORECASE),
    "tckn": re.compile(r"\b[1-9]\d{10}\b"),
    "vkn": re.compile(r"\b\d{10}\b"),
    "telefon": re.compile(r"(?:\+90|0)?[\s-]?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b"),
}

VARSAYILAN_DESENLER = ("eposta", "iban", "tckn", "vkn", "telefon")


class MaskSession:
    """Tek bir LLM çağrısının maskeleme oturumu.

    Harita SÜREÇTE KALMAZ (ZK2): `to_payload()` ile dışa verilir ve
    `state.pii_map` tarafından şifrelenip `steps.pii_map` kolonuna yazılır.
    Çağrının ortasında ölen bir worker'ın haritası böylece kaybolmaz —
    RAM'de tutulsaydı, kaydedilmiş maskeli bir yanıt kalıcı olarak
    çözülemez hâle gelirdi.
    """

    def __init__(self, desenler: list[tuple[str, re.Pattern[str]]]):
        self._desenler = desenler
        self._harita: dict[str, str] = {}   # yer tutucu → gerçek değer
        self._ters: dict[str, str] = {}     # gerçek değer → yer tutucu
        self._sayaclar: dict[str, int] = {}

    def mask(self, text: str) -> str:
        for ad, desen in self._desenler:
            text = desen.sub(lambda m, ad=ad: self._yer_tutucu(ad, m.group(0)), text)
        return text

    def unmask(self, text: str) -> str:
        for yer_tutucu, gercek in self._harita.items():
            text = text.replace(yer_tutucu, gercek)
        return text

    def to_payload(self) -> dict[str, str]:
        """Kalıcılaştırılacak harita. ASLA olay kaydına yazılmaz (Kural 5)."""
        return dict(self._harita)

    @classmethod
    def from_payload(cls, harita: dict[str, str]) -> MaskSession:
        """Kaydedilmiş haritayla yalnız maskeyi ÇÖZMEK için kurulur."""
        s = cls([])
        s._harita = dict(harita)
        s._ters = {v: k for k, v in harita.items()}
        return s

    def _yer_tutucu(self, ad: str, deger: str) -> str:
        if deger in self._ters:
            return self._ters[deger]
        self._sayaclar[ad] = self._sayaclar.get(ad, 0) + 1
        yer_tutucu = f"[{ad.upper()}_{self._sayaclar[ad]}]"
        self._harita[yer_tutucu] = deger
        self._ters[deger] = yer_tutucu
        return yer_tutucu


class Masker:
    def __init__(self, patterns: list[str] | tuple[str, ...]):
        eksik = [p for p in patterns if p not in DESENLER]
        if eksik:
            raise KeyError(f"bilinmeyen maskeleme deseni: {eksik}; geçerli: {sorted(DESENLER)}")
        self._desenler = [(p, DESENLER[p]) for p in patterns]

    def session(self) -> MaskSession:
        return MaskSession(self._desenler)

    def scrub(self, text: str) -> str:
        """Geri döndürülemez temizlik — olay kaydı ve denetim izi için."""
        return self.session().mask(text)


class _KapaliMasker(Masker):
    """KK5: maskelemeyi açıkça kapatmak isteyen profil için."""

    def __init__(self):
        super().__init__(())


_default: Masker = Masker(VARSAYILAN_DESENLER)


def set_default(m: Masker) -> None:
    """Worker başlangıcında profilden kurulur. Varsayılan AÇIKTIR (KK5)."""
    global _default
    _default = m


def default_masker() -> Masker:
    return _default


def scrub(text: str) -> str:
    """Olay kaydına giden her serbest metin bundan geçer."""
    return _default.scrub(text)


def from_policy(enabled: bool, patterns: list[str]) -> Masker:
    return Masker(patterns) if enabled else _KapaliMasker()
```

- [ ] **Adım 4: Testleri koş**

Koş: `.venv/bin/python -m pytest tests/gateway/test_masking.py -v`
Beklenen: 8 PASS

- [ ] **Adım 4b: `state/pii_map.py` yaz (ZK2)**

`pyproject.toml` bağımlılıklarına `"cryptography>=43"` ekle, sonra:

```python
"""Maskeleme haritasının şifreli, süreli kalıcılığı (ZK2).

Harita `steps.pii_map` kolonunda Fernet ile şifreli durur. Anahtar YALNIZ
OTOMASYON_PII_KEY ortam değişkeninden gelir (K14).

Kolonun `steps`'te olması bilinçlidir: `events` ekleme-yalnızdır (Kural 5),
oraya yazılan bir harita asla silinemezdi. `steps` UPDATE edilebilir olduğu
için TTL'li temizlik mümkündür — bu kolonun kalıcı bir PII arşivine
dönüşmemesinin tek güvencesi budur.
"""
from __future__ import annotations

import json
import os
from uuid import UUID

import psycopg
from cryptography.fernet import Fernet

# Harita iş kirasından uzun yaşar (devralan worker kaydedilmiş yanıtı
# çözebilsin) ama sonsuz değildir (kolon PII arşivine dönüşmesin).
TTL_SECONDS = int(os.environ.get("OTOMASYON_PII_MAP_TTL", "3600"))


def _fernet() -> Fernet:
    anahtar = os.environ.get("OTOMASYON_PII_KEY")
    if not anahtar:
        raise RuntimeError(
            "OTOMASYON_PII_KEY tanımlı değil; PII haritası şifresiz yazılamaz (ZK2)"
        )
    return Fernet(anahtar.encode("utf-8"))


def save(conn: psycopg.Connection, step_id: UUID, harita: dict[str, str]) -> None:
    """Taşıma katmanı çağrılmadan ÖNCE commit edilir.

    Sıra bağlayıcıdır: sonra yazılsaydı, çağrının ortasında ölen worker'ın
    maskeli yanıtı bir daha çözülemezdi.
    """
    if not harita:
        return
    conn.execute(
        "UPDATE steps SET pii_map = %s,"
        " pii_map_expires_at = now() + make_interval(secs => %s) WHERE id = %s",
        (
            _fernet().encrypt(json.dumps(harita, ensure_ascii=False).encode("utf-8")),
            TTL_SECONDS,
            step_id,
        ),
    )


def load(conn: psycopg.Connection, step_id: UUID) -> dict[str, str]:
    row = conn.execute(
        "SELECT pii_map FROM steps WHERE id = %s AND pii_map_expires_at > now()",
        (step_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return {}
    return json.loads(_fernet().decrypt(bytes(row[0])).decode("utf-8"))


def clear(conn: psycopg.Connection, step_id: UUID) -> None:
    conn.execute(
        "UPDATE steps SET pii_map = NULL, pii_map_expires_at = NULL WHERE id = %s",
        (step_id,),
    )


def sweep(conn: psycopg.Connection) -> int:
    """Süresi dolmuş haritaları siler. Worker BOŞTAYKEN çağrılır — ayrı bir
    temizlik süreci yok (K3: cron da bir bağımlılıktır)."""
    cur = conn.execute(
        "UPDATE steps SET pii_map = NULL, pii_map_expires_at = NULL"
        " WHERE pii_map IS NOT NULL AND pii_map_expires_at <= now()"
    )
    return cur.rowcount
```

`runner.run_forever` içinde iş bulunamadığında, uyumadan önce
`pii_map.sweep()` çağrılır.

- [ ] **Adım 5: Ağ geçidine bağla**

`gateway.py`:

```python
    def __init__(self, transport: Transport, masker: masking.Masker | None = None):
        self._transport = transport
        self._masker = masker

    def complete(self, req: LLMRequest, run_id: UUID, step_id: UUID) -> LLMResponse:
        model, effort = tiers.resolve(req.tier)
        self._reserve_budget(model, req, run_id, step_id)

        # PII sınırı: bu noktadan SONRA hiçbir ham kurumsal veri süreçten
        # çıkmaz (§6.5).
        oturum = (self._masker or masking.default_masker()).session()
        maskeli = {
            "system_layer1": oturum.mask(req.system_layer1),
            "system_layer2": oturum.mask(req.system_layer2),
            "user_content": oturum.mask(req.user_content),
        }
        # ZK2: harita çağrıdan ÖNCE kalıcılaşır. Bu sıra bağlayıcıdır.
        with db.tx() as conn:
            pii_map.save(conn, step_id, oturum.to_payload())

        raw = self._transport.send(
            model=model, effort=effort, max_tokens=req.max_tokens, **maskeli
        )
        raw = raw.model_copy(update={"text": oturum.unmask(raw.text)})

        # Maskesi çözülen yanıt elimizde: harita artık gereksiz, silinir.
        with db.tx() as conn:
            pii_map.clear(conn, step_id)
        ...
```

Maskeleme sırası **kritiktir**: `system_layer1` önce maskelenir ki yer tutucu numaraları Katman 3'ün içeriğinden bağımsız kalsın (§6.2 önbellek öneki).

- [ ] **Adım 6: Olay kaydını kapat**

`step.py` içindeki `error_payload`:

```python
def error_payload(error_class: str, error: str) -> dict:
    """Olay kaydı SİLİNEMEZ (Kural 5): giden metin hem kırpılır hem maskelenir."""
    return {
        "error_class": error_class,
        "error_summary": masking.scrub(error[:ERROR_SUMMARY_LIMIT]),
    }
```

`runner.py` içinde profil yüklenirken varsayılan maskeleyiciyi kur:

```python
def build_masker() -> masking.Masker:
    """OTOMASYON_PROFILE tanımlıysa profilin politikası, değilse varsayılan.

    Varsayılan AÇIKTIR (KK5): profili unutulmuş bir kurulum maskelemesiz
    çalışmaz.
    """
    yol = os.environ.get("OTOMASYON_PROFILE")
    if not yol:
        return masking.default_masker()
    pr = load_profile(Path(yol))
    return masking.from_policy(pr.masking.enabled, pr.masking.patterns)
```

ve `main()` içinde `masking.set_default(build_masker())` çağır, `build_gateway()`
maskeleyiciyi `Gateway(...)`'e geçirsin.

- [ ] **Adım 7: Uçtan uca sızıntı testini yaz**

`tests/gateway/test_masking.py` sonuna:

```python
def test_agdan_cikan_promptta_PII_YOK(kayit_eden_gateway):
    """Ağ geçidi tek çıkış kapısıdır: taşımaya ulaşan metin maskeli olmalı."""
    gw, kayit = kayit_eden_gateway
    ...  # gw.complete(LLMRequest(user_content=f"TCKN {TCKN}"), run_id, step_id)
    assert TCKN not in kayit["user_content"]
    assert "[TCKN_1]" in kayit["user_content"]


def test_yanit_maskesi_cozulmus_doner(kayit_eden_gateway):
    """Model [TCKN_1] döndürürse çağıran GERÇEK değeri görmeli."""
    ...


def test_harita_CAGRIDAN_ONCE_commit_edilir(db, adim_id, olen_gateway):
    """ZK2: taşıma katmanı patlarsa bile harita veritabanında olmalı."""
    from kernel.state import pii_map
    with pytest.raises(RuntimeError):
        olen_gateway.complete(istek_tckn(), run_id, adim_id)
    with db.tx() as conn:
        ham = conn.execute(
            "SELECT pii_map FROM steps WHERE id = %s", (adim_id,)
        ).fetchone()[0]
        assert ham is not None, "harita çağrıdan ÖNCE yazılmalıydı"
        assert TCKN.encode() not in bytes(ham), "kolon düz metin PII taşıyor"
        assert pii_map.load(conn, adim_id) == {"[TCKN_1]": TCKN}


def test_basarili_cagrida_harita_silinir(db, adim_id, kayit_eden_gateway):
    gw, _ = kayit_eden_gateway
    gw.complete(istek_tckn(), run_id, adim_id)
    with db.tx() as conn:
        assert conn.execute(
            "SELECT pii_map FROM steps WHERE id = %s", (adim_id,)
        ).fetchone()[0] is None


def test_suresi_dolmus_harita_supurulur(db, adim_id):
    from kernel.state import pii_map
    with db.tx() as conn:
        pii_map.save(conn, adim_id, {"[TCKN_1]": TCKN})
        conn.execute("UPDATE steps SET pii_map_expires_at = now() -"
                     " interval '1 s' WHERE id = %s", (adim_id,))
        assert pii_map.sweep(conn) == 1
        assert pii_map.load(conn, adim_id) == {}


def test_anahtar_yoksa_PATLAR(db, adim_id, monkeypatch):
    """Sessizce şifresiz yazmaktansa çalışmamak (K14 felsefesi)."""
    from kernel.state import pii_map
    monkeypatch.delenv("OTOMASYON_PII_KEY", raising=False)
    with db.tx() as conn, pytest.raises(RuntimeError, match="OTOMASYON_PII_KEY"):
        pii_map.save(conn, adim_id, {"[TCKN_1]": TCKN})


def test_olay_kaydinda_ham_PII_yok(db, run_id):
    """Kural 5: bu satır asla silinemez."""
    from kernel.orchestrator import step
    yuk = step.error_payload("ToolFailed", f"müşteri {TCKN} bulunamadı")
    assert TCKN not in yuk["error_summary"]
```

`kayit_eden_gateway` fixture'ı: `send()` çağrısının argümanlarını bir sözlüğe yazan sahte bir taşıma katmanı + `Gateway`. Mevcut `tests/fakes.py` desenini izleyin.

- [ ] **Adım 8: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: 137 passed.

```bash
git add packages/kernel/gateway/masking.py packages/kernel/gateway/gateway.py \
        packages/kernel/orchestrator tests/gateway
git commit -m "feat(gateway): tek noktalı PII maskeleme (§6.5) — prompt ve olay kaydı"
```

---

### Görev 8: Derlenmiş grafın yürütülmesi

M1'in `flow.py`'sindeki sabit iki düğümlü akış siliniyor. Orkestratör artık `workflows` tablosundan yüklenen derlenmiş grafı yürütüyor, girdileri tipli kanaldan topluyor ve idempotency anahtarını konfigden çözüyor (KK4).

**Dosyalar:**
- Oluştur: `packages/kernel/state/workflows.py`
- Oluştur: `packages/kernel/state/artifacts.py`
- Oluştur: `packages/kernel/orchestrator/channel.py`
- Sil: `packages/kernel/orchestrator/flow.py`
- Değiştir: `packages/kernel/orchestrator/step.py`, `runner.py`
- Değiştir: `packages/kernel/tools/base.py` (`idempotency_key` kaldırılır), `tools/execution.py`
- Test: `tests/state/test_workflows.py`, `tests/state/test_artifacts.py`, `tests/orchestrator/test_channel.py`, `tests/orchestrator/test_runner.py` (güncelleme)

**Arayüzler:**
- Üretir: `RuntimeGraph(entry, nodes, children)`, `workflows.register(conn, tenant_id, derlenmis) -> UUID`, `workflows.load(conn, workflow_id) -> RuntimeGraph`, `artifacts.put(conn, tenant_id, run_id, data, media_type) -> ArtifactRef`, `artifacts.get(ref) -> bytes`, `channel.collect_inputs(conn, run_id, node) -> dict`, `channel.resolve_path(conn, run_id, path) -> object`, `channel.idempotency_key(conn, run_id, node) -> str`
- Değişen: `start_run(tenant_id, workflow_id, payload, budget_usd, idempotency_key=None)`, `execute_step(job, gateway, graph)`, `execute_tool(tool, run_id, node_id, step_id, payload, idempotency_key, lease_seconds)`

- [ ] **Adım 1: `Tool` sözleşmesinden `idempotency_key`'i kaldır (KK4)**

`tools/base.py` içinden `idempotency_key` soyut metodunu sil ve sınıf docstring'ine ekle:

```python
"""...
Idempotency anahtarı ARAÇTAN gelmez, KONFİGDEN gelir (KK4): YAML'daki
`idempotency: ["{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]` listesi derleme anında
doğrulanır ve çalışma zamanında çözülür. Anahtar araç kodunda gizliyken
derleyici §4.1(1)'i uygulayamıyordu.
"""
```

`tools/execution.py` içinde `execute_tool` imzasını değiştir: `tool.idempotency_key(...)`
çağrısı yerine dışarıdan gelen `idempotency_key: str` parametresini kullan.
`tests/fakes.py` içindeki araçlardan metodu kaldır.

- [ ] **Adım 2: Başarısız kanal testini yaz**

`tests/orchestrator/test_channel.py`:

```python
import pytest

from kernel.orchestrator import channel


def test_beyan_edilen_girdi_uretici_adimdan_okunur(db, tamamlanmis_akis):
    run_id, node = tamamlanmis_akis
    girdi = channel.collect_inputs_from(run_id, node)
    assert girdi == {"oku.belgeler": [{"uri": "file://a", "media_type": "application/pdf",
                                       "bytes": 10, "sha256": "x"}]}


def test_beyan_EDILMEYEN_alan_calisma_zamani_hatasi(db, tamamlanmis_akis):
    """Kural 3: sessizce None dönmez, HATA verir."""
    run_id, node = tamamlanmis_akis
    with pytest.raises(KeyError, match="beyan edilmemiş"):
        channel.resolve_path(run_id, "oku.gizli_alan")


def test_derin_yol_cozulur(db, faturali_akis):
    run_id, _ = faturali_akis
    assert channel.resolve_path(run_id, "cikar.fatura.fatura_no") == "F-2026-001"


def test_idempotency_anahtari_konfigden_cozulur(db, faturali_akis):
    """KK4 + K15: anahtar yeniden denemede DEĞİŞMEMELİ."""
    run_id, node = faturali_akis
    a = channel.idempotency_key(run_id, node)
    b = channel.idempotency_key(run_id, node)
    assert a == b
    assert "F-2026-001" in a and str(run_id) in a and node.id in a


def test_ayni_arac_iki_dugumde_FARKLI_anahtar(db, iki_yazma_akisi):
    """Ö5: node_id anahtarın parçası; ikinci düğüm birincinin rezervasyonunu
    bulup yan etkiyi SESSİZCE atlamamalı."""
    run_id, a, b = iki_yazma_akisi
    assert channel.idempotency_key(run_id, a) != channel.idempotency_key(run_id, b)
```

- [ ] **Adım 3: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/orchestrator/test_channel.py -v`
Beklenen: FAIL — `No module named 'kernel.orchestrator.channel'`

- [ ] **Adım 4: `channel.py` yaz**

```python
"""Tipli kanal: bir düğümün BEYAN ETTİĞİ girdilerin toplanması (Kural 2, 3).

Bağlam izolasyonunun çalışma zamanı yarısı burada. Düğüme verilen sözlük
YALNIZ `inputs`'ta beyan edilen yolları içerir; beyan edilmeyen bir alan
sözlükte HİÇ YOKTUR, dolayısıyla erişim denemesi KeyError'dır. "Erişimi
engelleme" mekanizması bir kontrol değil, verinin orada olmamasıdır — en
ucuz ve en güvenilir biçim.

Bu kural aynı zamanda §6.2'nin önbellek verimliliğinin kaynağıdır: Katman 3
küçük kaldığı için önbellek öneki kaymaz.
"""
from __future__ import annotations

import hashlib
from uuid import UUID

from kernel.state import db

YERLESIK = {"run_id", "node_id", "tenant_id"}


def resolve_path(run_id: UUID, path: str) -> object:
    """'cikar.fatura.tutar' → üretici adımın çıktısındaki değer.

    Bulunamayan her şey HATADIR: derleyici bu yolu doğruladıysa çalışma
    zamanında olmaması bir tutarsızlıktır ve sessizce None dönmek Kural 3'ü
    delerdi.
    """
    parcalar = path.split(".")
    kaynak, kalan = parcalar[0], parcalar[1:]
    with db.tx() as conn:
        row = conn.execute(
            "SELECT output FROM steps WHERE run_id = %s AND node_id = %s"
            " AND status = 'completed' ORDER BY attempt DESC LIMIT 1",
            (run_id, kaynak),
        ).fetchone()
    if row is None or row[0] is None:
        raise KeyError(f"{path}: {kaynak} düğümünün tamamlanmış çıktısı yok")
    deger: object = row[0]
    gezilen = kaynak
    for p in kalan:
        if not isinstance(deger, dict) or p not in deger:
            raise KeyError(f"{path}: {gezilen} içinde beyan edilmemiş alan {p!r}")
        deger = deger[p]
        gezilen = f"{gezilen}.{p}"
    return deger


def collect_inputs_from(run_id: UUID, node) -> dict:
    """Düğümün göreceği TÜM veri. Beyan edilmeyen hiçbir şey burada yok."""
    return {yol: resolve_path(run_id, yol) for yol in node.inputs}


def idempotency_key(run_id: UUID, node, tenant_id: str = "") -> str:
    """YAML'daki `idempotency` listesinden anahtar üretir (KK4).

    Anahtar yeniden denemede DEĞİŞMEMELİDİR (K15): bu yüzden step_id, attempt
    ya da zaman damgası anahtara GİRMEZ — yalnız run kimliği, düğüm kimliği ve
    iş verisinden gelen sabit alanlar girer.
    """
    parcalar: list[str] = []
    for p in node.idempotency:
        # ZK3: derleyiciyle AYNI ayrıştırıcı. İki ayrı yorumcu, doğrulanan
        # anahtarla üretilen anahtarın sessizce farklılaşması demektir.
        ref = expr.parse_part(p)
        if ref is None:
            parcalar.append(p)                       # düz metin sabit
        elif ref == "run.id":
            parcalar.append(str(run_id))
        elif ref == "node.id":
            parcalar.append(node.id)
        elif ref == "tenant.id":
            parcalar.append(tenant_id)
        else:
            parcalar.append(str(resolve_path(run_id, ref)))
    ham = "|".join(parcalar)
    # Anahtar hem okunabilir hem sınırlı uzunlukta olmalı: teşhis için düğüm
    # adı önde, çarpışma güvencesi için sonda hash.
    return f"{node.id}:{ham}:{hashlib.sha256(ham.encode('utf-8')).hexdigest()[:16]}"
```

- [ ] **Adım 5: `workflows.py` yaz**

```python
"""Derlenmiş grafın kalıcılığı ve run'a sabitlenmesi (§5).

`workflows` satırı içerik hash'iyle tekildir: aynı YAML iki kez kaydedilirse
aynı satır kullanılır. Run başlarken `workflow_id` yazılır ve bir daha
değişmez — müşteri çalışan bir işin ortasında YAML'ı düzenlerse, o iş ESKİ
tanımla biter. Dayanıklı iş akışı motorlarının ayrı özellik olarak sattığı
"workflow versioning", içerik hash'i + sabitleme ile ek altyapı olmadan çözülür.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from kernel.compiler.schema import WorkflowSpec


class RuntimeGraph:
    """Çalışma zamanı grafı: derlenmiş artefaktın yeniden doğrulanmış hâli."""

    def __init__(self, spec: WorkflowSpec, entry: str, children: dict[str, list[str]]):
        self.spec = spec
        self.entry = entry
        self.children = children
        self.nodes = {n.id: n for n in spec.graph}

    @classmethod
    def from_compiled(cls, compiled: dict) -> RuntimeGraph:
        # Şemadan yeniden doğrulanır: veritabanındaki artefakt bozuksa run
        # başlamadan patlar, adım ortasında değil.
        return cls(
            spec=WorkflowSpec.model_validate(compiled["spec"]),
            entry=compiled["entry"],
            children=compiled["children"],
        )

    def next_of(self, node_id: str) -> str | None:
        """Dallanmayan düğümün tek ardılı (KK2). Yoksa None = akış biter."""
        cocuklar = self.children.get(node_id, [])
        return cocuklar[0] if cocuklar else None


def register(conn: psycopg.Connection, tenant_id: str, derlenmis) -> UUID:
    """Derlenmiş grafı kaydeder; aynı hash varsa mevcut kimliği döner."""
    row = conn.execute(
        "SELECT id FROM workflows WHERE tenant_id = %s AND name = %s"
        " AND version_hash = %s",
        (tenant_id, derlenmis.name, derlenmis.version_hash),
    ).fetchone()
    if row is not None:
        return row[0]
    wf_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO workflows (id, tenant_id, name, version_hash, yaml_source, compiled)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            wf_id, tenant_id, derlenmis.name, derlenmis.version_hash,
            derlenmis.source, Jsonb(derlenmis.to_compiled_json()),
        ),
    )
    return wf_id


def load(conn: psycopg.Connection, workflow_id: UUID) -> RuntimeGraph:
    row = conn.execute(
        "SELECT compiled FROM workflows WHERE id = %s", (workflow_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"workflow bulunamadı: {workflow_id}")
    return RuntimeGraph.from_compiled(row[0])


def load_for_run(conn: psycopg.Connection, run_id: UUID) -> RuntimeGraph:
    """Run'ın SABİTLENDİĞİ versiyonu yükler — dosyadan değil, satırdan."""
    row = conn.execute(
        "SELECT w.compiled FROM runs r JOIN workflows w ON w.id = r.workflow_id"
        " WHERE r.id = %s",
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"run'ın workflow'u yok: {run_id}")
    return RuntimeGraph.from_compiled(row[0])
```

- [ ] **Adım 6: `artifacts.py` yaz**

```python
"""Yerel dosya sistemi nesne deposu (K3: yönetilen servis yasak).

Büyük içerik grafta dolaşmaz; `ArtifactRef` dolaşır (§4.1/4). Depo dizini
OTOMASYON_ARTIFACT_DIR ile verilir. SaaS profilinde S3 uyumlu bir sürücü
aynı arayüzün arkasına takılır — M6 paketlemesinin işi.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from uuid import UUID

import psycopg

from kernel.types import ArtifactRef


def kok() -> Path:
    yol = os.environ.get("OTOMASYON_ARTIFACT_DIR")
    if not yol:
        raise RuntimeError("OTOMASYON_ARTIFACT_DIR tanımlı değil")
    p = Path(yol)
    p.mkdir(parents=True, exist_ok=True)
    return p


def put(
    conn: psycopg.Connection,
    tenant_id: str,
    run_id: UUID | None,
    data: bytes,
    media_type: str,
) -> ArtifactRef:
    sha = hashlib.sha256(data).hexdigest()
    ad = f"{uuid.uuid4()}.bin"
    (kok() / ad).write_bytes(data)
    ref = ArtifactRef(uri=f"file://{ad}", media_type=media_type, bytes=len(data), sha256=sha)
    conn.execute(
        "INSERT INTO artifacts (id, tenant_id, run_id, uri, media_type, bytes, sha256)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (uuid.uuid4(), tenant_id, run_id, ref.uri, media_type, ref.bytes, sha),
    )
    return ref


def get(ref: ArtifactRef) -> bytes:
    data = (kok() / ref.uri.removeprefix("file://")).read_bytes()
    if hashlib.sha256(data).hexdigest() != ref.sha256:
        raise ValueError(f"artifact bütünlüğü bozuk: {ref.uri}")
    return data
```

- [ ] **Adım 7: `step.py` ve `runner.py`'yi derlenmiş grafa geçir**

- `flow` import'unu sil, `execute_step(job, gateway, graph: RuntimeGraph)` imzasını kullan.
- `node = graph.nodes[job.node_id]`.
- `_advance(job, node, graph)` içinde `node.next` yerine `graph.next_of(node.id)`.
- `_run_tool` içinde: `payload = channel.collect_inputs_from(job.run_id, node)` (giriş düğümünde `inputs` boşsa `runs.input`), `anahtar = channel.idempotency_key(job.run_id, node, tenant_id)`, ve `execution.execute_tool(..., idempotency_key=anahtar, ...)`.
- Adım çıktısı düğümün beyan ettiği ada yazılır: tek çıktı beyanı varsa `{ad: sonuc}`.
- `runner.run_once` işi aldıktan sonra `graph = workflows.load_for_run(conn, job.run_id)` yükler ve `execute_step`'e geçirir. **Graf dosyadan değil satırdan gelir** — YAML'ın run ortasında değişmesi işi etkilemez.
- `start_run(tenant_id, workflow_id, payload, ...)`: `workflow_id`'yi ve grafın adını/hash'ini `runs` satırına yazar, `max_steps`'i `limits.max_steps`'ten, `budget_usd`'yi çağırandan alır ve `graph.entry` düğümünü kuyruğa koyar.
- `flow.py` silinir; `M1_FLOW`'a bağlı testler derlenmiş graf fixture'ına taşınır.

- [ ] **Adım 8: Sabitleme testini yaz**

`tests/state/test_workflows.py`:

```python
def test_ayni_hash_ayni_satir(db, derlenmis):
    with db.tx() as conn:
        a = workflows.register(conn, "acme", derlenmis)
        b = workflows.register(conn, "acme", derlenmis)
    assert a == b


def test_run_SABITLENDIGI_versiyonla_biter(db, derlenmis, derlenmis_v2):
    """§5 / §7 Katman 4: YAML çalışma ortasında değişirse run eski tanımla biter."""
    with db.tx() as conn:
        v1 = workflows.register(conn, "acme", derlenmis)
    run_id = runner.start_run("acme", v1, {"text": "x"})
    with db.tx() as conn:
        workflows.register(conn, "acme", derlenmis_v2)   # yeni versiyon yayımlandı
        g = workflows.load_for_run(conn, run_id)
    assert g.spec.model_dump() == derlenmis.graph.spec.model_dump()
```

- [ ] **Adım 9: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: tüm testler geçer (M1 testlerinin derlenmiş graf fixture'ına taşınmış hâli dahil). Tekel testi hâlâ yeşil olmalı: yeni `packages/kernel/**` dosyalarında "anthropic" dizgesi **yok**.

```bash
git add packages/kernel tests/
git rm packages/kernel/orchestrator/flow.py
git commit -m "feat(orchestrator): derlenmiş grafın yürütülmesi, tipli kanal, workflow sabitleme"
```

---

### Görev 9: Yapılandırılmış çıktı, `router` ve `switch` düğümleri

Bu görev grafı gerçekten *dallandırır*. `router` olasılıksaldır ve kaçış kapısı zorunludur; `switch` deterministiktir ve LLM görmez. İkisi bilerek ayrı tutulur çünkü farklı şeyler garanti ederler (§3.1).

Yapılandırılmış çıktı burada gelir: §6.3'e göre şema dışı yanıt boşa giden tam bir turdur ve bu **bedava** bir maliyet kaldıracıdır. Aynı zamanda Kural 2'nin API karşılığıdır — tipli kanalın sözleşmesi ancak modelden tipli çıktı istenirse tutar.

**Dosyalar:**
- Değiştir: `packages/kernel/gateway/types.py` (`LLMRequest.output_schema`)
- Değiştir: `packages/kernel/gateway/transport.py` (yapılandırılmış çıktı + düşünme özeti)
- Oluştur: `packages/kernel/orchestrator/nodes.py`
- Değiştir: `packages/kernel/orchestrator/step.py` (düğüm tipine göre dağıtım)
- Test: `tests/orchestrator/test_nodes.py`, `tests/gateway/test_transport.py` (ekleme)

**Arayüzler:**
- Üretir: `nodes.run_router(...) -> RouteChoice`, `nodes.eval_switch(...) -> str | None`, `nodes.schema_for_outputs(node, tipler) -> dict | None`
- Değişen: `Transport.send(..., output_schema: dict | None = None)`, `RawResponse.thinking_summary: str | None`

- [ ] **Adım 1: Yapılandırılmış çıktıyı `types.py`'ye ekle**

```python
class LLMRequest(BaseModel):
    ...
    output_schema: dict | None = None
    """§6.3: şema dışı yanıt boşa giden tam bir turdur. Şema verilirse
    `output_config.format` + `strict: true` ile gönderilir; yanıt JSON'dur."""


class RawResponse(BaseModel):
    ...
    thinking_summary: str | None = None
    """§6.5: router düğümlerinde seçim gerekçesi denetim izine yazılır.
    "Bu fatura neden onaya gitti" sorusunun cevabı budur."""
```

- [ ] **Adım 2: Taşıma katmanını genişlet — CANLI ŞEKİL DOĞRULAMASI ZORUNLU**

`AnthropicTransport.send`'e `output_schema` ve düşünme özeti eklenecek. **Önce
kurulu SDK'daki parametre adını doğrula**, sonra yaz:

```bash
.venv/bin/python -c "import anthropic, inspect; print(anthropic.__version__)"
.venv/bin/python -c "import anthropic; print([p for p in __import__('inspect').signature(anthropic.Anthropic().beta.messages.create).parameters])"
```

Spec §6.3 `output_config.format` + `strict: true` adlandırmasını kullanıyor;
`display: \"summarized\"` düşünme özeti için §6.5'te geçiyor. Kurulu SDK farklı
bir ad kullanıyorsa **SDK'nınki kazanır** ve sapma rapor dosyasına yazılır.
Hiçbir test canlı şekle bağlanmaz: `AnthropicTransport` testleri sahte istemciyle
gönderilen sözlüğü denetler, `OfflineTransport` ise şemaya uyan sabit JSON döner.

Yasaklar değişmedi: `thinking` **asla** `disabled` gönderilmez, `budget_tokens`
**hiç** gönderilmez, model kimliği yalnız `tiers.py`'den gelir.

`OfflineTransport.send`: `output_schema` verilirse şemanın `required` alanlarını
tip-uygun sabit değerlerle dolduran bir JSON metni döner; verilmezse mevcut
davranışını korur.

- [ ] **Adım 3: Başarısız düğüm testlerini yaz**

`tests/orchestrator/test_nodes.py`:

```python
import pytest

from kernel.compiler.loader import parse_workflow
from kernel.orchestrator import nodes


# --- switch: deterministik, LLM YOK ---

@pytest.mark.parametrize(
    "deger,beklenen",
    [(15000, "onay_al"), (10000, "erp_yaz"), (9999.5, "erp_yaz")],
)
def test_switch_esikleri_deterministik(esik_dugumu, deger, beklenen):
    assert nodes.eval_switch(esik_dugumu, deger) == beklenen


def test_switch_metin_esitligi(metin_switch):
    assert nodes.eval_switch(metin_switch, "fatura") == "cikar"
    assert nodes.eval_switch(metin_switch, "baska") == "son"


def test_switch_LLM_CAGIRMAZ(esik_dugumu, patlayan_gateway):
    """Determinizm iddiası: switch yolunda ağ geçidi hiç dokunulmamalı."""
    nodes.eval_switch(esik_dugumu, 1)
    assert patlayan_gateway.cagri_sayisi == 0


# --- router: olasılıksal, kaçış kapısı zorunlu ---

def test_router_beyan_edilen_kumeden_secer(router_dugumu, sahte_gateway):
    sahte_gateway.yanit = '{"route": "fatura", "confidence": 0.93, "reason": "KDV satırı var"}'
    secim = nodes.run_router(router_dugumu, {}, sahte_gateway, run_id=None, step_id=None)
    assert secim.route == "fatura"
    assert secim.target == "cikar"


def test_router_dusuk_guvende_KACIS_KAPISINA_gider(router_dugumu, sahte_gateway):
    """§4.1(2): model emin değilken bir kutuya girmeye zorlanırsa uydurur."""
    sahte_gateway.yanit = '{"route": "fatura", "confidence": 0.40, "reason": "emin degilim"}'
    secim = nodes.run_router(router_dugumu, {}, sahte_gateway, run_id=None, step_id=None)
    assert secim.route == "belirsiz"
    assert secim.target == "insan_kuyrugu"
    assert secim.low_confidence is True


def test_router_KUME_DISI_rota_kacis_kapisina_dusurulur(router_dugumu, sahte_gateway):
    """Model beyan edilmemiş bir rota uydurursa akış sapmaz, insana gider."""
    sahte_gateway.yanit = '{"route": "uydurma", "confidence": 0.99, "reason": "x"}'
    secim = nodes.run_router(router_dugumu, {}, sahte_gateway, run_id=None, step_id=None)
    assert secim.target == "insan_kuyrugu"


def test_router_bozuk_json_kacis_kapisina_duser(router_dugumu, sahte_gateway):
    sahte_gateway.yanit = "bu json degil"
    secim = nodes.run_router(router_dugumu, {}, sahte_gateway, run_id=None, step_id=None)
    assert secim.target == "insan_kuyrugu"


def test_router_semayi_beyan_edilen_rotalarla_kisitlar(router_dugumu, sahte_gateway):
    sahte_gateway.yanit = '{"route": "fatura", "confidence": 0.9, "reason": "x"}'
    nodes.run_router(router_dugumu, {}, sahte_gateway, run_id=None, step_id=None)
    sema = sahte_gateway.son_istek.output_schema
    assert set(sema["properties"]["route"]["enum"]) == {"fatura", "dekont", "belirsiz"}
    assert sema["required"] == ["route", "confidence", "reason"]
```

- [ ] **Adım 4: Testi koş, düştüğünü gör**

Koş: `.venv/bin/python -m pytest tests/orchestrator/test_nodes.py -v`
Beklenen: FAIL — `No module named 'kernel.orchestrator.nodes'`

- [ ] **Adım 5: `nodes.py` yaz**

```python
"""`router` ve `switch` düğüm çalışma zamanları.

İkisi bilerek ayrıdır (§3.1): `switch` deterministiktir ve test gerektirmez;
`router` olasılıksaldır, her kenarı için altın veri seti gerektirir ve KAÇIŞ
KAPISI zorunludur.

Router'ın üç ayrı düşüş yolu vardır ve ÜÇÜ DE aynı yere, insan kuyruğuna
gider: düşük güven, beyan edilmemiş rota, ayrıştırılamayan yanıt. Gerekçe
§4.1(2) ile aynı — "emin değilim"in maliyeti bir insan kuyruğu satırı, yanlış
kutunun maliyeti yanlış bir ERP kaydıdır.
"""
from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel

from kernel.compiler.validate_graph import parse_case
from kernel.gateway.types import LLMRequest

ROUTER_LAYER1 = (
    "Sen kurumsal bir belge işleme otomasyonunun yönlendirme adımısın. "
    "Girdiyi verilen rotalardan TAM OLARAK BİRİNE ata. Emin değilsen düşük "
    "güven puanı ver; uydurma."
)


class RouteChoice(BaseModel):
    route: str
    target: str | None
    confidence: float
    reason: str
    low_confidence: bool = False


def schema_for_router(node) -> dict:
    """Model YALNIZ beyan edilmiş rotalardan seçebilir (§3.1).

    Şema kiracıdan bağımsızdır: rotalar workflow tanımından gelir, kiracı
    konfigünden değil — K16 ihlali değildir.
    """
    return {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": sorted(node.routes)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
        },
        "required": ["route", "confidence", "reason"],
        "additionalProperties": False,
    }


def run_router(node, girdi: dict, gateway, run_id: UUID, step_id: UUID) -> RouteChoice:
    esik = node.on_low_confidence.threshold
    kacis_rota = node.on_low_confidence.route
    kacis_hedef = node.routes[kacis_rota]

    yanit = gateway.complete(
        LLMRequest(
            tier=node.model_tier or "fast",
            system_layer1=ROUTER_LAYER1,
            system_layer2="",
            user_content=json.dumps(girdi, ensure_ascii=False, default=str),
            output_schema=schema_for_router(node),
        ),
        run_id,
        step_id,
    )

    try:
        ham = json.loads(yanit.text)
        rota = str(ham["route"])
        guven = float(ham["confidence"])
        gerekce = str(ham.get("reason", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return RouteChoice(
            route=kacis_rota, target=kacis_hedef, confidence=0.0,
            reason="yanıt ayrıştırılamadı", low_confidence=True,
        )

    if rota not in node.routes:
        return RouteChoice(
            route=kacis_rota, target=kacis_hedef, confidence=guven,
            reason=f"beyan edilmemiş rota önerildi: {rota!r}", low_confidence=True,
        )
    if guven < esik:
        return RouteChoice(
            route=kacis_rota, target=kacis_hedef, confidence=guven,
            reason=gerekce, low_confidence=True,
        )
    return RouteChoice(
        route=rota, target=node.routes[rota], confidence=guven, reason=gerekce
    )


def eval_switch(node, deger) -> str | None:
    """Deterministik dal seçimi. LLM YOK, ağ YOK, maliyet YOK.

    Sıra beyan sırasıdır; ilk eşleşen kazanır, hiçbiri eşleşmezse `default`.
    Derleyici `default`'un varlığını garanti eder (E_SWITCH_DEFAULT).
    """
    for anahtar, hedef in node.cases.items():
        if anahtar == "default":
            continue
        op, esik = parse_case(anahtar)
        if _karsilastir(deger, op, esik):
            return hedef
    return node.cases["default"]


def _karsilastir(deger, op: str, esik) -> bool:
    if isinstance(esik, float):
        try:
            deger = float(deger)
        except (TypeError, ValueError):
            return False
    else:
        deger = str(deger)
    return {
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    }[op](deger, esik)
```

- [ ] **Adım 6: `step.py`'de dağıtımı genişlet**

`execute_step` içindeki `if node.type == "llm_task": ... return _run_tool(...)`
dağıtımını beş dala çıkar:

```python
    if node.type == "llm_task":
        return _run_llm_task(job, node, step_id, girdi, tenant_id, gateway, graph)
    if node.type == "router":
        return _run_router(job, node, step_id, girdi, gateway, graph)
    if node.type == "switch":
        return _run_switch(job, node, step_id, graph)
    if node.type == "human_approval":
        return _run_human_approval(job, node, step_id, girdi)   # Görev 10
    return _run_tool(job, node, step_id, girdi, tenant_id, graph)
```

`_run_router` seçimi yaptıktan sonra **gerekçeyi denetim izine yazar** (§6.5):

```python
    events.append(
        conn, job.run_id, "router_decision",
        {
            "node_id": node.id,
            "route": secim.route,
            "confidence": secim.confidence,
            # Olay kaydı SİLİNEMEZ (Kural 5): gerekçe maskelenir ve kırpılır.
            "reason": masking.scrub(secim.reason[:ERROR_SUMMARY_LIMIT]),
            "low_confidence": secim.low_confidence,
        },
    )
```

`_advance` dallanan düğümlerde `graph.next_of()` yerine seçilen hedefi kullanır:
`_advance(job, node, graph, hedef=secim.target)`. `hedef is None` ise run
tamamlanır — router'ın `null` rotası ve switch'in `null` case'i akışı bitirir.

`_run_switch` girdiyi `channel.resolve_path(run_id, node.on)` ile okur, LLM'e
hiç dokunmaz, ve `switch_decision` olayı yazar.

- [ ] **Adım 7: Testleri koş**

Koş: `.venv/bin/python -m pytest tests/orchestrator tests/gateway -v`
Beklenen: tümü PASS

- [ ] **Adım 8: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: tümü yeşil. Tekel testini özellikle kontrol et — `nodes.py` ve
`transport.py` değişiklikleri "anthropic" dizgesini yalnız `transport.py` içinde
bırakmalı.

```bash
git add packages/kernel tests/
git commit -m "feat(orchestrator): yapılandırılmış çıktı, router ve switch düğümleri"
```

---

### Görev 10: `human_approval` askıya alma, referans akış ve M2 kabul kapısı

M2'nin kabul kriteri burada kanıtlanır: **her şema zorunluluğu için bir bozuk YAML reddedilir; referans akış derlenir; tavan maliyet raporlanır.** Üstüne referans akış gerçekten yürür ve onay kapısında park eder.

**Dosyalar:**
- Oluştur: `customers/acme/workflows/belge_girisi.yaml`, `customers/acme/types.py`, `customers/acme/profile.yaml`, `customers/acme/evals/.gitkeep`
- Oluştur: `tests/compiler/fixtures/bozuk/*.yaml` (zorunluluk başına bir dosya)
- Değiştir: `packages/kernel/orchestrator/step.py` (`_run_human_approval`)
- Oluştur: `tests/acceptance/test_m2_derleyici.py`
- Değiştir: `tests/compiler/test_compile.py` (skip işaretlerini kaldır)

**Arayüzler:**
- Üretir: `_run_human_approval` — run `awaiting_approval`, `approvals` satırı `pending`, iş kuyruktan çıkar

- [ ] **Adım 1: `customers/acme/types.py` yaz**

```python
"""Acme'nin iş tipleri. customers/ altındaki TEK .py dosyası (§9 sert kuralı).

Buraya mantık yazılmaz — sadece Pydantic modelleri. Mantık ya connectors/'a
ya kernel/'e aittir.
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from kernel.types import ArtifactRef


class BelgeListesi(BaseModel):
    belgeler: list[ArtifactRef]


class FaturaModeli(BaseModel):
    fatura_no: str
    tedarikci: str
    tutar: Decimal
    para_birimi: str
    fatura_tarihi: str


class YazmaSonucu(BaseModel):
    erp_belge_no: str
```

- [ ] **Adım 2: `customers/acme/profile.yaml` yaz**

```yaml
# Acme dağıtım profili. Sır YOK, yalnız REFERANS (K14).
# Kademe → model eşlemesi TEK YERDE burada durur; müşteri YAML'ına model
# kimliği gömülmez, aksi halde model değiştiğinde N müşteri konfigi elle
# güncellenir (§6.1).
tenant_id: acme

tiers:
  deep:     {model: claude-opus-5, effort: xhigh}
  standard: {model: claude-opus-5, effort: high}
  fast:     {model: claude-opus-5, effort: low}
  # bulk kademesi BİLEREK yok: eval kanıtı olmadan kilitli (K9).

budgets:
  daily_usd: 50.00

masking:
  enabled: true
  patterns: [eposta, iban, tckn, vkn, telefon]

connectors:
  erp:
    endpoint: https://sap.acme.internal
    username: "env: ACME_SAP_USER"
    password: "env: ACME_SAP_PASSWORD"
  eposta:
    host: mail.acme.com.tr
    username: "env: ACME_MAIL_USER"
    password: "env: ACME_MAIL_PASSWORD"
```

- [ ] **Adım 3: `customers/acme/workflows/belge_girisi.yaml` yaz**

```yaml
apiVersion: v1
name: belge_girisi
description: Gelen belgeyi sınıflandırır, faturayı çıkarır, eşik üstünü onaya gönderir, ERP'ye tek kez yazar.

trigger:
  type: http

limits:
  max_steps: 25
  max_usd_per_run: 3.00
  max_wallclock: 4h

defaults:
  model_tier: standard
  retry: {attempts: 3, backoff: exponential}
  max_tokens: 4000

graph:
  - id: oku
    type: tool
    tool: belge.oku
    idempotency: ["belge-oku", "{{ run.id }}"]   # ZK3: run-değişken zorunlu
    compensation: belge.oku_geri_al
    outputs: {belgeler: BelgeListesi}

  - id: siniflandir
    type: router
    depends_on: [oku]
    model_tier: fast
    inputs: [oku.belgeler]
    routes:
      fatura: cikar
      dekont: null            # akış burada biter
      alakasiz: null
      belirsiz: insan_kuyrugu # kaçış kapısı — ZORUNLU (§4.1/2)
    on_low_confidence: {threshold: 0.75, route: belirsiz}

  - id: cikar
    type: llm_task
    depends_on: [siniflandir]
    model_tier: standard
    inputs: [oku.belgeler]
    outputs: {fatura: FaturaModeli}

  - id: dogrula
    type: switch                # deterministik — LLM yok, maliyet yok
    depends_on: [cikar]
    on: cikar.fatura.tutar
    cases:
      "> 10000": onay_al
      default: erp_yaz

  - id: onay_al
    type: human_approval
    depends_on: [dogrula]
    assignee_role: muhasebe_muduru
    context_fields: [cikar.fatura]   # onaycıya SADECE bu gösterilir
    timeout: {after: 48h, route: insan_kuyrugu}

  - id: erp_yaz
    type: tool
    depends_on: [dogrula, onay_al]
    tool: erp.post_invoice
    inputs: [cikar.fatura]
    idempotency: ["{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]
    compensation: erp.void_invoice
    batchable: false            # K10: v1'de yok sayılır
    outputs: {sonuc: YazmaSonucu}

  - id: insan_kuyrugu
    type: human_approval
    depends_on: [siniflandir]
    assignee_role: muhasebe_muduru
    context_fields: [oku.belgeler]
```

**NOT — KK1/KK2 kontrolü.** `erp_yaz.depends_on: [dogrula, onay_al]` iki gelen
kenardır ve bu **serbesttir**: `dogrula` bir switch'tir, ya `onay_al`'ı ya
`erp_yaz`'ı seçer, ikisini birden asla değil. Tek etkin dal değişmezi korunur.
`oku`'nun tek çocuğu `siniflandir`'dır — örtük dallanma yok.

- [ ] **Adım 4: Bozuk YAML vakalarını yaz**

`tests/compiler/fixtures/bozuk/` altında, her biri referans akışın **tek bir
alanı bozulmuş** kopyası olacak biçimde:

| Dosya | Bozukluk | Beklenen kod |
|---|---|---|
| `idempotency_yok.yaml` | `erp_yaz`'dan `idempotency` silinmiş | `E_IDEMPOTENCY` |
| `telafi_yok.yaml` | `erp_yaz`'dan `compensation` silinmiş | `E_TELAFI` |
| `kacis_kapisi_yok.yaml` | `siniflandir`'dan `on_low_confidence` silinmiş | `E_KACIS_KAPISI` |
| `beyan_edilmemis_alan.yaml` | `cikar.inputs: [oku.gizli]` | `E_TIP_UYUMSUZ` |
| `gomulu_icerik.yaml` | `outputs: {belgeler: HamBelge}` (bytes alanlı) | `E_ARTIFACT` |
| `bulk_eval_yok.yaml` | `siniflandir.model_tier: bulk` | `E_BULK_EVAL` |
| `duz_metin_sir.yaml` | profil: `password: "Hunter2!"` | `E_SIR` |
| `dongu.yaml` | `oku.depends_on: [erp_yaz]` | `E_DONGU` |
| `ortulu_dallanma.yaml` | `oku`'ya ikinci bir çocuk | `E_ORTULU_DALLANMA` |
| `butce_asimi.yaml` | `max_usd_per_run: 0.001` | `E_BUTCE_TAVANI` |
| `adim_asimi.yaml` | `max_steps: 2` | `E_ADIM_TAVANI` |
| `bilinmeyen_arac.yaml` | `tool: erp.hayalet` | `E_BILINMEYEN_ARAC` |
| `statik_idempotency.yaml` | `idempotency: ["{{ node.id }}"]` | `E_STATIK_IDEMPOTENCY` |

Bozuk profil gerektiren vaka (`duz_metin_sir`) için `bozuk/profile_sirli.yaml`
ayrıca yazılır. `gomulu_icerik.yaml` için `bozuk/types_gomulu.py` **test
fixture'ı olarak** `tests/compiler/fixtures/` altına konur — `customers/`
altına DEĞİL (§9 sert kuralı ve Görev 4'teki CI testi bunu zaten kırar).

- [ ] **Adım 5: `_run_human_approval`'ı yaz (KK3)**

```python
def _run_human_approval(job, node, step_id: UUID, girdi: dict) -> str:
    """Akışı askıya alır. Hiçbir süreç ayakta kalmaz (§5).

    Run 'awaiting_approval' olur, approvals satırı 'pending' yazılır, iş
    kuyruktan çıkar. Üç gün beklemenin maliyeti bir veritabanı satırıdır.
    Onayın İŞLENMESİ (karar, zaman aşımı yönlendirmesi, rol yetkilendirmesi)
    M3'ün işidir; M2 yalnız askıya alır (KK3).
    """
    with db.tx() as conn:
        if not queue.complete(conn, job.id, job.worker_id):
            return "lease_lost"
        conn.execute(
            "UPDATE steps SET status = 'completed', ended_at = now() WHERE id = %s",
            (step_id,),
        )
        conn.execute(
            "INSERT INTO approvals (id, run_id, node_id, status, assignee_role, context)"
            " VALUES (%s, %s, %s, 'pending', %s, %s)"
            " ON CONFLICT (run_id, node_id) DO NOTHING",
            (uuid4(), job.run_id, node.id, node.assignee_role,
             Jsonb(_onay_baglami(job.run_id, node))),
        )
        conn.execute(
            "UPDATE runs SET status = 'awaiting_approval', updated_at = now()"
            " WHERE id = %s",
            (job.run_id,),
        )
        events.append(conn, job.run_id, "approval_requested",
                      {"node_id": node.id, "assignee_role": node.assignee_role})
    return "awaiting_approval"


def _onay_baglami(run_id: UUID, node) -> dict:
    """Onaycıya SADECE context_fields gösterilir (§4 YAML sözleşmesi).

    Bağlam izolasyonu insan arayüzünde de geçerlidir: onaycı, kararı için
    beyan edilmemiş hiçbir veriyi görmez.
    """
    return {yol: channel.resolve_path(run_id, yol) for yol in node.context_fields}
```

Sahiplik koruması **ilk ifadedir** — M1'in `_advance`/`_fail_run`/DEFERRED
dallarındaki kalıbın birebir aynısı. Kirasını kaybetmiş bir worker onay satırı
açmamalı ve run durumunu yazmamalıdır.

- [ ] **Adım 6: Kabul testini yaz**

`tests/acceptance/test_m2_derleyici.py`:

```python
"""M2 KABUL KAPISI (spec §11).

Üç iddia:
  1. Her şema zorunluluğu için bir bozuk YAML REDDEDİLİR — doğru kodla.
  2. Referans akış DERLENİR.
  3. Tavan maliyet RAPORLANIR.
Dördüncüsü M2'nin kendi eklediği kanıt: derlenen akış gerçekten yürür ve
onay kapısında park eder.
"""
from pathlib import Path

import pytest

import kernel.compiler as K
from kernel.compiler.errors import CompileFailed

BOZUK = Path(__file__).parent.parent / "compiler" / "fixtures" / "bozuk"

VAKALAR = [
    ("idempotency_yok.yaml", "E_IDEMPOTENCY"),
    ("telafi_yok.yaml", "E_TELAFI"),
    ("kacis_kapisi_yok.yaml", "E_KACIS_KAPISI"),
    ("beyan_edilmemis_alan.yaml", "E_TIP_UYUMSUZ"),
    ("gomulu_icerik.yaml", "E_ARTIFACT"),
    ("bulk_eval_yok.yaml", "E_BULK_EVAL"),
    ("dongu.yaml", "E_DONGU"),
    ("ortulu_dallanma.yaml", "E_ORTULU_DALLANMA"),
    ("butce_asimi.yaml", "E_BUTCE_TAVANI"),
    ("adim_asimi.yaml", "E_ADIM_TAVANI"),
    ("bilinmeyen_arac.yaml", "E_BILINMEYEN_ARAC"),
    ("statik_idempotency.yaml", "E_STATIK_IDEMPOTENCY"),
]


@pytest.mark.parametrize("dosya,kod", VAKALAR)
def test_bozuk_yaml_dogru_kodla_reddedilir(dosya, kod, acme_paths, kayitli_araclar):
    with pytest.raises(CompileFailed) as exc:
        K.compile_workflow(BOZUK / dosya, acme_paths.profile, acme_paths.types,
                           eval_dir=acme_paths.evals)
    assert kod in exc.value.report.codes(), exc.value.report.codes()


def test_duz_metin_sir_reddedilir(acme_paths):
    with pytest.raises(CompileFailed) as exc:
        K.compile_workflow(acme_paths.workflow, BOZUK / "profile_sirli.yaml",
                           acme_paths.types)
    assert "E_SIR" in exc.value.report.codes()


def test_referans_akis_derlenir_ve_tavan_raporlanir(acme_paths, kayitli_araclar):
    d = K.compile_workflow(acme_paths.workflow, acme_paths.profile,
                           acme_paths.types, eval_dir=acme_paths.evals)
    assert d.report.ok
    rapor = d.cost.render()
    assert "TAVAN MALİYET" in rapor
    assert d.cost.total_usd <= d.graph.spec.limits.max_usd_per_run
    assert d.cost.worst_path[0] == "oku"
    print("\n" + rapor)   # kabul kanıtı: rapor gerçekten üretiliyor


def test_CLI_derler_ve_sifir_doner(acme_paths, capsys):
    from kernel.compiler.__main__ import main
    kod = main([str(acme_paths.workflow), "--profile", str(acme_paths.profile),
                "--types", str(acme_paths.types), "--evals", str(acme_paths.evals)])
    assert kod == 0
    assert "TAVAN MALİYET" in capsys.readouterr().out


def test_bozuk_yamlda_CLI_bir_doner(acme_paths, capsys):
    from kernel.compiler.__main__ import main
    kod = main([str(BOZUK / "telafi_yok.yaml"), "--profile", str(acme_paths.profile),
                "--types", str(acme_paths.types)])
    assert kod == 1


@pytest.mark.slow
def test_referans_akis_ONAY_KAPISINDA_park_eder(db, acme_paths, kayitli_araclar,
                                                yuksek_tutarli_belge):
    """Uçtan uca: oku → siniflandir(router) → cikar → dogrula(switch) → onay_al.

    Eşik üstü tutar onaya gitmeli; run 'awaiting_approval' olmalı, kuyrukta iş
    KALMAMALI (hiçbir süreç ayakta değil), ve approvals satırı yalnız
    context_fields'ı taşımalı.
    """
    ...
    assert durum == "awaiting_approval"
    assert kuyruk_uzunlugu == 0
    assert set(onay["context"]) == {"cikar.fatura"}
    assert "erp_belge_no" not in json.dumps(onay["context"]), (
        "onaycı beyan edilmemiş veriyi GÖRMEMELİ (Kural 3)"
    )
    assert yan_etki_sayisi == 0, "onay beklerken ERP'ye yazılmamalı"
```

- [ ] **Adım 7: Görev 5'in skip işaretlerini kaldır**

`tests/compiler/test_compile.py` içindeki `@pytest.mark.skipif(...)` satırlarını
sil; `customers/acme/` artık var.

- [ ] **Adım 8: Referans akışı elle derle ve raporu kaydet**

```bash
.venv/bin/python -m kernel.compiler customers/acme/workflows/belge_girisi.yaml \
    --profile customers/acme/profile.yaml \
    --types customers/acme/types.py \
    --evals customers/acme/evals
```

Çıktıyı rapor dosyasına yapıştır: M2 kabul kanıtının insan-okunur yarısı budur.
Uyarı olarak `W_RECONCILE` (belge.oku ve erp.post_invoice geri-okuma sunmuyorsa)
ve `W_EVAL_YOK` (altın veri seti Gün 3'te müşteri uzmanıyla üretilir) beklenir —
**bunlar hata değildir ve raporda görünmeleri doğrudur.**

- [ ] **Adım 9: Tam paketi koş ve commit et**

Koş: `.venv/bin/python -m pytest -q -W error`
Beklenen: tümü yeşil, çıktı temiz.

Ayrıca `customers/` sızıntı testinin gerçekten dosya taradığını doğrula
(kanarya): `customers/acme/types.py` dosyasını geçici olarak `helper.py`
adına kopyalayıp testin **düştüğünü** gör, sonra sil.

```bash
git add customers packages/kernel tests/
git commit -m "feat(m2): referans akış, human_approval askıya alma ve M2 kabul kapısı"
```

---

## Öz Değerlendirme

**Spec kapsama denetimi.** §4 YAML sözleşmesi → Görev 1. §4.1'in altı
zorunluluğu → (1) Görev 3, (2) Görev 3, (3) Görev 4, (4) Görev 4, (5) Görev 3,
(6) Görev 2. §4.2'nin on denetimi → Görev 3 (yapısal beş), Görev 4 (tip iki),
Görev 5 (tavan iki), Görev 2 (sır bir). K8 tavan maliyet → Görev 5. §5
`workflows` tablosu ve versiyon sabitleme → Görev 6 + 8. §5.1 K15 → M1'de
tamam, KK4 ile konfige taşındı (Görev 8). §6.4 bütçe sigortası ön kontrolü →
Görev 6. §6.5 PII maskeleme ve yönlendirme gerekçesi → Görev 7 + 9. §3.1 beş
düğüm tipi → Görev 8 (tool, llm_task), Görev 9 (router, switch), Görev 10
(human_approval, KK3 kapsamında). §9 repo yapısı ve sert kural → Görev 4
(CI testi), Görev 10 (customers/acme). §10 referans akış → Görev 10.
§11 M2 kabul kriteri → Görev 10.

**Kapsam dışı bırakılanlar ve nedenleri.** Onay kararının İŞLENMESİ, telafi
zincirlerinin YÜRÜTÜLMESİ ve zaman aşımı yönlendirmesi M3'tedir (spec §11
"M3 | İnsan onayı + telafi + bütçe sigortası + kaos testleri"). Kaset
kayıt/tekrar ve eval koşucusu M4'tedir; M2 yalnız eval raporunun VARLIĞINI
denetler (K9). K16'nın "araç şemaları iki kiracı bağlamında bayt bayt
karşılaştırılır" CI kuralı, ağ geçidi modele araç şeması GÖNDERDİĞİNDE
anlamlı olur; M2'de göndermiyor, bu yüzden M4'e park edildi.

**M1'den devralınan park listesinin durumu.** Kapatılanlar: bütçe TOCTOU
(Görev 6), PII maskeleme (Görev 7), ertelemenin adım bütçesini tüketmesi ve
`steps.status='deferred'` (Görev 6), `_check_budget`'taki ölü `FOR UPDATE`
(Görev 6), Ö5'in "aynı araç iki düğümde" riski (Görev 8, konfig tabanlı
anahtarla yapısal olarak). Park kalmaya devam edenler: kira-tabanlı kurtarmada
fencing token yokluğu (tasarım sınırı, `external_idempotency` ile kapatılır),
`tool_calls`'ta `attempt`/`error` kolonlarının olmayışı (telafi şemasıyla
birlikte M3), ölü-mektup yolunda ham hata metninin `steps.error`'a yazılmaması
(M3, ölü-mektup semantiğine karar gerektirir), üretim kodunda `assert`
kullanımı (`python -O` altında elenir — M3'te toplu temizlik).

**Tip tutarlılığı.** `CompileError`/`CompileReport`/`CompileFailed` Görev
1'de tanımlanır ve 2–5'te aynı imzayla kullanılır. `Graph` Görev 3'te,
`RuntimeGraph` Görev 8'de ayrı sınıflardır: birincisi derleme zamanı
(hata toplar), ikincisi çalışma zamanı (veritabanından yeniden doğrulanır).
`ArtifactRef` Görev 4'te `packages/kernel/types.py` içinde tanımlanır;
Görev 8'in `artifacts.py`'si ve Görev 10'un `customers/acme/types.py`'si
oradan import eder. `parse_case` Görev 3'te yazılır, Görev 9'da `eval_switch`
tarafından tüketilir — derleme ve çalışma zamanı AYNI ayrıştırıcıyı kullanır,
yoksa switch'in determinizm iddiası iki farklı yorumcuya bölünür.

**Bilinen zayıf noktalar.** (1) Tavan maliyetin token sabitleri (`ARTIFACT_TOKEN`
= 8000 vb.) kalibre edilmemiş yer tutuculardır; spec §13 bunu zaten "M4'te
gerçek ölçümle kalibre edilir" diye kaydediyor ve hepsi güvenli tarafta
yuvarlıdır. (2) `AnthropicTransport`'un yapılandırılmış çıktı parametre
şekli canlı SDK'ya karşı doğrulanmalıdır (Görev 9 Adım 2); hiçbir test o
şekle bağlanmadığı için yanlış çıkarsa maliyeti tek dosyada tek satırdır.
(3) Sır alanı tespiti alan ADINA dayanır; adı desene uymayan bir sır alanı
kaçabilir — `secret_fields:` ile açık işaretleme bu boşluğun kapağıdır ve
referans profilde gösterilmiştir.

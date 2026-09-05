# Kurumsal Çoklu-Ajan Otomasyon Omurgası — Tasarım Belgesi

**Tarih:** 2026-09-05
**Durum:** Onaylandı, implementasyon planı bekliyor
**Kapsam:** L0 (Çekirdek Runtime) + L1 (Gözlemlenebilirlik & Maliyet) + L2 (Test & Değerlendirme)

---

## 0. Karar Kaydı

Tasarımı belirleyen kararlar ve gerekçeleri. Bu kararlar tartışılıp kapandı; değiştirilmeleri yeni bir tasarım turu gerektirir.

| # | Karar | Gerekçe |
|---|---|---|
| K1 | Görev tanımı **bildirimsel YAML** ile yapılır; müşteriye giden şey konfig paketidir, kod değil | Çekirdek runtime bir ürün olur, her satış yeniden yazılan danışmanlık projesi olmaz |
| K2 | **Tek kod, iki dağıtım profili** (SaaS + on-prem) | KVKK/veri ikametgâhı hassas müşteriler masaya oturur; "sizin sunucunuzda da çalışır" cümlesi anlaşma kapatır |
| K3 | Altyapı tabanı **yalnız Postgres + nesne deposu** (+ opsiyonel Redis) | K2'nin zorunlu sonucu: on-prem'de çalışamayacak yönetilen servise bağımlılık yasak |
| K4 | Yürütme motoru **kendi ince motorumuz**, `ExecutionBackend` arayüzü arkasında | Postgres yeterli; ölçek gerektiğinde Temporal adaptörü yazılır, yeniden yazım gerekmez |
| K5 | Ajan topolojisi: **kısıtlı yönlendirmeli deterministik graf** | Adım sayısı üstten sınırlı → maliyet tavanı hesaplanabilir; her dal test edilebilir; denetim izi iş diline çevrilebilir |
| K6 | Dil: **Python 3.13** | Ajan/LLM ekosistemi, OpenTelemetry, Pydantic, resmî `anthropic` SDK |
| K7 | `map` düğümü **v1'de yok**; toplu girdide tetikleyici birim başına ayrı run açar | Kısmi hata semantiği kurumsal SLA yönetimini bulandırır |
| K8 | **Tavan Maliyet Prensibi** korunur; kaba üst sınır tahminiyle | Sabit fiyatlı B2B sözleşme yazabilmenin ön koşulu. Tavanın güvenli tarafta yanlış olması yeterli, kesin olması gerekmez |
| K9 | `bulk` (ucuz model) kademesi **eval kanıtı olmadan kilitli** | Sessiz kalite düşüşü sözleşme iptaline yol açar; kalite düşürmek peşin kanıt ister |
| K10 | **Batch API v1'de ertelenir**, YAML'da `batchable` alanı rezerve edilir | v1 motorunun sadeliği ve teslimat hızı öncelikli; sonra kırıcı değişiklik olmadan devreye alınır |
| K11 | **İki katmanlı kaset**: merkezi repoda yalnız sentetik, gerçek kasetler müşteri sunucusunda | KVKK/ZDR uyumu; CI'sız veya KVKK'sız kalmanın üçüncü yolu |
| K12 | **LLM-hakem dar kapsamlı v1'de**: yalnız serbest metin, üst kademe hakem, kalibrasyon şartı | Serbest metin başka türlü ölçülemez; hakemin kendisi de doğrulanması gereken bir bileşen |
| K13 | **İnşa M3'e kadar kesintisiz**, ilk canlı doğrulama orada | M3'te elde "kaos dayanıklı, bütçe korumalı, insan onaylı yürüyen çekirdek" olur — güzel demodan daha ikna edici |
| K14 | **Sır Çözümleme kuralı**: konfigde düz metin sır yasak, yalnız `env: VAR_NAME` referansı | Güvenceyi iyi niyete değil derleyiciye bağlamak |
| K15 | **İki aşamalı rezervasyon**: yan etki öncesi kiralı rezervasyon, sonra tamamlama | Tek başına `UNIQUE` kısıtı çift yazmayı *tespit* eder, *engellemez*; yarış koşulunda iki dış çağrı çoktan gitmiş olur |
| K16 | **Araç şemaları %100 statik ve kiracıdan bağımsız**; kiracı özelleştirmesi yalnız Katman 2'de | Şemalar önbellek önekinin en tepesinde; kiracıya özel tek alan paylaşılan Katman 1 dahil her şeyi geçersizleştirir |

---

## 1. Kapsam ve Ayrıştırma

Talep bir "proje" değil bir **platform**. Tek spec'e sığmaz. Ayrıştırma:

| Katman | İçerik | Durum |
|---|---|---|
| **L0** Çekirdek Runtime | Ajan soyutlaması, orkestratör, derleyici, durum deposu, olay yolu, araç kaydı, LLM ağ geçidi, bütçe motoru | **Bu spec** |
| **L1** Gözlemlenebilirlik & Maliyet | OTel trace, token muhasebesi, maliyet raporu, bütçe sigortası, denetim izi | **Bu spec** |
| **L2** Test & Değerlendirme | Kayıt/tekrar, altın veri seti, LLM-hakem, kaos testleri, gölge çalıştırma | **Bu spec** |
| L3 | Konnektör katmanı (e-posta, CRM, ERP, SQL, dosya, webhook — MCP üstünden) | Ayrı spec |
| L4 | Kontrol düzlemi & çok kiracılılık (müşteri konfigi, sır yönetimi, RBAC, KVKK) | Ayrı spec |
| L5 | Paketleme & teslimat (on-prem Helm, SaaS, lisanslama) | Ayrı spec |

L0+L1+L2 birlikte kurulur, çünkü ölçülemeyen ve test edilemeyen bir ajan sistemi kurumsal müşteriye satılamaz. Kurumsal alıcının ilk üç sorusu: "ne kadar tutacak", "yanlış yaparsa ne olur", "nasıl kanıtlarsın".

---

## 2. Mimari

```
┌─────────────────────────────────────────────────────────────────────────┐
│  TETİKLEYİCİLER                                                         │
│  HTTP/API · Zamanlanmış (cron) · Webhook · E-posta · Dosya izleme       │
└────────────────────────────────┬────────────────────────────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  ORKESTRATÖR  (graf yürütücü)                                           │
│  YAML grafını okur · sıradaki adımı seçer · adımı kuyruğa yazar         │
│  bütçeyi ve adım tavanını uygular · onay kapılarında akışı askıya alır  │
└──────┬──────────────────────────────────────────────────────┬───────────┘
       ▼                                                      ▼
┌──────────────────────────┐                    ┌─────────────────────────┐
│  AJANLAR  (saf fonksiyon)│                    │  ARAÇLAR (Tool Registry)│
│  router · extractor      │                    │  tek yan etki noktası   │
│  validator · writer      │                    │  MCP · HTTP · SQL · SMTP│──▶ dış
│  judge · summarizer      │                    │  idempotency + telafi   │    sistemler
└──────────┬───────────────┘                    └─────────────────────────┘
           ▼
┌──────────────────────────┐
│  LLM AĞ GEÇİDİ           │  model kademeleme · prompt cache · token
│  (tek çıkış kapısı)      │  muhasebesi · bütçe sigortası · yeniden deneme
└──────────────────────────┘
           │
╞══════════▼══════════════════════════════════════════════════════════════╡
║  ORTAK DURUM & OLAY YOLU  —  Postgres                                   ║
║  runs · steps · events (append-only) · artifacts · job_queue · approvals ║
╞═════════════════════════════════════════════════════════════════════════╡
║  GÖZLEMLENEBİLİRLİK  —  OTel trace · token muhasebesi · maliyet · audit  ║
╚═════════════════════════════════════════════════════════════════════════╝
```

### 2.1 Beş Değişmez Kural

Bunlar tasarım tercihi değil, **sözleşme**. İhlalleri derleme veya çalışma zamanı hatasıdır.

**Kural 1 — Ajanlar birbirini doğrudan çağırmaz.**
Tüm haberleşme orkestratör üzerinden, durum deposuna yazarak ve YAML'da beyan edilmiş kenarlar (edges) üzerinden olur. Doğrudan ajan-ajan çağrısı izlenemeyen bir çağrı grafiği ve sınırsız özyineleme doğurur. Orkestratör tek geçit olunca her kenar kaydedilir, sayılır, sınırlanabilir.

**Kural 2 — Paylaşılan durum sözlüğü (blackboard) yok; tipli kanal var.**
Her adım girdisini ve çıktısını Pydantic modeliyle beyan eder. Ortak mutable sözlük, çoklu-ajan hatalarının bir numaralı kaynağıdır: kim neyi bozdu bilinmez, tek adım izole test edilemez. Tipli sözleşmede adım N'yi test etmek için sadece girdi tipini üretmek yeterlidir.

**Kural 3 — Bağlam izolasyonu: her ajan yalnız beyan ettiğini görür.**
Beyan edilmeyen alana erişim çalışma zamanı hatasıdır, sessizce geçmez. Bu tek kural hem doğruluğun hem token maliyetinin ana kaldıracıdır.

**Kural 4 — Ajan saf fonksiyondur; yan etki yalnız Araç'ta.**
Ajan `(tipli girdi, bağlam) → tipli çıktı` döner, dış dünyaya hiçbir şey yazmaz. Her dış yazma bir Araç'tan geçer ve Araç şunları beyan etmek zorundadır: idempotency anahtarı, onay gerekliliği, telafi (compensation) yordamı. Sonuç: ajanlar dünya taklit edilmeden test edilebilir; "sistem SAP'ye iki kere yazdı" senaryosu mimaride imkânsızdır.

**Kural 5 — Olay kaydı ekleme-yalnız (append-only).**
Her durum geçişi bir olaydır; hiçbir satır güncellenmez. Tek mekanizma üç ürün verir: zamanda geri giderek hata ayıklama, değişmez denetim izi, ve kayıt/tekrar testi.

### 2.2 Süreç Topolojisi

`api` ve `worker` ayrı süreçler, ikisi de durumsuz. Tüm durum Postgres'te.
- Yatay ölçekleme = worker sayısını artırmak.
- Çökme kurtarması = "kimsenin kilitlemediği işi 30 sn sonra başkası alır".
- On-prem: `docker compose up`. SaaS: aynı imaj, replika sayısıyla.

---

## 3. Çekirdek Soyutlamalar

| Soyutlama | Nedir | Sözleşmesi |
|---|---|---|
| **Workflow** | Versiyonlanmış graf tanımı | YAML → doğrulanmış, derlenmiş graf |
| **Node** | Graftaki tek düğüm | 5 tipten biri |
| **Agent** | Saf fonksiyon | `(tipli girdi, bağlam) → tipli çıktı`, yan etkisiz |
| **Tool** | Tek yan etki noktası | idempotency + onay bayrağı + telafi |
| **Run / Step** | Yürütme örneği | durum makinesi, Postgres satırı |
| **Artifact** | Büyük içerik | nesne deposunda; grafta yalnız referansı dolaşır |

**Bilinçli olarak yazılmayanlar:**
- `sequence` / `parallel` düğüm tipi yok — sıra ve paralellik `depends_on` kenarlarından türer; ayrıca tanımlamak aynı şeyi iki yerden ifade etmek olur.
- `subgraph` v1'de yok — ilk üç müşteride hangi parçaların gerçekten tekrarlandığı görülmeden ortak kütüphane tasarlamak, kullanılmayan soyutlama üretmektir.
- `map` v1'de yok (K7).

### 3.1 Beş Düğüm Tipi

```
llm_task        LLM çağırır, tipli çıktı üretir. Akışı yönlendirmez.
router          LLM bir sonraki kenarı seçer — YALNIZCA beyan edilmiş kümeden.
switch          Kod bir sonraki kenarı seçer (bir değere bakarak). LLM yok.
tool            Dış dünyaya dokunur. Tek yan etki noktası.
human_approval  Akışı askıya alır, insan kararını bekler.
```

`router` ve `switch` ayrı tutulur çünkü farklı şeyler garanti ederler: `switch` deterministiktir, test gerektirmez. `router` olasılıksaldır ve **her kenarı için altın veri seti zorunludur**.

---

## 4. YAML Sözleşmesi

```yaml
apiVersion: v1
name: fatura_giris
description: Muhasebe kutusuna gelen faturaları ERP'ye işler.

trigger:
  type: email
  mailbox: muhasebe@acme.com

limits:                       # ihlali = run iptal + alarm
  max_steps: 25
  max_usd_per_run: 0.40
  max_wallclock: 4h

defaults:
  model_tier: standard
  retry: {attempts: 3, backoff: exponential}

graph:
  - id: oku
    type: tool
    tool: email.fetch_attachments
    outputs: {belgeler: List[ArtifactRef]}    # içerik DEĞİL, referans

  - id: siniflandir
    type: router
    depends_on: [oku]
    model_tier: fast
    inputs:  [oku.belgeler]                   # bağlam izolasyonu (Kural 3)
    routes:
      fatura:   cikar
      dekont:   dekont_akisi
      alakasiz: son
      belirsiz: insan_kuyrugu                 # kaçış kapısı ZORUNLU
    on_low_confidence: {threshold: 0.75, route: belirsiz}

  - id: cikar
    type: llm_task
    model_tier: standard
    inputs:  [oku.belgeler]
    outputs: {fatura: FaturaModeli}           # customers/<x>/types.py

  - id: dogrula
    type: switch                              # deterministik — LLM yok
    depends_on: [cikar]
    on: cikar.fatura.tutar
    cases:
      "> 10000": onay_al
      default:   erp_yaz

  - id: onay_al
    type: human_approval
    assignee_role: muhasebe_muduru
    timeout: {after: 48h, route: insan_kuyrugu}
    context_fields: [cikar.fatura]            # onaycıya SADECE bu gösterilir

  - id: erp_yaz
    type: tool
    tool: sap.post_invoice
    idempotency: [run_id, cikar.fatura.fatura_no]
    compensation: sap.void_invoice
    batchable: false                          # v1'de yok sayılır (K10)
```

### 4.1 Şema Zorunlulukları

Aşağıdakiler ihlal edilirse YAML **yüklenmez**:

1. Yan etkili her `tool` düğümünde `idempotency` **ve** `compensation` bulunmalı; `reconcile` (geri-okuma) beyanı yoksa derleyici **uyarır** (§5.1).
2. Her `router` düğümünde bir kaçış kapısı (düşük güven rotası) bulunmalı.
3. Bir düğüm `inputs`'ta beyan etmediği alana erişemez.
4. Büyük içerik `ArtifactRef` olarak taşınır, adım çıktısına gömülmez.
5. `bulk` kademesi talep eden düğüm için geçerli bir eval raporu bulunmalı (K9).
6. Konfigdeki sır alanları yalnız `env: VAR_NAME` biçiminde olabilir (K14).

**Gerekçeler.** (1) Çift yazma korumasını konfig seviyesinde imkânsız kılar. (2) Model emin olmadığında bir kutuya girmeye zorlanırsa uydurur; "emin değilim"in maliyeti bir insan kuyruğu satırı, yanlış kutunun maliyeti yanlış ERP kaydıdır. (4) Postgres satırları küçük ve olay kaydı okunabilir kalır; ajan belgeyi ancak açıkça istediğinde yükler — bağlam izolasyonu dilekten kurala döner.

### 4.2 Statik Doğrulama (derleme zamanı, LLM'siz, maliyetsiz)

```
✓ Graf asiklik mi                      ✓ Yan etkili araçta idempotency + telafi var mı
✓ Tip uyumu: N.çıktı ⊇ N+1.girdi       ✓ Her router'da kaçış kapısı var mı
✓ Beyan edilmemiş alana erişim yok     ✓ En kötü yol max_steps'i aşıyor mu
✓ Referans verilen araç kayıtlı mı     ✓ En kötü yolun maliyeti bütçeyi aşıyor mu
✓ Sır alanları env: referansı mı       ✓ bulk kademesi için eval raporu var mı
```

**Tavan Maliyet Prensibi (K8).** Derleyici grafta en pahalı yolu yürüyerek çalışma başına maliyet üst sınırını hesaplar. Token tahminleri kaba ve **güvenli tarafta** (conservative) yapılır — tavanın kesin olması gerekmez, aşılmaması gerekir. Bu, sabit fiyatlı sözleşme yazabilmenin ön koşuludur.

---

## 5. Veri Modeli (Postgres)

```sql
workflows  (id, tenant_id, name, version_hash, yaml_source, compiled, created_at)
runs       (id, tenant_id, workflow_id, workflow_version_hash,
            status, trigger, input_ref, output_ref,
            budget_usd, spent_usd, step_count, max_steps, idempotency_key)
steps      (id, run_id, node_id, attempt, status, input_ref, output_ref,
            model, tokens_in, tokens_out, cache_read_tokens, cost_usd, error)
events     (id BIGSERIAL, run_id, seq, type, payload JSONB, created_at)  -- APPEND-ONLY
artifacts  (id, tenant_id, run_id, uri, media_type, bytes, sha256)
approvals  (id, run_id, node_id, status, assignee_role, decided_by, decided_at, reason)
tool_calls (id, step_id, tool_name, idempotency_key UNIQUE, req_ref, resp_ref,
            status, lease_expires_at, attempt, created_at, completed_at)
            -- status: reserved | completed | failed | uncertain
job_queue  (id, run_id, node_id, available_at, locked_by, locked_until, attempts)
```

### 5.1 İki Aşamalı Rezervasyon — Yan Etki Koruması (K15)

`idempotency_key UNIQUE` tek başına **yetmez**. Satır dış çağrıdan *sonra* yazılırsa, iki worker
aynı anda dış API'yi çağırır ve yalnızca ikinci `INSERT` reddedilir — çift yazma çoktan olmuştur.
Kısıt olayı tespit eder, engellemez. Bu yüzden yan etki iki aşamada yürür:

```
1. REZERVE ET   INSERT tool_calls(idempotency_key, status='reserved',
                                  lease_expires_at = now() + kira)
                → KENDİ TRANSACTION'INDA COMMIT EDİLİR (dış transaction'a bağlanmaz;
                  rollback'te rezervasyon buharlaşırsa koruma yoktur)
                → UNIQUE ihlali = başka bir worker bu işi üstlenmiş

2. ÇAĞIR        Dış sistem çağrısı yapılır

3. TAMAMLA      UPDATE status='completed', resp_ref=..., completed_at=now()
                (hata → status='failed', yeniden deneme politikası işler)
```

**Aynı anahtarla gelen ikinci worker ne yapar** — bloke olmaz, çünkü bloke olmak bir worker'ı
meşgul tutar. İş kuyruğa gecikmeyle geri konur ve durum okunur:

| Bulduğu durum | Davranış |
|---|---|
| `completed` | Dış çağrı **hiç yapılmaz**; kaydedilmiş yanıt döndürülür. Idempotency'nin asıl kazancı |
| `reserved`, kira geçerli | İş `lease_expires_at` sonrasına ertelenir; worker serbest kalır |
| `failed` | Yeniden deneme politikası işler (yeni `attempt`) |
| `reserved`, **kira dolmuş** | Kritik durum — aşağıya bak |
| `uncertain` | İnsan kuyruğu. Otomatik tekrar **yok** |

**Kirası dolmuş rezervasyon** en tehlikeli durumdur: çağrının dış sisteme ulaşıp ulaşmadığı
bilinmez. Körlemesine tekrar denenmez. Çözüm sırası:

1. Araç bir `reconcile` (geri-okuma) yordamı beyan etmişse çağrılır — yazma gerçekleşmiş mi diye sorulur.
2. Yoksa ve dış API istemci tarafı idempotency anahtarı kabul ediyorsa, **kendi anahtarımız iletilerek** güvenle tekrar denenir.
3. İkisi de yoksa satır `uncertain` işaretlenir ve run insan kuyruğuna düşer.

Üçüncü madde spec'in kendi felsefesinin gereğidir: doğrulanamayan bir yan etki kör tekrar edilmez,
ve hiçbir iş sessizce yarım kalmaz (bkz. §6.4 bütçe aşımı davranışı).

**Şema sonucu:** yan etkili araçlar `reconcile` yordamını beyan etmeye *teşvik edilir*; beyan
etmeyenlerde kira dolması otomatik olarak insan kuyruğu demektir. Derleyici bunu YAML doğrulamasında
uyarı olarak raporlar (hata değil — her dış sistem geri-okuma sunmaz).

**`runs.workflow_version_hash` sabitlenir.** Run başlarken bağlandığı YAML versiyonu yazılır ve değişmez. Müşteri çalışan bir işin ortasında YAML'ı düzenlerse, o iş eski tanımla biter. Dayanıklı iş akışı motorlarının ayrı özellik olarak sattığı "workflow versioning" problemi, içerik hash'i + sabitleme ile ek altyapısız çözülür.

**`tenant_id` her tabloda, ilk günden** — on-prem tek kiracılı kurulumda bile. Bugün bedava, sonradan eklenmesi veri göçü demek. SaaS profilinde üstüne Postgres Row Level Security bindirilir (savunmayı tek `WHERE` cümlesine emanet etmemek için).

**İnsan onayı RAM tutmaz.** `human_approval` düğümü `awaiting_approval` durumuna geçer, satır Postgres'te bekler, hiçbir süreç ayakta kalmaz. 3 gün beklemenin maliyeti bir veritabanı satırıdır.

**İş kuyruğu:** `SELECT ... FOR UPDATE SKIP LOCKED` + kilit süresi. Kilit süresi dolan iş yeniden alınabilir hale gelir.

---

## 6. LLM Ağ Geçidi ve Maliyet Stratejisi

Hiçbir ajan `anthropic` SDK'sını doğrudan import etmez. Sekiz sorumluluk tek yerde: token muhasebesi, bütçe sigortası, model kademeleme, önbellek disiplini, yeniden deneme/geri düşüş, PII maskeleme, sağlayıcı taşınabilirliği, trace. Dağıtılırsa hiçbiri güvenilir olmaz — özellikle bütçe.

### 6.1 Model Kademeleme

> **İlk maliyet kaldıracı "daha ucuz modele geç" değildir. Aynı modelde `effort` düşürmektir.**

Gerekçeler: (a) **Önbellek modele özeldir** — modeller arası basamak (cascade) kurmak, basamaklar arası önbellek yeniden kullanımını kaybettirir; tasarrufun bir kısmı daha başlamadan geri verilir. (b) Yeni nesil modelin düşük effort'u, eski/ucuz modelin yüksek effort'unu sık sık geçer.

Bu yüzden `model_tier` bir model adı değil, bir **(model, effort) çifti**dir:

| Tier | Eşleme | Kullanım |
|---|---|---|
| `deep` | `claude-opus-5` @ `effort: xhigh` | Doğrulama, çelişki çözümü, nihai yazma |
| `standard` | `claude-opus-5` @ `effort: high` | **Varsayılan.** Çıkarım, özetleme |
| `fast` | `claude-opus-5` @ `effort: low` | Basit sınıflandırma, biçim dönüştürme, kısa kararlar |
| `bulk` | `claude-haiku-4-5` (effort yok) | **KİLİTLİ** — yalnız eval kanıtıyla açılır (K9) |

Fiyatlar ($/1M token, Anthropic birinci-parti, uygulama anında canlı kaynaktan doğrulanacak):
Opus 5 → $5 girdi / $25 çıktı · Sonnet 5 → $2 / $10 · Haiku 4.5 → $1 / $5.

Sonnet 5 kasıtlı olarak hiçbir kademeye atanmamıştır. Kademe merdiveni önce tek model üzerinde
`effort` ile inilecek biçimde kurulmuştur (6.1 başındaki gerekçe); ara bir model kademesi ancak
eval ölçümü `fast` ile `bulk` arasında gerçek bir boşluk gösterirse eklenir. Fiyatı burada
karşılaştırma tabanı olarak listelenmiştir.

Tier → model eşlemesi **tek yerde** (dağıtım profili dosyasında) tutulur. Müşteri YAML'ına model kimliği gömülmez; aksi halde model değiştiğinde N müşterinin konfigi elle güncellenir.

**Ağ geçidi hiçbir zaman `thinking: disabled` göndermez.** Opus 5'te düşünmeyi kapatmanın sessiz bir arıza modu vardır: model bazen araç çağrısını `tool_use` bloğu yerine görünür metne yazar — çağrı çalışmaz, hata fırlatılmaz. Ajanlı bir döngüde bu sessiz veri bozulmasıdır. Ucuzlatma yolu: düşünme açık, `effort` düşük.

### 6.2 Prompt Önbelleği — Üç Katmanlı Prompt

Önbellek önek eşlemesiyle çalışır (`tools → system → messages`); öneğin herhangi bir baytı değişirse sonrası geçersizleşir.

```
┌─ Katman 1 ── Ajan sistem promptu + araç şemaları ────── [önbellek noktası 1]
│  Ürün sabiti. Bayt bayt aynı. TÜM kiracılarda, TÜM çalışmalarda ortak.
├─ Katman 2 ── Kiracıya özel talimatlar ───────────────── [önbellek noktası 2]
│  Kiracı başına sabit. Çalışmalar arası değişmez.
└─ Katman 3 ── Adımın beyan edilmiş girdisi ───────────── önbelleklenmez
```

Bu yapının mimarimizde işleyen özel yanı: ajanlar bağlam-izole saf fonksiyonlar olduğu için Katman 3 küçük, Katman 1–2 sabittir. Serbest delegasyon mimarilerinde konuşma geçmişi büyüdükçe önbellek öneki kayar. **Doğruluk için koyduğumuz Kural 3, aynı zamanda önbelleğin en verimli çalıştığı biçimdir.**

Ağ geçidinde zorunlu kurallar:
- **Araç şemaları %100 statik ve kiracıdan bağımsızdır (K16).** Şemalar önekin *en tepesinde*
  durur; kiracıya özel tek bir alan, altındaki her şeyi — paylaşılan Katman 1 sistem promptu
  dahil — geçersizleştirir ve kiracılar arası önbellek paylaşımını tamamen öldürür. Kiracıya
  özel hiçbir dinamik parametre, enum değeri veya yetki bilgisi araç şemasına gömülmez;
  bunlar yalnızca Katman 2'de yer alır.
- Araç listesi **deterministik sıralanır** (sıralanmamış liste her süreçte farklı bayt dizisi üretir ve önbelleği sessizce öldürür).
- Katman 1'e **asla** zaman damgası, run kimliği veya sayaç girmez.
- Katman 1 minimum önbelleklenebilir uzunluğun üstünde tutulur (modele göre 512–4096 token); altında sessizce önbelleğe girmez.
- Her çağrıda `usage.cache_read_input_tokens` kaydedilir; ajan bazlı isabet oranı eşiğin altına düşerse **alarm**. Önbellek sessizce bozulur; ancak ölçülürse fark edilir.
- Kademe değişikliği o düğümün önbelleğini sıfırlar → kademe değişimi bir **dağıtım olayıdır**, çalışma zamanı kararı değil.

**K16'nın zorlayıcı sonucu — yetenek araçları.** Kural yalnız şema *alanlarını* değil araç
*kümesini* de bağlar: kiracı A'da SAP, kiracı B'de Netsis varsa araç listesi kiracıya göre değişir
ve aynı öneği bozar. Bu yüzden ajanlara ürüne özgü araçlar değil **jenerik yetenek araçları**
verilir (`erp.post_invoice`, `crm.create_lead`); araç kaydı bunu çalışma anında o kiracının
konnektörüne bağlar. Model her kiracıda bayt bayt aynı şemayı görür; bağlama modelin görüş
alanının dışında yapılır.

Bunun dürüst maliyeti: kiracıya özel enum'lar şemadan çıktığı için API'nin `strict: true` şema
doğrulaması o alanlarda devre dışı kalır. Doğrulama kaybolmaz, **Aracın kendi Pydantic
doğrulamasına** taşınır — yani Kural 4 gereği zaten olması gereken yere. Yan etki sınırında
model çıktısına zaten güvenilmiyor.

**Bilinen sınır:** önbellek ömrü kısadır (varsayılan 5 dk). `human_approval` sonrasındaki adımlar için önbellek isabeti **planlanmaz**; maliyet modeli bunu baştan varsayar.

### 6.3 Maliyet Kaldıraçları

**Bedava (kalite kaybı yok):**

| Kaldıraç | Etki |
|---|---|
| Yapılandırılmış çıktı (`output_config.format` + `strict: true`) | Şema dışı yanıt = boşa giden tam bir tur; sıfırlanır. Tipli kanal kuralının API karşılığı |
| `max_tokens` doğru ayarı + streaming | Düşük tavan → kesilme → yeniden deneme → 2× maliyet |
| Bağlam izolasyonu (Kural 3) | Girdi tokenlarını kaynağında keser |
| Sıkıştırma/bağlam düzenleme **gerekmiyor** | Hiçbir ajan pencereyi doldurmuyor; serbest delegasyonun ödediği bedeli ödemiyoruz |
| Batch API (%50) | **v1'de ertelendi (K10)**, şemada `batchable` rezerve |

**Ödünlü (ölçmeden yapılmaz):** önce `effort` düşürme, sonra ucuz model. Düğüm bazında, her zaman eval kanıtıyla.

**Ölçüm birimi: istek başına maliyet değil, tamamlanmış iş başına maliyet.** Daha ucuz bir istek işi bitirmek için iki tur daha atıyorsa ucuz değildir.

### 6.4 Bütçe Sigortası

```
LLM çağrısından ÖNCE:  spent_usd + kaba_tahmin > budget_usd  →  çağrı reddedilir
LLM çağrısından SONRA: spent_usd atomik olarak artırılır (aynı transaction)
Ayrıca: kiracı başına günlük tavan · workflow başına p95 maliyet sapma alarmı
```

Bütçe aşımı run'ı **kontrollü** sonlandırır: durum `budget_exceeded`, telafi yordamları çalışır, insan kuyruğuna düşer. Sessizce yarıda kesilmez — kurumsal bir sistemde en kötü sonuç, yarım kalmış ve kimsenin haberi olmayan bir iştir.

### 6.5 Dayanıklılık, Denetlenebilirlik, Taşınabilirlik

- **Sunucu tarafı geri düşüş:** Opus 5'te güvenlik sınıflandırıcısı bir isteği reddedebilir (HTTP 200 + `stop_reason: "refusal"`). Ağ geçidi bunu her yanıtta kontrol eder ve sunucu tarafı `fallbacks` parametresini varsayılan açık tutar.
- **Yönlendirme gerekçesi denetim izine yazılır.** `router` düğümlerinde düşünme özeti (`display: "summarized"`) açıktır; seçim gerekçesi olay kaydına düşer. "Bu fatura neden onaya gitti" sorusunun cevabı budur. Maliyeti düşük, satış değeri yüksek.
- **PII maskeleme tek noktada.** Kurumsal veri dışarı çıkmadan maskelenir; KVKK tartışmasında gösterilecek tek nokta ağ geçididir. ZDR sözleşmesi de burada konfigüre edilir.
- **Taşınabilirlik dürüst tarifi:** ağ geçidi sağlayıcı değiştirmeyi mümkün kılar ama **sıcak takas değildir** — önbellek davranışı ve düşünme blokları sağlayıcıya özgüdür. Sağlayıcı değişimi, eval setinin yeniden koşulmasını gerektiren bir olaydır. Müşteriye "kilitli değilsiniz" derken bu böyle anlatılır.

---

## 7. Test, Değerlendirme ve Canlıya Alma

İlke: **her testin maliyeti var, en ucuz katman en çok koşar.**

```
   5  │  Gölge çalıştırma        │  gerçek veri, yan etki yok · haftalar
   4  │  Kaos & dayanıklılık     │  ~dakikalar · LLM yok · her CI koşusu
   3  │  Eval / altın veri seti  │  $$ · değişiklik-güdümlü + gecelik
   2  │  Kayıt / Tekrar          │  saniyeler · LLM YOK · her commit
   1  │  Birim testleri          │  milisaniye · her commit
   0  │  Statik doğrulama        │  milisaniye · her YAML commit'i
```

### Katman 0–1 — Statik doğrulama ve birim testleri
Bölüm 4.2'deki denetimler burada koşar. LLM yok, maliyet yok; müşteri konfigindeki hataların çoğunu deploy öncesi yakalar.

İki zorunlu birim testi (ihmal edilirse CI kırılır):
- **Her araç için idempotency testi:** aynı anahtarla iki çağrı → **tek** yan etki.
- **Her araç için telafi testi:** `compensation` yordamı gerçekten geri alıyor mu.

Satış masasındaki güvence tam olarak bu iki cümledir; test edilmeyen bir güvence pazarlama metnidir.

### Katman 2 — Kayıt/Tekrar (Kural 5'in getirisi)
Ağ geçidi her gerçek çalışmada `(istek özeti → yanıt)` çiftlerini bir **kasete** yazar. Tekrar modunda ağ geçidi ağa çıkmaz, kasetten okur. Orkestratör mantığı, graf geçişleri, hata yolları, yeniden denemeler ve telafi zincirleri **sıfır LLM maliyetiyle, tam deterministik**, her commit'te koşar.

- **İstek özeti kapsamı:** model + effort + prompt baytları + araç şeması. Prompt değişirse kaset düşer ve test **kırılır** — bu bir özelliktir: prompt değişikliği eval'i yeniden koşmayı gerektirir, sessizce geçmemelidir.
- **Kaset düşünce sessizce ağa çıkılmaz.** Tekrar modunda ağ erişimi kapalıdır; aksi halde CI farkında olmadan para harcayan ve rastgele kırılan bir yapıya dönüşür.
- **İki katmanlı kaset (K11):** merkezi repo ve standart CI hattı yalnız **PII içermeyen sentetik** kasetler koşar. Gerçek müşteri kasetleri KVKK/ZDR gereği müşterinin on-prem sunucusunda kalır ve yerel regresyon testlerinde kullanılır.

### Katman 3 — Eval / Altın Veri Seti

**Kapsam kuralı:** her `router` düğümünün **her kenarı** için ayrı örnek kümesi (`belirsiz` dahil), kenar başına ≥20 örnek. Örnek yoksa derleyici uyarır; `bulk` kademesi talep ediliyorsa **hata verir**.

**Değerlendirme yöntemi, tercih sırasıyla:**

| # | Yöntem | Nerede |
|---|---|---|
| 1 | Programatik doğrulama (tip, alan eşleşmesi, sayısal tolerans) | Her yerde mümkünse |
| 2 | Tam / küme eşleşmesi | Router kararları, sınıflandırma |
| 3 | LLM-hakem | **Yalnız serbest metin** (K12) |

**LLM-hakem kuralları (K12):** yalnız serbest metin düğümlerinde; hakem modeli değerlendirilenden **üst kademede**; hakem–insan tutarlılık skoru ölçülüp raporlanmadan hiçbir eval sonucu "geçti" sayılmaz.

**Metrikler:** doğruluk · `belirsiz` oranı · **kalibrasyon** · çalışma başına maliyet · gecikme p50/p95.

Kalibrasyon birinci sınıf metriktir: `on_low_confidence` eşiği, güven skorunun gerçek doğrulukla uyuşması varsayımına dayanır. Kalibrasyon bozuksa eşik anlamsız bir sayıdır ve "emin olmadığında insana sor" güvencesi sessizce çalışmaz.

**Koşum politikası:** her commit'te değil — promptu/modeli/kademesi değişen düğümler için, artı gecelik tam koşu, artı aylık bütçe tavanı. Eval seti üçe ayrılır (geliştirme / doğrulama / **test**); test kümesine ayar yapılmaz, yalnız raporlanır.

### Katman 4 — Kaos ve Dayanıklılık (her CI koşusunda)

| Senaryo | Beklenen davranış |
|---|---|
| Worker adım ortasında öldürülür | İş 30 sn sonra yeniden alınır, **tek** yan etki |
| Aynı iş iki worker'a teslim edilir | İki aşamalı rezervasyon (§5.1): tek dış çağrı; ikinci worker `completed` yanıtını okur |
| Worker dış çağrının **ortasında** ölür, kira dolar | `reconcile` varsa geri-okunur; yoksa `uncertain` → insan kuyruğu. Kör tekrar **yok** |
| Araç zaman aşımı / HTTP 500 | Yeniden deneme → telafi → insan kuyruğu |
| Bütçe run ortasında tükenir | Kontrollü sonlandırma + telafi + insan kuyruğu |
| Sağlayıcı 429 / `stop_reason: refusal` | Geri düşüş devreye girer, run devam eder |
| Postgres bağlantısı kopar | Kayıp iş yok, kuyruk kendini toparlar |
| YAML çalışma ortasında değişir | Run sabitlenmiş versiyonla tamamlanır |

`testkit` içinde **kasıtlı olarak kötü davranan sahte dış sistemler** bulunur (rastgele 500 döner, bazen yavaş, bazen aynı isteği iki kez alır). Dayanıklılık gerçek bir sisteme karşı test edilemez — kötü davranması istenerek sağlanamaz.

### Katman 5 — Gölge Çalıştırma

Sistem müşterinin **gerçek üretim verisiyle** çalışır; tüm `tool` düğümleri yan etkisiz moddadır (ne yazacağını kaydeder, yazmaz). Paralelde insan işi normal yapmaya devam eder. 2–4 hafta sonra elde:

```
· insan kararıyla uyum oranı (düğüm bazında)
· ayrışılan vakaların tam listesi — kim haklıydı, incelenebilir
· gerçek maliyet dağılımı (p50 / p95 / en kötü) — tahmin değil ölçüm
· gerçek gecikme, gerçek insan-onayı oranı
```

Müşteriye sunulan şey bir vaat değil, **kendi verisiyle ölçülmüş bir rapor**. Pilot sözleşmesi, fiyatlandırma ve SLA bu rapordan çıkar; müşteri içindeki en büyük direnç noktası (departman müdürünün "bu benim işimi bozar" endişesi) veriyle çözülür.

**Kademeli canlıya alma** — her geçiş tarihe değil **metriğe** bağlıdır:

```
gölge (yan etki yok)  →  %5 canlı, tüm yazmalar onaylı
                      →  %50, eşik altı onaylı
                      →  tam otonom + istisna kuyruğu
```

### 7.1 Sürekli Üretim Doğrulaması

Canlıda sürekli izlenen üç sinyal: maliyet p95 sapması · `belirsiz` oranındaki artış (veri kayması) · insan onaycının **reddettiği** kararlar.

Son maddenin yan etkisi sistemin en değerli özelliklerinden biri: bir insan sistemin kararını reddettiğinde, o vaka otomatik olarak **eval setine aday** düşer. Altın veri seti üretimden kendini besler; sistem kullanıldıkça ölçülebilir biçimde iyileşir. "Sistem zamanla öğreniyor" cümlesinin somut karşılığı budur — model ağırlıkları değil, sürekli genişleyen ve düzenli koşulan bir regresyon seti.

---

## 8. Adaptasyon Oyun Kitabı

### 8.1 Adaptasyon Maliyetinin Gerçek Dağılımı

| Kova | İlk müşteri | 5. müşteri | Neden |
|---|---|---|---|
| Çekirdek runtime | **0** | **0** | Hiç değişmez. Ürün bu. |
| Konnektörler | 2–3 gün | ~0.5 gün | Kütüphane büyüdükçe sıfıra gider |
| İş akışı YAML'ı | 1 gün | 0.5 gün | Desenler tekrarlanır |
| **Altın veri seti** | **1–2 gün** | **1–2 gün** | **Azalmaz** |

Sonuçlar: (a) fiyatlandırma buna göre yapılır; (b) veri seti adımı müşterinin uzmanıyla **birlikte** yapılır — hem kalite hem sahiplenme; (c) uzun vadeli savunma hattı kod değil, biriktirilen etiketli vaka arşividir. Kod kopyalanabilir; üç yıllık etiketli kurumsal vaka seti kopyalanamaz.

### 8.2 Gün Gün

**Gün 0 — Keşif (yarım gün). Teknik değil, eleme adımı.**
```
Hacim:                  Haftada <20 ise otomasyon değmez.
Kural yazılabilirliği:  Karar tamamen sezgiselse henüz değil.
Hata geri alınabilir mi: İlk iş, geri alınamayan iş OLMAMALI.
Erişim:                 Girdi ve çıktı sistemlerine API/DB/kutu erişimi yoksa proje yok.
```
Yanlış işi seçmek kötü mimariden pahalıya patlar; başarısız pilot, teknik olarak haklı olunan sözleşmeyi bile öldürür.

**Gün 1 — Süreç haritası ve sözleşmeler.** İnsan sürecinin adımları → her adımın girdi/çıktı tipi Pydantic modeline, karar noktaları `router`/`switch` ayrımına, yan etkiler araçlara + idempotency anahtarı + telafi yordamına. Çıktı: YAML taslağı + `types.py`.

**Gün 2 — Konnektörler.** Kütüphanede varsa konfig; yoksa yaz — iki zorunlu testiyle.

**Gün 3 — Altın veri seti.** Geçmiş 100–200 vaka, gerçek insan kararlarıyla; router kenarı başına ≥20. Müşteri uzmanıyla birlikte.

**Gün 4 — Eval, kalibrasyon, tavan maliyet raporu.** Eşikler burada ayarlanır; elde hem statik tavan hem ölçülmüş gerçek maliyet.

**Gün 5 — Gölge çalıştırma başlar** (2–4 hafta).

**Hafta 5+ — Kademeli canlıya alma**, metriğe bağlı geçişlerle.

> Satış cümlesi: **"Bir hafta mühendislik, iki-dört hafta kendi verinizle gölge çalıştırma, sonra kademeli devreye alma."**

---

## 9. Repo Yapısı ve Sır Çözümleme

```
otomasyon/
├── packages/
│   ├── kernel/              ← ÜRÜN. Müşteriye göre asla değişmez.
│   │   ├── compiler/            YAML → doğrulanmış graf + statik/tavan maliyet analizi
│   │   ├── orchestrator/        graf yürütücü, durum makinesi, job queue
│   │   ├── state/               Postgres şeması, olay kaydı, göçler
│   │   ├── gateway/             LLM ağ geçidi — tek çıkış kapısı
│   │   ├── tools/               araç kaydı, idempotency, telafi
│   │   ├── agents/              ajan taban sınıfları, 3 katmanlı prompt
│   │   └── observability/       OTel, token muhasebesi, denetim izi
│   ├── connectors/          ← BÜYÜYEN VARLIK
│   │   └── email/ sql/ http/ sheets/ erp/ mcp/
│   └── testkit/             ← kaset, kaos, eval koşucusu, gölge modu, sahte dış sistemler
├── customers/               ← KOD YOK. Sadece konfig + tip + veri.
│   └── acme/
│       ├── workflows/*.yaml
│       ├── types.py             Pydantic modelleri
│       ├── evals/               altın veri setleri
│       ├── cassettes/           yalnız sentetik (gerçekler müşteri sunucusunda)
│       └── profile.yaml         tier eşlemesi, bütçeler, sır REFERANSLARI
├── deploy/onprem/           docker-compose + tek komutluk kurulum
└── deploy/saas/
```

**Sert kural:** `customers/` altında tip tanımı dışında kod olmaz. Oraya mantık yazıldığı fark edilen an, o mantık ya `connectors/`'a ya `kernel/`'e aittir. Bu kural gevşerse ürün altı ay içinde N ayrı danışmanlık projesine dönüşür.

### 9.1 Sır Çözümleme Kuralı (K14)

`profile.yaml` ve tüm konfig dosyalarında hiçbir API sırrı veya kurumsal parola düz metin tutulmaz. Sistem sırları **yalnız** ortam değişkeni referansı üzerinden çözer:

```yaml
# customers/acme/profile.yaml
connectors:
  sap:
    endpoint: https://sap.acme.internal
    username: env: ACME_SAP_USER        # ✓
    password: env: ACME_SAP_PASSWORD    # ✓
  email:
    password: "Hunter2!"                # ✗ derleyici REDDEDER
```

Derleyici, sır alanı olarak işaretlenmiş bir alanda `env:` öneki görmezse konfigi **yüklemez**. Güvence iyi niyete değil derleyiciye bağlanır — Bölüm 4.1'deki diğer şema zorunluluklarıyla aynı gerekçe.

---

## 10. Referans İş Akışı

Sektör belirlenmediği için, her mekanizmaya dokunan sentetik bir referans akış kurulur:
**"gelen belge → yapılandırılmış kayıt → onaylı yazma"**.

Bu desen B2B arka-ofis otomasyonlarının büyük kısmının iskeletidir: fatura girişi (muhasebe), CV eleme (İK), sigorta hasar dosyası, tedarikçi sözleşme incelemesi, müşteri şikâyet yönlendirme, sipariş girişi.

```
tetikleyici → tool(oku) → router(LLM + kaçış kapısı) → llm_task(çıkarım)
           → switch(deterministik eşik) → human_approval(askıya alma)
           → tool(idempotent yazma + telafi) → son
```

M1–M6'nın tamamının doğrulaması bu tek akışla yapılabilir; ilk gerçek müşteride işin ~%70'i hazır gelir.

---

## 11. İnşa Sırası ve Kabul Kriterleri

| # | Dilim | Kabul kriteri (kanıt) |
|---|---|---|
| **M1** | Yürüyen iskelet: Postgres + kuyruk + olay kaydı + ağ geçidi + tek adımlı akış | Worker adım ortasında `kill -9` → iş tamamlanır, **tek** yan etki oluşur |
| **M2** | Derleyici + 5 düğüm tipi + statik analiz + tavan maliyet | Bozuk YAML'lar (her şema zorunluluğu için bir vaka) reddedilir; referans akış derlenir; tavan maliyet raporlanır |
| **M3** | İnsan onayı + telafi + bütçe sigortası + kaos testleri | Bölüm 7 Katman 4'teki 7 senaryonun **tamamı** geçer |
| — | **İlk canlı sistem doğrulaması (K13)** | M3 çıktısı gerçek bir uçta doğrulanır |
| **M4** | Test koşum takımı: kaset kayıt/tekrar + eval koşucusu + LLM-hakem + kalibrasyon | Referans akışın tam regresyonu **sıfır** LLM maliyetiyle koşuyor |
| **M5** | Gözlemlenebilirlik + gölge modu | Gölge raporu (uyum oranı, ayrışma listesi, maliyet dağılımı) üretiliyor |
| **M6** | On-prem paketleme + referans akışın tamamlanması | Temiz makinede `docker compose up` → çalışıyor |

**M3'e kadar kesintisiz gidilir (K13).** M3 bittiğinde elde "kaos dayanıklı, bütçe korumalı, insan onaylı yürüyen çekirdek" olur. Kurumsal alıcıya en etkili demo, akışın görsel akması değil — sunucuyu kapatıp işin kaldığı yerden tamamlandığını ve hedef sisteme tek kayıt yazıldığını göstermektir.

---

## 12. v1 Kapsam Dışı

Bilinçli olarak ertelenenler ve gerekçeleri:

| Öğe | Gerekçe | Ne zaman |
|---|---|---|
| `map` düğümü | Kısmi hata semantiği SLA'yı bulandırır; tetikleyici birim başına run açar | v2 |
| `subgraph` | İlk üç müşteride hangi parçaların tekrarlandığı görülmeden ortak kütüphane tasarlanmaz | 3 müşteriden sonra |
| Batch API yürütmesi | v1 motorunun sadeliği öncelikli; `batchable` alanı şemada rezerve | v2, kırıcı değişiklik olmadan |
| L3 konnektör kütüphanesi | Ayrı spec | Referans akışın ihtiyacı kadarı v1'de |
| L4 kontrol düzlemi / RBAC | Ayrı spec | M6 sonrası |
| L5 Helm / SaaS otomasyonu | Ayrı spec; v1'de docker-compose yeterli | M6 sonrası |
| Temporal adaptörü | `ExecutionBackend` arayüzü hazır; ölçek gerektirene kadar yazılmaz | İhtiyaç doğduğunda |

---

## 13. Riskler ve Açık Noktalar

| Risk | Etki | Azaltma |
|---|---|---|
| Kaba token tahmini gerçek maliyetin altında kalır | Tavan maliyet taahhüdü tutmaz | Tahmin güvenli tarafta yapılır; M4'te gerçek ölçümle kalibre edilir; p95 sapma alarmı |
| Router kalibrasyonu bozuk | "Emin değilsen insana sor" güvencesi sessizce çalışmaz | Kalibrasyon birinci sınıf metrik; eval geçme şartı |
| Sentetik kasetler gerçek dünyayı temsil etmez | CI yeşil, üretim kırık | Katman 4 kaos testleri + müşteri tarafında yerel gerçek-kaset regresyonu (K11) |
| LLM-hakem kendisi hatalı | Eval sonuçları yanıltıcı | Hakem–insan tutarlılık skoru raporlanmadan eval "geçti" sayılmaz (K12) |
| Konnektör yazımı tahminden uzun sürer | Adaptasyon vaadi (1 hafta) tutmaz | Gün 0 keşfinde erişim şartı elenir; ilk müşterilerde 2–3 gün bütçelenir |
| `customers/` altına kod sızması | Ürün N danışmanlık projesine dönüşür | CI kuralı: `customers/**` altında `types.py` dışında `.py` dosyası → build kırılır |
| `reconcile` sunmayan dış sistemlerde kira dolması | `uncertain` kuyruğu büyür, operasyon yükü artar | Kira süresi araç bazında en uzun gerçekçi çağrı süresine göre ayarlanır; `uncertain` oranı izlenen bir metriktir |
| Araç şeması kiracıya sızar (K16 ihlali) | Kiracılar arası önbellek paylaşımı sessizce ölür | CI kuralı: araç şemaları iki farklı kiracı bağlamında üretilip **bayt bayt** karşılaştırılır; fark → build kırılır |

**Uygulama anında doğrulanacak varsayımlar:**
- Model fiyatları ve önbellek okuma çarpanı (canlı fiyat kaynağından).
- Minimum önbelleklenebilir önek uzunluğunun kullanılan model için tam değeri.
- `effort` seviyelerinin referans akıştaki gerçek kalite/maliyet eğrisi (M4'te ölçülür).

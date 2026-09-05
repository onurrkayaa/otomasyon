# M1 — Yürüyen İskelet Implementasyon Planı

> **Ajan işçiler için:** ZORUNLU ALT BECERİ: Bu planı görev görev uygulamak için
> `superpowers:subagent-driven-development` (önerilen) veya `superpowers:executing-plans`
> kullanın. Adımlar takip için checkbox (`- [ ]`) söz dizimindedir.

**Hedef:** Postgres üstünde çalışan, çökmeye dayanıklı, tek yan etki garantisi veren ve token maliyetini kaydeden en küçük uçtan uca ajan yürütme çekirdeği.

**Mimari:** Durumsuz `worker` süreçleri Postgres'teki `job_queue`'dan `SELECT ... FOR UPDATE SKIP LOCKED` ile iş çeker. Her durum geçişi `events` tablosuna ekleme-yalnız yazılır (veritabanı tetikleyicisi UPDATE'i reddeder). Yan etkiler iki aşamalı kiralı rezervasyondan geçer: rezerve → çağır → tamamla; kirası dolan rezervasyon `reconcile` ile geri okunur, okunamıyorsa `uncertain` işaretlenip insan kuyruğuna düşer. Tüm LLM çağrıları tek bir ağ geçidinden geçer ve token/maliyet muhasebesi orada tutulur.

**Teknoloji:** Python 3.13 · PostgreSQL 17 (Homebrew) · psycopg 3 (senkron) · Pydantic v2 · anthropic SDK · pytest

**Spec:** `docs/superpowers/specs/2026-09-05-multi-agent-omurga-design.md`

## Global Kısıtlar

Bu bölüm her görevin gereksinimlerine örtük olarak dahildir. Değerler spec'ten birebir alınmıştır.

- **Dil:** Python 3.13 (K6).
- **Altyapı tabanı:** Yalnız PostgreSQL + nesne deposu (+ opsiyonel Redis). On-prem'de çalışamayacak yönetilen servise bağımlılık **yasak** (K3).
- **Kural 1:** Ajanlar birbirini doğrudan çağırmaz; tüm geçişler orkestratör üzerinden.
- **Kural 2:** Paylaşılan durum sözlüğü yok; her adım girdi/çıktısını Pydantic modeliyle beyan eder.
- **Kural 3:** Bağlam izolasyonu — beyan edilmeyen alana erişim hatadır.
- **Kural 4:** Ajan saf fonksiyondur; yan etki yalnız `Tool` içinde.
- **Kural 5:** `events` tablosu ekleme-yalnızdır; UPDATE veritabanı seviyesinde reddedilir.
- **K15:** Yan etki iki aşamalı kiralı rezervasyondan geçer. Rezervasyon **kendi transaction'ında** commit edilir. Kirası dolmuş rezervasyon **kör tekrar edilmez**.
- **Ağ geçidi kuralı:** Hiçbir modül `anthropic` SDK'sını doğrudan import etmez — yalnız `packages/kernel/gateway/transport.py`.
- **Ağ geçidi kuralı:** `thinking: {"type": "disabled"}` **asla** gönderilmez. Ucuzlatma yolu `output_config.effort` düşürmektir (§6.1).
- **Model kimliği:** `claude-opus-5`. Müşteri konfigine model kimliği gömülmez; yalnız `model_tier` (§6.1).
- **Sır kuralı (K14):** Konfigde düz metin sır yok; yalnız ortam değişkeni referansı.
- **M1 kapsam dışı:** YAML derleyicisi (M2), `router`/`switch`/`human_approval` düğümleri (M2–M3), bütçe sigortasının tam hali (M3), kaset kayıt/tekrar (M4). M1'de iş akışı Python'da sabit tanımlıdır.

## Dosya Yapısı

| Dosya | Sorumluluk |
|---|---|
| `pyproject.toml` | Paket tanımı, bağımlılıklar, pytest konfigi |
| `packages/kernel/state/db.py` | Bağlantı havuzu, `tx()` ve `independent_tx()` |
| `packages/kernel/state/migrations/001_initial.sql` | Şema DDL'i + ekleme-yalnız tetikleyici |
| `packages/kernel/state/migrate.py` | Göç koşucusu (idempotent) |
| `packages/kernel/state/events.py` | Ekleme-yalnız olay kaydı |
| `packages/kernel/state/runs.py` | Run/step yaşam döngüsü |
| `packages/kernel/state/queue.py` | Kiralı iş kuyruğu (SKIP LOCKED) |
| `packages/kernel/tools/base.py` | `Tool` protokolü, `ToolResult`, `ToolOutcome` |
| `packages/kernel/tools/registry.py` | Araç kaydı (ad → örnek) |
| `packages/kernel/tools/execution.py` | **İki aşamalı rezervasyon (§5.1)** |
| `packages/kernel/gateway/tiers.py` | `model_tier` → (model, effort) eşlemesi |
| `packages/kernel/gateway/types.py` | `LLMRequest`, `LLMResponse`, `Usage` |
| `packages/kernel/gateway/accounting.py` | Token → USD maliyet hesabı |
| `packages/kernel/gateway/transport.py` | `anthropic` SDK'sının **tek** import noktası |
| `packages/kernel/gateway/gateway.py` | Tek çıkış kapısı: muhasebe + kayıt + reddedilme kontrolü |
| `packages/kernel/orchestrator/flow.py` | M1'in sabit 2 düğümlü akış tanımı |
| `packages/kernel/orchestrator/step.py` | Tek adım yürütücü (düğüm tipine göre dağıtım) |
| `packages/kernel/orchestrator/runner.py` | Worker döngüsü; `python -m` ile çalışır |
| `tests/conftest.py` | Test veritabanı, temizlik, sahte taşıma katmanı |
| `tests/fakes.py` | Test araçları: `SlowWriterTool`, sahte LLM taşıması |

---

### Görev 1: Proje iskeleti, Postgres ve şema göçü

**Dosyalar:**
- Oluştur: `pyproject.toml`
- Oluştur: `packages/kernel/__init__.py`, `packages/kernel/state/__init__.py`
- Oluştur: `packages/kernel/state/db.py`
- Oluştur: `packages/kernel/state/migrations/001_initial.sql`
- Oluştur: `packages/kernel/state/migrate.py`
- Oluştur: `tests/conftest.py`
- Test: `tests/state/test_migrate.py`

**Arayüzler:**
- Üretir: `db.tx() -> ContextManager[psycopg.Connection]`, `db.independent_tx() -> ContextManager[psycopg.Connection]`, `db.reset_pool() -> None`, `migrate.apply_migrations() -> list[str]`

- [ ] **Adım 1: Postgres'i kur ve başlat**

Docker bu makinede yok ve M1 için gerekli değil (konteyner paketlemesi M6'nın işi). Yerel geliştirme için Homebrew Postgres:

```bash
brew install postgresql@17
brew services start postgresql@17
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
createdb otomasyon_dev
createdb otomasyon_test
psql -d otomasyon_test -c 'SELECT 1;'
```

Beklenen: `?column?` sütununda `1` döner.

- [ ] **Adım 2: Sanal ortam ve `pyproject.toml`**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

`pyproject.toml`:

```toml
[project]
name = "otomasyon-kernel"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "psycopg[binary,pool]>=3.2",
    "pydantic>=2.9",
    "anthropic>=0.40",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-timeout>=2.3"]

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["packages"]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 60
markers = ["slow: kill -9 gibi süreç seviyesi testler"]
```

```bash
pip install -e ".[dev]"
```

- [ ] **Adım 3: `packages/kernel/state/db.py` yaz**

```python
"""Postgres bağlantı yönetimi.

İki transaction yardımcısı vardır ve farkı kritiktir:
  tx()             — normal iş; havuzdan bir bağlantı, bir transaction.
  independent_tx() — dış transaction'dan BAĞIMSIZ commit gerektiren iş.
                     K15 rezervasyonu bunu kullanmak ZORUNDADIR: rezervasyon
                     dış transaction'a bağlanırsa rollback'te buharlaşır ve
                     çift yazma koruması ortadan kalkar.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def dsn() -> str:
    value = os.environ.get("OTOMASYON_DB_DSN")
    if not value:
        raise RuntimeError(
            "OTOMASYON_DB_DSN tanımlı değil. Örnek: "
            "postgresql://localhost/otomasyon_dev"
        )
    return value


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(dsn(), min_size=1, max_size=10, open=True)
    return _pool


def reset_pool() -> None:
    """Testlerin DSN değiştirebilmesi için havuzu kapatır."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[psycopg.Connection]:
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


@contextmanager
def independent_tx() -> Iterator[psycopg.Connection]:
    """Havuzdan AYRI bir bağlantı alır ve kendi başına commit eder.

    psycopg havuzu her çağrıda farklı bir bağlantı verdiği için bu, çağıran
    kodun içinde bulunduğu transaction'dan bağımsızdır. İsim kasıtlı olarak
    ayrıdır: niyet okunabilir olmalı (K15).
    """
    with pool().connection() as conn:
        with conn.transaction():
            yield conn
```

- [ ] **Adım 4: `packages/kernel/state/migrations/001_initial.sql` yaz**

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    id                    uuid PRIMARY KEY,
    tenant_id             text NOT NULL,
    workflow_name         text NOT NULL,
    workflow_version_hash text NOT NULL,
    status                text NOT NULL,
    input                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    output                jsonb,
    budget_usd            numeric(12,6) NOT NULL,
    spent_usd             numeric(12,6) NOT NULL DEFAULT 0,
    step_count            int NOT NULL DEFAULT 0,
    max_steps             int NOT NULL,
    idempotency_key       text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runs_status_check CHECK (status IN
        ('pending','running','completed','failed','budget_exceeded','uncertain')),
    CONSTRAINT runs_idem_unique UNIQUE (tenant_id, workflow_name, idempotency_key)
);

CREATE TABLE steps (
    id                uuid PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_id           text NOT NULL,
    attempt           int  NOT NULL DEFAULT 1,
    status            text NOT NULL,
    input             jsonb,
    output            jsonb,
    model             text,
    tokens_in         int,
    tokens_out        int,
    cache_read_tokens int,
    cost_usd          numeric(12,6),
    error             text,
    started_at        timestamptz NOT NULL DEFAULT now(),
    ended_at          timestamptz,
    CONSTRAINT steps_status_check CHECK (status IN ('running','completed','failed')),
    CONSTRAINT steps_attempt_unique UNIQUE (run_id, node_id, attempt)
);

-- Kural 5: ekleme-yalnız olay kaydı.
CREATE TABLE events (
    id         bigserial PRIMARY KEY,
    run_id     uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq        int  NOT NULL,
    type       text NOT NULL,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT events_seq_unique UNIQUE (run_id, seq)
);

CREATE OR REPLACE FUNCTION events_reject_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events tablosu ekleme-yalnizdir (Kural 5): UPDATE reddedildi';
END;
$$ LANGUAGE plpgsql;

-- Yalnız UPDATE engellenir. DELETE serbesttir; runs silinince CASCADE çalışmalı
-- (kiracı silme, test temizliği). Değişmezlik güvencesi "yazılan satır değişmez"dir.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION events_reject_update();

-- K15: yan etki rezervasyonu. idempotency_key global olarak tekildir.
-- step_id, anahtarı İLK rezerve eden adımı gösterir; yeniden denemede yeni bir
-- steps satırı açılır ama tool_calls satırı aynı kalır.
CREATE TABLE tool_calls (
    id               uuid PRIMARY KEY,
    step_id          uuid NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    tool_name        text NOT NULL,
    idempotency_key  text NOT NULL UNIQUE,
    request          jsonb NOT NULL,
    response         jsonb,
    status           text NOT NULL,
    lease_expires_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz,
    CONSTRAINT tool_calls_status_check CHECK (status IN
        ('reserved','completed','failed','uncertain'))
);

CREATE TABLE job_queue (
    id           bigserial PRIMARY KEY,
    run_id       uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_id      text NOT NULL,
    available_at timestamptz NOT NULL DEFAULT now(),
    locked_by    text,
    locked_until timestamptz,
    attempts     int NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX job_queue_claimable ON job_queue (available_at, locked_until);

-- Yalnız testlerin yan etki saymasi icin. Uretim semasinin parcasi degildir;
-- M6 paketlemesinde test semasina tasinacaktir.
CREATE TABLE side_effects (
    id         bigserial PRIMARY KEY,
    key        text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

- [ ] **Adım 5: `packages/kernel/state/migrate.py` yaz**

```python
"""Idempotent göç koşucusu.

Alembic kasıtlı olarak kullanılmıyor: M1'de tek bir ileri-yönlü göç var ve
Alembic bir bağımlılık + konfig dizini + revizyon grafiği getiriyor. İhtiyaç
doğduğunda (M2'de şema büyüyünce) geçilir.
"""
from __future__ import annotations

from pathlib import Path

from kernel.state import db

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def apply_migrations() -> list[str]:
    """Uygulanmamış göçleri sırayla uygular. Uygulananların adını döner."""
    applied: list[str] = []
    with db.tx() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        done = {r[0] for r in rows}

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,)
            )
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    for name in apply_migrations():
        print(f"uygulandı: {name}")
```

- [ ] **Adım 6: Test paket yapısını kur ve `tests/conftest.py` yaz**

`tests/` bir Python paketidir (alt süreçten `tests.fakes` import edilebilmesi
için gerekir). Paket olduğunda pytest paket modunda toplar ve **her alt dizinin
de** `__init__.py` içermesi gerekir; yoksa modül adı çakışması doğar. Hepsini
şimdi oluştur:

```bash
mkdir -p tests/state tests/tools tests/gateway tests/orchestrator tests/acceptance
touch tests/__init__.py tests/state/__init__.py tests/tools/__init__.py \
      tests/gateway/__init__.py tests/orchestrator/__init__.py \
      tests/acceptance/__init__.py
```

```python
from __future__ import annotations

import os

import pytest

TEST_DSN = os.environ.get(
    "OTOMASYON_TEST_DSN", "postgresql://localhost/otomasyon_test"
)

TABLES = [
    "side_effects", "job_queue", "tool_calls",
    "events", "steps", "runs",
]


@pytest.fixture(scope="session", autouse=True)
def _schema():
    os.environ["OTOMASYON_DB_DSN"] = TEST_DSN
    from kernel.state import db, migrate

    db.reset_pool()
    with db.tx() as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db.reset_pool()
    migrate.apply_migrations()
    yield
    db.reset_pool()


@pytest.fixture(autouse=True)
def _clean():
    from kernel.state import db

    with db.tx() as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    yield
```

- [ ] **Adım 7: Başarısız testi yaz**

`tests/state/test_migrate.py`:

```python
import psycopg
import pytest

from kernel.state import db, migrate


def test_migrations_idempotent():
    """İkinci koşu hiçbir şey uygulamamalı."""
    assert migrate.apply_migrations() == []


def test_expected_tables_exist():
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        ).fetchall()
    names = {r[0] for r in rows}
    for expected in ("runs", "steps", "events", "tool_calls", "job_queue"):
        assert expected in names


def test_events_table_rejects_update():
    """Kural 5 veritabanı seviyesinde uygulanıyor mu."""
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key) VALUES"
            " ('11111111-1111-1111-1111-111111111111', 't1', 'wf', 'h1',"
            " 'pending', 1.0, 10, 'k1')"
        )
        conn.execute(
            "INSERT INTO events (run_id, seq, type) VALUES"
            " ('11111111-1111-1111-1111-111111111111', 1, 'run_created')"
        )

    with pytest.raises(psycopg.errors.RaiseException):
        with db.tx() as conn:
            conn.execute("UPDATE events SET type = 'degistirildi'")
```

- [ ] **Adım 8: Testleri koş, başarısız olduklarını gör**

```bash
export OTOMASYON_TEST_DSN=postgresql://localhost/otomasyon_test
pytest tests/state/test_migrate.py -v
```

Beklenen: `ModuleNotFoundError: No module named 'kernel'` veya göç dosyaları eksikse ilgili hata.

- [ ] **Adım 9: Testleri koş, geçtiklerini gör**

Adım 3–6'daki dosyalar yazıldıktan sonra:

```bash
pytest tests/state/test_migrate.py -v
```

Beklenen: 3 test PASSED.

- [ ] **Adım 10: Commit**

```bash
git add pyproject.toml packages tests
git commit -m "feat(state): proje iskeleti, Postgres şeması ve göç koşucusu

events tablosunda UPDATE'i reddeden tetikleyici ile Kural 5 veritabanı
seviyesinde uygulanıyor.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 2: Ekleme-yalnız olay kaydı

**Dosyalar:**
- Oluştur: `packages/kernel/state/events.py`
- Test: `tests/state/test_events.py`

**Arayüzler:**
- Tüketir: `db.tx()` (Görev 1)
- Üretir: `events.append(conn, run_id: UUID, type: str, payload: dict) -> int` (yeni `seq` döner), `events.read(conn, run_id: UUID) -> list[Event]`, `events.Event` (Pydantic: `seq: int`, `type: str`, `payload: dict`, `created_at: datetime`)

- [ ] **Adım 1: Başarısız testi yaz**

`tests/state/test_events.py`:

```python
import threading
import uuid

import pytest

from kernel.state import db, events


def _make_run() -> uuid.UUID:
    run_id = uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'pending', 1.0, 10, %s)",
            (run_id, str(run_id)),
        )
    return run_id


def test_append_returns_increasing_seq():
    run_id = _make_run()
    with db.tx() as conn:
        assert events.append(conn, run_id, "run_created", {}) == 1
        assert events.append(conn, run_id, "step_started", {"node": "a"}) == 2


def test_read_returns_events_in_order():
    run_id = _make_run()
    with db.tx() as conn:
        events.append(conn, run_id, "run_created", {})
        events.append(conn, run_id, "step_started", {"node": "a"})

    with db.tx() as conn:
        got = events.read(conn, run_id)

    assert [e.seq for e in got] == [1, 2]
    assert got[1].payload == {"node": "a"}


def test_concurrent_appends_produce_unique_seqs():
    """İki eşzamanlı yazar aynı seq'i almamalı."""
    run_id = _make_run()
    errors: list[Exception] = []

    def writer():
        try:
            for _ in range(10):
                with db.tx() as conn:
                    events.append(conn, run_id, "tick", {})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with db.tx() as conn:
        got = events.read(conn, run_id)
    assert [e.seq for e in got] == list(range(1, 41))
```

- [ ] **Adım 2: Testi koş, başarısız olduğunu gör**

```bash
pytest tests/state/test_events.py -v
```

Beklenen: `ImportError: cannot import name 'events'`.

- [ ] **Adım 3: `packages/kernel/state/events.py` yaz**

```python
"""Ekleme-yalnız olay kaydı (Kural 5).

seq numarası run başına ardışıktır. Yarış koşulunu önlemek için run satırı
FOR UPDATE ile kilitlenir: events tablosunda henüz satır yokken oradan kilit
alınamaz, ama run satırı her zaman vardır.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel


class Event(BaseModel):
    seq: int
    type: str
    payload: dict
    created_at: datetime


def append(conn: psycopg.Connection, run_id: UUID, type: str, payload: dict) -> int:
    conn.execute("SELECT id FROM runs WHERE id = %s FOR UPDATE", (run_id,))
    row = conn.execute(
        "INSERT INTO events (run_id, seq, type, payload)"
        " SELECT %s, coalesce(max(seq), 0) + 1, %s, %s FROM events WHERE run_id = %s"
        " RETURNING seq",
        (run_id, type, Jsonb(payload), run_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def read(conn: psycopg.Connection, run_id: UUID) -> list[Event]:
    rows = conn.execute(
        "SELECT seq, type, payload, created_at FROM events"
        " WHERE run_id = %s ORDER BY seq",
        (run_id,),
    ).fetchall()
    return [
        Event(seq=r[0], type=r[1], payload=r[2], created_at=r[3]) for r in rows
    ]
```

- [ ] **Adım 4: Testi koş, geçtiğini gör**

```bash
pytest tests/state/test_events.py -v
```

Beklenen: 3 test PASSED.

- [ ] **Adım 5: Commit**

```bash
git add packages/kernel/state/events.py tests/state/test_events.py
git commit -m "feat(state): ekleme-yalnız olay kaydı, run başına ardışık seq

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 3: Kiralı iş kuyruğu

**Dosyalar:**
- Oluştur: `packages/kernel/state/queue.py`
- Test: `tests/state/test_queue.py`

**Arayüzler:**
- Tüketir: `db.tx()` (Görev 1)
- Üretir:
  - `queue.Job` (Pydantic: `id: int`, `run_id: UUID`, `node_id: str`, `attempts: int`)
  - `queue.enqueue(conn, run_id: UUID, node_id: str, delay_seconds: int = 0) -> int`
  - `queue.claim(conn, worker_id: str, lease_seconds: int = 30) -> Job | None`
  - `queue.complete(conn, job_id: int) -> None`
  - `queue.release(conn, job_id: int, delay_seconds: int) -> None`

- [ ] **Adım 1: Başarısız testi yaz**

`tests/state/test_queue.py`:

```python
import uuid

from kernel.state import db, queue


def _make_run() -> uuid.UUID:
    run_id = uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'pending', 1.0, 10, %s)",
            (run_id, str(run_id)),
        )
    return run_id


def test_claim_returns_enqueued_job():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
    with db.tx() as conn:
        job = queue.claim(conn, "w1")
    assert job is not None
    assert job.node_id == "n1"
    assert job.attempts == 1


def test_claim_returns_none_when_empty():
    with db.tx() as conn:
        assert queue.claim(conn, "w1") is None


def test_two_workers_never_get_the_same_job():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        queue.enqueue(conn, run_id, "n2")

    with db.tx() as conn:
        first = queue.claim(conn, "w1")
    with db.tx() as conn:
        second = queue.claim(conn, "w2")
    with db.tx() as conn:
        third = queue.claim(conn, "w3")

    assert first is not None and second is not None
    assert first.id != second.id
    assert third is None, "iki iş vardı, üçüncü talep boş dönmeli"


def test_expired_lease_is_reclaimable():
    """Worker çökerse iş kirası dolunca başkası alabilmeli."""
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        job = queue.claim(conn, "w1", lease_seconds=30)
        assert job is not None
        # Kirayı geçmişe çekerek çökmüş worker'ı simüle et.
        conn.execute(
            "UPDATE job_queue SET locked_until = now() - interval '1 second'"
            " WHERE id = %s",
            (job.id,),
        )

    with db.tx() as conn:
        again = queue.claim(conn, "w2")

    assert again is not None
    assert again.id == job.id
    assert again.attempts == 2, "yeniden alım attempts sayacını artırmalı"


def test_release_delays_availability():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        job = queue.claim(conn, "w1")
        assert job is not None
        queue.release(conn, job.id, delay_seconds=60)

    with db.tx() as conn:
        assert queue.claim(conn, "w2") is None, "gecikmeli iş hemen alınamaz"


def test_complete_removes_job():
    run_id = _make_run()
    with db.tx() as conn:
        queue.enqueue(conn, run_id, "n1")
        job = queue.claim(conn, "w1")
        assert job is not None
        queue.complete(conn, job.id)

    with db.tx() as conn:
        assert queue.claim(conn, "w2") is None
```

- [ ] **Adım 2: Testi koş, başarısız olduğunu gör**

```bash
pytest tests/state/test_queue.py -v
```

Beklenen: `ImportError: cannot import name 'queue'`.

- [ ] **Adım 3: `packages/kernel/state/queue.py` yaz**

```python
"""Postgres tabanlı kiralı iş kuyruğu.

Dayanıklılık modeli: bir iş "kira" ile alınır. Worker çökerse kira dolar ve iş
başka bir worker tarafından alınabilir hale gelir. Harici bir kuyruk servisi
YOK (K3): tek altyapı bağımlılığı Postgres.
"""
from __future__ import annotations

from uuid import UUID

import psycopg
from pydantic import BaseModel


class Job(BaseModel):
    id: int
    run_id: UUID
    node_id: str
    attempts: int


def enqueue(
    conn: psycopg.Connection, run_id: UUID, node_id: str, delay_seconds: int = 0
) -> int:
    row = conn.execute(
        "INSERT INTO job_queue (run_id, node_id, available_at)"
        " VALUES (%s, %s, now() + make_interval(secs => %s)) RETURNING id",
        (run_id, node_id, delay_seconds),
    ).fetchone()
    assert row is not None
    return int(row[0])


def claim(
    conn: psycopg.Connection, worker_id: str, lease_seconds: int = 30
) -> Job | None:
    row = conn.execute(
        "UPDATE job_queue SET"
        "   locked_by = %s,"
        "   locked_until = now() + make_interval(secs => %s),"
        "   attempts = attempts + 1"
        " WHERE id = ("
        "   SELECT id FROM job_queue"
        "   WHERE available_at <= now()"
        "     AND (locked_until IS NULL OR locked_until < now())"
        "   ORDER BY available_at"
        "   FOR UPDATE SKIP LOCKED"
        "   LIMIT 1"
        " )"
        " RETURNING id, run_id, node_id, attempts",
        (worker_id, lease_seconds),
    ).fetchone()
    if row is None:
        return None
    return Job(id=row[0], run_id=row[1], node_id=row[2], attempts=row[3])


def complete(conn: psycopg.Connection, job_id: int) -> None:
    conn.execute("DELETE FROM job_queue WHERE id = %s", (job_id,))


def release(conn: psycopg.Connection, job_id: int, delay_seconds: int) -> None:
    """İşi kuyruğa geri koyar; kilidi bırakır ve erişimi geciktirir."""
    conn.execute(
        "UPDATE job_queue SET locked_by = NULL, locked_until = NULL,"
        " available_at = now() + make_interval(secs => %s) WHERE id = %s",
        (delay_seconds, job_id),
    )
```

- [ ] **Adım 4: Testi koş, geçtiğini gör**

```bash
pytest tests/state/test_queue.py -v
```

Beklenen: 6 test PASSED.

- [ ] **Adım 5: Commit**

```bash
git add packages/kernel/state/queue.py tests/state/test_queue.py
git commit -m "feat(state): SKIP LOCKED tabanlı kiralı iş kuyruğu

Çökmüş worker'ın işi kira dolunca yeniden alınabilir; harici kuyruk
servisi yok (K3).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 4: Araç sözleşmesi ve iki aşamalı rezervasyon — mutlu yol

**Dosyalar:**
- Oluştur: `packages/kernel/tools/__init__.py`, `packages/kernel/tools/base.py`
- Oluştur: `packages/kernel/tools/registry.py`
- Oluştur: `packages/kernel/tools/execution.py`
- Oluştur: `tests/fakes.py`
- Test: `tests/tools/test_execution_happy.py`

**Arayüzler:**
- Tüketir: `db.independent_tx()` (Görev 1)
- Üretir:
  - `base.ToolOutcome` (StrEnum: `COMPLETED`, `FAILED`, `UNCERTAIN`, `DEFERRED`)
  - `base.ToolResult` (Pydantic: `outcome: ToolOutcome`, `response: dict | None`, `error: str | None`, `retry_after_seconds: int`)
  - `base.Tool` (ABC: sınıf değişkenleri `name: str`, `supports_reconcile: bool`, `external_idempotency: bool`; metotlar `idempotency_key(run_id, payload) -> str`, `execute(payload) -> dict`, `compensate(payload, response) -> None`, `reconcile(payload) -> dict | None`)
  - `registry.register(tool)`, `registry.get(name) -> Tool`, `registry.clear()`
  - `execution.execute_tool(tool, run_id, step_id, payload, lease_seconds=60) -> ToolResult`

- [ ] **Adım 1: `packages/kernel/tools/base.py` yaz**

```python
"""Araç sözleşmesi — Kural 4: yan etkinin TEK noktası.

İki yetenek bayrağı, K15 kurtarma sırasını belirler:
  supports_reconcile   — yazma gerçekleşti mi diye dış sisteme sorulabilir
  external_idempotency — dış API bizim anahtarımızı kabul eder, tekrar güvenlidir
İkisi de yoksa kirası dolmuş bir rezervasyon 'uncertain' olur ve insana gider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel


class ToolOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    DEFERRED = "deferred"


class ToolResult(BaseModel):
    outcome: ToolOutcome
    response: dict | None = None
    error: str | None = None
    retry_after_seconds: int = 0


class Tool(ABC):
    name: ClassVar[str]
    supports_reconcile: ClassVar[bool] = False
    external_idempotency: ClassVar[bool] = False

    @abstractmethod
    def idempotency_key(self, run_id: UUID, payload: dict) -> str:
        """Bu yan etkinin evrensel kimliği. Yeniden denemede DEĞİŞMEMELİDİR."""

    @abstractmethod
    def execute(self, payload: dict) -> dict:
        """Yan etkiyi uygular. Hata durumunda istisna fırlatır."""

    @abstractmethod
    def compensate(self, payload: dict, response: dict) -> None:
        """Yan etkiyi geri alır. M1 akışında çağrılmaz; telafi zincirleri M3'te
        devreye girer. Sözleşmenin parçasıdır: her araç geri almayı ilk günden
        düşünmek zorundadır (§4.1 madde 1)."""

    def reconcile(self, payload: dict) -> dict | None:
        """Yazma gerçekleşmiş mi diye dış sisteme sorar.

        Bulundu → yanıt sözlüğü. Bulunamadı → None.
        supports_reconcile=True olan araçlar bunu uygulamak ZORUNDADIR.
        """
        raise NotImplementedError(
            f"{self.name}: supports_reconcile=True ama reconcile() uygulanmamış"
        )
```

- [ ] **Adım 2: `packages/kernel/tools/registry.py` yaz**

```python
"""Araç kaydı: ad → örnek.

M1'de süreç-içi basit bir sözlük. Kiracıya göre bağlama (yetenek araçları,
§6.2 K16) M2'de derleyiciyle birlikte gelir.
"""
from __future__ import annotations

from kernel.tools.base import Tool

_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _TOOLS[tool.name] = tool


def get(name: str) -> Tool:
    if name not in _TOOLS:
        raise KeyError(f"kayıtlı olmayan araç: {name}")
    return _TOOLS[name]


def clear() -> None:
    _TOOLS.clear()
```

- [ ] **Adım 3: `tests/fakes.py` yaz**

```python
"""Test araçları.

SlowWriterTool, kill -9 penceresini açmak için yazma öncesi/sonrası
bekleyebilen bir araçtır. Yan etkisi side_effects tablosuna bir satırdır;
test sadece satır sayar.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import ClassVar
from uuid import UUID

from kernel.state import db
from kernel.tools.base import Tool


class SlowWriterTool(Tool):
    name: ClassVar[str] = "test.slow_writer"
    supports_reconcile: ClassVar[bool] = True

    def idempotency_key(self, run_id: UUID, payload: dict) -> str:
        return f"{run_id}:{payload['key']}"

    def execute(self, payload: dict) -> dict:
        self._pause("before_write")
        with db.independent_tx() as conn:
            conn.execute(
                "INSERT INTO side_effects (key) VALUES (%s)", (payload["key"],)
            )
        self._pause("after_write")
        return {"written": payload["key"]}

    def compensate(self, payload: dict, response: dict) -> None:
        with db.independent_tx() as conn:
            conn.execute(
                "DELETE FROM side_effects WHERE key = %s", (payload["key"],)
            )

    def reconcile(self, payload: dict) -> dict | None:
        with db.independent_tx() as conn:
            row = conn.execute(
                "SELECT 1 FROM side_effects WHERE key = %s LIMIT 1",
                (payload["key"],),
            ).fetchone()
        return {"written": payload["key"]} if row else None

    @staticmethod
    def _pause(point: str) -> None:
        """OTOMASYON_KILL_POINT bu noktaya eşitse marker dosyası yazıp bekler.

        Test, marker dosyasını görünce worker'ı SIGKILL ile öldürür.
        """
        if os.environ.get("OTOMASYON_KILL_POINT") != point:
            return
        marker = os.environ.get("OTOMASYON_KILL_MARKER")
        if marker:
            Path(marker).write_text(point, encoding="utf-8")
        time.sleep(30)


class NoReconcileTool(SlowWriterTool):
    """reconcile sunmayan araç — kirası dolunca 'uncertain' olmalı."""

    name: ClassVar[str] = "test.no_reconcile"
    supports_reconcile: ClassVar[bool] = False


class AlwaysFailsTool(Tool):
    name: ClassVar[str] = "test.always_fails"

    def idempotency_key(self, run_id: UUID, payload: dict) -> str:
        return f"{run_id}:fail"

    def execute(self, payload: dict) -> dict:
        raise RuntimeError("dış sistem patladı")

    def compensate(self, payload: dict, response: dict) -> None:
        return None
```

- [ ] **Adım 4: Başarısız testi yaz**

`tests/tools/test_execution_happy.py`:

```python
import uuid

import pytest

from kernel.state import db
from kernel.tools import execution, registry
from kernel.tools.base import ToolOutcome
from tests.fakes import AlwaysFailsTool, SlowWriterTool


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    registry.register(SlowWriterTool())
    registry.register(AlwaysFailsTool())
    yield
    registry.clear()


def _make_run_and_step() -> tuple[uuid.UUID, uuid.UUID]:
    run_id, step_id = uuid.uuid4(), uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'running', 1.0, 10, %s)",
            (run_id, str(run_id)),
        )
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, status)"
            " VALUES (%s, %s, 'kaydet', 'running')",
            (step_id, run_id),
        )
    return run_id, step_id


def _side_effect_count(key: str) -> int:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT count(*) FROM side_effects WHERE key = %s", (key,)
        ).fetchone()
    return int(row[0])


def test_happy_path_writes_once_and_completes():
    run_id, step_id = _make_run_and_step()
    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "a"}
    )
    assert result.outcome is ToolOutcome.COMPLETED
    assert result.response == {"written": "a"}
    assert _side_effect_count("a") == 1

    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s",
            (f"{run_id}:a",),
        ).fetchone()
    assert row[0] == "completed"


def test_second_call_with_same_key_does_not_execute_again():
    """Idempotency'nin asıl kazancı: kaydedilmiş yanıt döner, çağrı yapılmaz."""
    run_id, step_id = _make_run_and_step()
    first = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "a"}
    )
    second = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "a"}
    )

    assert first.outcome is ToolOutcome.COMPLETED
    assert second.outcome is ToolOutcome.COMPLETED
    assert second.response == {"written": "a"}
    assert _side_effect_count("a") == 1, "ikinci çağrı yan etkiyi tekrarlamamalı"


def test_reservation_is_created_before_execution():
    """K15: rezervasyon satırı dış çağrıdan ÖNCE var olmalı."""
    run_id, step_id = _make_run_and_step()
    seen: list[str | None] = []

    class Peeking(SlowWriterTool):
        def execute(self, payload: dict) -> dict:
            with db.independent_tx() as conn:
                row = conn.execute(
                    "SELECT status FROM tool_calls WHERE idempotency_key = %s",
                    (f"{run_id}:{payload['key']}",),
                ).fetchone()
            seen.append(row[0] if row else None)
            return super().execute(payload)

    execution.execute_tool(Peeking(), run_id, step_id, {"key": "b"})
    assert seen == ["reserved"], "dış çağrı sırasında rezervasyon commit'li olmalı"


def test_failure_marks_failed_and_allows_retry():
    run_id, step_id = _make_run_and_step()
    first = execution.execute_tool(
        registry.get("test.always_fails"), run_id, step_id, {}
    )
    assert first.outcome is ToolOutcome.FAILED
    assert "dış sistem patladı" in (first.error or "")

    second = execution.execute_tool(
        registry.get("test.always_fails"), run_id, step_id, {}
    )
    assert second.outcome is ToolOutcome.FAILED, "başarısız iş yeniden denenebilmeli"
```

- [ ] **Adım 5: Testi koş, başarısız olduğunu gör**

```bash
pytest tests/tools/test_execution_happy.py -v
```

Beklenen: `ImportError: cannot import name 'execution'`.

- [ ] **Adım 6: `packages/kernel/tools/execution.py` yaz**

```python
"""İki aşamalı kiralı rezervasyon (§5.1 / K15).

  1. REZERVE ET — tool_calls satırı KENDİ transaction'ında commit edilir.
     Dış transaction'a bağlanırsa rollback'te rezervasyon buharlaşır ve
     çift yazma koruması ortadan kalkar. UNIQUE ihlali = başkası üstlendi.
  2. ÇAĞIR    — dış sistem çağrısı.
  3. TAMAMLA  — status='completed' + yanıt kaydı.

Kirası dolmuş bir rezervasyon ASLA kör tekrar edilmez: önce reconcile,
sonra external_idempotency, ikisi de yoksa 'uncertain' → insan kuyruğu.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from kernel.state import db
from kernel.tools.base import Tool, ToolOutcome, ToolResult


def execute_tool(
    tool: Tool,
    run_id: UUID,
    step_id: UUID,
    payload: dict,
    lease_seconds: int = 60,
) -> ToolResult:
    key = tool.idempotency_key(run_id, payload)

    if _reserve(tool.name, step_id, key, payload, lease_seconds):
        return _call_and_complete(tool, key, payload)
    return _handle_existing(tool, key, payload, lease_seconds)


# --- 1. aşama -----------------------------------------------------------------

def _reserve(
    tool_name: str, step_id: UUID, key: str, payload: dict, lease_seconds: int
) -> bool:
    """True = rezervasyon bizim. False = anahtar zaten alınmış."""
    try:
        with db.independent_tx() as conn:
            conn.execute(
                "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
                " request, status, lease_expires_at) VALUES"
                " (%s, %s, %s, %s, %s, 'reserved', now() + make_interval(secs => %s))",
                (uuid4(), step_id, tool_name, key, Jsonb(payload), lease_seconds),
            )
        return True
    except psycopg.errors.UniqueViolation:
        return False


# --- 2. ve 3. aşama -----------------------------------------------------------

def _call_and_complete(tool: Tool, key: str, payload: dict) -> ToolResult:
    try:
        response = tool.execute(payload)
    except Exception as exc:  # noqa: BLE001 — her dış hata kaydedilir
        _mark(key, "failed", {"error": str(exc)})
        return ToolResult(outcome=ToolOutcome.FAILED, error=str(exc))
    _mark(key, "completed", response)
    return ToolResult(outcome=ToolOutcome.COMPLETED, response=response)


def _mark(key: str, status: str, response: dict) -> None:
    with db.independent_tx() as conn:
        conn.execute(
            "UPDATE tool_calls SET status = %s, response = %s, completed_at = now()"
            " WHERE idempotency_key = %s",
            (status, Jsonb(response), key),
        )


# --- Anahtar zaten alınmışsa --------------------------------------------------

def _handle_existing(
    tool: Tool, key: str, payload: dict, lease_seconds: int
) -> ToolResult:
    with db.independent_tx() as conn:
        row = conn.execute(
            "SELECT status, response, (lease_expires_at < now()) AS expired"
            " FROM tool_calls WHERE idempotency_key = %s",
            (key,),
        ).fetchone()

    if row is None:
        # Yarış: satır az önce silinmiş olabilir. İşi ertele, baştan denensin.
        return ToolResult(outcome=ToolOutcome.DEFERRED, retry_after_seconds=1)

    status, response, expired = row[0], row[1], row[2]

    if status == "completed":
        return ToolResult(outcome=ToolOutcome.COMPLETED, response=response)

    if status == "uncertain":
        return ToolResult(
            outcome=ToolOutcome.UNCERTAIN,
            error="önceki çağrının sonucu belirsiz; insan incelemesi gerekli",
        )

    if status == "failed":
        if _retake_failed(key, lease_seconds):
            return _call_and_complete(tool, key, payload)
        return ToolResult(outcome=ToolOutcome.DEFERRED, retry_after_seconds=1)

    # status == 'reserved'
    if not expired:
        # Başka bir worker çalışıyor. Bloke OLMA — worker'ı meşgul tutar.
        return ToolResult(
            outcome=ToolOutcome.DEFERRED, retry_after_seconds=lease_seconds
        )

    return _recover_expired(tool, key, payload, lease_seconds)


def _retake_failed(key: str, lease_seconds: int) -> bool:
    """Başarısız bir çağrıyı atomik olarak yeniden rezerve eder."""
    with db.independent_tx() as conn:
        row = conn.execute(
            "UPDATE tool_calls SET status = 'reserved', response = NULL,"
            " completed_at = NULL,"
            " lease_expires_at = now() + make_interval(secs => %s)"
            " WHERE idempotency_key = %s AND status = 'failed' RETURNING id",
            (lease_seconds, key),
        ).fetchone()
    return row is not None


def _recover_expired(
    tool: Tool, key: str, payload: dict, lease_seconds: int
) -> ToolResult:
    """Kirası dolmuş rezervasyon — çağrı dış sisteme ulaştı mı bilinmiyor."""
    with db.independent_tx() as conn:
        row = conn.execute(
            "UPDATE tool_calls"
            " SET lease_expires_at = now() + make_interval(secs => %s)"
            " WHERE idempotency_key = %s AND status = 'reserved'"
            "   AND lease_expires_at < now() RETURNING id",
            (lease_seconds, key),
        ).fetchone()
    if row is None:
        # Başkası devraldı.
        return ToolResult(
            outcome=ToolOutcome.DEFERRED, retry_after_seconds=lease_seconds
        )

    if tool.supports_reconcile:
        found = tool.reconcile(payload)
        if found is not None:
            _mark(key, "completed", found)
            return ToolResult(outcome=ToolOutcome.COMPLETED, response=found)
        return _call_and_complete(tool, key, payload)

    if tool.external_idempotency:
        return _call_and_complete(tool, key, payload)

    _mark(key, "uncertain", {"reason": "kira doldu, doğrulama yolu yok"})
    return ToolResult(
        outcome=ToolOutcome.UNCERTAIN,
        error="kira doldu ve reconcile/external_idempotency yok; kör tekrar yapılmadı",
    )
```

- [ ] **Adım 7: Testi koş, geçtiğini gör**

```bash
pytest tests/tools/test_execution_happy.py -v
```

Beklenen: 4 test PASSED.

- [ ] **Adım 8: Commit**

```bash
git add packages/kernel/tools tests/fakes.py tests/tools/test_execution_happy.py
git commit -m "feat(tools): iki aşamalı kiralı rezervasyon — mutlu yol

Rezervasyon dış çağrıdan önce kendi transaction'ında commit ediliyor;
aynı anahtarla ikinci çağrı kaydedilmiş yanıtı döndürüyor (K15).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 5: Rezervasyon kurtarma yolları — kira dolması

**Dosyalar:**
- Test: `tests/tools/test_execution_recovery.py`

**Arayüzler:**
- Tüketir: `execution.execute_tool` (Görev 4), `fakes.SlowWriterTool`, `fakes.NoReconcileTool`

Bu görev yeni üretim kodu **yazmaz**; Görev 4'te yazılan `_recover_expired` yolunu doğrular. Ayrı görevdir çünkü bir gözden geçiren mutlu yolu onaylayıp kurtarma semantiğini reddedebilir.

- [ ] **Adım 1: Başarısız testi yaz**

`tests/tools/test_execution_recovery.py`:

```python
import uuid

import pytest
from psycopg.types.json import Jsonb

from kernel.state import db
from kernel.tools import execution, registry
from kernel.tools.base import ToolOutcome
from tests.fakes import NoReconcileTool, SlowWriterTool


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    registry.register(SlowWriterTool())
    registry.register(NoReconcileTool())
    yield
    registry.clear()


def _make_run_and_step() -> tuple[uuid.UUID, uuid.UUID]:
    run_id, step_id = uuid.uuid4(), uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'running', 1.0, 10, %s)",
            (run_id, str(run_id)),
        )
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, status)"
            " VALUES (%s, %s, 'kaydet', 'running')",
            (step_id, run_id),
        )
    return run_id, step_id


def _orphan_reservation(step_id: uuid.UUID, key: str, payload: dict) -> None:
    """Çökmüş bir worker'ın bıraktığı, kirası dolmuş rezervasyonu simüle eder."""
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at) VALUES"
            " (%s, %s, 'test.slow_writer', %s, %s, 'reserved',"
            "  now() - interval '1 second')",
            (uuid.uuid4(), step_id, key, Jsonb(payload)),
        )


def _count(key: str) -> int:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT count(*) FROM side_effects WHERE key = %s", (key,)
        ).fetchone()
    return int(row[0])


def test_live_lease_defers_instead_of_blocking():
    """Kirası geçerli rezervasyon: worker bloke olmaz, iş ertelenir."""
    run_id, step_id = _make_run_and_step()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
            " request, status, lease_expires_at) VALUES"
            " (%s, %s, 'test.slow_writer', %s, '{}'::jsonb, 'reserved',"
            "  now() + interval '60 seconds')",
            (uuid.uuid4(), step_id, f"{run_id}:c"),
        )

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "c"}
    )
    assert result.outcome is ToolOutcome.DEFERRED
    assert result.retry_after_seconds > 0
    assert _count("c") == 0


def test_expired_lease_with_reconcile_finding_write_does_not_rewrite():
    """Yazma gerçekleşmişti: reconcile bulur, TEKRAR YAZILMAZ."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:d"
    _orphan_reservation(step_id, key, {"key": "d"})
    # Çökmeden önce yazma gerçekleşmişti:
    with db.tx() as conn:
        conn.execute("INSERT INTO side_effects (key) VALUES ('d')")

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "d"}
    )

    assert result.outcome is ToolOutcome.COMPLETED
    assert _count("d") == 1, "yan etki tekrarlanmamalı"


def test_expired_lease_with_reconcile_finding_nothing_reexecutes():
    """Yazma gerçekleşmemişti: reconcile bulamaz, güvenle tekrar çalıştırılır."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:e"
    _orphan_reservation(step_id, key, {"key": "e"})

    result = execution.execute_tool(
        registry.get("test.slow_writer"), run_id, step_id, {"key": "e"}
    )

    assert result.outcome is ToolOutcome.COMPLETED
    assert _count("e") == 1


def test_expired_lease_without_reconcile_becomes_uncertain():
    """Doğrulama yolu yok → kör tekrar YOK, insan kuyruğu."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:f"
    _orphan_reservation(step_id, key, {"key": "f"})

    result = execution.execute_tool(
        registry.get("test.no_reconcile"), run_id, step_id, {"key": "f"}
    )

    assert result.outcome is ToolOutcome.UNCERTAIN
    assert _count("f") == 0, "belirsiz durumda kör yazma yapılmamalı"

    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM tool_calls WHERE idempotency_key = %s", (key,)
        ).fetchone()
    assert row[0] == "uncertain"


def test_uncertain_stays_uncertain_on_retry():
    """Belirsiz bir çağrı otomatik olarak tekrar denenmez."""
    run_id, step_id = _make_run_and_step()
    key = f"{run_id}:g"
    _orphan_reservation(step_id, key, {"key": "g"})
    execution.execute_tool(
        registry.get("test.no_reconcile"), run_id, step_id, {"key": "g"}
    )

    again = execution.execute_tool(
        registry.get("test.no_reconcile"), run_id, step_id, {"key": "g"}
    )
    assert again.outcome is ToolOutcome.UNCERTAIN
    assert _count("g") == 0
```

- [ ] **Adım 2: Testi koş, sonucu incele**

```bash
pytest tests/tools/test_execution_recovery.py -v
```

Beklenen: Görev 4'ün kodu doğruysa 5 test PASSED. Bir test kırılırsa hata Görev 4'ün `_recover_expired` fonksiyonundadır — orayı düzelt, testi değiştirme.

- [ ] **Adım 3: Commit**

```bash
git add tests/tools/test_execution_recovery.py
git commit -m "test(tools): kira dolması kurtarma yolları

reconcile bulursa tekrar yazmaz; bulamazsa güvenle tekrarlar; doğrulama
yolu yoksa uncertain işaretler ve kör tekrar yapmaz (K15).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 6: LLM Ağ Geçidi ve token muhasebesi

**Dosyalar:**
- Oluştur: `packages/kernel/gateway/__init__.py`, `tiers.py`, `types.py`, `accounting.py`, `transport.py`, `gateway.py`
- Test: `tests/gateway/test_gateway.py`

**Arayüzler:**
- Tüketir: `db.tx()`, `events.append` (Görev 1–2)
- Üretir:
  - `types.Usage` (Pydantic: `input_tokens: int`, `output_tokens: int`, `cache_read_input_tokens: int = 0`, `cache_creation_input_tokens: int = 0`)
  - `types.LLMRequest` (Pydantic: `tier: str`, `system_layer1: str`, `system_layer2: str`, `user_content: str`, `max_tokens: int = 16000`)
  - `types.LLMResponse` (Pydantic: `text: str`, `model: str`, `usage: Usage`, `cost_usd: Decimal`, `stop_reason: str`)
  - `types.RawResponse` (Pydantic: `text: str`, `model: str`, `usage: Usage`, `stop_reason: str`)
  - `tiers.resolve(name: str) -> tuple[str, str]` → `(model, effort)`
  - `accounting.cost(model: str, usage: Usage) -> Decimal`
  - `gateway.Gateway(transport)` ve `Gateway.complete(req, run_id, step_id) -> LLMResponse`
  - `gateway.RefusalError`, `gateway.BudgetExceeded`

- [ ] **Adım 1: `packages/kernel/gateway/tiers.py` yaz**

```python
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
```

- [ ] **Adım 2: `packages/kernel/gateway/types.py` yaz**

```python
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
```

- [ ] **Adım 3: `packages/kernel/gateway/accounting.py` yaz**

```python
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


def cost(model: str, usage: Usage) -> Decimal:
    if model not in PRICES:
        raise KeyError(f"fiyatı bilinmeyen model: {model}")
    p = PRICES[model]
    total = (
        p.inp * usage.input_tokens
        + p.out * usage.output_tokens
        + p.cache_read * usage.cache_read_input_tokens
        + p.cache_write * usage.cache_creation_input_tokens
    )
    return (total / MILLION).quantize(Decimal("0.000001"))
```

- [ ] **Adım 4: `packages/kernel/gateway/transport.py` yaz**

```python
"""anthropic SDK'sının TEK import noktası (spec §6.1).

Başka hiçbir modül anthropic'i import etmez. Bu dosya değiştirilirken
§6.1'deki iki kuralı ihlal etmemeye dikkat: thinking ASLA disabled
gönderilmez, ve model kimliği yalnız tiers.py'den gelir.
"""
from __future__ import annotations

from typing import Protocol

from kernel.gateway.types import RawResponse, Usage


class Transport(Protocol):
    def send(
        self,
        *,
        model: str,
        effort: str,
        system_layer1: str,
        system_layer2: str,
        user_content: str,
        max_tokens: int,
    ) -> RawResponse: ...


class AnthropicTransport:
    def __init__(self, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client

    def send(
        self,
        *,
        model: str,
        effort: str,
        system_layer1: str,
        system_layer2: str,
        user_content: str,
        max_tokens: int,
    ) -> RawResponse:
        resp = self._client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[
                # Önbellek noktası 1 — ürün sabiti, tüm kiracılarda ortak.
                {
                    "type": "text",
                    "text": system_layer1,
                    "cache_control": {"type": "ephemeral"},
                },
                # Önbellek noktası 2 — kiracıya özel, çalışmalar arası sabit.
                {
                    "type": "text",
                    "text": system_layer2,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": user_content}],
            # ASLA {"type": "disabled"} — §6.1'deki sessiz arıza modu.
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            # §6.5: sunucu tarafı geri düşüş varsayılan açık.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        text = "".join(
            block.text for block in resp.content if block.type == "text"
        )
        return RawResponse(
            text=text,
            model=resp.model,
            stop_reason=resp.stop_reason,
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_input_tokens=getattr(
                    resp.usage, "cache_read_input_tokens", 0
                ) or 0,
                cache_creation_input_tokens=getattr(
                    resp.usage, "cache_creation_input_tokens", 0
                ) or 0,
            ),
        )
```

- [ ] **Adım 5: Başarısız testi yaz**

`tests/gateway/test_gateway.py`:

```python
import uuid
from decimal import Decimal

import pytest

from kernel.gateway import accounting, tiers
from kernel.gateway.gateway import BudgetExceeded, Gateway, RefusalError
from kernel.gateway.types import LLMRequest, RawResponse, Usage
from kernel.state import db, events


class FakeTransport:
    def __init__(self, response: RawResponse):
        self.response = response
        self.calls: list[dict] = []

    def send(self, **kwargs) -> RawResponse:
        self.calls.append(kwargs)
        return self.response


def _raw(stop_reason: str = "end_turn") -> RawResponse:
    return RawResponse(
        text="özet",
        model="claude-opus-5",
        stop_reason=stop_reason,
        usage=Usage(
            input_tokens=1000,
            output_tokens=200,
            cache_read_input_tokens=4000,
        ),
    )


def _make_run_and_step(budget: str = "1.0") -> tuple[uuid.UUID, uuid.UUID]:
    run_id, step_id = uuid.uuid4(), uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, 't1', 'wf', 'h1', 'running', %s, 10, %s)",
            (run_id, Decimal(budget), str(run_id)),
        )
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, status)"
            " VALUES (%s, %s, 'ozetle', 'running')",
            (step_id, run_id),
        )
    return run_id, step_id


def test_tier_resolves_to_model_and_effort():
    assert tiers.resolve("standard") == ("claude-opus-5", "high")
    assert tiers.resolve("fast") == ("claude-opus-5", "low")


def test_bulk_tier_is_locked():
    """K9: ucuz kademe eval kanıtı olmadan kullanılamaz."""
    with pytest.raises(KeyError):
        tiers.resolve("bulk")


def test_cost_uses_separate_cache_read_rate():
    usage = Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=4000)
    # 1000*5 + 200*25 + 4000*0.50 = 5000 + 5000 + 2000 = 12000 / 1e6
    assert accounting.cost("claude-opus-5", usage) == Decimal("0.012000")


def test_complete_records_usage_cost_and_event():
    run_id, step_id = _make_run_and_step()
    gw = Gateway(FakeTransport(_raw()))

    resp = gw.complete(
        LLMRequest(
            tier="fast",
            system_layer1="sabit",
            system_layer2="kiracı",
            user_content="girdi",
        ),
        run_id,
        step_id,
    )

    assert resp.text == "özet"
    assert resp.cost_usd == Decimal("0.012000")

    with db.tx() as conn:
        row = conn.execute(
            "SELECT model, tokens_in, tokens_out, cache_read_tokens, cost_usd"
            " FROM steps WHERE id = %s",
            (step_id,),
        ).fetchone()
        spent = conn.execute(
            "SELECT spent_usd FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        recorded = [e.type for e in events.read(conn, run_id)]

    assert row == ("claude-opus-5", 1000, 200, 4000, Decimal("0.012000"))
    assert spent == Decimal("0.012000")
    assert "llm_call_completed" in recorded


def test_refusal_raises():
    """§6.5: stop_reason='refusal' sessizce geçilmez."""
    run_id, step_id = _make_run_and_step()
    gw = Gateway(FakeTransport(_raw(stop_reason="refusal")))
    with pytest.raises(RefusalError):
        gw.complete(
            LLMRequest(
                tier="fast", system_layer1="a", system_layer2="b", user_content="c"
            ),
            run_id,
            step_id,
        )


def test_budget_exhausted_rejects_before_calling_model():
    run_id, step_id = _make_run_and_step(budget="0.000001")
    transport = FakeTransport(_raw())
    gw = Gateway(transport)

    with pytest.raises(BudgetExceeded):
        gw.complete(
            LLMRequest(
                tier="fast", system_layer1="a", system_layer2="b", user_content="c"
            ),
            run_id,
            step_id,
        )
    assert transport.calls == [], "bütçe aşımında model ÇAĞRILMAMALI"


def test_layers_are_passed_separately_for_caching():
    """§6.2: katmanlar ayrı gönderilmeli ki önbellek noktaları kurulabilsin."""
    run_id, step_id = _make_run_and_step()
    transport = FakeTransport(_raw())
    Gateway(transport).complete(
        LLMRequest(
            tier="standard",
            system_layer1="URUN_SABITI",
            system_layer2="KIRACI",
            user_content="GIRDI",
        ),
        run_id,
        step_id,
    )
    call = transport.calls[0]
    assert call["system_layer1"] == "URUN_SABITI"
    assert call["system_layer2"] == "KIRACI"
    assert call["user_content"] == "GIRDI"
    assert call["effort"] == "high"
```

- [ ] **Adım 6: Testi koş, başarısız olduğunu gör**

```bash
pytest tests/gateway/test_gateway.py -v
```

Beklenen: `ModuleNotFoundError: No module named 'kernel.gateway.gateway'`.

- [ ] **Adım 7: `packages/kernel/gateway/gateway.py` yaz**

```python
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
```

- [ ] **Adım 8: Testi koş, geçtiğini gör**

```bash
pytest tests/gateway/test_gateway.py -v
```

Beklenen: 7 test PASSED.

- [ ] **Adım 9: Ağ geçidi tekelini doğrulayan testi ekle**

`tests/gateway/test_gateway.py` sonuna:

```python
def test_only_transport_module_imports_anthropic():
    """§6.1: anthropic SDK'sının tek import noktası transport.py'dir."""
    import pathlib

    root = pathlib.Path(__file__).parents[2] / "packages" / "kernel"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if p.name != "transport.py" and "anthropic" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"anthropic'i doğrudan import eden modüller: {offenders}"
```

```bash
pytest tests/gateway/test_gateway.py -v
```

Beklenen: 8 test PASSED.

- [ ] **Adım 10: Commit**

```bash
git add packages/kernel/gateway tests/gateway
git commit -m "feat(gateway): tek çıkış kapısı, kademeleme ve token muhasebesi

Üç katmanlı prompt önbellek yapısı, adaptive thinking (asla disabled),
sunucu tarafı geri düşüş, bütçe ön kontrolü ve reddedilme yakalama.
bulk kademesi kilitli (K9).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 7: Orkestratör — worker döngüsü ve sabit akış

**Dosyalar:**
- Oluştur: `packages/kernel/config.py`
- Değiştir: `packages/kernel/gateway/transport.py` (`OfflineTransport` ekle)
- Oluştur: `packages/kernel/orchestrator/__init__.py`, `flow.py`, `step.py`, `runner.py`
- Oluştur: `tests/__init__.py` (boş; alt süreçten `tests.fakes` import edilebilsin)
- Değiştir: `tests/fakes.py` (`register_tools()` ekle)
- Test: `tests/orchestrator/test_runner.py`

**Arayüzler:**
- Tüketir: `queue.claim/complete/release/enqueue`, `events.append`, `execution.execute_tool`, `registry.get`, `Gateway.complete`
- Üretir:
  - `config.JOB_LEASE_SECONDS`, `config.TOOL_LEASE_SECONDS`, `config.POLL_INTERVAL_SECONDS`
  - `transport.OfflineTransport`
  - `flow.Node` (Pydantic: `id: str`, `type: Literal["llm_task","tool"]`, `tier: str | None`, `tool: str | None`, `next: str | None`)
  - `flow.M1_FLOW: dict[str, Node]`, `flow.FIRST_NODE: str`
  - `step.execute_step(job: Job, gateway: Gateway) -> str`
  - `runner.start_run(tenant_id, payload, budget_usd) -> UUID`
  - `runner.run_once(worker_id, gateway) -> bool`
  - `runner.run_forever(worker_id, gateway) -> None`

- [ ] **Adım 1: `packages/kernel/config.py` yaz**

```python
"""Çalışma zamanı ayarları. Sırlar burada DEĞİL (K14) — yalnız süre/eşik."""
from __future__ import annotations

import os

JOB_LEASE_SECONDS = int(os.environ.get("OTOMASYON_JOB_LEASE_SECONDS", "30"))
TOOL_LEASE_SECONDS = int(os.environ.get("OTOMASYON_TOOL_LEASE_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.environ.get("OTOMASYON_POLL_INTERVAL", "0.5"))
```

- [ ] **Adım 2: `OfflineTransport`'u `transport.py` sonuna ekle**

```python
class OfflineTransport:
    """Ağa çıkmadan sabit yanıt döndürür.

    Test iskelesi değildir: API anahtarı olmadan yerel geliştirme, kaos
    testleri ve M5'teki gölge modunun temelidir. Kullanım maliyeti sıfırdır
    ve muhasebe yolu yine de çalışır (usage alanları doldurulur).
    """

    def __init__(self, text: str = "offline-yanit"):
        self._text = text

    def send(
        self,
        *,
        model: str,
        effort: str,
        system_layer1: str,
        system_layer2: str,
        user_content: str,
        max_tokens: int,
    ) -> RawResponse:
        return RawResponse(
            text=self._text,
            model=model,
            stop_reason="end_turn",
            usage=Usage(input_tokens=10, output_tokens=5),
        )
```

- [ ] **Adım 3: `packages/kernel/orchestrator/flow.py` yaz**

```python
"""M1'in sabit iş akışı.

YAML derleyicisi M2'nin işidir. M1'in amacı yürütme çekirdeğinin dayanıklı
olduğunu kanıtlamak; akış bu yüzden Python'da sabit tanımlıdır ve spec §10'daki
referans akışın en kısa hali olarak seçilmiştir:

    ozetle (llm_task) → kaydet (tool, yan etkili) → son
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Node(BaseModel):
    id: str
    type: Literal["llm_task", "tool"]
    tier: str | None = None
    tool: str | None = None
    next: str | None = None


FIRST_NODE = "ozetle"

M1_FLOW: dict[str, Node] = {
    "ozetle": Node(id="ozetle", type="llm_task", tier="fast", next="kaydet"),
    "kaydet": Node(id="kaydet", type="tool", tool="test.slow_writer", next=None),
}

WORKFLOW_NAME = "m1_referans"
WORKFLOW_VERSION_HASH = "m1-sabit-akis-v1"
```

- [ ] **Adım 4: `packages/kernel/orchestrator/step.py` yaz**

```python
"""Tek adım yürütücü.

Bir adımın yürütülmesi bir steps satırı açar, düğüm tipine göre dağıtır ve
sonuca göre akışı ilerletir. Yeniden denemede YENİ bir steps satırı açılır
(attempt artar); çökmüş denemenin satırı 'running' olarak kalır ve denetim
izinin parçasıdır.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from kernel import config
from kernel.gateway.gateway import BudgetExceeded, Gateway, RefusalError
from kernel.gateway.types import LLMRequest
from kernel.orchestrator import flow
from kernel.state import db, events, queue
from kernel.tools import execution, registry
from kernel.tools.base import ToolOutcome

# Katman 1: ürün sabiti. Bayt bayt sabit tutulur — §6.2 önbellek noktası 1.
SYSTEM_LAYER1 = (
    "Sen kurumsal bir belge işleme otomasyonunun parçasısın. "
    "Girdiyi tek cümlede özetle. Yorum ekleme, soru sorma."
)


def execute_step(job: queue.Job, gateway: Gateway) -> str:
    node = flow.M1_FLOW[job.node_id]
    step_id = uuid4()

    with db.tx() as conn:
        run = conn.execute(
            "SELECT input, tenant_id FROM runs WHERE id = %s", (job.run_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO steps (id, run_id, node_id, attempt, status, input)"
            " VALUES (%s, %s, %s, %s, 'running', %s)",
            (step_id, job.run_id, node.id, job.attempts, Jsonb(run[0])),
        )
        events.append(
            conn,
            job.run_id,
            "step_started",
            {"node_id": node.id, "attempt": job.attempts, "step_id": str(step_id)},
        )

    payload, tenant_id = run[0], run[1]

    if node.type == "llm_task":
        return _run_llm_task(job, node, step_id, payload, tenant_id, gateway)
    return _run_tool(job, node, step_id, payload)


def _run_llm_task(
    job: queue.Job,
    node: flow.Node,
    step_id: UUID,
    payload: dict,
    tenant_id: str,
    gateway: Gateway,
) -> str:
    assert node.tier is not None
    request = LLMRequest(
        tier=node.tier,
        system_layer1=SYSTEM_LAYER1,
        # Katman 2: kiracıya özel, çalışmalar arası sabit.
        system_layer2=f"Kiracı: {tenant_id}.",
        # Katman 3: adımın beyan edilmiş girdisi (Kural 3).
        user_content=str(payload.get("text", "")),
    )
    try:
        response = gateway.complete(request, job.run_id, step_id)
    except BudgetExceeded as exc:
        return _fail_run(job, step_id, "budget_exceeded", str(exc))
    except RefusalError as exc:
        return _fail_run(job, step_id, "failed", str(exc))

    _finish_step(job, step_id, {"ozet": response.text})
    return _advance(job, node)


def _run_tool(
    job: queue.Job, node: flow.Node, step_id: UUID, payload: dict
) -> str:
    assert node.tool is not None
    result = execution.execute_tool(
        registry.get(node.tool),
        job.run_id,
        step_id,
        payload,
        lease_seconds=config.TOOL_LEASE_SECONDS,
    )

    if result.outcome is ToolOutcome.COMPLETED:
        _finish_step(job, step_id, result.response or {})
        return _advance(job, node)

    if result.outcome is ToolOutcome.DEFERRED:
        with db.tx() as conn:
            conn.execute(
                "UPDATE steps SET status = 'failed', ended_at = now(),"
                " error = 'ertelendi' WHERE id = %s",
                (step_id,),
            )
            queue.release(conn, job.id, result.retry_after_seconds)
            events.append(
                conn, job.run_id, "step_deferred", {"node_id": node.id}
            )
        return "deferred"

    if result.outcome is ToolOutcome.UNCERTAIN:
        return _fail_run(job, step_id, "uncertain", result.error or "belirsiz")

    return _fail_run(job, step_id, "failed", result.error or "araç hatası")


# --- ortak ---

def _finish_step(job: queue.Job, step_id: UUID, output: dict) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE steps SET status = 'completed', output = %s, ended_at = now()"
            " WHERE id = %s",
            (Jsonb(output), step_id),
        )
        conn.execute(
            "UPDATE runs SET step_count = step_count + 1, updated_at = now()"
            " WHERE id = %s",
            (job.run_id,),
        )
        events.append(
            conn, job.run_id, "step_completed", {"step_id": str(step_id)}
        )


def _advance(job: queue.Job, node: flow.Node) -> str:
    with db.tx() as conn:
        queue.complete(conn, job.id)
        if node.next is None:
            conn.execute(
                "UPDATE runs SET status = 'completed', updated_at = now()"
                " WHERE id = %s",
                (job.run_id,),
            )
            events.append(conn, job.run_id, "run_completed", {})
            return "run_completed"
        queue.enqueue(conn, job.run_id, node.next)
        return "advanced"


def _fail_run(job: queue.Job, step_id: UUID, run_status: str, error: str) -> str:
    with db.tx() as conn:
        conn.execute(
            "UPDATE steps SET status = 'failed', error = %s, ended_at = now()"
            " WHERE id = %s",
            (error, step_id),
        )
        conn.execute(
            "UPDATE runs SET status = %s, updated_at = now() WHERE id = %s",
            (run_status, job.run_id),
        )
        queue.complete(conn, job.id)
        events.append(
            conn, job.run_id, "run_" + run_status, {"error": error}
        )
    return "run_" + run_status
```

- [ ] **Adım 5: `packages/kernel/orchestrator/runner.py` yaz**

```python
"""Worker döngüsü. `python -m kernel.orchestrator.runner` ile çalışır.

Durumsuzdur (§2.2): tüm durum Postgres'te. Yatay ölçekleme = süreç sayısını
artırmak. Çökme kurtarması = kirası dolan işi başkasının alması.
"""
from __future__ import annotations

import importlib
import os
import signal
import time
import uuid
from decimal import Decimal
from uuid import UUID

from psycopg.types.json import Jsonb

from kernel import config
from kernel.gateway.gateway import Gateway
from kernel.gateway.transport import AnthropicTransport, OfflineTransport
from kernel.orchestrator import flow, step
from kernel.state import db, events, queue

_running = True


def start_run(tenant_id: str, payload: dict, budget_usd: str = "1.0") -> UUID:
    run_id = uuid.uuid4()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO runs (id, tenant_id, workflow_name, workflow_version_hash,"
            " status, input, budget_usd, max_steps, idempotency_key)"
            " VALUES (%s, %s, %s, %s, 'running', %s, %s, 25, %s)",
            (
                run_id,
                tenant_id,
                flow.WORKFLOW_NAME,
                flow.WORKFLOW_VERSION_HASH,
                Jsonb(payload),
                Decimal(budget_usd),
                str(run_id),
            ),
        )
        events.append(conn, run_id, "run_created", {"tenant_id": tenant_id})
        queue.enqueue(conn, run_id, flow.FIRST_NODE)
    return run_id


def run_once(worker_id: str, gateway: Gateway) -> bool:
    """Bir iş varsa alır ve yürütür. İş yoksa False döner."""
    with db.tx() as conn:
        job = queue.claim(conn, worker_id, config.JOB_LEASE_SECONDS)
    if job is None:
        return False
    step.execute_step(job, gateway)
    return True


def run_forever(worker_id: str, gateway: Gateway) -> None:
    while _running:
        if not run_once(worker_id, gateway):
            time.sleep(config.POLL_INTERVAL_SECONDS)


def build_gateway() -> Gateway:
    """OTOMASYON_TRANSPORT: 'offline' (varsayılan) veya 'anthropic'."""
    kind = os.environ.get("OTOMASYON_TRANSPORT", "offline")
    if kind == "anthropic":
        return Gateway(AnthropicTransport())
    if kind == "offline":
        return Gateway(OfflineTransport())
    raise ValueError(f"bilinmeyen OTOMASYON_TRANSPORT: {kind!r}")


def load_tool_modules() -> None:
    """OTOMASYON_TOOL_MODULES: virgülle ayrılmış modül adları.

    Her modül register_tools() sunmalıdır. M2'de müşteri konnektörleri de
    bu kancadan yüklenecek.
    """
    spec = os.environ.get("OTOMASYON_TOOL_MODULES", "")
    for name in (s.strip() for s in spec.split(",")):
        if name:
            importlib.import_module(name).register_tools()


def _stop(signum, frame) -> None:  # noqa: ARG001
    global _running
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    load_tool_modules()
    run_forever(os.environ.get("OTOMASYON_WORKER_ID", str(uuid.uuid4())),
                build_gateway())


if __name__ == "__main__":
    main()
```

- [ ] **Adım 6: `tests/fakes.py` sonuna kayıt kancası ekle**

(Paket yapısı Görev 1 Adım 6'da kuruldu.) `tests/fakes.py` sonuna:

```python
def register_tools() -> None:
    """runner.load_tool_modules() bu fonksiyonu çağırır."""
    from kernel.tools import registry

    registry.register(SlowWriterTool())
    registry.register(NoReconcileTool())
    registry.register(AlwaysFailsTool())
```

- [ ] **Adım 7: Başarısız testi yaz**

`tests/orchestrator/test_runner.py`:

```python
import pytest

from kernel.gateway.gateway import Gateway
from kernel.gateway.transport import OfflineTransport
from kernel.orchestrator import runner
from kernel.state import db, events
from kernel.tools import registry
from tests.fakes import register_tools


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    register_tools()
    yield
    registry.clear()


def _drain(worker_id: str = "w1", limit: int = 10) -> None:
    gw = Gateway(OfflineTransport())
    for _ in range(limit):
        if not runner.run_once(worker_id, gw):
            return


def test_full_run_completes_with_single_side_effect():
    run_id = runner.start_run("t1", {"text": "merhaba", "key": "k1"})
    _drain()

    with db.tx() as conn:
        status = conn.execute(
            "SELECT status FROM runs WHERE id = %s", (run_id,)
        ).fetchone()[0]
        effects = conn.execute(
            "SELECT count(*) FROM side_effects WHERE key = 'k1'"
        ).fetchone()[0]
        types = [e.type for e in events.read(conn, run_id)]

    assert status == "completed"
    assert effects == 1
    assert types[0] == "run_created"
    assert types[-1] == "run_completed"
    assert "llm_call_completed" in types


def test_both_steps_recorded():
    run_id = runner.start_run("t1", {"text": "x", "key": "k2"})
    _drain()
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT node_id, status FROM steps WHERE run_id = %s ORDER BY started_at",
            (run_id,),
        ).fetchall()
    assert rows == [("ozetle", "completed"), ("kaydet", "completed")]


def test_uncertain_tool_stops_the_run():
    """reconcile sunmayan araçta kira dolarsa run 'uncertain' olur."""
    import uuid as _uuid

    from kernel.orchestrator import flow

    original = flow.M1_FLOW["kaydet"].tool
    flow.M1_FLOW["kaydet"].tool = "test.no_reconcile"
    try:
        run_id = runner.start_run("t1", {"text": "x", "key": "k3"})
        # Çökmüş worker'ın bıraktığı, kirası dolmuş rezervasyonu simüle et.
        with db.tx() as conn:
            step_id = _uuid.uuid4()
            conn.execute(
                "INSERT INTO steps (id, run_id, node_id, status)"
                " VALUES (%s, %s, 'kaydet', 'failed')",
                (step_id, run_id),
            )
            conn.execute(
                "INSERT INTO tool_calls (id, step_id, tool_name, idempotency_key,"
                " request, status, lease_expires_at) VALUES"
                " (%s, %s, 'test.no_reconcile', %s, '{}'::jsonb, 'reserved',"
                "  now() - interval '1 second')",
                (_uuid.uuid4(), step_id, f"{run_id}:k3"),
            )
        _drain()
        with db.tx() as conn:
            status = conn.execute(
                "SELECT status FROM runs WHERE id = %s", (run_id,)
            ).fetchone()[0]
            effects = conn.execute(
                "SELECT count(*) FROM side_effects WHERE key = 'k3'"
            ).fetchone()[0]
        assert status == "uncertain"
        assert effects == 0
    finally:
        flow.M1_FLOW["kaydet"].tool = original
```

- [ ] **Adım 8: Testi koş, başarısız olduğunu gör**

```bash
pytest tests/orchestrator/test_runner.py -v
```

Beklenen: `ModuleNotFoundError: No module named 'kernel.orchestrator'`.

- [ ] **Adım 9: Testi koş, geçtiğini gör**

```bash
pytest tests/orchestrator/test_runner.py -v
```

Beklenen: 3 test PASSED.

- [ ] **Adım 10: Tüm paketi koş**

```bash
pytest -v
```

Beklenen: Görev 1–7'nin tüm testleri PASSED.

- [ ] **Adım 11: Commit**

```bash
git add packages/kernel/config.py packages/kernel/orchestrator packages/kernel/gateway/transport.py tests/fakes.py tests/orchestrator
git commit -m "feat(orchestrator): durumsuz worker döngüsü ve M1 sabit akışı

llm_task -> tool akışı uçtan uca çalışıyor; belirsiz araç sonucu run'ı
'uncertain' yapıp durduruyor.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Görev 8: M1 kabul testi — `kill -9` dayanıklılığı

**Dosyalar:**
- Test: `tests/acceptance/test_crash_recovery.py`

**Arayüzler:**
- Tüketir: `runner.start_run`, `fakes.SlowWriterTool` (`OTOMASYON_KILL_POINT` desteği), `config.*_LEASE_SECONDS`

Bu, spec §11'deki M1 kabul kriteridir: **worker adım ortasında `kill -9` → iş tamamlanır, tek yan etki oluşur.** İki senaryoyu birden kanıtlar: yazmadan önce ölme (tekrar güvenli) ve yazdıktan sonra ölme (reconcile tekrar yazmayı önler).

- [ ] **Adım 1: Başarısız testi yaz**

`tests/acceptance/test_crash_recovery.py`:

```python
"""M1 kabul kriteri (spec §11).

Worker'ı adım ortasında SIGKILL ile öldür, taze bir worker başlat, işin
tamamlandığını ve yan etkinin TEK kez oluştuğunu doğrula.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from kernel.orchestrator import runner
from kernel.state import db
from kernel.tools import registry
from tests.conftest import TEST_DSN
from tests.fakes import register_tools

REPO_ROOT = Path(__file__).parents[2]
DEADLINE = 60.0


@pytest.fixture(autouse=True)
def _tools():
    registry.clear()
    register_tools()
    yield
    registry.clear()


def _worker_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "OTOMASYON_DB_DSN": TEST_DSN,
            "OTOMASYON_TRANSPORT": "offline",
            "OTOMASYON_TOOL_MODULES": "tests.fakes",
            # Kısa kiralar: çökmüş rezervasyon hızla kurtarılabilir olsun.
            "OTOMASYON_JOB_LEASE_SECONDS": "2",
            "OTOMASYON_TOOL_LEASE_SECONDS": "2",
            "OTOMASYON_POLL_INTERVAL": "0.1",
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    env.update(extra)
    return env


def _spawn(**extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "kernel.orchestrator.runner"],
        cwd=REPO_ROOT,
        env=_worker_env(**extra),
    )


def _wait_for_file(path: Path, deadline: float = DEADLINE) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"marker dosyası oluşmadı: {path}")


def _wait_for_status(run_id: uuid.UUID, expected: str, deadline: float = DEADLINE) -> None:
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        with db.tx() as conn:
            last = conn.execute(
                "SELECT status FROM runs WHERE id = %s", (run_id,)
            ).fetchone()[0]
        if last == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"run {expected} olmadı; son durum: {last}")


def _side_effects(key: str) -> int:
    with db.tx() as conn:
        return int(
            conn.execute(
                "SELECT count(*) FROM side_effects WHERE key = %s", (key,)
            ).fetchone()[0]
        )


def _run_crash_scenario(tmp_path: Path, kill_point: str, key: str) -> uuid.UUID:
    marker = tmp_path / f"marker-{key}"
    run_id = runner.start_run("t1", {"text": "merhaba", "key": key})

    victim = _spawn(
        OTOMASYON_KILL_POINT=kill_point,
        OTOMASYON_KILL_MARKER=str(marker),
    )
    try:
        _wait_for_file(marker)
        os.kill(victim.pid, signal.SIGKILL)
        victim.wait(timeout=10)
    finally:
        if victim.poll() is None:
            victim.kill()

    assert victim.returncode == -signal.SIGKILL

    # Kiraların dolmasını bekle, sonra taze worker başlat.
    time.sleep(3)
    rescuer = _spawn()
    try:
        _wait_for_status(run_id, "completed")
    finally:
        rescuer.terminate()
        rescuer.wait(timeout=10)

    return run_id


@pytest.mark.slow
@pytest.mark.timeout(180)
def test_crash_before_write_recovers_with_exactly_one_side_effect(tmp_path):
    """Yazmadan önce ölüm: kurtarma güvenle tekrar çalıştırır."""
    key = "crash-before"
    assert _side_effects(key) == 0
    _run_crash_scenario(tmp_path, "before_write", key)
    assert _side_effects(key) == 1, "kurtarma tam olarak bir yan etki üretmeli"


@pytest.mark.slow
@pytest.mark.timeout(180)
def test_crash_after_write_does_not_duplicate_side_effect(tmp_path):
    """Yazdıktan sonra ölüm: reconcile yazmayı bulur, TEKRAR YAZMAZ.

    K15'in asıl kanıtı budur. İki aşamalı rezervasyon olmasaydı burada
    iki yan etki oluşurdu.
    """
    key = "crash-after"
    _run_crash_scenario(tmp_path, "after_write", key)
    assert _side_effects(key) == 1, "yan etki tekrarlanmamalı"


@pytest.mark.slow
@pytest.mark.timeout(180)
def test_crashed_attempt_is_visible_in_audit_trail(tmp_path):
    """Çökmüş deneme denetim izinde görünür kalmalı (Kural 5)."""
    run_id = _run_crash_scenario(tmp_path, "after_write", "crash-audit")
    with db.tx() as conn:
        attempts = conn.execute(
            "SELECT count(*) FROM steps WHERE run_id = %s AND node_id = 'kaydet'",
            (run_id,),
        ).fetchone()[0]
    assert attempts >= 2, "çökmüş deneme ve kurtarma denemesi ayrı satırlar olmalı"
```

- [ ] **Adım 2: Testi koş, başarısız olduğunu gör**

```bash
pytest tests/acceptance/test_crash_recovery.py -v -m slow
```

Beklenen: `tests/acceptance` dizini yoksa toplama hatası; dosya oluşturulduktan sonra gerçek koşu.

- [ ] **Adım 3: Testi koş, geçtiğini gör**

```bash
pytest tests/acceptance/test_crash_recovery.py -v
```

Beklenen: 3 test PASSED. Süre ~2–3 dakika (kira beklemeleri nedeniyle).

**Kırılırsa nereye bakılmalı:**
- `crash_after` testinde iki yan etki görülüyorsa: `execution._recover_expired` reconcile dalını atlıyordur, ya da `SlowWriterTool.reconcile` `independent_tx` yerine dış transaction kullanıyordur.
- Run `completed` olmuyorsa: `OTOMASYON_TOOL_LEASE_SECONDS` beklemesi kısa kalmıştır; `time.sleep(3)` değerini kira + 1 saniyeye ayarla.
- `attempts >= 2` kırılıyorsa: `queue.claim` `attempts` sayacını artırmıyordur (Görev 3).

- [ ] **Adım 4: Tüm paketi koş**

```bash
pytest -v
```

Beklenen: Görev 1–8'in tüm testleri PASSED.

- [ ] **Adım 5: Commit**

```bash
git add tests/acceptance
git commit -m "test(acceptance): M1 kabul kriteri — kill -9 dayanıklılığı

Worker adım ortasında SIGKILL ile öldürülüyor; taze worker işi tamamlıyor
ve yan etki tam olarak bir kez oluşuyor. Yazma sonrası çökmede reconcile
tekrar yazmayı önlüyor (K15).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## M1 Tamamlanma Kontrol Listesi

Spec §11'deki M1 satırı ile karşılaştırma:

| Spec gereksinimi | Karşılayan görev |
|---|---|
| Postgres şeması | Görev 1 |
| İş kuyruğu | Görev 3 |
| Olay kaydı (ekleme-yalnız) | Görev 1 (tetikleyici) + Görev 2 |
| LLM ağ geçidi | Görev 6 |
| Tek adımlı akış | Görev 7 |
| **Kabul: `kill -9` → iş tamamlanır, tek yan etki** | **Görev 8** |
| K15 iki aşamalı rezervasyon | Görev 4 + 5 |
| Kural 5 veritabanı seviyesinde | Görev 1 |
| §6.1 ağ geçidi tekeli | Görev 6 (Adım 9 testi) |
| §6.2 üç katmanlı prompt | Görev 6 |
| K9 `bulk` kilidi | Görev 6 (`tiers.py`) |

**M1 kapsamına girmeyenler (spec'te sonraki dilimlerde):** YAML derleyicisi ve statik analiz (M2) · `router`/`switch`/`human_approval` (M2–M3) · telafi zincirleri (M3) · kiracı günlük tavanı ve p95 alarmı (M3/M5) · kaset kayıt/tekrar (M4) · OTel trace (M5).

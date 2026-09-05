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

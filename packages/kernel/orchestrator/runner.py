"""Worker döngüsü. `python -m kernel.orchestrator.runner` ile çalışır.

Durumsuzdur (§2.2): tüm durum Postgres'te. Yatay ölçekleme = süreç sayısını
artırmak. Çökme kurtarması = kirası dolan işi başkasının alması.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import signal
import time
import uuid
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from kernel import config
from kernel.gateway.gateway import Gateway
from kernel.gateway.transport import AnthropicTransport, OfflineTransport
from kernel.orchestrator import flow, step
from kernel.state import db, events, queue

_running = True


def start_run(
    tenant_id: str,
    payload: dict,
    budget_usd: str = "1.0",
    idempotency_key: str | None = None,
) -> UUID:
    """Anahtar verilmezse her çağrı yeni bir run başlatır.

    Verilirse runs_idem_unique kısıtı çalışma-seviyesi tam-bir-kez oluşturma
    garantisi verir: aynı anahtarla ikinci istek mevcut run_id'yi döndürür,
    yeni iş kuyruğa girmez ("aynı faturayı iki kere gönderdim").
    """
    run_id = uuid.uuid4()
    key = idempotency_key if idempotency_key is not None else str(run_id)
    try:
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO runs (id, tenant_id, workflow_name,"
                " workflow_version_hash, status, input, budget_usd, max_steps,"
                " idempotency_key)"
                " VALUES (%s, %s, %s, %s, 'running', %s, %s, 25, %s)",
                (
                    run_id,
                    tenant_id,
                    flow.WORKFLOW_NAME,
                    flow.WORKFLOW_VERSION_HASH,
                    Jsonb(payload),
                    Decimal(budget_usd),
                    key,
                ),
            )
            events.append(conn, run_id, "run_created", {"tenant_id": tenant_id})
            queue.enqueue(conn, run_id, flow.FIRST_NODE)
    except psycopg.errors.UniqueViolation:
        # Transaction tümüyle geri alındı: olay da iş de yazılmadı.
        with db.tx() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE tenant_id = %s AND workflow_name = %s"
                " AND idempotency_key = %s",
                (tenant_id, flow.WORKFLOW_NAME, key),
            ).fetchone()
        assert row is not None
        return row[0]
    return run_id


def run_once(worker_id: str, gateway: Gateway) -> bool:
    """Bir iş varsa alır ve yürütür. İş yoksa False döner.

    Adım yürütmesi bir istisna sınırıyla çevrilidir: worker ASLA bir uygulama
    istisnası yüzünden ölmez. Ölen worker'ın bıraktığı iş kirası dolana kadar
    kimseye verilmez ve aynı hata sonsuza dek tekrarlanır.
    """
    with db.tx() as conn:
        job = queue.claim(conn, worker_id, config.JOB_LEASE_SECONDS)
    if job is None:
        return False
    try:
        step.execute_step(job, gateway)
    except Exception as exc:  # noqa: BLE001 — sınırın amacı budur
        _handle_step_exception(job, exc)
    return True


def _backoff_seconds(attempts: int) -> int:
    """Üstel geri çekilme, bir dakikada sınırlanır."""
    return min(2 ** attempts, 60)


def _handle_step_exception(job: queue.Job, exc: Exception) -> None:
    """Tavanın altındaysa geri çekilmeyle kuyruğa koy, değilse ölü-mektup.

    Ölü mektup: iş kuyruktan çıkar, run 'uncertain' olur (otomatik tekrar YOK,
    insan incelemesi gerekir) ve olay kaydına gerekçe düşer.
    """
    if job.attempts < config.MAX_JOB_ATTEMPTS:
        with db.tx() as conn:
            queue.release(
                conn, job.id, job.worker_id, _backoff_seconds(job.attempts)
            )
        return
    with db.tx() as conn:
        if not queue.complete(conn, job.id, job.worker_id):
            return
        conn.execute(
            "UPDATE runs SET status = 'uncertain', updated_at = now()"
            " WHERE id = %s",
            (job.run_id,),
        )
        events.append(
            conn,
            job.run_id,
            "run_uncertain",
            step.error_payload(type(exc).__name__, str(exc)),
        )


def run_forever(worker_id: str, gateway: Gateway) -> None:
    while _running:
        if not run_once(worker_id, gateway):
            time.sleep(config.POLL_INTERVAL_SECONDS)


def build_gateway() -> Gateway:
    """OTOMASYON_TRANSPORT: 'live' ya da 'offline'. VARSAYILAN YOK.

    Sessiz bir varsayılan, env'i set edilmeden dağıtılan bir worker'ın her
    LLM çağrısına sabit yanıt vermesi ve run'ın 'completed' olması demektir:
    müşterinin işi "başarıyla" biter, hiçbir şey yapılmamıştır.
    """
    kind = os.environ.get("OTOMASYON_TRANSPORT")
    if kind is None:
        raise ValueError(
            "OTOMASYON_TRANSPORT tanımlı değil. Geçerli değerler: 'live', 'offline'"
        )
    if kind == "live":
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
    try:
        run_forever(os.environ.get("OTOMASYON_WORKER_ID", str(uuid.uuid4())),
                    build_gateway())
    finally:
        # Havuzu burada açıkça kapat: aksi halde temizlik __del__'e kalır ve
        # yorumlayıcı kapanışı sırasında psycopg_pool işçi thread'lerini
        # durduramayıp SIGTERM sonrası çıkışı ~20sn geciktirir. reset_pool()
        # kendisi patlarsa run_forever'ın asıl istisnasını maskelemesin.
        with contextlib.suppress(Exception):
            db.reset_pool()


if __name__ == "__main__":
    main()

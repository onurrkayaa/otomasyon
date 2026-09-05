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
    """OTOMASYON_TRANSPORT: 'offline' (varsayılan) veya 'live'."""
    kind = os.environ.get("OTOMASYON_TRANSPORT", "offline")
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
        # durduramayıp SIGTERM sonrası çıkışı ~20sn geciktirir.
        db.reset_pool()


if __name__ == "__main__":
    main()

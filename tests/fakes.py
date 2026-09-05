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
        if not marker:
            raise RuntimeError(
                "OTOMASYON_KILL_POINT ayarlı ama OTOMASYON_KILL_MARKER tanımsız;"
                " kill -9 testi marker dosyası olmadan bekleyemez"
            )
        Path(marker).write_text(point, encoding="utf-8")
        while True:
            time.sleep(1)


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

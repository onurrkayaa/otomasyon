"""Havuz kurulumunun eşzamanlılık kilidi (Küçük 1)."""
from __future__ import annotations

import threading
import time

from kernel.state import db


def test_pool_is_created_only_once_under_concurrency(monkeypatch):
    """İki thread aynı anda girerse iki havuz kurulur ve biri hiç kapanmaz."""
    created: list[object] = []

    class _SlowPool:
        def __init__(self, *args, **kwargs):
            time.sleep(0.05)  # yarış penceresini genişlet
            created.append(self)

        def close(self) -> None:
            return None

    db.reset_pool()
    monkeypatch.setattr(db, "ConnectionPool", _SlowPool)
    try:
        threads = [threading.Thread(target=db.pool) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(created) == 1, f"tek havuz beklenirken {len(created)} kuruldu"
    finally:
        db.reset_pool()

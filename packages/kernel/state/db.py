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
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None
# Havuz kurulumu çift kontrollü kilitle korunur: iki thread aynı anda girerse
# iki havuz kurulur ve biri hiç kapanmaz (10 bağlantı sızar).
_pool_lock = threading.Lock()


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
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(dsn(), min_size=1, max_size=10, open=True)
    return _pool


def reset_pool() -> None:
    """Testlerin DSN değiştirebilmesi için havuzu kapatır."""
    global _pool
    with _pool_lock:
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

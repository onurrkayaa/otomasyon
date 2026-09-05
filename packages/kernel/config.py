"""Çalışma zamanı ayarları. Sırlar burada DEĞİL (K14) — yalnız süre/eşik."""
from __future__ import annotations

import os

JOB_LEASE_SECONDS = int(os.environ.get("OTOMASYON_JOB_LEASE_SECONDS", "30"))
TOOL_LEASE_SECONDS = int(os.environ.get("OTOMASYON_TOOL_LEASE_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.environ.get("OTOMASYON_POLL_INTERVAL", "0.5"))

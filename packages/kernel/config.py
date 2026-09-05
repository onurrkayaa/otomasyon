"""Çalışma zamanı ayarları. Sırlar burada DEĞİL (K14) — yalnız süre/eşik."""
from __future__ import annotations

import os

JOB_LEASE_SECONDS = int(os.environ.get("OTOMASYON_JOB_LEASE_SECONDS", "30"))
TOOL_LEASE_SECONDS = int(os.environ.get("OTOMASYON_TOOL_LEASE_SECONDS", "60"))
POLL_INTERVAL_SECONDS = float(os.environ.get("OTOMASYON_POLL_INTERVAL", "0.5"))

# Bir işin kaç kez alınabileceğinin tavanı. Tavana ulaşan iş ölü-mektup olur:
# kuyruktan çıkar, run 'uncertain' işaretlenir ve insan incelemesine gider.
MAX_JOB_ATTEMPTS = int(os.environ.get("OTOMASYON_MAX_JOB_ATTEMPTS", "5"))


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
            victim.wait(timeout=10)

    assert victim.returncode == -signal.SIGKILL

    # Kiraların dolmasını bekle, sonra taze worker başlat.
    time.sleep(3)
    rescuer = _spawn()
    try:
        _wait_for_status(run_id, "completed")
    finally:
        rescuer.terminate()
        try:
            rescuer.wait(timeout=10)
        finally:
            if rescuer.poll() is None:
                rescuer.kill()
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
    """Çökmüş denemenin steps satırı denetim izinde kalıcı olarak durur.

    (Not: bu Kural 5 değildir — Kural 5 `events` tablosunun ekleme-yalnız
    olmasıdır ve `steps`'te öyle bir kısıt yok. Burada doğrulanan,
    yürütücünün çökmüş denemeyi ASLA güncellemediği ve kurtarmanın YENİ,
    daha büyük attempt'li bir satır açtığıdır — sadece "2+ satır var"
    değil, kurbanın satırının 'running' durumunda gerçekten hâlâ orada
    olduğu.)
    """
    run_id = _run_crash_scenario(tmp_path, "after_write", "crash-audit")
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT attempt, status FROM steps"
            " WHERE run_id = %s AND node_id = 'kaydet' ORDER BY attempt",
            (run_id,),
        ).fetchall()
    assert len(rows) >= 2, "çökmüş deneme ve kurtarma denemesi ayrı satırlar olmalı"
    victim_attempt, victim_status = rows[0]
    rescue_attempt, rescue_status = rows[-1]
    assert victim_status == "running", (
        "kurbanın steps satırı hâlâ 'running' durumunda durmuş olmalı"
        f" (temizlenmemiş/güncellenmemiş); bulundu: {victim_status!r}"
    )
    assert rescue_attempt > victim_attempt, (
        "kurtarma denemesi kurbanınkinden BÜYÜK bir attempt taşımalı"
        " (kurbanın satırının üzerine yazılmadığının kanıtı)"
    )
    assert rescue_status == "completed", "kurtarma denemesi tamamlanmış olmalı"

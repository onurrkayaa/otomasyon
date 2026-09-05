"""conftest'in şema silme koruması (Küçük 2)."""
from __future__ import annotations

import pytest

from tests.conftest import require_test_database


def test_non_test_database_is_refused():
    """DROP SCHEMA çalıştırılıyor: yanlış DSN üretim şemasını siler."""
    with pytest.raises(pytest.exit.Exception):
        require_test_database("postgresql://localhost/otomasyon_uretim")


def test_test_database_is_accepted():
    require_test_database("postgresql://localhost/otomasyon_test")

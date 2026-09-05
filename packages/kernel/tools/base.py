"""Araç sözleşmesi — Kural 4: yan etkinin TEK noktası.

İki yetenek bayrağı, K15 kurtarma sırasını belirler:
  supports_reconcile   — yazma gerçekleşti mi diye dış sisteme sorulabilir
  external_idempotency — dış API bizim anahtarımızı kabul eder, tekrar güvenlidir
İkisi de yoksa kirası dolmuş bir rezervasyon 'uncertain' olur ve insana gider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel


class ToolOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    DEFERRED = "deferred"


class ToolResult(BaseModel):
    outcome: ToolOutcome
    response: dict | None = None
    error: str | None = None
    retry_after_seconds: int = 0


class Tool(ABC):
    name: ClassVar[str]
    supports_reconcile: ClassVar[bool] = False
    external_idempotency: ClassVar[bool] = False

    @abstractmethod
    def idempotency_key(self, run_id: UUID, payload: dict) -> str:
        """Bu yan etkinin evrensel kimliği. Yeniden denemede DEĞİŞMEMELİDİR."""

    @abstractmethod
    def execute(self, payload: dict) -> dict:
        """Yan etkiyi uygular. Hata durumunda istisna fırlatır.

        İstisna fırlatan bir araç, yan etkinin OLUŞMADIĞINI garanti etmek
        ZORUNDADIR. Bunu garanti edemeyen bir araç (ör. ağ zaman aşımından
        sonra dış sistemin yazmayı yine de almış olabileceği durumlar) ya
        `external_idempotency` bayrağını set etmeli ya da belirsizliği
        `reconcile()` ile kendi içinde ele almalıdır."""

    @abstractmethod
    def compensate(self, payload: dict, response: dict) -> None:
        """Yan etkiyi geri alır. M1 akışında çağrılmaz; telafi zincirleri M3'te
        devreye girer. Sözleşmenin parçasıdır: her araç geri almayı ilk günden
        düşünmek zorundadır (§4.1 madde 1)."""

    def reconcile(self, payload: dict) -> dict | None:
        """Yazma gerçekleşmiş mi diye dış sisteme sorar.

        Bulundu → yanıt sözlüğü. Bulunamadı → None.
        supports_reconcile=True olan araçlar bunu uygulamak ZORUNDADIR.
        """
        raise NotImplementedError(
            f"{self.name}: supports_reconcile=True ama reconcile() uygulanmamış"
        )

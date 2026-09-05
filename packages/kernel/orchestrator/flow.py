"""M1'in sabit iş akışı.

YAML derleyicisi M2'nin işidir. M1'in amacı yürütme çekirdeğinin dayanıklı
olduğunu kanıtlamak; akış bu yüzden Python'da sabit tanımlıdır ve spec §10'daki
referans akışın en kısa hali olarak seçilmiştir:

    ozetle (llm_task) → kaydet (tool, yan etkili) → son
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Node(BaseModel):
    id: str
    type: Literal["llm_task", "tool"]
    tier: str | None = None
    tool: str | None = None
    next: str | None = None


FIRST_NODE = "ozetle"

M1_FLOW: dict[str, Node] = {
    "ozetle": Node(id="ozetle", type="llm_task", tier="fast", next="kaydet"),
    "kaydet": Node(id="kaydet", type="tool", tool="test.slow_writer", next=None),
}

WORKFLOW_NAME = "m1_referans"
WORKFLOW_VERSION_HASH = "m1-sabit-akis-v1"

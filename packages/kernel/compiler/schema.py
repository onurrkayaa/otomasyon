"""§4 YAML sözleşmesinin Pydantic karşılığı.

Bu dosya izin verilen alanların TEK gerçek kaynağıdır. `extra="forbid"`
sayesinde YAML'daki bir yazım hatası sessizce yok sayılan bir alan üretmez,
derleme hatası üretir — spec §4.1'in "ihlal edilirse YAML yüklenmez" kuralının
mekanik karşılığı.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# Süre biçimi: 30s, 15m, 4h, 2d
SURE = r"^\d+[smhd]$"
KIMLIK = r"^[a-z][a-z0-9_]*$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Retry(Strict):
    attempts: int = Field(default=3, ge=1, le=10)
    backoff: Literal["exponential", "linear"] = "exponential"


class Limits(Strict):
    max_steps: int = Field(ge=1, le=500)
    max_usd_per_run: Decimal = Field(gt=0)
    max_wallclock: str = Field(pattern=SURE)


class Defaults(Strict):
    model_tier: str = "standard"
    retry: Retry = Retry()
    max_tokens: int = Field(default=16000, ge=256, le=64000)


class Trigger(Strict):
    type: Literal["http", "email", "cron", "webhook", "file"]
    mailbox: str | None = None
    schedule: str | None = None
    path: str | None = None


class LowConfidence(Strict):
    threshold: float = Field(gt=0.0, lt=1.0)
    route: str


class ApprovalTimeout(Strict):
    after: str = Field(pattern=SURE)
    route: str


class NodeBase(Strict):
    id: str = Field(pattern=KIMLIK)
    depends_on: list[str] = []
    inputs: list[str] = []


class LLMTaskNode(NodeBase):
    type: Literal["llm_task"]
    model_tier: str | None = None
    max_tokens: int | None = Field(default=None, ge=256, le=64000)
    outputs: dict[str, str] = {}


class RouterNode(NodeBase):
    type: Literal["router"]
    model_tier: str | None = None
    routes: dict[str, str | None] = Field(min_length=2)
    on_low_confidence: LowConfidence | None = None


class SwitchNode(NodeBase):
    type: Literal["switch"]
    on: str
    cases: dict[str, str | None] = Field(min_length=1)


class ToolNode(NodeBase):
    type: Literal["tool"]
    tool: str
    idempotency: list[str] = []
    """Yan etkinin evrensel kimliğini üreten parçalar (ZK3).

    Her parça ya düz metin sabittir ya da `{{ ... }}` şablon referansıdır:

        idempotency: ["erp-yazma", "{{ run.id }}", "{{ cikar.fatura.fatura_no }}"]

    Söz dizimi burada serbesttir; ANLAMSAL denetim Görev 3'tedir — listede
    run'dan run'a değişen en az bir referans bulunmak ZORUNDADIR, aksi halde
    ikinci run'ın yan etkisi sessizce atlanır (E_STATIK_IDEMPOTENCY)."""
    compensation: str | None = None
    batchable: bool = False          # K10: v1'de yok sayılır, şemada rezerve
    outputs: dict[str, str] = {}


class HumanApprovalNode(NodeBase):
    type: Literal["human_approval"]
    assignee_role: str
    context_fields: list[str] = []
    timeout: ApprovalTimeout | None = None


Node = Annotated[
    Union[LLMTaskNode, RouterNode, SwitchNode, ToolNode, HumanApprovalNode],
    Field(discriminator="type"),
]

# Yan etki üretebilen düğüm tipleri. §4.1(1) yalnız bunları bağlar.
YAN_ETKILI_TIPLER = frozenset({"tool"})
# LLM harcayan düğüm tipleri. Tavan maliyet yalnız bunları sayar.
LLM_TIPLERI = frozenset({"llm_task", "router"})
# Dallanan düğüm tipleri. KK2: yalnız bunların birden fazla çocuğu olabilir.
DALLANAN_TIPLER = frozenset({"router", "switch"})


class WorkflowSpec(Strict):
    api_version: Literal["v1"] = Field(alias="apiVersion")
    name: str = Field(pattern=KIMLIK)
    description: str = ""
    trigger: Trigger
    limits: Limits
    defaults: Defaults = Defaults()
    graph: list[Node] = Field(min_length=1)

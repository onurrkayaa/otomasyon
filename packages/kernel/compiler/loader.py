"""YAML dosyası → tipli `WorkflowSpec` + içerik hash'i.

version_hash kaynak baytların sha256'sıdır. Spec §5: run başlarken bağlandığı
YAML versiyonu sabitlenir; "workflow versioning" problemi böylece ek altyapı
olmadan çözülür.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from kernel.compiler.errors import E_SEMA, CompileError, CompileFailed, CompileReport
from kernel.compiler.schema import WorkflowSpec


class LoadedWorkflow(BaseModel):
    spec: WorkflowSpec
    source: str
    version_hash: str


def parse_workflow(source: str) -> WorkflowSpec:
    """Metin → spec. Bozuksa CompileFailed fırlatır."""
    try:
        ham = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise CompileFailed(
            CompileReport(errors=[CompileError(code=E_SEMA, message=f"YAML ayrıştırılamadı: {exc}")])
        ) from exc
    if not isinstance(ham, dict):
        raise CompileFailed(
            CompileReport(errors=[CompileError(code=E_SEMA, message="kök öğe eşleme olmalı")])
        )
    try:
        return WorkflowSpec.model_validate(ham)
    except ValidationError as exc:
        raise CompileFailed(CompileReport(errors=_sema_hatalari(exc))) from exc


def _sema_hatalari(exc: ValidationError) -> list[CompileError]:
    """Pydantic hatalarını CompileError'a çevirir; düğüm kimliğini korur."""
    out: list[CompileError] = []
    for e in exc.errors():
        yer = ".".join(str(p) for p in e["loc"])
        out.append(
            CompileError(code=E_SEMA, message=f"{yer}: {e['msg']}", node_id=None)
        )
    return out


def load_workflow(path: Path) -> LoadedWorkflow:
    source = Path(path).read_text(encoding="utf-8")
    return LoadedWorkflow(
        spec=parse_workflow(source),
        source=source,
        version_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )

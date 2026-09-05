"""Araç kaydı: ad → örnek.

M1'de süreç-içi basit bir sözlük. Kiracıya göre bağlama (yetenek araçları,
§6.2 K16) M2'de derleyiciyle birlikte gelir.
"""
from __future__ import annotations

from kernel.tools.base import Tool

_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _TOOLS[tool.name] = tool


def get(name: str) -> Tool:
    if name not in _TOOLS:
        raise KeyError(f"kayıtlı olmayan araç: {name}")
    return _TOOLS[name]


def clear() -> None:
    _TOOLS.clear()

from __future__ import annotations

from .antigravity import AntigravityAdapter
from .base import ProviderAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .kimi import KimiAdapter
from ..models import InstrumentName


def build_instrument_registry() -> dict[InstrumentName, ProviderAdapter]:
    adapters: list[ProviderAdapter] = [
        AntigravityAdapter(),
        CodexAdapter(),
        ClaudeAdapter(),
        KimiAdapter(),
    ]
    return {adapter.name: adapter for adapter in adapters}

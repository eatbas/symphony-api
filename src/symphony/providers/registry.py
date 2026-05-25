from __future__ import annotations

from .antigravity import AntigravityAdapter
from .base import ProviderAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .copilot import CopilotAdapter
from .kimi import KimiAdapter
from .opencode import OpenCodeAdapter
from ..models import InstrumentName


def build_instrument_registry() -> dict[InstrumentName, ProviderAdapter]:
    adapters: list[ProviderAdapter] = [
        AntigravityAdapter(),
        CodexAdapter(),
        ClaudeAdapter(),
        KimiAdapter(),
        CopilotAdapter(),
        OpenCodeAdapter(),
    ]
    return {adapter.name: adapter for adapter in adapters}

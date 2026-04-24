from __future__ import annotations

from typing import Any


THINKING_LEVELS: dict[str, dict[str, str]] = {
    "low": {
        "label": "Low",
        "description": "Fast responses with lighter reasoning.",
    },
    "medium": {
        "label": "Medium",
        "description": "Balanced reasoning depth for everyday work.",
    },
    "high": {
        "label": "High",
        "description": "Deeper reasoning for complex tasks.",
    },
    "xhigh": {
        "label": "Extra high",
        "description": "Maximum available reasoning for difficult tasks.",
    },
    "max": {
        "label": "Max",
        "description": "Highest available reasoning on providers and models that support it.",
    },
}

PROMPT_HINTS = {
    "low": "Use concise reasoning and prioritize a direct answer.",
    "medium": "Use balanced reasoning and explain key tradeoffs briefly.",
    "high": "Use deeper reasoning and check important assumptions before answering.",
    "xhigh": "Use the deepest available reasoning and be careful with edge cases.",
    "max": "Use the maximum available reasoning and be careful with edge cases.",
}

BOOLEAN_THINKING_CHOICES = [
    {
        "value": "enabled",
        "label": "Enabled",
        "description": "Run with the provider's thinking mode enabled.",
    },
    {
        "value": "disabled",
        "label": "Disabled",
        "description": "Run without the provider's explicit thinking mode.",
    },
]


def thinking_level_schema(
    *,
    levels: tuple[str, ...] = ("low", "medium", "high", "xhigh"),
    default: str = "medium",
) -> list[dict[str, Any]]:
    return [
        {
            "key": "thinking_level",
            "label": "Thinking",
            "type": "select",
            "default": default,
            "choices": [
                {
                    "value": level,
                    "label": THINKING_LEVELS[level]["label"],
                    "description": THINKING_LEVELS[level]["description"],
                }
                for level in levels
            ],
        }
    ]


def boolean_thinking_schema(*, default: str = "enabled") -> list[dict[str, Any]]:
    return [
        {
            "key": "thinking_mode",
            "label": "Thinking",
            "type": "select",
            "default": default,
            "choices": BOOLEAN_THINKING_CHOICES,
        }
    ]


def get_thinking_level(
    provider_options: dict[str, Any],
    *,
    allowed: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max"),
) -> str | None:
    raw = provider_options.get("thinking_level")
    if raw is None:
        raw = provider_options.get("reasoning_effort")
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in allowed:
        allowed_text = ", ".join(allowed)
        raise ValueError(f"provider_options.thinking_level must be one of: {allowed_text}")
    return raw


def thinking_enabled(provider_options: dict[str, Any], *, default: bool = True) -> bool:
    raw = provider_options.get("thinking_mode")
    if raw is None:
        return default
    if raw not in {"enabled", "disabled"}:
        raise ValueError("provider_options.thinking_mode must be 'enabled' or 'disabled'")
    return raw == "enabled"


def ralph_iterations_schema(
    *,
    default: str = "1",
) -> list[dict[str, Any]]:
    return [
        {
            "key": "max_ralph_iterations",
            "label": "Ralph Iterations",
            "type": "select",
            "default": default,
            "choices": [
                {"value": "1", "label": "1", "description": "Single pass — no extra iterations."},
                {"value": "3", "label": "3", "description": "Up to two extra iterations."},
                {"value": "5", "label": "5", "description": "Up to four extra iterations."},
                {"value": "-1", "label": "Unlimited", "description": "Agent decides when to stop."},
            ],
        }
    ]


def get_ralph_iterations(provider_options: dict[str, Any]) -> int | None:
    raw = provider_options.get("max_ralph_iterations")
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            pass
    raise ValueError("provider_options.max_ralph_iterations must be an integer or integer string")


def apply_thinking_prompt_hint(prompt: str, provider_options: dict[str, Any]) -> str:
    level = get_thinking_level(provider_options)
    if level is None:
        return prompt
    return f"{PROMPT_HINTS[level]}\n\n{prompt}"

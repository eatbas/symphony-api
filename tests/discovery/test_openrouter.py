"""Coverage for the OpenRouter self-heal discovery pipeline."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from symphony.discovery import openrouter as openrouter_mod
from symphony.discovery.openrouter import (
    MAX_FREE_MODELS,
    OpenRouterDiscoveryError,
    discover_openrouter_free_models,
    fetch_openrouter_catalogue,
    invalidate_cache,
    is_free_text_model,
    rank_and_cap,
)


# ---------------------------------------------------------------------------
# is_free_text_model
# ---------------------------------------------------------------------------


def _model(
    *,
    model_id: str = "qwen/qwen3-coder:free",
    prompt: str = "0",
    completion: str = "0",
    modalities: list[str] | None = None,
    context_length: int = 100,
    skip_arch: bool = False,
    skip_pricing: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": model_id,
        "context_length": context_length,
    }
    if not skip_pricing:
        body["pricing"] = {"prompt": prompt, "completion": completion}
    if not skip_arch:
        body["architecture"] = {
            "output_modalities": ["text"] if modalities is None else modalities,
        }
    return body


def test_is_free_text_model_accepts_canonical_free_entry() -> None:
    assert is_free_text_model(_model()) is True


def test_is_free_text_model_rejects_non_string_id() -> None:
    bad = _model()
    bad["id"] = 123  # type: ignore[assignment]
    assert is_free_text_model(bad) is False


def test_is_free_text_model_rejects_when_suffix_missing() -> None:
    assert is_free_text_model(_model(model_id="qwen/qwen3-coder")) is False


def test_is_free_text_model_rejects_when_prompt_priced() -> None:
    assert is_free_text_model(_model(prompt="0.0000001")) is False


def test_is_free_text_model_rejects_when_completion_priced() -> None:
    assert is_free_text_model(_model(completion="0.0000005")) is False


def test_is_free_text_model_rejects_when_pricing_missing() -> None:
    assert is_free_text_model(_model(skip_pricing=True)) is False


def test_is_free_text_model_rejects_when_modality_includes_vision() -> None:
    assert is_free_text_model(_model(modalities=["text", "image"])) is False


def test_is_free_text_model_rejects_when_architecture_missing() -> None:
    assert is_free_text_model(_model(skip_arch=True)) is False


# ---------------------------------------------------------------------------
# rank_and_cap
# ---------------------------------------------------------------------------


def test_rank_and_cap_orders_by_context_desc_then_id_asc() -> None:
    catalogue = [
        _model(model_id="b/m:free", context_length=10),
        _model(model_id="a/m:free", context_length=100),
        _model(model_id="c/m:free", context_length=10),
    ]
    assert rank_and_cap(catalogue) == [
        "openrouter/a/m:free",
        "openrouter/b/m:free",
        "openrouter/c/m:free",
    ]


def test_rank_and_cap_caps_at_max_free_models() -> None:
    catalogue = [
        _model(model_id=f"vendor/m{i}:free", context_length=100 - i)
        for i in range(MAX_FREE_MODELS + 5)
    ]
    result = rank_and_cap(catalogue)
    assert len(result) == MAX_FREE_MODELS
    assert result[0].startswith("openrouter/")


def test_rank_and_cap_treats_missing_context_length_as_zero() -> None:
    catalogue = [
        {"id": "x/m1:free"},
        _model(model_id="y/m2:free", context_length=50),
    ]
    assert rank_and_cap(catalogue) == [
        "openrouter/y/m2:free",
        "openrouter/x/m1:free",
    ]


# ---------------------------------------------------------------------------
# fetch_openrouter_catalogue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_catalogue_returns_data_array() -> None:
    payload = {"data": [_model()]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await fetch_openrouter_catalogue(client)
        assert data == payload["data"]


@pytest.mark.asyncio
async def test_fetch_catalogue_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OpenRouterDiscoveryError):
            await fetch_openrouter_catalogue(client)


@pytest.mark.asyncio
async def test_fetch_catalogue_raises_when_data_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OpenRouterDiscoveryError):
            await fetch_openrouter_catalogue(client)


# ---------------------------------------------------------------------------
# discover_openrouter_free_models
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.mark.asyncio
async def test_discover_returns_top_n_from_mocked_catalogue(monkeypatch) -> None:
    catalogue = [_model(model_id=f"v/m{i}:free", context_length=100 - i) for i in range(15)]
    # Inject a non-free entry to confirm filtering excludes it.
    catalogue.append(_model(model_id="paid/model", prompt="0.0001"))

    async def fake_fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        return catalogue

    monkeypatch.setattr(openrouter_mod, "fetch_openrouter_catalogue", fake_fetch)

    result = await discover_openrouter_free_models()
    assert result is not None
    assert len(result) == MAX_FREE_MODELS
    assert all(entry.endswith(":free") for entry in result)


@pytest.mark.asyncio
async def test_discover_returns_none_on_http_error(monkeypatch) -> None:
    async def fake_fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(openrouter_mod, "fetch_openrouter_catalogue", fake_fetch)
    assert await discover_openrouter_free_models() is None


@pytest.mark.asyncio
async def test_discover_returns_none_on_parse_error(monkeypatch) -> None:
    async def fake_fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        raise OpenRouterDiscoveryError("bad response")

    monkeypatch.setattr(openrouter_mod, "fetch_openrouter_catalogue", fake_fetch)
    assert await discover_openrouter_free_models() is None


@pytest.mark.asyncio
async def test_discover_caches_within_ttl(monkeypatch) -> None:
    """Second call within TTL must not re-fetch."""
    catalogue = [_model(model_id="a/b:free", context_length=10)]
    calls = {"count": 0}

    async def fake_fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        calls["count"] += 1
        return catalogue

    monkeypatch.setattr(openrouter_mod, "fetch_openrouter_catalogue", fake_fetch)

    first = await discover_openrouter_free_models()
    second = await discover_openrouter_free_models()
    assert first == second == ["openrouter/a/b:free"]
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_discover_refetches_after_ttl_expires(monkeypatch) -> None:
    """Once the TTL has elapsed the cache must be treated as cold."""
    catalogue = [_model(model_id="a/b:free", context_length=10)]
    calls = {"count": 0}

    async def fake_fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        calls["count"] += 1
        return catalogue

    monkeypatch.setattr(openrouter_mod, "fetch_openrouter_catalogue", fake_fetch)

    # First call populates the cache.
    await discover_openrouter_free_models()
    # Pretend a very large amount of time has elapsed by pinning
    # ``time.monotonic`` to a much later value than the cached entry.
    original_monotonic = openrouter_mod.time.monotonic
    monkeypatch.setattr(
        openrouter_mod.time,
        "monotonic",
        lambda: original_monotonic() + openrouter_mod._CACHE_TTL_SECONDS + 1,
    )
    await discover_openrouter_free_models()
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_invalidate_cache_forces_refetch(monkeypatch) -> None:
    catalogue = [_model(model_id="a/b:free", context_length=10)]
    calls = {"count": 0}

    async def fake_fetch(client: httpx.AsyncClient) -> list[dict[str, Any]]:
        calls["count"] += 1
        return catalogue

    monkeypatch.setattr(openrouter_mod, "fetch_openrouter_catalogue", fake_fetch)

    await discover_openrouter_free_models()
    invalidate_cache()
    await discover_openrouter_free_models()
    assert calls["count"] == 2

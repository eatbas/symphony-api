from __future__ import annotations

from collections.abc import Iterable

from ..config import AppConfig
from ..models import InstrumentName, ModelDetail, MusicianInfo, ProviderCapability
from ..providers.base import ProviderAdapter
from .musician import Musician


def build_capabilities(
    *,
    config: AppConfig,
    registry: dict[InstrumentName, ProviderAdapter],
    available_providers: dict[InstrumentName, bool],
) -> list[ProviderCapability]:
    capabilities: list[ProviderCapability] = []
    for instrument, adapter in registry.items():
        instrument_config = config.providers[instrument]
        capabilities.append(
            ProviderCapability(
                provider=instrument,
                executable=adapter.resolve_executable(instrument_config.executable),
                enabled=instrument_config.enabled,
                available=available_providers.get(instrument, False),
                models=instrument_config.models,
                supports_resume=adapter.supports_resume,
                supports_model_override=adapter.supports_model_override,
                session_reference_format=adapter.session_reference_format,
            )
        )
    return capabilities


def build_model_details(
    *,
    pool: dict[tuple[InstrumentName, str], list[Musician]],
    registry: dict[InstrumentName, ProviderAdapter],
) -> list[ModelDetail]:
    """Return one ``ModelDetail`` per (provider, model) pool key.

    Lazy-pending pools (those with an empty musician list) are included
    so the API and UI can list models that have not yet been
    materialised on first request — the user-visible model menu must
    not require a chat to populate.
    """
    details: list[ModelDetail] = []
    for (provider, model), musicians in pool.items():
        adapter = registry[provider]
        primary = musicians[0] if musicians else None
        details.append(
            ModelDetail(
                provider=provider,
                model=model,
                ready=primary.ready if primary is not None else False,
                busy=primary.busy if primary is not None else False,
                supports_resume=adapter.supports_resume,
                provider_options_schema=adapter.model_option_schema(model),
                chat_request_example={
                    "provider": provider.value,
                    "model": model,
                    "workspace_path": "/path/to/your/project",
                    "mode": "new",
                    "prompt": "Your prompt here",
                    "provider_options": {},
                },
            )
        )
    return details


def build_musician_info(
    pool: dict[tuple[InstrumentName, str], list[Musician]],
    *,
    shell_backend: str,
) -> list[MusicianInfo]:
    """Return musician info for every pool key, lazy-pending or warm.

    For lazy-pending keys (empty musician list) the returned
    ``MusicianInfo`` reports the model as not-ready/idle so the UI can
    still render the entry; the first call to ``POST /v1/chat`` for
    that model will materialise a real musician on demand.  Placeholder
    entries inherit the orchestra's ``shell_backend`` so the schema
    contract still holds.
    """
    info: list[MusicianInfo] = []
    for (provider, model), musicians in pool.items():
        if musicians:
            info.extend(m.info() for m in musicians)
        else:
            info.append(
                MusicianInfo(
                    provider=provider,
                    model=model,
                    shell_backend=shell_backend,
                    ready=False,
                    busy=False,
                    queue_length=0,
                    last_error=None,
                )
            )
    return info


def build_health_details(musicians: Iterable[Musician]) -> list[str]:
    return [
        f"{musician.provider.value}/{musician.model}: {musician.last_error}"
        for musician in musicians
        if musician.last_error
    ]

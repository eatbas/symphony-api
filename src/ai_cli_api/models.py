from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProviderName(StrEnum):
    """Supported AI CLI provider identifiers."""
    GEMINI = "gemini"
    CODEX = "codex"
    CLAUDE = "claude"
    KIMI = "kimi"


class ChatMode(StrEnum):
    """Whether to start a new session or resume an existing one."""
    NEW = "new"
    RESUME = "resume"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    """Request body for submitting a prompt to an AI CLI provider.

    Supports both new sessions and resuming previous conversations.
    The response can be delivered as a single JSON object or as a
    Server-Sent Events stream.
    """

    provider: ProviderName = Field(
        description="AI CLI provider to use for this chat session.",
    )
    model: str = Field(
        min_length=1,
        description="Model identifier within the provider (e.g. 'sonnet', 'opus', 'codex-mini').",
    )
    workspace_path: str = Field(
        min_length=1,
        description="Absolute path to the workspace directory for the CLI session. Must start with '/' (Unix) or a drive letter like 'C:\\' (Windows).",
    )
    mode: ChatMode = Field(
        description="'new' starts a fresh session; 'resume' continues a previous conversation (requires provider_session_ref).",
    )
    prompt: str = Field(
        min_length=1,
        description="The prompt or instruction to send to the AI provider.",
    )
    provider_session_ref: str | None = Field(
        default=None,
        description="Session reference for resuming a previous conversation. Required when mode is 'resume'. Obtained from a prior ChatResponse or SSE provider_session event.",
    )
    stream: bool = Field(
        default=True,
        description="When true (default), the response is delivered as Server-Sent Events. When false, a single JSON ChatResponse is returned after completion.",
    )
    provider_options: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific options passed through to the underlying CLI. Common key: 'extra_args' (list of additional CLI arguments).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "workspace_path": "/home/user/project",
                    "mode": "new",
                    "prompt": "Explain the main entry point of this project.",
                    "stream": True,
                    "provider_options": {},
                },
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "workspace_path": "/home/user/project",
                    "mode": "resume",
                    "prompt": "Now refactor that function to use async/await.",
                    "provider_session_ref": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "stream": False,
                    "provider_options": {"extra_args": ["--verbose"]},
                },
            ]
        }
    )

    @field_validator("workspace_path")
    @classmethod
    def workspace_path_must_be_absolute(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("workspace_path must not be empty")
        if normalized.startswith("/"):
            return normalized
        if len(normalized) >= 3 and normalized[1:3] == ":\\":
            return normalized
        if len(normalized) >= 3 and normalized[1:3] == ":/":
            return normalized
        raise ValueError("workspace_path must be an absolute path")

    @model_validator(mode="after")
    def validate_resume_fields(self) -> "ChatRequest":
        if self.mode is ChatMode.RESUME and not self.provider_session_ref:
            raise ValueError("provider_session_ref is required for resume mode")
        return self


class ChatResponse(BaseModel):
    """Response returned for a non-streaming chat request, or the payload
    of the ``completed`` SSE event in streaming mode.
    """

    provider: ProviderName = Field(description="Provider that handled the request.")
    model: str = Field(description="Model that was used.")
    provider_session_ref: str | None = Field(description="Session reference that can be used to resume this conversation later.")
    final_text: str = Field(description="Complete accumulated output text from the AI provider.")
    exit_code: int = Field(description="CLI process exit code. 0 indicates success.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings emitted during execution.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "provider_session_ref": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "final_text": "The main entry point is in `main.py`. It initializes the FastAPI application...",
                    "exit_code": 0,
                    "warnings": [],
                }
            ]
        }
    )


class ProviderCapability(BaseModel):
    """Capability metadata for a registered AI CLI provider."""

    provider: ProviderName = Field(description="Provider identifier.")
    executable: str | None = Field(description="Resolved path to the provider CLI executable, or null if not found.")
    enabled: bool = Field(description="Whether this provider is enabled in the configuration.")
    supports_resume: bool = Field(description="Whether the provider supports resuming previous sessions.")
    supports_streaming: bool = Field(description="Whether the provider supports streaming output.")
    supports_model_override: bool = Field(description="Whether a custom model can be specified per request.")
    session_reference_format: str = Field(description="Format of the session reference (e.g. 'uuid').")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "provider": "claude",
                    "executable": "/usr/local/bin/claude",
                    "enabled": True,
                    "supports_resume": True,
                    "supports_streaming": True,
                    "supports_model_override": True,
                    "session_reference_format": "uuid",
                }
            ]
        }
    )


class WorkerInfo(BaseModel):
    """Runtime status of a warm worker process."""

    provider: ProviderName = Field(description="Provider this worker serves.")
    model: str = Field(description="Model this worker is configured for.")
    shell_backend: str = Field(description="Path to the shell executable backing this worker.")
    ready: bool = Field(description="True if the worker shell has started and is accepting requests.")
    busy: bool = Field(description="True if the worker is currently processing a request.")
    queue_length: int = Field(description="Number of requests waiting in the worker's queue.")
    last_error: str | None = Field(default=None, description="Most recent error message, or null if healthy.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "provider": "claude",
                    "model": "sonnet",
                    "shell_backend": "/usr/bin/bash",
                    "ready": True,
                    "busy": False,
                    "queue_length": 0,
                    "last_error": None,
                }
            ]
        }
    )


class HealthResponse(BaseModel):
    """System health check result. Returns ``ok`` when all workers are
    healthy, or ``degraded`` when one or more workers report errors.
    """

    status: Literal["ok", "degraded"] = Field(description="Overall health status.")
    config_path: str = Field(description="Filesystem path to the loaded configuration file.")
    shell_path: str | None = Field(description="Resolved shell executable path, or null if auto-detection failed.")
    workers_booted: bool = Field(description="True if all configured workers have started successfully.")
    worker_count: int = Field(description="Total number of configured workers.")
    details: list[str] = Field(default_factory=list, description="Error messages from unhealthy workers. Empty when status is 'ok'.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "status": "ok",
                    "config_path": "/home/user/ai-cli-api/config.toml",
                    "shell_path": "/usr/bin/bash",
                    "workers_booted": True,
                    "worker_count": 7,
                    "details": [],
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# Error model (for OpenAPI error response documentation)
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    """Standard error response body returned by the API."""

    detail: str = Field(description="Human-readable error message.")


# ---------------------------------------------------------------------------
# SSE event models (documentation-only, for OpenAPI schema generation)
# ---------------------------------------------------------------------------

class SSERunStarted(BaseModel):
    """SSE event emitted when the CLI process is launched."""

    provider: ProviderName = Field(description="Provider handling the request.")
    model: str = Field(description="Model being used.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"provider": "claude", "model": "sonnet"}]
        }
    )


class SSEProviderSession(BaseModel):
    """SSE event emitted when a session reference is assigned or known."""

    provider_session_ref: str = Field(description="Session reference for resuming this conversation later.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"provider_session_ref": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}]
        }
    )


class SSEOutputDelta(BaseModel):
    """SSE event emitted for each incremental chunk of output text."""

    text: str = Field(description="Incremental output text from the AI provider.")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"text": "Here is the refactored function:\n```python\n"}]
        }
    )


class SSECompleted(BaseModel):
    """SSE event emitted when the CLI process finishes successfully."""

    provider: ProviderName = Field(description="Provider that handled the request.")
    model: str = Field(description="Model that was used.")
    provider_session_ref: str | None = Field(description="Session reference for resuming later.")
    final_text: str = Field(description="Complete accumulated output text.")
    exit_code: int = Field(description="CLI exit code (0 = success).")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings.")


class SSEFailed(BaseModel):
    """SSE event emitted when the CLI process exits with an error."""

    provider: ProviderName = Field(description="Provider that handled the request.")
    model: str = Field(description="Model that was used.")
    provider_session_ref: str | None = Field(description="Session reference, if one was assigned.")
    exit_code: int = Field(description="CLI exit code (non-zero).")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings.")
    error: str = Field(description="Human-readable error message describing the failure.")

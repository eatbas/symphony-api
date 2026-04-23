# Symphony API

Symphony -- coordinated AI CLI orchestra for **Gemini**, **Codex**, **Claude**, **Kimi**, **Copilot**, and **OpenCode**.

## What it does

- Starts one warm background bash musician per configured `provider + model`
- Accepts API calls over HTTP
- Runs the matching CLI inside the already-open bash musician
- Returns a durable score ID immediately (HTTP 202) — poll or connect via WebSocket for results
- Keeps no persistent conversation state in the bridge
- Periodically checks for CLI updates and can auto-update idle musicians

The caller sends `provider`, `model`, `workspace_path`, and a prompt. When resuming, include the provider-native session reference. Track results via polling or WebSocket.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .[dev]
uvicorn symphony.main:app --reload
```

Default config is loaded from `config.toml`. Override with `SYMPHONY_CONFIG=/path/to/config.toml`.
Start from `config.example.toml` for new local installs. Runtime config is local-only; model discovery can update it based on installed CLIs.

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) to use the built-in web test console.

Interactive API docs are available at `/docs` (Swagger) and `/redoc` (ReDoc).

## Providers

| Provider     | CLI executable | Default models                           | Resume |
| ------------ | -------------- | ---------------------------------------- | ------ |
| **Gemini** | `gemini` | `gemini-3-flash-preview`, `gemini-2.5-pro`, `gemini-3.1-pro-preview` | Yes |
| **Codex** | `codex` | `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.2` | Yes |
| **Claude** | `claude` | `opus`, `sonnet`, `haiku` | Yes |
| **Kimi** | `kimi` | `kimi-code/kimi-for-coding` | Yes |
| **Copilot** | `copilot` | `claude-sonnet-4.6`, `claude-haiku-4.5`, `claude-opus-4.6`, `gpt-5.4`, `gpt-5.3-codex`, `gpt-5.4-mini`, `gpt-5.2`, `gpt-5-mini`, `gpt-4.1`, `gemini-3-pro`, `gemini-3-flash`, `gemini-2.5-pro`, `grok-code-fast-1` | Yes |
| **OpenCode** | `opencode` | `glm-4.5`, `glm-4.5-air`, `glm-4.5-flash`, `glm-4.5v`, `glm-4.6`, `glm-4.6v`, `glm-4.7`, `glm-4.7-flash`, `glm-4.7-flashx`, `glm-5`, `glm-5-turbo`, `glm-5.1` | Yes |

## Config

`config.toml` prewarms musicians for the configured provider-model pairs.

```toml
[server]
host = "127.0.0.1"
port = 8000

[shell]
path = ""  # Auto-detect on Windows

[storage]
score_dir = ""  # empty = ~/.maestro/symphony/scores; override with SYMPHONY_SCORE_DIR

[providers.claude]
enabled = true
executable = ""            # auto-detect from PATH
models = ["opus", "sonnet", "haiku"]
default_options = { extra_args = [] }
cli_timeout = 0            # seconds; 0 = no timeout
concurrency = 4            # max concurrent musicians per model

[updater]
enabled = true
interval_hours = 4
auto_update = true
```

- `models` -- Each string becomes a musician. The value is passed to the provider CLI `--model` flag.
- `executable` -- Leave empty to auto-detect from PATH, or set an absolute path.
- `default_options.extra_args` -- Raw CLI flags appended to every command for this provider.
- `cli_timeout` -- Maximum seconds a single CLI invocation may run. `0` means no timeout.
- `concurrency` -- Maximum musician instances per model. Pools scale lazily from 1 up to this limit.
- `updater` -- Controls automatic CLI version checking and updates.

## API

### Health & System

#### `GET /health`
Returns health status, shell availability, and musician boot state.

#### `GET /v1/cli-versions`
Returns cached CLI version statuses (current version, latest version, update availability).

#### `POST /v1/cli-versions/check`
Triggers a version check for all provider CLIs. Returns current and latest versions for each.

#### `POST /v1/cli-versions/{provider}/check`
Checks a single provider CLI for updates. Returns `404` if the provider name is unknown.

#### `POST /v1/cli-versions/{provider}/update`
Force-updates a single provider CLI. The provider's musicians are restarted after the update completes. Returns `404` if the provider name is unknown.

### Providers & Models

#### `GET /v1/providers`
Returns provider capabilities and executable discovery results.

#### `GET /v1/models`
Returns all available models across all providers with per-model status and chat examples.

#### `GET /v1/musicians`
Returns the musician inventory, status, and queue depth.

### Chat

#### `POST /v1/chat`
Submits a prompt to a provider. Returns **HTTP 202 Accepted** immediately with a `score_id`. Use polling or WebSocket to track progress.

**New chat:**
```json
{
  "provider": "claude",
  "model": "sonnet",
  "workspace_path": "/home/user/project",
  "mode": "new",
  "prompt": "say hello in one word",
  "provider_options": {}
}
```

**Resume chat:**
```json
{
  "provider": "gemini",
  "model": "gemini-3-flash-preview",
  "workspace_path": "/home/user/project",
  "mode": "resume",
  "prompt": "say hi in one word",
  "provider_session_ref": "e3c7d445-d2f3-4e61-931f-62d7182902e6",
  "provider_options": {}
}
```

**Provider options** -- per-request overrides passed through to the CLI:

| Key | Providers | Description |
|-----|-----------|-------------|
| `extra_args` | All | Raw CLI flags appended to the command (list of strings). |
| `effort` | Claude | Reasoning effort: `"low"`, `"medium"`, or `"high"`. Omit for CLI default (medium). |
| `max_turns` | Claude | Maximum autonomous tool-use turns (integer as string). Omit for CLI default. |

#### `GET /v1/chat/{score_id}` (Polling)
Returns the authoritative `ScoreSnapshot` for a score. Poll this endpoint at your preferred interval until the status reaches a terminal state (`completed`, `failed`, or `stopped`).

**ScoreSnapshot fields:**

| Field | Description |
|-------|-------------|
| `score_id` | Unique score identifier. |
| `status` | `queued`, `running`, `completed`, `failed`, or `stopped`. |
| `accumulated_text` | All output captured so far. |
| `final_text` | Authoritative output text (set when terminal). |
| `provider_session_ref` | Session reference for resume mode. |
| `exit_code` | CLI process exit code (set when terminal). |
| `warnings` | Non-fatal warnings emitted by the CLI. |
| `created_at` / `started_at` / `updated_at` / `finished_at` | RFC 3339 timestamps. |

#### `WS /v1/chat/{score_id}/ws` (WebSocket)
Opens a WebSocket connection for real-time score events. Receives an initial `score_snapshot` message, then individual events as they occur.

**WebSocket event types:**

| Event               | Description                            |
| ------------------- | -------------------------------------- |
| `score_snapshot`    | Full score state (sent on connect)     |
| `run_started`       | Musician picked up the score           |
| `provider_session`  | Session ID from the provider CLI       |
| `output_delta`      | Incremental text chunk                 |
| `completed`         | Final text and exit code               |
| `failed`            | Error message and exit code            |
| `stopped`           | Score was cancelled by the user        |

The connection closes automatically when the score reaches a terminal state.

#### `POST /v1/chat/{score_id}/stop`
Stops a running or queued score. The `score_id` is returned in the accepted response from `POST /v1/chat`.

- If the score is **running**, sends an interrupt signal (Ctrl-C) to the CLI process.
- If the score is **queued**, removes it before execution begins.
- If the score already **completed** or **failed**, returns the terminal status (idempotent).

Returns `404` if the score ID is not found.

### Test Lab

#### `POST /v1/test/verify`

Verifies test results across multiple models using keyword matching. A model receives **PASS** when: NEW chat exited 0, RESUME chat exited 0, and every keyword is found in the resume response (case-insensitive).

```json
{
  "items": [
    {
      "provider": "claude",
      "model": "sonnet",
      "new_exit_code": 0,
      "resume_text": "Your responsibilities include managing PF, ATM, and Transit.",
      "resume_exit_code": 0,
      "keywords": ["PF", "ATM", "Transit"]
    }
  ]
}
```

#### `POST /v1/test/generate-scenario`

AI-generates test scenario content (story + QA pairs). By default uses the cheapest available model (Claude Haiku -> Codex GPT-5.4-mini). Optionally specify `provider` and `model` to target a specific musician.

**Auto (cheapest):**
```json
{
  "field": "all",
  "workspace_path": "C:\\Github\\hive-api"
}
```

**Explicit model:**
```json
{
  "field": "all",
  "workspace_path": "/home/user/project",
  "provider": "codex",
  "model": "gpt-5.4-mini"
}
```

## Test Lab (Web Console)

The web console includes a **Test Lab** section that runs automated 2-step tests across all configured models in parallel:

1. **NEW** -- Sends a "story" prompt to each selected model as a new chat session
2. **RESUME** -- Sends a follow-up question to each model, resuming the session from step 1
3. **VERIFY** -- Checks the resume response for expected keywords and grades each model PASS/FAIL

Features:
- Run all models in parallel with one click
- Magic buttons to AI-generate test scenarios
- Real-time results table with progressive status updates
- Keyword-based verification (case-insensitive)

## Adding a new provider

1. Create `src/symphony/providers/<name>.py` -- subclass `ProviderAdapter`
2. Add `<NAME>` to the `InstrumentName` enum in `models.py`
3. Register the adapter in `providers/registry.py`
4. Add a `[providers.<name>]` section to `config.toml`
5. Add the CLI package mapping in `updater.py` `PACKAGE_REGISTRY`
6. Add a fake CLI branch in `tests/fakes/fake_cli.py`
7. Add a config section in `tests/conftest.py`
8. Add a test in `tests/test_api.py`
9. Update `README.md` provider table

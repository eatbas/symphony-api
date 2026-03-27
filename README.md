# Hive API

Hive — coordinated AI CLI collective for **Gemini**, **Codex**, **Claude**, **Kimi**, **Copilot**, and **OpenCode**.

## What it does

- Starts one warm background bash drone per configured `provider + model`
- Accepts API calls over HTTP
- Runs the matching CLI inside the already-open bash drone
- Streams output back over Server-Sent Events or returns JSON
- Keeps no persistent conversation state in the bridge
- Periodically checks for CLI updates and can auto-update idle drones

The caller must send `provider`, `model`, `workspace_path`, and when resuming, the provider-native session reference.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .[dev]
uvicorn hive_api.main:app --reload
```

Default config is loaded from `config.toml`. Override with `HIVE_API_CONFIG=/path/to/config.toml`.

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) to use the built-in web test console.

Interactive API docs are available at `/docs` (Swagger) and `/redoc` (ReDoc).

## Providers

| Provider     | CLI executable | Default models                           | Resume |
| ------------ | -------------- | ---------------------------------------- | ------ |
| **Gemini**   | `gemini`       | `gemini-3-flash-preview`                 | Yes |
| **Codex**    | `codex`        | `codex-5.3`, `gpt-5.4`, `gpt-5.4-mini`  | Yes    |
| **Claude**   | `claude`       | `opus`, `sonnet`, `haiku`                | Yes    |
| **Kimi**     | `kimi`         | `kimi-code/kimi-for-coding`              | Yes    |
| **Copilot**  | `copilot`      | `claude-sonnet-4.6`, `claude-haiku-4.5`, `claude-opus-4.6`, `gpt-5.4`, `gpt-5.3-codex`, `gpt-5.4-mini` | Yes |
| **OpenCode** | `opencode`     | `glm-5`, `glm-5.1`, `glm-5-turbo`, `glm-4.7` | Yes    |

## Config

`config.toml` prewarms drones for the configured provider-model pairs.

```toml
[server]
host = "127.0.0.1"
port = 8000

[shell]
path = ""  # Auto-detect on Windows

[providers.claude]
enabled = true
executable = ""            # auto-detect from PATH
models = ["opus", "sonnet", "haiku"]
default_options = { extra_args = [] }
cli_timeout = 0            # seconds; 0 = no timeout
concurrency = 4            # max concurrent drones per model

[updater]
enabled = true
interval_hours = 4
auto_update = true
```

- `models` — Each string becomes a drone. The value is passed to the provider CLI `--model` flag.
- `executable` — Leave empty to auto-detect from PATH, or set an absolute path.
- `default_options.extra_args` — Raw CLI flags appended to every command for this provider.
- `cli_timeout` — Maximum seconds a single CLI invocation may run. `0` means no timeout.
- `concurrency` — Maximum drone instances per model. Pools scale lazily from 1 up to this limit.
- `updater` — Controls automatic CLI version checking and updates.

## API

### Health & System

#### `GET /health`
Returns health status, shell availability, and drone boot state.

#### `GET /v1/cli-versions`
Returns cached CLI version statuses (current version, latest version, update availability).

#### `POST /v1/cli-versions/check`
Triggers a version check for all provider CLIs. Returns current and latest versions for each.

#### `POST /v1/cli-versions/{provider}/check`
Checks a single provider CLI for updates. Returns `404` if the provider name is unknown.

#### `POST /v1/cli-versions/{provider}/update`
Force-updates a single provider CLI. The provider's drones are restarted after the update completes. Returns `404` if the provider name is unknown.

### Providers & Models

#### `GET /v1/providers`
Returns provider capabilities and executable discovery results.

#### `GET /v1/models`
Returns all available models across all providers with per-model status and chat examples.

#### `GET /v1/drones`
Returns the drone inventory, status, and queue depth.

### Chat

#### `POST /v1/chat`
Sends a prompt to a provider. Supports streaming (SSE) and JSON response modes.

**New chat:**
```json
{
  "provider": "claude",
  "model": "sonnet",
  "workspace_path": "/home/user/project",
  "mode": "new",
  "prompt": "say hello in one word",
  "stream": true,
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
  "stream": false,
  "provider_options": {}
}
```

**Provider options** — per-request overrides passed through to the CLI:

| Key | Providers | Description |
|-----|-----------|-------------|
| `extra_args` | All | Raw CLI flags appended to the command (list of strings). |
| `effort` | Claude | Reasoning effort: `"low"`, `"medium"`, or `"high"`. Omit for CLI default (medium). |
| `max_turns` | Claude | Maximum autonomous tool-use turns (integer as string). Omit for CLI default. |

**SSE events** (when `stream: true`):

| Event               | Description                            |
| ------------------- | -------------------------------------- |
| `run_started`       | Drone picked up the job (includes `job_id`) |
| `provider_session`  | Session ID from the provider CLI       |
| `output_delta`      | Incremental text chunk                 |
| `completed`         | Final text and exit code               |
| `failed`            | Error message and exit code            |
| `stopped`           | Job was cancelled by the user          |

#### `POST /v1/chat/{job_id}/stop`
Stops a running or queued job. The `job_id` is returned in the `run_started` SSE event or in the JSON response.

- If the job is **running**, sends an interrupt signal (Ctrl-C) to the CLI process.
- If the job is **queued**, removes it before execution begins.
- If the job already **completed** or **failed**, returns the terminal status (idempotent).

Returns `404` if the job ID is not found.

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

AI-generates test scenario content (story + QA pairs). By default uses the cheapest available model (Claude Haiku → Codex GPT-5.4-mini). Optionally specify `provider` and `model` to target a specific drone.

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

1. **NEW** — Sends a "story" prompt to each selected model as a new chat session
2. **RESUME** — Sends a follow-up question to each model, resuming the session from step 1
3. **VERIFY** — Checks the resume response for expected keywords and grades each model PASS/FAIL

Features:
- Run all models in parallel with one click
- Magic buttons to AI-generate test scenarios
- Real-time results table with progressive status updates
- Keyword-based verification (case-insensitive)

## Adding a new provider

1. Create `src/hive_api/providers/<name>.py` — subclass `ProviderAdapter`
2. Add `<NAME>` to the `ProviderName` enum in `models.py`
3. Register the adapter in `providers/registry.py`
4. Add a `[providers.<name>]` section to `config.toml`
5. Add the CLI package mapping in `updater.py` `PACKAGE_REGISTRY`
6. Add a fake CLI branch in `tests/fakes/fake_cli.py`
7. Add a config section in `tests/conftest.py`
8. Add a test in `tests/test_api.py`
9. Update `README.md` provider table

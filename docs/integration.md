# Symphony Integration Guide

This guide describes how another service, desktop application, or automation can integrate with Symphony.

Symphony is an HTTP bridge for AI CLI providers. It accepts a prompt, schedules it as a durable score, runs the matching provider CLI inside a warm musician process, and exposes progress by polling or WebSocket.

## Source Of Truth

Use the OpenAPI schema as the contract for generated clients:

- `GET /openapi.json` - machine-readable OpenAPI schema.
- `GET /docs` - Swagger UI for exploration.
- `GET /redoc` - ReDoc reference view.
- `GET /llms.txt` - short AI-readable integration index.

The Markdown files in this directory explain workflow and operational behaviour that OpenAPI cannot express fully.

## Typical Flow

1. Call `GET /health` to check readiness.
2. Call `GET /v1/providers` or `GET /v1/models` to choose a provider/model pair.
3. Submit work with `POST /v1/chat`.
4. Store the returned `score_id`.
5. Track progress using either polling or WebSocket streaming.
6. Persist the returned `provider_session_ref` if you need provider-native resume.
7. Stop active work with `POST /v1/chat/{score_id}/stop` when needed.

## Submit A New Chat

```bash
curl -sS http://127.0.0.1:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "claude",
    "model": "opus",
    "workspace_path": "/absolute/path/to/project",
    "mode": "new",
    "prompt": "Summarise the repository structure.",
    "provider_options": {}
  }'
```

`POST /v1/chat` returns `202 Accepted` immediately. It does not wait for the provider CLI to finish.

Example response:

```json
{
  "score_id": "7b5e0f8a86c146f7a6cb74294818f3d4",
  "status": "queued",
  "provider": "claude",
  "model": "opus",
  "created_at": "2026-04-23T18:00:00Z",
  "started_at": null
}
```

## Polling

Polling is the simplest integration path.

```bash
curl -sS http://127.0.0.1:8000/v1/chat/7b5e0f8a86c146f7a6cb74294818f3d4
```

Poll until `status` is one of:

- `completed`
- `failed`
- `stopped`

Use `final_text` as the authoritative final response when `status` is `completed`. Use `error` when `status` is `failed` or `stopped`.

## WebSocket Streaming

Use WebSocket streaming when the caller needs live output.

Connect to:

```text
WS /v1/chat/{score_id}/ws
```

The server sends an initial `score_snapshot` event, then incremental score events. The socket closes automatically when the score reaches a terminal status.

Common event types:

- `score_snapshot` - full current score state.
- `run_started` - a musician has started the score.
- `provider_session` - provider-native session reference is available.
- `output_delta` - incremental generated text.
- `completed` - terminal success.
- `failed` - terminal failure.
- `stopped` - terminal cancellation.

## Resume

Provider-native resume requires a previous `provider_session_ref`.

```json
{
  "provider": "claude",
  "model": "opus",
  "workspace_path": "/absolute/path/to/project",
  "mode": "resume",
  "prompt": "Continue from the previous answer.",
  "provider_session_ref": "claude-session-id",
  "provider_options": {}
}
```

Resume must use the same model that created the original provider session. Symphony rejects model changes for known sessions to avoid provider-level inconsistency.

## Cancellation

Stop a queued or running score:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/chat/{score_id}/stop
```

The operation is idempotent for terminal scores.

## Inspect Usage

Symphony exposes per-instrument quota and consumption snapshots so callers can see how much of each provider's plan has been consumed.

```bash
curl -sS http://127.0.0.1:8000/v1/usage
```

The response is a list of `UsageSnapshot` entries. Each available instrument that publishes usable data returns two entries -- one for the 5-hour rolling window (matching Claude Code's native cadence) and one for the weekly window. Instruments without a published quota surface return a single entry with `supported=false` and `source="not_supported"`.

Force an immediate re-probe with:

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/usage/refresh
```

Per-instrument variants are available as `GET /v1/usage/{provider}` and `POST /v1/usage/{provider}/refresh`. The cache otherwise refreshes itself every 15 minutes in the background while the updater is enabled.

Key `UsageSnapshot` fields:

- `provider` and optional `model` identify the snapshot's scope.
- `supported` is `false` when Symphony cannot read usage for the instrument.
- `window` is `"5h_rolling"`, `"weekly"`, or null.
- `used`, `limit`, `remaining`, and `percent_remaining` carry the counters; any may be null when the underlying CLI does not publish that figure.
- `resets_at` is set when the provider reports a window reset time.
- `source` is one of `"cli_command"`, `"session_log"`, `"stream"`, or `"not_supported"`.

## Error Handling

Recommended client behaviour:

- Treat `400` as an invalid or unavailable provider.
- Treat `404` as an unknown provider/model or score.
- Treat `422` as invalid request shape.
- Treat `5xx` as a server or provider execution failure.
- Always inspect the final score snapshot for `status`, `error`, `exit_code`, and `warnings`.

## Provider Options

`provider_options.extra_args` is passed through to the provider CLI as raw arguments. Only send arguments that are intended for that CLI.

Provider-specific options may be supported by individual adapters. See `/openapi.json` and the provider adapter tests for the current request schema and examples.

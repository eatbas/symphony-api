# AI Agent Integration Contract

This file is written for AI agents and automation systems that need to call Symphony reliably.

## What Symphony Does

Symphony runs prompts through configured AI CLI providers such as Claude, Codex, Antigravity, Kimi, and OpenCode. Each submitted prompt becomes a score. A score is durable and can be tracked by `score_id`.

## Required Behaviour

Do this:

1. Read `GET /openapi.json` before generating client code.
2. Discover available models with `GET /v1/models`.
3. Submit prompts with `POST /v1/chat`.
4. Save `score_id`.
5. Poll `GET /v1/chat/{score_id}` or connect to `WS /v1/chat/{score_id}/ws`.
6. Stop when score status is `completed`, `failed`, or `stopped`.
7. Save `provider_session_ref` if you need to resume later.

Do not do this:

- Do not wait for `POST /v1/chat` to return the model answer.
- Do not invent provider/model names. Discover them first.
- Do not use relative `workspace_path` values.
- Do not resume without `provider_session_ref`.
- Do not assume every provider emits the same intermediate events.
- Do not treat partial `accumulated_text` as final until the score is terminal.

## Minimal Algorithm

```text
models = GET /v1/models
choose provider/model where ready is true
accepted = POST /v1/chat
score_id = accepted.score_id

loop:
  snapshot = GET /v1/chat/{score_id}
  if snapshot.status in ["completed", "failed", "stopped"]:
    return snapshot
  wait briefly
```

Use exponential backoff with a small cap for polling, for example 250 ms to 2 s. Use WebSocket streaming for interactive interfaces.

## Request Shape

Minimum new chat request:

```json
{
  "provider": "codex",
  "model": "gpt-5.4",
  "workspace_path": "/absolute/path/to/workspace",
  "mode": "new",
  "prompt": "Implement the requested change.",
  "provider_options": {}
}
```

Minimum resume request:

```json
{
  "provider": "codex",
  "model": "gpt-5.4",
  "workspace_path": "/absolute/path/to/workspace",
  "mode": "resume",
  "prompt": "Continue the previous task.",
  "provider_session_ref": "provider-native-session-id",
  "provider_options": {}
}
```

## Terminal Snapshot Rules

When `status` is `completed`:

- Use `final_text` as the result.
- Store `provider_session_ref` if present.
- Inspect `warnings`.

When `status` is `failed`:

- Treat the score as failed.
- Read `error`, `exit_code`, and `warnings`.
- Do not retry blindly if the failure is request validation or model mismatch.

When `status` is `stopped`:

- Treat the score as intentionally cancelled.
- Do not continue polling.

## WebSocket Rules

Connect to `WS /v1/chat/{score_id}/ws`.

Expected behaviour:

- First message is `score_snapshot`.
- Later messages are score events.
- Socket closes after `completed`, `failed`, or `stopped`.

If the WebSocket disconnects before a terminal event, recover by polling `GET /v1/chat/{score_id}`.

## Idempotence And Recovery

`score_id` is the recovery key. If the client process crashes after submission, resume tracking by polling the score endpoint.

`POST /v1/chat/{score_id}/stop` is safe to call for queued, running, or already-terminal scores.

## Usage Surface

The usage endpoints are read-only and orthogonal to the chat lifecycle. Use them when the agent needs to reason about how much of a provider's plan has been consumed before submitting a new chat.

Endpoints:

- `GET /v1/usage` - list `UsageSnapshot` entries across instruments. Cache-first; lazy probe on cold cache.
- `POST /v1/usage/refresh` - force an immediate re-probe of every available instrument.
- `GET /v1/usage/{provider}` - per-instrument snapshots. Returns `400` if the instrument's CLI is not installed; returns a single `not_supported` snapshot (HTTP 200) when the CLI is installed but exposes no quota data.
- `POST /v1/usage/{provider}/refresh` - per-instrument re-probe.

Rules:

- Treat `supported=false` and `source="not_supported"` as "no quota signal available" rather than an error.
- A provider may return more than one snapshot: typically one for `window="5h_rolling"` and one for `window="weekly"`.
- `limit`, `remaining`, and `percent_remaining` are populated only where the CLI publishes a cap. Do not infer caps from `used` alone.
- `as_of` is the probe timestamp. Treat snapshots older than a few minutes as stale and call `POST /v1/usage/refresh` before acting on them.

## Client Generation

Generate clients from:

```text
GET /openapi.json
```

Generated clients should still implement the score lifecycle rules in this document, because OpenAPI describes request/response shapes but not the asynchronous workflow.

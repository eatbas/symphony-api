# Client Generation

Symphony exposes a FastAPI OpenAPI schema at:

```text
GET /openapi.json
```

Use this schema as the source of truth for generated SDKs. FastAPI includes path operations, request models, response models, status codes, tags, summaries, and descriptions in the OpenAPI output.

## TypeScript

Example using an OpenAPI client generator:

```bash
curl -sS http://127.0.0.1:8000/openapi.json -o openapi.json
npx openapi-typescript openapi.json -o symphony-openapi.d.ts
```

For a full request client, use the generator already standardised by your application. Keep generated files in a dedicated folder and regenerate them when the API contract changes.

## Python

Python clients can use the OpenAPI schema directly, or call the HTTP API with a typed wrapper around the documented request and response models.

For durable integrations, wrap these operations:

- `get_health()`
- `list_providers()`
- `list_models()`
- `submit_chat()`
- `get_score()`
- `stop_score()`
- `stream_score_events()`

## Versioning

The API version is currently exposed in the FastAPI application metadata and endpoint paths such as `/v1/chat`.

When integrating:

- Pin the Symphony deployment version you target.
- Regenerate clients after API changes.
- Keep score lifecycle handling outside generated code so it remains explicit and testable.

## Contract Tests

Client libraries should include integration tests that verify:

- `POST /v1/chat` returns `202` and a `score_id`.
- Polling reaches a terminal status.
- WebSocket streams start with `score_snapshot`.
- WebSocket closes after terminal status.
- `POST /v1/chat/{score_id}/stop` is idempotent.

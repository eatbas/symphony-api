# Usage Endpoints

Symphony exposes per-instrument quota / consumption snapshots. Two windows are reported where the underlying CLI surfaces them: a 5-hour rolling window (matching Claude Code's native quota cadence) and a weekly window. Providers without a public quota surface (currently Antigravity) appear in the response with `supported=false` and `source="not_supported"` so consumers see a uniform shape across instruments.

Data is sourced from each CLI's local session JSONL files:

- Claude Code: `~/.claude/projects/**/*.jsonl`
- Codex: `~/.codex/sessions/**/*.jsonl`
- Kimi: `$KIMI_SHARE_DIR/sessions/**/*.jsonl` (defaulting to `~/.kimi/sessions/`)

Reads are bounded (newest files first, capped per-file and per-probe) and run inside `asyncio.to_thread` so probes never block the event loop. A background refresh runs every 15 minutes while the updater is enabled.

## Endpoints

### `GET /v1/usage`
Returns the cached list of `UsageSnapshot` entries for every available instrument. Runs a lazy probe on a cold cache.

### `POST /v1/usage/refresh`
Forces an immediate re-probe of every available instrument and updates the cache.

### `GET /v1/usage/{provider}`
Returns the cached snapshots for one instrument (lazy on cold cache). Returns `400` if the provider's CLI is not installed; returns a single `not_supported` snapshot (HTTP 200) when the CLI is installed but exposes no quota data.

### `POST /v1/usage/{provider}/refresh`
Forces an immediate re-probe of one instrument.

## `UsageSnapshot` fields

| Field | Description |
|-------|-------------|
| `provider` | Instrument identifier. |
| `model` | Model the snapshot covers, or null when the source aggregates plan-wide. |
| `supported` | `true` when Symphony can read usage data for this instrument. |
| `window` | `"5h_rolling"`, `"weekly"`, or a fallback value; null when unsupported. |
| `used` | Counters consumed in the window: `requests`, `input_tokens`, `output_tokens`, `total_tokens`, `cost_usd` (any may be null). |
| `limit` / `remaining` / `percent_remaining` | Populated only where the underlying CLI publishes a cap. |
| `resets_at` | ISO-8601 timestamp when the window resets, if known. |
| `as_of` | ISO-8601 timestamp of the probe. |
| `source` | `"cli_command"`, `"session_log"`, `"stream"`, or `"not_supported"`. |
| `note` | Optional human-readable detail (fallback reason, no-data notice, error). |

## Quick examples

```bash
# List every instrument's current snapshot (lazy probe on cold cache).
curl -sS http://127.0.0.1:8000/v1/usage

# Force an immediate re-probe of every instrument.
curl -sS -X POST http://127.0.0.1:8000/v1/usage/refresh

# Inspect one instrument.
curl -sS http://127.0.0.1:8000/v1/usage/claude

# Force a re-probe of one instrument.
curl -sS -X POST http://127.0.0.1:8000/v1/usage/claude/refresh
```

# Configuration

Symphony loads configuration from `config.toml` by default. Override the path with:

```bash
SYMPHONY_CONFIG=/path/to/config.toml
```

Use `config.example.toml` as the committed template. Keep `config.toml` local because startup discovery can update provider model lists based on installed CLIs.

## Server

```toml
[server]
host = "127.0.0.1"
port = 8000
```

## Shell

```toml
[shell]
path = ""
```

Leave `path` empty to auto-detect Bash. On Windows, Symphony prefers Git Bash.

## Storage

```toml
[storage]
score_dir = ""
```

When empty, score snapshots are stored in:

```text
~/.maestro/symphony/scores
```

Override with either config or environment:

```bash
SYMPHONY_SCORE_DIR=/absolute/writable/path
```

Use a writable, persistent path in production if score recovery after restart matters.

## Providers

Each provider section controls CLI availability, configured models, timeouts, and pool concurrency.

```toml
[providers.claude]
enabled = true
executable = ""
models = ["opus", "sonnet", "haiku"]
default_options = { extra_args = [] }
cli_timeout = 0
idle_timeout = 0
concurrency = 4
```

Fields:

- `enabled` - whether the provider participates in boot.
- `executable` - empty means resolve from `PATH`.
- `models` - models exposed by Symphony for that provider.
- `default_options.extra_args` - CLI arguments applied to every request for this provider.
- `cli_timeout` - maximum run duration in seconds; `0` disables the timeout.
- `idle_timeout` - maximum seconds without CLI output; `0` disables idle detection.
- `concurrency` - maximum warm musician processes per model.

## Discovery

Startup discovery can update local `config.toml` with models found from installed CLIs.

Disable discovery for deterministic tests or controlled deployments:

```bash
SYMPHONY_SKIP_DISCOVERY=1
```

## Updater

```toml
[updater]
enabled = true
interval_hours = 4
auto_update = true
```

The updater checks CLI versions and can update idle providers. Disable it in tests or deployments where package updates are managed externally.

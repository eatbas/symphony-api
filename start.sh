#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR=".venv"

# ---------------------------------------------------------------------------
# Find a Python 3.12+ interpreter (Unix versioned binaries, Windows py
# launcher, then generic python/python3 with version check)
# ---------------------------------------------------------------------------
PYTHON_BIN=""
PY_LAUNCHER_VER=""

# 1. Unix-style versioned binaries (macOS/Linux)
for candidate in python3.14 python3.13 python3.12; do
  if command -v "$candidate" &>/dev/null; then
    PYTHON_BIN="$candidate"
    break
  fi
done

# 2. Windows Python Launcher (py.exe) — used in Git Bash
if [ -z "$PYTHON_BIN" ] && command -v py &>/dev/null; then
  for ver in 3.14 3.13 3.12; do
    if py "-$ver" --version &>/dev/null 2>&1; then
      PYTHON_BIN="py"
      PY_LAUNCHER_VER="-$ver"
      break
    fi
  done
fi

# 3. Generic python3 / python — only if already >= 3.12
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
      if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
      fi
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "Error: Python 3.12 or newer is required but was not found on PATH."
  case "$(uname -s)" in
    Darwin) echo "  Install via: brew install python@3.12" ;;
    Linux)  echo "  Install via: sudo apt install python3.12  (or distro equivalent)" ;;
    *)      echo "  Install via: https://www.python.org/downloads/" ;;
  esac
  exit 1
fi

# ---------------------------------------------------------------------------
# Validate or recreate the venv
# ---------------------------------------------------------------------------

# Locate the venv's Python executable (Unix: bin/python, Windows: Scripts/python)
VENV_PYTHON="$VENV_DIR/bin/python"
[ -f "$VENV_DIR/Scripts/python.exe" ] && VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
[ -f "$VENV_DIR/Scripts/python" ]     && VENV_PYTHON="$VENV_DIR/Scripts/python"

# Recreate if missing or built with Python < 3.12
if [ -f "$VENV_PYTHON" ] && "$VENV_PYTHON" -c "import sys; exit(0 if sys.version_info >= (3,12) else 1)" 2>/dev/null; then
  : # venv is valid
else
  [ -d "$VENV_DIR" ] && echo "Existing venv has wrong Python version — recreating..." && rm -rf "$VENV_DIR"
  echo "Creating virtual environment..."
  # $PY_LAUNCHER_VER is intentionally unquoted so it expands to nothing when empty
  # shellcheck disable=SC2086
  "$PYTHON_BIN" $PY_LAUNCHER_VER -m venv "$VENV_DIR"
fi

# ---------------------------------------------------------------------------
# Activate
# ---------------------------------------------------------------------------
if [ -f "$VENV_DIR/Scripts/activate" ]; then
  # Windows (Git Bash / MSYS2)
  source "$VENV_DIR/Scripts/activate"
else
  source "$VENV_DIR/bin/activate"
fi

# ---------------------------------------------------------------------------
# Install dependencies if not already installed
# ---------------------------------------------------------------------------
if ! command -v uvicorn &>/dev/null; then
  echo "Installing dependencies..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -e ".[dev]"
fi

# ---------------------------------------------------------------------------
# Check CLI availability
# ---------------------------------------------------------------------------
HOST="${HIVE_API_HOST:-127.0.0.1}"
PORT="${HIVE_API_PORT:-8000}"

echo "Checking CLI availability..."
for cli in claude gemini codex kimi copilot opencode; do
  if command -v "$cli" &>/dev/null; then
    echo "  $cli: $(command -v "$cli")"
  else
    echo "  $cli: not found"
  fi
done
echo ""

echo "Starting Hive on http://${HOST}:${PORT}"
exec python -m uvicorn hive_api.main:app --host "$HOST" --port "$PORT"

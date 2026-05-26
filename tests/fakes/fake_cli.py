import os
import re
import sys
from pathlib import Path

import time

provider = sys.argv[1]
args = sys.argv[2:]


# ---------------------------------------------------------------------------
# Auxiliary modes used by version/update/discovery tests. These short-circuit
# before the chat-event emission below so they never produce JSON events.
# ---------------------------------------------------------------------------
if "--version" in args:
    sys.stdout.write(f"{provider} v1.2.3\n")
    sys.stdout.flush()
    sys.exit(0)

if "--help" in args:
    sys.stdout.write(f"Usage: {provider} [options]\n")
    sys.stdout.flush()
    sys.exit(0)

if args[:1] == ["models"]:
    sys.stdout.write('{"models": []}\n')
    sys.stdout.flush()
    sys.exit(0)

if args[:1] == ["update"]:
    sys.stdout.write("Updated successfully\n")
    sys.stdout.flush()
    sys.exit(0)


def read_flag(flag: str):
    if flag in args:
        index = args.index(flag)
        return args[index + 1]
    return None


def has_flag(flag: str) -> bool:
    return flag in args


def last_non_flag(arguments):
    value = None
    skip_next = False
    flags_with_values = {
        "-p", "--prompt", "-m", "--model", "--resume", "--session-id", "--session",
        "--output-format", "--permission-mode", "-o", "--format", "--agent",
    }
    for arg in arguments:
        if skip_next:
            skip_next = False
            continue
        if arg in flags_with_values:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        value = arg
    return value or ""


def emit(line: str):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# Antigravity passes the prompt via -p; kimi via --prompt; claude and codex use a positional arg.
if provider == "antigravity":
    prompt = read_flag("-p") or last_non_flag(args)
elif provider == "kimi":
    prompt = read_flag("--prompt") or last_non_flag(args)
else:
    prompt = last_non_flag(args)

model = read_flag("-m") or read_flag("--model") or "default"
if provider == "antigravity":
    # Antigravity has no --model flag (model is set via settings.json)
    # and `-p` does not emit a conversation ID. The real CLI prints the
    # assistant response as plain text on stdout, so the fake mirrors
    # that contract exactly.
    emit(f"antigravity:{prompt}")
elif provider == "claude":
    session_id = read_flag("--resume") or read_flag("--session-id") or "claude-session-new"
    emit(f'{{"type":"system","subtype":"init","session_id":"{session_id}","model":"{model}"}}')
    emit(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"claude:'
        + prompt.replace('"', '\\"')
        + '"}]},"session_id":"'
        + session_id
        + '"}'
    )
    emit(f'{{"type":"result","subtype":"success","session_id":"{session_id}","result":"claude:{prompt}"}}')
elif provider == "kimi":
    session_id = read_flag("--session") or "kimi-session-new"
    if "hang-after-fatal" in prompt:
        # Simulate the real-world kimi failure that motivated the
        # adapter's fatal-pattern detection: print the LLM provider
        # connection error, then sleep without ever exiting. The
        # executor must spot the error in the parser and interrupt.
        emit('<system>ERROR: LLM provider error when running agent: Connection error.</system>')
        time.sleep(60)
        sys.exit(0)
    emit('{"role":"assistant","content":[{"type":"text","text":"kimi:' + prompt.replace('"', '\\"') + '"}]}')
elif provider == "codex":
    if len(args) >= 2 and args[0] == "exec" and args[1] == "resume":
        non_flags = [arg for arg in args[2:] if not arg.startswith("-")]
        thread_id = non_flags[0]
        prompt = non_flags[-1]
    else:
        thread_id = "codex-thread-new"
    emit(f'{{"type":"thread.started","thread_id":"{thread_id}"}}')
    emit('{"type":"item.completed","item":{"type":"agent_message","text":"codex:' + prompt.replace('"', '\\"') + '"}}')
    emit('{"type":"turn.completed","usage":{"output_tokens":1}}')
else:
    emit('{"error":"unknown provider"}')
    sys.exit(1)

if "slow" in prompt:
    time.sleep(5)

if "silent-hang" in prompt:
    # No further output -- drives the idle watcher.
    time.sleep(120)

if "hang-forever" in prompt:
    # Periodically emit so the idle watcher does NOT fire, but the
    # CLI never exits -- drives the cli_timeout branch.
    for _ in range(600):
        emit("still working")
        time.sleep(0.1)

_exit_match = re.search(r"exitcode=(\d+)", prompt)
if _exit_match:
    sys.exit(int(_exit_match.group(1)))

if "fail" in prompt:
    sys.exit(3)

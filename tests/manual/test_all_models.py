"""Manual end-to-end test of every musician known to Symphony.

Run against a live preview server (default http://127.0.0.1:8002).
Writes the structured results to ``model_test_report.md`` at the
repository root.

Usage:
    python tests/manual/test_all_models.py [--base-url URL] [--prompt TEXT]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx

DEFAULT_BASE = "http://127.0.0.1:8002"
DEFAULT_PROMPT = "Reply with exactly one short word and nothing else."
DEFAULT_WORKSPACE = "C:/Github/symphony-api"
TERMINAL_STATUSES = {"completed", "failed", "stopped"}


@dataclass
class ModelResult:
    provider: str
    model: str
    status: str
    exit_code: int | None
    duration_s: float
    final_text: str
    error: str | None
    warnings: list[str]
    score_id: str


def submit_chat(client: httpx.Client, provider: str, model: str, prompt: str, workspace: str) -> str:
    response = client.post(
        "/v1/chat",
        json={
            "provider": provider,
            "model": model,
            "workspace_path": workspace,
            "mode": "new",
            "prompt": prompt,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["score_id"]


def poll_score(client: httpx.Client, score_id: str, *, timeout_s: float, poll_interval_s: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/v1/chat/{score_id}", timeout=15.0)
        response.raise_for_status()
        snapshot = response.json()
        if snapshot["status"] in TERMINAL_STATUSES:
            return snapshot
        time.sleep(poll_interval_s)
    return {
        "status": "timeout",
        "exit_code": None,
        "final_text": None,
        "accumulated_text": None,
        "error": f"Score did not reach a terminal state within {timeout_s:.0f}s",
        "warnings": [],
    }


def run_one(client: httpx.Client, provider: str, model: str, prompt: str, workspace: str, timeout_s: float) -> ModelResult:
    start = time.monotonic()
    try:
        score_id = submit_chat(client, provider, model, prompt, workspace)
    except httpx.HTTPError as exc:
        return ModelResult(
            provider=provider,
            model=model,
            status="submit_error",
            exit_code=None,
            duration_s=time.monotonic() - start,
            final_text="",
            error=str(exc),
            warnings=[],
            score_id="",
        )

    snapshot = poll_score(client, score_id, timeout_s=timeout_s)
    duration = time.monotonic() - start
    text = snapshot.get("final_text") or snapshot.get("accumulated_text") or ""
    return ModelResult(
        provider=provider,
        model=model,
        status=snapshot.get("status", "unknown"),
        exit_code=snapshot.get("exit_code"),
        duration_s=round(duration, 1),
        final_text=text.strip(),
        error=snapshot.get("error"),
        warnings=snapshot.get("warnings", []) or [],
        score_id=score_id,
    )


def fetch_musicians(client: httpx.Client) -> list[tuple[str, str]]:
    response = client.get("/v1/musicians", timeout=15.0)
    response.raise_for_status()
    return [(m["provider"], m["model"]) for m in response.json()]


def format_text(text: str, max_len: int = 80) -> str:
    """Single-line repr suitable for a markdown table cell."""
    flat = text.replace("\n", " \\n ").replace("|", "\\|").strip()
    if len(flat) > max_len:
        flat = flat[: max_len - 1] + "…"
    return flat or "_(empty)_"


def write_report(results: list[ModelResult], out_path: Path, prompt: str) -> None:
    pass_count = sum(1 for r in results if r.status == "completed" and r.final_text)
    fail_count = len(results) - pass_count

    lines: list[str] = []
    lines.append("# Symphony — Per-Model Smoke Report")
    lines.append("")
    lines.append(f"- **Prompt:** `{prompt}`")
    lines.append(f"- **Models tested:** {len(results)}")
    lines.append(f"- **Pass (completed + non-empty text):** {pass_count}")
    lines.append(f"- **Fail (any other outcome):** {fail_count}")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| # | Provider | Model | Status | Exit | Duration (s) | Reply | Error |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for idx, result in enumerate(results, start=1):
        verdict = "✅" if (result.status == "completed" and result.final_text) else "❌"
        lines.append(
            "| {n} | {prov} | `{model}` | {verdict} `{status}` | {exit} | {dur} | {text} | {err} |".format(
                n=idx,
                prov=result.provider,
                model=result.model,
                verdict=verdict,
                status=result.status,
                exit=("`" + str(result.exit_code) + "`") if result.exit_code is not None else "—",
                dur=f"{result.duration_s:.1f}",
                text=format_text(result.final_text),
                err=format_text(result.error or "—", max_len=60),
            )
        )
    lines.append("")
    lines.append("## Raw JSON")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps([asdict(r) for r in results], indent=2, default=str))
    lines.append("```")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--per-model-timeout", type=float, default=180.0)
    parser.add_argument("--out", default="model_test_report.md")
    args = parser.parse_args()

    out_path = Path(args.out).resolve()

    with httpx.Client(base_url=args.base_url) as client:
        try:
            musicians = fetch_musicians(client)
        except httpx.HTTPError as exc:
            print(f"Failed to fetch musicians: {exc}", file=sys.stderr)
            return 2

        print(f"Found {len(musicians)} musicians; testing sequentially…")
        results: list[ModelResult] = []
        for idx, (provider, model) in enumerate(musicians, start=1):
            print(f"  [{idx}/{len(musicians)}] {provider}/{model} …", end=" ", flush=True)
            result = run_one(
                client,
                provider=provider,
                model=model,
                prompt=args.prompt,
                workspace=args.workspace,
                timeout_s=args.per_model_timeout,
            )
            results.append(result)
            tag = "ok" if (result.status == "completed" and result.final_text) else "FAIL"
            print(f"{tag} ({result.duration_s:.1f}s) — {format_text(result.final_text, max_len=40)}")

    write_report(results, out_path, args.prompt)
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

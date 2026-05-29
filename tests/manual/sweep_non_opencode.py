"""Manual end-to-end sweep of every non-OpenCode musician.

Exercises NEW + RESUME for Claude, Codex, Kimi (providers that advertise
``supports_resume=True``) and NEW only for Antigravity (which returns
``supports_resume=False`` from ``/v1/providers``). Reads the configured
model list from the running Symphony's ``/v1/providers`` endpoint so
discovery rewrites of ``config.toml`` are picked up automatically.

Run with the API booted on the default port:

    python tests/manual/sweep_non_opencode.py --base-url http://127.0.0.1:8002

Writes the markdown report to ``manual_provider_test_report.md`` at
the repository root (gitignored via ``opencode_resume_*.md``? no,
this artefact has a different prefix, so the user can choose whether
to commit it).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

DEFAULT_BASE = "http://127.0.0.1:8002"
DEFAULT_WORKSPACE = "C:/Github/symphony-api"
DEFAULT_NEW_PROMPT = (
    "Remember the phrase Symphony-Pulse-Quartet. "
    "Reply with one short sentence acknowledging it."
)
DEFAULT_RESUME_PROMPT = (
    "What phrase did I ask you to remember? Reply with just the phrase."
)
DEFAULT_MEMORY_TOKEN = "Symphony-Pulse-Quartet"
TERMINAL_STATUSES = {"completed", "failed", "stopped"}
TARGET_PROVIDERS = ("claude", "codex", "antigravity", "kimi")


@dataclass
class CallResult:
    status: str = "skipped"
    exit_code: int | None = None
    duration_s: float = 0.0
    final_text: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    score_id: str = ""
    provider_session_ref: str | None = None


@dataclass
class ModelResult:
    provider: str
    model: str
    supports_resume: bool
    new: CallResult
    resume: CallResult
    keyword_pass: bool
    notes: list[str] = field(default_factory=list)


def fetch_targets(client: httpx.Client) -> list[tuple[str, str, bool]]:
    response = client.get("/v1/providers", timeout=15.0)
    response.raise_for_status()
    out: list[tuple[str, str, bool]] = []
    for entry in response.json():
        if entry.get("provider") not in TARGET_PROVIDERS:
            continue
        if not entry.get("available", False):
            continue
        for model in entry.get("models", []):
            out.append((entry["provider"], model, bool(entry.get("supports_resume", True))))
    return out


def submit(client: httpx.Client, *, provider: str, model: str, prompt: str, workspace: str,
           mode: str, ref: str | None) -> str:
    body: dict[str, object] = {
        "provider": provider,
        "model": model,
        "workspace_path": workspace,
        "mode": mode,
        "prompt": prompt,
    }
    if mode == "resume":
        body["provider_session_ref"] = ref
    response = client.post("/v1/chat", json=body, timeout=30.0)
    response.raise_for_status()
    return response.json()["score_id"]


def poll(client: httpx.Client, score_id: str, *, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/v1/chat/{score_id}", timeout=15.0)
        response.raise_for_status()
        snap = response.json()
        if snap["status"] in TERMINAL_STATUSES:
            return snap
        time.sleep(2.0)
    return {"status": "timeout", "exit_code": None, "final_text": None,
            "accumulated_text": None,
            "error": f"Score did not reach a terminal state within {timeout_s:.0f}s",
            "warnings": [], "provider_session_ref": None}


def execute(client: httpx.Client, *, provider: str, model: str, prompt: str, workspace: str,
            mode: str, timeout_s: float, ref: str | None = None) -> CallResult:
    start = time.monotonic()
    try:
        sid = submit(client, provider=provider, model=model, prompt=prompt,
                     workspace=workspace, mode=mode, ref=ref)
    except httpx.HTTPError as exc:
        return CallResult(status="submit_error", duration_s=round(time.monotonic() - start, 1),
                          error=str(exc))
    snap = poll(client, sid, timeout_s=timeout_s)
    text = snap.get("final_text") or snap.get("accumulated_text") or ""
    return CallResult(
        status=snap.get("status", "unknown"),
        exit_code=snap.get("exit_code"),
        duration_s=round(time.monotonic() - start, 1),
        final_text=(text or "").strip(),
        error=snap.get("error"),
        warnings=list(snap.get("warnings") or []),
        score_id=sid,
        provider_session_ref=snap.get("provider_session_ref"),
    )


def run_one(client: httpx.Client, provider: str, model: str, supports_resume: bool,
            *, workspace: str, new_prompt: str, resume_prompt: str, memory_token: str,
            timeout_s: float, inter_call_sleep: float) -> ModelResult:
    notes: list[str] = []
    new = execute(client, provider=provider, model=model, prompt=new_prompt,
                  workspace=workspace, mode="new", timeout_s=timeout_s)
    if new.status != "completed":
        notes.append(f"NEW reached terminal status {new.status!r}, not completed.")
    keyword_pass = False
    if supports_resume:
        if new.status == "completed" and new.provider_session_ref:
            if inter_call_sleep > 0:
                time.sleep(inter_call_sleep)
            resume = execute(client, provider=provider, model=model, prompt=resume_prompt,
                             workspace=workspace, mode="resume", timeout_s=timeout_s,
                             ref=new.provider_session_ref)
            if resume.status != "completed":
                notes.append(f"RESUME reached terminal status {resume.status!r}, not completed.")
            keyword_pass = bool(resume.final_text) and memory_token.lower() in resume.final_text.lower()
            if not keyword_pass and resume.status == "completed":
                notes.append(f"Resume reply did not contain {memory_token!r}.")
        else:
            resume = CallResult(status="skipped",
                                error="NEW did not yield completed + session_ref")
            notes.append("RESUME skipped: NEW incomplete or session ref missing.")
    else:
        resume = CallResult(status="skipped", error="provider supports_resume=False")
        notes.append("Provider advertises supports_resume=False (NEW-only).")
    return ModelResult(provider=provider, model=model, supports_resume=supports_resume,
                       new=new, resume=resume, keyword_pass=keyword_pass, notes=notes)


def cell(text: str, limit: int = 60) -> str:
    flat = (text or "").replace("\n", " \\n ").replace("|", "\\|").strip()
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return flat or "_(empty)_"


def tag(status: str) -> str:
    return "✅" if status == "completed" else ("⏸" if status == "skipped" else "❌")


def write_report(results: list[ModelResult], out: Path, *, new_prompt: str,
                 resume_prompt: str, memory_token: str) -> None:
    lines: list[str] = [
        "# Symphony — Manual non-OpenCode provider sweep",
        "",
        f"- **NEW prompt:** `{new_prompt}`",
        f"- **RESUME prompt:** `{resume_prompt}`",
        f"- **Memorised token:** `{memory_token}`",
        f"- **Models tested:** {len(results)}",
        "",
    ]
    grouped: dict[str, list[ModelResult]] = {}
    for r in results:
        grouped.setdefault(r.provider, []).append(r)
    for prov in sorted(grouped.keys()):
        rs = grouped[prov]
        new_ok = sum(1 for r in rs if r.new.status == "completed")
        res_ok = sum(1 for r in rs if r.resume.status == "completed")
        phrase = sum(1 for r in rs if r.keyword_pass)
        lines += [
            f"## {prov}",
            "",
            f"- Models: {len(rs)} — NEW pass {new_ok}/{len(rs)}, RESUME pass {res_ok}/{len(rs)}, phrase pass {phrase}/{len(rs)}.",
            "",
            "| # | Model | NEW | Exit | Duration (s) | RESUME | Exit | Duration (s) | Phrase | Reply |",
            "|---|-------|-----|------|--------------|--------|------|--------------|--------|-------|",
        ]
        for idx, r in enumerate(rs, start=1):
            lines.append(
                f"| {idx} | `{r.model}` | "
                f"{tag(r.new.status)} `{r.new.status}` | "
                f"{('`'+str(r.new.exit_code)+'`') if r.new.exit_code is not None else '—'} | "
                f"{r.new.duration_s} | "
                f"{tag(r.resume.status)} `{r.resume.status}` | "
                f"{('`'+str(r.resume.exit_code)+'`') if r.resume.exit_code is not None else '—'} | "
                f"{r.resume.duration_s} | "
                f"{'✅' if r.keyword_pass else ('⏸' if not r.supports_resume else '❌')} | "
                f"{cell(r.resume.final_text if r.supports_resume else r.new.final_text)} |"
            )
        lines.append("")
        any_notes = [(r.model, '; '.join(r.notes)) for r in rs if r.notes]
        if any_notes:
            lines.append("Notes:")
            for model, n in any_notes:
                lines.append(f"- `{model}`: {n}")
            lines.append("")
    lines += ["## Raw JSON", "", "```json",
              json.dumps([asdict(r) for r in results], indent=2, default=str),
              "```", ""]
    out.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    p.add_argument("--new-prompt", default=DEFAULT_NEW_PROMPT)
    p.add_argument("--resume-prompt", default=DEFAULT_RESUME_PROMPT)
    p.add_argument("--memory-token", default=DEFAULT_MEMORY_TOKEN)
    p.add_argument("--per-call-timeout", type=float, default=180.0)
    p.add_argument("--inter-call-sleep", type=float, default=5.0)
    p.add_argument("--inter-model-sleep", type=float, default=3.0)
    p.add_argument("--out", default="manual_provider_test_report.md")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_path = Path(args.out).resolve()
    with httpx.Client(base_url=args.base_url) as client:
        targets = fetch_targets(client)
        print(f"Sweeping {len(targets)} musician(s) across {sorted({p for p, _, _ in targets})}")
        results: list[ModelResult] = []
        for idx, (provider, model, supports_resume) in enumerate(targets, start=1):
            print(f"  [{idx}/{len(targets)}] {provider}/{model} resume={supports_resume} ...",
                  flush=True)
            r = run_one(client, provider, model, supports_resume,
                        workspace=args.workspace, new_prompt=args.new_prompt,
                        resume_prompt=args.resume_prompt, memory_token=args.memory_token,
                        timeout_s=args.per_call_timeout,
                        inter_call_sleep=args.inter_call_sleep)
            results.append(r)
            print(f"      NEW={r.new.status} ({r.new.duration_s}s) "
                  f"RESUME={r.resume.status} ({r.resume.duration_s}s) "
                  f"phrase={'yes' if r.keyword_pass else 'no'}")
            if idx < len(targets) and args.inter_model_sleep > 0:
                time.sleep(args.inter_model_sleep)
    write_report(results, out_path, new_prompt=args.new_prompt,
                 resume_prompt=args.resume_prompt, memory_token=args.memory_token)
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

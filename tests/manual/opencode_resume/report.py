"""Markdown report writer for the OpenCode resume harness."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import ModelResult


def _cell(text: str, max_len: int = 60) -> str:
    flat = (text or "").replace("\n", " \\n ").replace("|", "\\|").strip()
    if len(flat) > max_len:
        flat = flat[: max_len - 1] + "…"
    return flat or "_(empty)_"


def _status_tag(status: str) -> str:
    if status == "completed":
        return "✅"
    if status == "skipped":
        return "⏸"
    return "❌"


def _exit_cell(exit_code: int | None) -> str:
    return f"`{exit_code}`" if exit_code is not None else "—"


def _summary_lines(
    results: list[ModelResult],
    *,
    new_prompt: str,
    resume_prompt: str,
    memory_token: str,
    inter_call_sleep: float,
    inter_model_sleep: float,
    rate_limited_at: str | None,
) -> list[str]:
    new_pass = sum(1 for r in results if r.new.status == "completed")
    resume_pass = sum(1 for r in results if r.resume.status == "completed")
    keyword_pass = sum(1 for r in results if r.keyword_pass)
    out = [
        "# Symphony — OpenCode Resume Smoke Report",
        "",
        f"- **NEW prompt:** `{new_prompt}`",
        f"- **RESUME prompt:** `{resume_prompt}`",
        f"- **Memorised token:** `{memory_token}`",
        f"- **Inter-call sleep:** {inter_call_sleep:.0f}s",
        f"- **Inter-model sleep:** {inter_model_sleep:.0f}s",
        f"- **Models tested:** {len(results)}",
        f"- **NEW pass:** {new_pass}",
        f"- **RESUME pass:** {resume_pass}",
        f"- **Keyword pass:** {keyword_pass}",
    ]
    if rate_limited_at:
        out.append(f"- **Rate-limit symptom on:** `{rate_limited_at}` (run stopped early)")
    return out


def write_report(
    results: list[ModelResult],
    out_path: Path,
    *,
    new_prompt: str,
    resume_prompt: str,
    memory_token: str,
    inter_call_sleep: float,
    inter_model_sleep: float,
    rate_limited_at: str | None,
) -> None:
    lines = _summary_lines(
        results,
        new_prompt=new_prompt,
        resume_prompt=resume_prompt,
        memory_token=memory_token,
        inter_call_sleep=inter_call_sleep,
        inter_model_sleep=inter_model_sleep,
        rate_limited_at=rate_limited_at,
    )
    lines += [
        "",
        "## Results",
        "",
        "| # | Model | NEW | Exit | RESUME | Exit | Phrase | Resume reply |",
        "|---|-------|-----|------|--------|------|--------|--------------|",
    ]
    for idx, r in enumerate(results, start=1):
        lines.append(
            f"| {idx} | `{r.model}` "
            f"| {_status_tag(r.new.status)} `{r.new.status}` | {_exit_cell(r.new.exit_code)} "
            f"| {_status_tag(r.resume.status)} `{r.resume.status}` | {_exit_cell(r.resume.exit_code)} "
            f"| {'✅' if r.keyword_pass else '❌'} "
            f"| {_cell(r.resume.final_text)} |"
        )

    lines += ["", "## Notes per model", ""]
    for r in results:
        if r.notes:
            lines.append(f"- `{r.model}`: " + "; ".join(r.notes))
    lines += [
        "",
        "## Raw JSON",
        "",
        "```json",
        json.dumps([asdict(r) for r in results], indent=2, default=str),
        "```",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")

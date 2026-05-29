"""CLI entry point: parse args, walk the OpenCode model list, write report."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

from .client import run_model
from .models import (
    DEFAULT_BASE,
    DEFAULT_MEMORY_TOKEN,
    DEFAULT_NEW_PROMPT,
    DEFAULT_RESUME_PROMPT,
    DEFAULT_WORKSPACE,
    ModelResult,
    load_opencode_models,
    looks_rate_limited,
)
from .report import write_report


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--new-prompt", default=DEFAULT_NEW_PROMPT)
    parser.add_argument("--resume-prompt", default=DEFAULT_RESUME_PROMPT)
    parser.add_argument("--memory-token", default=DEFAULT_MEMORY_TOKEN)
    parser.add_argument("--per-call-timeout", type=float, default=180.0)
    parser.add_argument("--inter-call-sleep", type=float, default=30.0)
    parser.add_argument("--inter-model-sleep", type=float, default=60.0)
    parser.add_argument("--out", default="opencode_resume_report.md")
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated subset of OpenCode model slugs; defaults to every model in config.toml.",
    )
    return parser.parse_args(argv)


def _resolve_models(args: argparse.Namespace) -> tuple[list[str], int | None]:
    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return [], 2
    all_models = load_opencode_models(config_path)
    if args.models:
        wanted = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = sorted(set(wanted) - set(all_models))
        if unknown:
            print(f"Unknown OpenCode models: {unknown}", file=sys.stderr)
            return [], 2
        return [m for m in all_models if m in set(wanted)], None
    return all_models, None


def _print_row(r: ModelResult) -> None:
    tag = (
        "ok"
        if r.new.status == "completed"
        and r.resume.status == "completed"
        and r.keyword_pass
        else "FAIL"
    )
    print(
        f"      NEW={r.new.status} ({r.new.duration_s}s) "
        f"RESUME={r.resume.status} ({r.resume.duration_s}s) "
        f"phrase={'yes' if r.keyword_pass else 'no'} -> {tag}",
    )


def _walk_models(args: argparse.Namespace, models: list[str]) -> tuple[list[ModelResult], str | None]:
    results: list[ModelResult] = []
    rate_limited_at: str | None = None
    with httpx.Client(base_url=args.base_url) as client:
        for idx, model in enumerate(models, start=1):
            print(f"  [{idx}/{len(models)}] {model} ...", flush=True)
            r = run_model(
                client,
                model,
                workspace=args.workspace,
                new_prompt=args.new_prompt,
                resume_prompt=args.resume_prompt,
                memory_token=args.memory_token,
                timeout_s=args.per_call_timeout,
                inter_call_sleep=args.inter_call_sleep,
            )
            results.append(r)
            _print_row(r)
            if looks_rate_limited(r.new) or looks_rate_limited(r.resume):
                rate_limited_at = model
                print(
                    f"      Rate-limit symptom detected on {model}; stopping early.",
                    file=sys.stderr,
                )
                break
            if idx < len(models) and args.inter_model_sleep > 0:
                time.sleep(args.inter_model_sleep)
    return results, rate_limited_at


def _exit_code(results: list[ModelResult], rate_limited_at: str | None) -> int:
    if rate_limited_at is not None:
        return 3
    if any(
        r.new.status != "completed" or r.resume.status != "completed" or not r.keyword_pass
        for r in results
    ):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    models, err = _resolve_models(args)
    if err is not None:
        return err
    out_path = Path(args.out).resolve()
    print(f"Testing {len(models)} OpenCode model(s) against {args.base_url} sequentially")

    results, rate_limited_at = _walk_models(args, models)

    write_report(
        results,
        out_path,
        new_prompt=args.new_prompt,
        resume_prompt=args.resume_prompt,
        memory_token=args.memory_token,
        inter_call_sleep=args.inter_call_sleep,
        inter_model_sleep=args.inter_model_sleep,
        rate_limited_at=rate_limited_at,
    )
    print(f"\nReport written to {out_path}")
    return _exit_code(results, rate_limited_at)

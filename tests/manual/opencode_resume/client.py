"""HTTP submit / poll / run-model helpers for the OpenCode harness."""

from __future__ import annotations

import time

import httpx

from .models import (
    CallResult,
    ModelResult,
    PROVIDER,
    TERMINAL_STATUSES,
)


def submit_chat(
    client: httpx.Client,
    *,
    model: str,
    prompt: str,
    workspace: str,
    mode: str,
    provider_session_ref: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "provider": PROVIDER,
        "model": model,
        "workspace_path": workspace,
        "mode": mode,
        "prompt": prompt,
    }
    if mode == "resume":
        if not provider_session_ref:
            raise ValueError("resume mode requires provider_session_ref")
        payload["provider_session_ref"] = provider_session_ref
    response = client.post("/v1/chat", json=payload, timeout=30.0)
    response.raise_for_status()
    return response.json()["score_id"]


def poll_score(
    client: httpx.Client,
    score_id: str,
    *,
    timeout_s: float,
    poll_interval_s: float = 2.0,
) -> dict:
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
        "provider_session_ref": None,
    }


def execute_call(
    client: httpx.Client,
    *,
    model: str,
    prompt: str,
    workspace: str,
    mode: str,
    timeout_s: float,
    provider_session_ref: str | None = None,
) -> CallResult:
    start = time.monotonic()
    try:
        score_id = submit_chat(
            client,
            model=model,
            prompt=prompt,
            workspace=workspace,
            mode=mode,
            provider_session_ref=provider_session_ref,
        )
    except httpx.HTTPError as exc:
        return CallResult(
            status="submit_error",
            duration_s=round(time.monotonic() - start, 1),
            error=str(exc),
        )
    snapshot = poll_score(client, score_id, timeout_s=timeout_s)
    text = snapshot.get("final_text") or snapshot.get("accumulated_text") or ""
    return CallResult(
        status=snapshot.get("status", "unknown"),
        exit_code=snapshot.get("exit_code"),
        duration_s=round(time.monotonic() - start, 1),
        final_text=(text or "").strip(),
        error=snapshot.get("error"),
        warnings=list(snapshot.get("warnings") or []),
        score_id=score_id,
        provider_session_ref=snapshot.get("provider_session_ref"),
    )


def run_model(
    client: httpx.Client,
    model: str,
    *,
    workspace: str,
    new_prompt: str,
    resume_prompt: str,
    memory_token: str,
    timeout_s: float,
    inter_call_sleep: float,
) -> ModelResult:
    notes: list[str] = []
    new = execute_call(
        client,
        model=model,
        prompt=new_prompt,
        workspace=workspace,
        mode="new",
        timeout_s=timeout_s,
    )
    if new.status != "completed":
        notes.append(f"NEW reached terminal status {new.status!r}, not completed.")
    if new.status == "completed" and not new.provider_session_ref:
        notes.append("NEW completed but the executor did not capture a provider_session_ref.")

    if new.provider_session_ref and new.status == "completed":
        if inter_call_sleep > 0:
            time.sleep(inter_call_sleep)
        resume = execute_call(
            client,
            model=model,
            prompt=resume_prompt,
            workspace=workspace,
            mode="resume",
            timeout_s=timeout_s,
            provider_session_ref=new.provider_session_ref,
        )
        if resume.status != "completed":
            notes.append(f"RESUME reached terminal status {resume.status!r}, not completed.")
    else:
        resume = CallResult(status="skipped", error="No provider_session_ref from NEW")
        notes.append("RESUME skipped because NEW did not yield a usable session reference.")

    keyword_pass = bool(resume.final_text) and memory_token.lower() in resume.final_text.lower()
    if not keyword_pass and resume.status == "completed":
        notes.append(f"Resume reply did not contain the memorised phrase {memory_token!r}.")

    return ModelResult(
        provider=PROVIDER,
        model=model,
        new=new,
        resume=resume,
        keyword_pass=keyword_pass,
        notes=notes,
    )

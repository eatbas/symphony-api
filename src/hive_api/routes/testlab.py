from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, Request

from ..models import (
    ChatMode,
    ChatRequest,
    ErrorDetail,
    ProviderName,
    TestGenerateRequest,
    TestGenerateResponse,
    TestQAPair,
    TestVerifyRequest,
    TestVerifyResponse,
    TestVerifyResultItem,
)
from ._deps import get_ready_colony

router = APIRouter(tags=["Test Lab"])

_CHEAPEST_MODELS = [
    (ProviderName.CLAUDE, "haiku"),
    (ProviderName.CODEX, "gpt-5.4-mini"),
]


@router.post(
    "/v1/test/verify",
    summary="Verify test results with keyword matching",
    response_model=TestVerifyResponse,
)
async def test_verify(request: TestVerifyRequest) -> TestVerifyResponse:
    results: list[TestVerifyResultItem] = []
    for item in request.items:
        new_status = "OK" if item.new_exit_code == 0 else "FAIL"
        resume_status = "OK" if item.resume_exit_code == 0 else "FAIL"
        keyword_results = {kw.strip(): kw.strip().lower() in item.resume_text.lower() for kw in item.keywords if kw.strip()}
        all_keywords_found = all(keyword_results.values()) if keyword_results else True
        grade = "PASS" if new_status == "OK" and resume_status == "OK" and all_keywords_found else "FAIL"
        results.append(
            TestVerifyResultItem(
                provider=item.provider,
                model=item.model,
                new_status=new_status,
                resume_status=resume_status,
                keyword_results=keyword_results,
                grade=grade,
            )
        )
    return TestVerifyResponse(results=results)


@router.post(
    "/v1/test/generate-scenario",
    summary="AI-generate a test scenario",
    description=(
        "Uses a drone to AI-generate test scenario content (story + QA pairs). "
        "Optionally specify `provider` and `model` to target a specific drone; "
        "when omitted the cheapest available model is chosen automatically "
        "(Claude Haiku → Codex GPT-5.4-mini)."
    ),
    response_model=TestGenerateResponse,
    responses={503: {"description": "No cheap model drone is available.", "model": ErrorDetail}},
)
async def test_generate_scenario(request: Request, body: TestGenerateRequest) -> TestGenerateResponse:
    colony = await get_ready_colony(request)

    # Honour explicit provider/model from the UI dropdown; fall back to
    # the cheapest available model when the user selects "auto".
    drone = None
    if body.provider and body.model:
        drone = await colony.acquire_drone(body.provider, body.model)
    if drone is None:
        for provider, model in _CHEAPEST_MODELS:
            candidate = await colony.acquire_drone(provider, model)
            if candidate is not None and candidate.ready:
                drone = candidate
                break
    if drone is None:
        raise HTTPException(
            status_code=503,
            detail="No cheap model drone is currently available. Ensure haiku or gpt-5.4-mini drones are running.",
        )

    prompt_text = (
        "Generate a test scenario for testing an AI assistant's memory. "
        "Return ONLY a JSON object (no markdown fencing) with keys: story and qa_pairs.\n"
        "story: 2-3 sentence intro including specific facts (name, job, responsibilities, personal detail).\n"
        "qa_pairs: an array of 3 objects with question and expected fields.\n"
        "expected must be short comma-separated keywords required in answers.\n"
        "Return ONLY the JSON object."
    )

    chat_req = ChatRequest(
        provider=drone.provider,
        model=drone.model,
        workspace_path=body.workspace_path,
        mode=ChatMode.NEW,
        prompt=prompt_text,
        stream=False,
    )
    handle = await drone.submit(chat_req)
    try:
        result = await handle.result_future
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

    return _parse_generate_response(result.final_text.strip(), body.field)


def _parse_generate_response(raw: str, field: str) -> TestGenerateResponse:
    """Best-effort parse of AI-generated JSON from model response."""

    def _build_response(data: dict) -> TestGenerateResponse:
        qa_pairs: list[TestQAPair] = []
        raw_pairs = data.get("qa_pairs", [])
        if isinstance(raw_pairs, list):
            for pair in raw_pairs:
                if isinstance(pair, dict) and "question" in pair and "expected" in pair:
                    qa_pairs.append(TestQAPair(question=str(pair["question"]), expected=str(pair["expected"])))
        return TestGenerateResponse(
            story=data.get("story"),
            questions=data.get("questions"),
            expected=data.get("expected"),
            qa_pairs=qa_pairs,
        )

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return _build_response(data)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            if isinstance(data, dict):
                return _build_response(data)
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            if isinstance(data, dict):
                return _build_response(data)
        except json.JSONDecodeError:
            pass

    return TestGenerateResponse(story=raw)

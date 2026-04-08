# Symphony API — Combined Refactoring & Simplification Report

**Date:** 2026-04-08
**Scope:** Full codebase audit over `src`, `tests`, `scripts`, and `frontend/web/src` — read-only, no production code was changed.
**Total:** 9,232 lines across reviewed Python/JS/TS source files, including 66 Python files.

**Review basis:**
- Repository structure, backend, frontend, and test suite were inspected directly.
- FastAPI guidance cross-checked with Context7 (closest supported version `0.128.0`; repo declares `>=0.135,<1.0` without a lockfile, so alignment is approximate).
- Pydantic guidance cross-checked with Context7 using `2.12` docs, matching the repo's declared range.
- Latest stable dependency versions verified against PyPI and npm registries on 2026-04-08.

---

## Executive Summary

The codebase is not suffering from one systemic design error. It is suffering from **responsibility accumulation** and **duplicated sources of truth**.

The architecture is fundamentally sound in several areas: clean provider abstraction, event-driven score lifecycle, comprehensive test coverage, and well-scoped Pydantic models. The problems are incremental — classes that grew too many responsibilities, constants defined in four places, and config mutation done via regex.

The highest-value refactors are:

1. Split orchestration/runtime lifecycle responsibilities out of `Orchestra` and `Musician`.
2. Eliminate duplicated code patterns (terminal statuses, idle predicates, future guards, subprocess helpers).
3. Replace regex-based config rewriting with parsed config persistence.
4. Create a single source of truth for provider metadata and model catalogues.
5. Break oversized files (Python and JS) to comply with the project's own 300-line rule.
6. Tighten dependency management so "latest stable" is reproducible.

---

## 1. Files Exceeding the 300-Line Limit

The project CLAUDE.md mandates **maximum 300 lines per file, no exceptions**. Eight files violate this:

| File | Lines | Overage | Category |
|------|------:|--------:|----------|
| `src/symphony/orchestra/orchestra.py` | 364 | +64 | Core |
| `scripts/sync_models.py` | 355 | +55 | Tooling |
| `src/symphony/orchestra/musician.py` | 340 | +40 | Core |
| `tests/test_api.py` | 337 | +37 | Tests |
| `src/symphony/ui/static/request.js` | 316 | +16 | UI |
| `tests/test_musicians.py` | 311 | +11 | Tests |
| `tests/updater/test_auto_update.py` | 309 | +9 | Tests |
| `src/symphony/ui/static/testlab.js` | 307 | +7 | UI |

---

## 2. Structural Refactor Targets

### 2.1 `Orchestra` Is Doing Too Many Jobs — P1

**File:** `src/symphony/orchestra/orchestra.py` (364 lines)

**Evidence — five distinct responsibility clusters:**
- Bootstrapping and musician creation: lines 41-87
- Pool scaling and acquisition: lines 107-155
- Score registry, persistence, and eviction: lines 161-230
- Provider activation, restart, and capability reporting: lines 252-338
- Health and shell inspection: lines 340-364

**Why this matters:**
- Musician construction logic is repeated in three places (`start`, `acquire_musician`, `activate_provider`), making provider lifecycle behaviour harder to reason about and easier to drift.
- The score registry and provider queries are orthogonal to pool management but share the same class.

**Recommended split:**
- Extract `ScoreRegistry` — owns `register_score`, `get_score`, `get_score_snapshot`, `stop_score`, `restore_scores`, `_evict_old_scores`, `_find_musician_for_score` (~80 lines).
- Extract `MusicianFactory` — owns musician construction, removing the three-way repetition.
- Extract provider query methods (`capabilities`, `model_details`, `musician_info`, `musicians_for_provider`, `health_details`, `get_bash_version`) into `orchestra/queries.py` (~70 lines).
- Extract provider lifecycle (`restart_provider`, `activate_provider`) into `orchestra/lifecycle.py` (~50 lines).
- Keep `Orchestra` as a thinner coordinator over pool management (~160 lines).

### 2.2 `Musician` Contains an Implicit State Machine — P1

**File:** `src/symphony/orchestra/musician.py` (340 lines)

**Evidence:**
- Queue worker and lifecycle loop: lines 102-164
- Request execution (130 lines, handles ~8 concerns): lines 165-295
- Timeout, idle, cancellation, shell restart: lines 206-240, 297-341

**Why this matters:**
- `_execute_request` interleaves session validation, command building, event publishing, watcher lifecycle, script execution with timeout handling, shell restart, output parsing, response construction, error publishing, and session model tracking.
- Failure paths are difficult to inspect and behaviour changes are risky, especially around cancellation and recovery.

**Recommended split:**
- Extract `_execute_request` + `_idle_watcher` + `_cancel_watcher` into `orchestra/execution.py` (~150 lines).
- Decompose `_execute_request` into phases: `_validate_session`, `_run_with_watchers`, `_build_response` — each independently testable.
- Introduce an execution context object so state (`parse_state`, `idle_event`, `handle`, timeout policy) is passed explicitly.
- Keep `Musician` with `start`/`stop`/`submit`/`_run`/`info` (~190 lines).

### 2.3 Config Mutation via Regex Is Brittle — P1

**File:** `src/symphony/discovery/discoverer.py`

**Evidence:**
- Regex parsing and replacement of TOML arrays: lines 28-61
- Full startup config rewrite: lines 69-127
- Single-provider rewrite: lines 130-176

**Why this matters:**
- Editing TOML with regex is fragile when comments, layout, ordering, or additional keys change.
- This is a maintainability risk, not a style concern — it couples discovery logic to config formatting.

**Recommended fix:**
- Parse config into a typed structure, modify the provider model list, then serialise deliberately.
- If preserving formatting/comments matters, store discovered model state in a separate generated file instead of rewriting the user-edited config.

### 2.4 Provider Discovery Should Be Modularised — P2

**File:** `src/symphony/discovery/providers.py` (285 lines)

**Evidence:**
- Contains all six provider-specific discovery strategies plus shared helpers in one file.
- Mixes npm bundle scraping, local cache reads, config parsing, subprocess calls, and regex heuristics.

**Recommended split:**
- One file per provider: `discovery/providers/claude.py`, `gemini.py`, `codex.py`, etc.
- Keep shared utilities and the `DISCOVERERS` registry in the package root.
- Standardise a small discovery protocol: `discover() -> list[str] | None`.

### 2.5 Updater Is Responsibility-Dense — P2

**File:** `src/symphony/updater/updater.py` (283 lines)

**Evidence:** Version check orchestration, update policy, update execution, rediscovery, scheduling, and result caching all live in one class.

**Recommended split:**
- Version inspector, update executor, rediscovery coordinator, periodic scheduler as separate concerns.
- Keep `CLIUpdater` as a facade if the current API surface needs to be preserved.

### 2.6 `service.py` Couples App Assembly with Operational Concerns — P3

**File:** `src/symphony/service.py` (172 lines)

**Evidence:** Config loading, startup discovery, orchestra construction, updater construction, lifespan boot, CORS, static mounting, router wiring, and `/health` are assembled in one function.

**Recommended split:**
- Config/bootstrap, lifespan wiring, middleware/router registration, and health route as separate modules.
- Expose typed application state accessors instead of reaching into `app.state` ad hoc.

---

## 3. Duplicated Code Patterns

### 3.1 Terminal Status Set — 4 Definitions

`{ScoreStatus.COMPLETED, ScoreStatus.FAILED, ScoreStatus.STOPPED}` constructed independently in:

| Location | Context |
|----------|---------|
| `orchestra/orchestra.py:186` | `stop_score` |
| `orchestra/orchestra.py:241` | `_evict_old_scores` |
| `routes/chat.py:90` | WebSocket handler |
| `score_store.py:54` | Pruning logic |

**Fix:** Define `TERMINAL_STATUSES` once in `models/enums.py`.

### 3.2 Idle Musician Predicate — 3 Identical Checks

`musician.ready and not musician.busy and musician.queue.qsize() == 0` at `orchestra.py` lines 124, 342, 348.

**Fix:** Add `Musician.is_idle` property:
```python
@property
def is_idle(self) -> bool:
    return self.ready and not self.busy and self.queue.qsize() == 0
```

### 3.3 `result_future` Guard — 6 Repetitions

`if handle.result_future and not handle.result_future.done()` followed by `set_result`/`set_exception` at `musician.py` lines 118, 133, 137, 158 and `orchestra.py` lines 194, 209.

**Fix:** Add helpers on `ScoreHandle`:
```python
def resolve(self, result: ChatResponse) -> None:
    if self.result_future and not self.result_future.done():
        self.result_future.set_result(result)

def reject(self, exc: BaseException) -> None:
    if self.result_future and not self.result_future.done():
        self.result_future.set_exception(exc)
```

### 3.4 `set_bash_path` Module Global — 2 Independent Copies

Two modules maintain their own `_bash_path` global + `set_bash_path()`:
- `providers/base.py:22-28`
- `updater/version_checker.py:23-28`

**Fix:** Centralise into `shells.bash_path` or pass the path as a function argument.

### 3.5 Windows `CREATE_NO_WINDOW` Guard — 5 Direct Sites

`if os.name == "nt": kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW` appears in five direct sites across `shells.py`, `providers/base.py`, `updater/version_checker.py`, and `discovery/providers.py`.

**Fix:** A shared `_subprocess_kwargs()` helper already exists in `discovery/providers.py:47` but the other modules don't use it. Promote it to a shared utility.

### 3.6 TOML Model Parsing — Duplicated

`discovery/discoverer.py:_parse_models_from_toml` and `scripts/sync_models.py:parse_config_models` parse the same `config.toml` structure with near-identical regex.

**Fix:** Extract a shared parser, or have `sync_models.py` import from `discovery.discoverer`.

### 3.7 Kimi Command Builder — Near-Identical Methods

`providers/kimi.py` lines 19-33 (`build_new_command`) and 35-48 (`build_resume_command`) differ only in `session_ref` source.

**Fix:** Extract `_build_argv(self, executable, prompt, model, session_ref, provider_options)`.

---

## 4. Complexity Hotspots

### 4.1 `Musician._execute_request` — 130 Lines, 8 Concerns

Handles session validation, command building, event publishing, watcher lifecycle, script execution with timeout/shell restart, output parsing, response construction, error publishing, and session tracking.

**Fix:** Decompose into `_validate_session`, `_run_with_watchers`, `_build_response`.

### 4.2 `CLIUpdater._check_single_provider` — Mixed Concerns

Version fetching, update decision logic, post-update side effects (restart, activate, rediscover), and status building in one method.

**Fix:** Separate `_decide_action` (returns intent) from `_execute_action` (carries it out).

### 4.3 Gemini Resume Shell Script — Fragile Inline Shell

`GeminiAdapter.make_shell_script` (lines 28-56) generates a shell script with inline `grep`/`sed`/`cut` for UUID-to-index resolution. This is the most fragile provider code in the project.

**Fix:** Move UUID-to-index resolution to a Python-side pre-step via `run_quick_command`.

### 4.4 Score Store Pruning — Hot-Path Directory Scan

`score_store.py` triggers `_prune_terminal_scores_locked()` on every save, which reads and validates every stored JSON snapshot.

**Fix:** Prune periodically or track terminal snapshot ordering incrementally, rather than scanning on every write.

---

## 5. Architectural Observations

### 5.1 Untyped Score Events

Events flowing through `ScoreHandle.publish()` are `dict[str, Any]`, dispatched on `event.get("type")` strings. This is the most likely source of silent bugs.

**Fix:** Replace with a `ScoreEvent` discriminated union type so the type checker catches missing/wrong fields.

### 5.2 `ScoreHandle` / `ScoreSnapshot` Bidirectional Mapping

14 fields are manually mapped in both `snapshot()` and `from_snapshot()`. Adding a field requires editing both and is easy to forget.

**Fix:** Derive field names from `ScoreSnapshot` model fields or use a shared field definition.

### 5.3 Provider Metadata Has Drifted Across Surfaces

Provider/model catalogues are independently maintained in:
- `README.md` (lines 34-40)
- `config.toml` (lines 9-89)
- `frontend/web/src/components/Providers.tsx` (lines 10-54)
- `tests/conftest.py` (lines 53-87)

Examples of drift:
- README lists Kimi as `kimi-code/kimi-for-coding`; the landing page splits it into two entries.
- README lists a larger OpenCode catalogue than the landing page.
- Landing page includes Codex model names that don't match README/runtime.

**Fix:** Introduce one canonical provider metadata source generated from backend config. The landing page, docs, and test fixtures should consume shared metadata rather than hard-coding provider lists. The built-in console already fetches provider data dynamically from `/v1/providers`.

### 5.4 Built-In Console JS Duplicates API Logic

`request.js` and `testlab.js` both exceed 300 lines and duplicate polling helpers (`request.js:72-88`, `testlab.js:103-121`). Both mix DOM queries, API access, UI state, and rendering.

**Fix:** Extract a shared `api.js` for HTTP requests and score polling. Extract rendering helpers. Keep event handlers thin.

### 5.5 `_parse_generate_response` Re-export Appears Intentional

`service.py:18` imports `_parse_generate_response` from routes and re-exports via `__all__`. This is currently consumed by `tests/test_testlab.py`, so it should not be treated as dead code. If kept, add a short comment clarifying that the re-export is intentional.

---

## 6. Dependency Version Audit

### 6.1 Backend Python Dependencies

Declared in `pyproject.toml` as broad ranges, **with no lockfile**.

| Package | Pinned Range | Latest Stable | Status |
|---------|-------------|---------------|--------|
| `fastapi` | `>=0.135,<1.0` | 0.135.3 | OK |
| `uvicorn[standard]` | `>=0.42,<1.0` | 0.44.0 | **Bump lower bound to >=0.44** |
| `pydantic` | `>=2.12,<3.0` | 2.12.5 | OK |
| `httpx` | `>=0.28,<1.0` | 0.28.1 | OK |
| `pytest` | `>=9.0,<10.0` | 9.0.3 | OK |
| `pytest-asyncio` | `>=1.3,<2.0` | 1.3.0 | OK — at floor |
| `playwright` | `>=1.58,<2.0` | 1.58.0 | OK — at floor |

**Key issue:** The declared ranges allow the current latest stable versions, but the **absence of a lockfile** makes the effective environment non-reproducible. Add `uv.lock`, `requirements.lock`, or equivalent.

### 6.2 Frontend JavaScript Dependencies

The frontend has a lockfile. Current state versus latest stable:

| Package | Locked | Latest Stable | Status |
|---------|--------|---------------|--------|
| `react` | 19.2.4 | 19.2.4 | Current |
| `react-dom` | 19.2.4 | 19.2.4 | Current |
| `motion` | 12.38.0 | 12.38.0 | Current |
| `tailwindcss` | 4.2.2 | 4.2.2 | Current |
| `@tailwindcss/vite` | 4.2.2 | 4.2.2 | Current |
| `lucide-react` | 0.511.0 | 1.7.0 | **Major version behind** |
| `typescript` | 5.9.3 | 6.0.2 | **Major version behind** |
| `vite` | 6.4.1 | 8.0.7 | **2 major versions behind** |
| `@vitejs/plugin-react` | 4.7.0 | 6.0.1 | **2 major versions behind** |

**Recommendation:** Treat frontend dependency refresh as a separate upgrade task. Do not mix major-version upgrades into the structural refactor — that blurs regression boundaries.

---

## 7. Consolidated Priority Matrix

### Phase 1 — Structural Simplification (P1)

| # | Issue | Impact | Effort |
|---|-------|--------|--------|
| 1 | Split `Orchestra` into pool, score registry, queries, lifecycle | High — 300-line violation + SRP | Medium |
| 2 | Split `Musician` into core class + execution module | High — 300-line violation | Medium |
| 3 | Extract `TERMINAL_STATUSES` constant | Medium — 4x duplication | Low |
| 4 | Add `Musician.is_idle` property | Medium — 3x duplication | Low |
| 5 | Add `ScoreHandle.resolve`/`reject` helpers | Medium — 6x duplication | Low |
| 6 | Extract `MusicianFactory` to remove 3-way construction repetition | Medium — DRY | Low |

### Phase 2 — Configuration & Metadata Correctness (P2)

| # | Issue | Impact | Effort |
|---|-------|--------|--------|
| 7 | Replace regex-based TOML editing with parsed persistence | High — fragility risk | Medium |
| 8 | Canonical provider metadata source across runtime/docs/frontends | High — drift risk | Medium |
| 9 | Type score events as discriminated union | High — silent bug risk | High |
| 10 | Centralise `set_bash_path` global state | Medium — 2 globals | Low |
| 11 | Share `_subprocess_kwargs` across modules | Low — small platform-specific duplication | Low |
| 12 | Modularise discovery per provider | Medium — cognitive load | Medium |

### Phase 3 — File Size & Test Hygiene (P3)

| # | Issue | Impact | Effort |
|---|-------|--------|--------|
| 13 | Split JS files (`request.js`, `testlab.js`) + extract shared `api.js` | Medium — 300-line violation | Medium |
| 14 | Split oversized test files + add shared fixtures/builders | Medium — 300-line violation | Medium |
| 15 | Split `sync_models.py` into package | Low — tooling only | Medium |
| 16 | Simplify Kimi command builder duplication | Low — 2x dup | Low |
| 17 | Unify TOML parser between discoverer and sync script | Low — script + core dup | Low |

### Phase 4 — Dependency Reproducibility (P4)

| # | Issue | Impact | Effort |
|---|-------|--------|--------|
| 18 | Add Python lockfile | Medium — reproducibility | Low |
| 19 | Bump `uvicorn` lower bound to `>=0.44` | Low — version hygiene | Trivial |
| 20 | Refresh stale frontend packages (dedicated branch with CI) | Medium — security/compat | Medium |

---

## 8. What NOT to Refactor

These areas are **well-designed** and should be left alone:

- **Provider adapter inheritance** — clean abstract base, appropriate per-provider variation. Needs targeted deduplication (Kimi), not wholesale rewrite.
- **Score lifecycle state machine** — event-driven, with persistence and pub/sub.
- **Route structure** — clean separation by domain (chat, providers, updates, testlab, console).
- **Model layer** — Pydantic models are well-scoped and correctly typed.
- **Test structure** — comprehensive coverage with appropriate use of fakes/fixtures. Needs splitting, not rearchitecting.
- **No problematic runtime import tangles observed** — the module structure is generally clean, but avoid presenting the import graph as formally acyclic unless it is checked explicitly.

Do not start with cosmetic route reorganisation. Do not combine a frontend major-version upgrade with orchestration refactors.

---

## Bottom Line

The most important simplification is not "less code". It is **clearer ownership boundaries**:

- Orchestration versus execution
- Config persistence versus discovery
- Provider metadata versus presentation
- API access versus UI rendering
- Test setup versus behaviour assertions

If those boundaries are fixed first, the rest of the codebase becomes much easier to simplify without destabilising runtime behaviour.

---

*Combined from two independent read-only audits. No files were modified.*

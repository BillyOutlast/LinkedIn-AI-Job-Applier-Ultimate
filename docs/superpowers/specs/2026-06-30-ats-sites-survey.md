# External ATS Sites — Discovery Survey

**Date:** 2026-06-30
**Status:** Design — pending user approval
**Author:** brainstorming session
**Scope:** Sub-project 1 of N (per-ATS automation specs follow)

## Goal

Run the bot with `EASY_APPLY_ONLY_MODE=False` once and produce a structured catalog of every external ATS site encountered. The catalog feeds follow-up brainstorming cycles — one per unique ATS — that produce formal per-site automation specs (Workday-style phase handlers).

## Background

Three ATS handlers exist today:

| Handler | Site | Source |
|---|---|---|
| LinkedIn Easy Apply | linkedin.com (modal or new in-page form) | `src/job_manager/linkedin/easy_applier_linkedin.py` |
| Workday | `*.myworkdayjobs.com` | `src/job_manager/workday/workday_applier.py` |
| Indeed Easy Apply | indeed.com (always Easy Apply flow) | `src/job_manager/indeed/easy_applier_indeed.py` |

Anything else routes to `ApplyAgent` (browser-use) in `src/llm/apply_agent.py`. Three ATS hosts are blacklisted in `EXTERNAL_APPLY_SKIP_ATSES`: `phenom`, `taleo`, `successfactors`. Just-shipped Easy Apply fixes (commits `57d899e`..`a7338d0`) make the LinkedIn Easy Apply path work against LinkedIn's new in-page apply form, so `EASY_APPLY_ONLY_MODE=False` is now viable for discovery.

A prior run on the failing URL (`https://www.linkedin.com/jobs/view/4425305553/`, Retail Sales Associate at Harbor Freight Tools) revealed that the "Easy Apply" button navigates to a LinkedIn `/apply/?applicantTrackingSystemName=Infinite+Brassring` URL — suggesting Infinite Brassring is one of many external ATSes in the wild. No general catalog exists.

## Architecture

Two-piece system:

1. **Dispatcher instrumentation.** One logging call at each apply_url encounter in `job_manager_linkedin.py` and `job_manager_indeed.py`. Logs `host`, full `url`, matched `signature` (substring pattern), and `outcome` (one of `existing-handler` / `routed-to-workday` / `skipped-known-ats` / `sent-to-browser-use` / `no-external-apply` / `error`). Logging wraps the existing try/except — a logging failure cannot break the apply path.

2. **Discoverer driver.** A new module (`src/discovery/ats_discoverer.py`) wraps the bot run, writes per-session JSONL log, captures DOM fingerprints + debug captures for unknown / non-Workday destinations, and produces a per-session `summary.md` that groups entries by host with first-seen/last-seen timestamps and counts.

## Components

### `src/discovery/ats_discoverer.py` (new, ~150 lines)

- `run_discovery(max_applies=10, headless=True) -> DiscoveryReport` — top-level entry. Spawns the bot's normal `create_and_run_bot` flow, intercepts apply_url events via the dispatcher hooks, captures DOM fingerprints, writes outputs.
- `summarize(jsonl_path: Path) -> str` (markdown) — pure function; reads the JSONL log, groups by host, renders a summary table.
- `capture_fingerprint(page, url) -> dict` — opens `url` in a headless browser, captures `{title, h1, h2, form-input-types: set, button-labels: list, ats-marker-classes: set, iframe-count: int, network-hostnames: set}`.

### Dispatcher hooks (modify `job_manager_linkedin.py`, `job_manager_indeed.py`)

- `_record_encounter(url, outcome, signature)` — appends one JSONL line to the active discovery session log. Reads session log path from a module-level `_DISCOVERY_LOG_PATH` (set by the discoverer before run, `None` outside discovery mode).
- Called from:
  - `job_manager_linkedin.py:apply_job` — before the `apply_url = await self._check_apply_button()` decision (capture pre-routing `signature` from URL substring).
  - `job_manager_linkedin.py:apply_job` — inside the existing dispatcher's each-branch (routed-to-workday / skipped / browser-use) to record `outcome`.
  - `job_manager_indeed.py:apply_job` — analogous hooks.

### Storage layout

```
data/output/discovery/
  sessions/
    <session_id>/
      encountered.jsonl          # one line per apply_url hit
      captures/
        <host>_<timestamp>.json  # DOM fingerprint
        <host>_<timestamp>.png   # page screenshot (when DEBUG_MODE)
        <host>_<timestamp>.html  # page HTML (when DEBUG_MODE)
      summary.md                 # generated from JSONL
  summary.md                     # rolled-up across sessions (future; not in scope for v1)
```

## Data flow

```
1. Operator runs:  uv run python -m src.discovery.ats_discoverer
2. Discoverer sets _DISCOVERY_LOG_PATH, spawns bot with discovery config.
3. Bot iterates LinkedIn jobs.
4. For each job:
   a. dispatcher hook fires pre-decision → records host + url + signature
   b. dispatcher dispatches per existing logic
   c. dispatcher hook fires post-decision → records outcome
   d. if outcome is not "existing-handler" or "routed-to-workday":
      - capture_fingerprint(page, url) writes captures/<host>_<ts>.{json,png,html}
5. End of run.
6. Discoverer calls summarize(jsonl_path) → writes summary.md.
```

## Run parameters

`config/app_config.py` change (committed as part of this spec):

- `EASY_APPLY_ONLY_MODE = False` (was `True` — included in the user's pending dirty change)
- `MAX_APPLIES_NUM = 10` (cap discovery scope)
- `MONKEY_MODE = True` (skip LLM interest filter — discover everything)
- `TEST_MODE = True` (don't actually submit; safer for first run)
- `DEBUG_MODE = True` (enables debug_capture for the captures/)
- `HEADLESS_MODE = True` (capture via PNG/HTML, no GUI)

## Error handling

- Dispatcher logging: try/except wrapping the log append; failure logs a debug warning and continues.
- DOM fingerprint capture: best-effort; failure records `"fingerprint_status": "capture-failed"` in JSONL and continues.
- Bot crash mid-session: summary is generated from whatever JSONL exists.
- Duplicate-host collapse: summary groups by host; first-seen + last-seen + count.
- No external apply encountered (all jobs Easy Apply): summary still produced with zero counts.

## Testing

Unit tests in `tests/test_discovery.py` (new file):

- `test_summarize_groups_by_host` — fixture JSONL with 5 entries across 3 hosts; assert summary groups correctly and counts match.
- `test_summarize_handles_empty_log` — empty JSONL; summary renders "0 sites encountered, 0 dispatches".
- `test_summarize_includes_capture_paths` — entry has `fingerprint_path`; summary references it.
- `test_dispatcher_hook_writes_jsonl_line` — mock the bot's job_manager, call `_record_encounter(url, outcome, signature)`, assert the JSONL has one valid line.

Integration test:

- Manual: `uv run python -m src.discovery.ats_discoverer` with the user's real LinkedIn session. Verify a session folder is produced under `data/output/discovery/sessions/`.

## Out of scope

- Per-ATS automation specs (one brainstorming cycle each, sequenced after discovery data exists).
- Workday-style phase handlers for new ATSes (each is its own sub-project).
- Changes to `EXTERNAL_APPLY_SKIP_ATSES` (driven by per-ATS specs).
- Cross-session roll-up summary (the per-session summary is enough for v1).
- Authentication / session capture for discovered sites (each per-ATS spec owns its auth).

## Open questions

None — resolved during brainstorming.

## Decomposition note

This is sub-project 1 of N. After discovery completes and produces `summary.md` + per-host JSONL, the user will see the catalog and pick which ATS sites deserve formal automation. Each gets its own brainstorming → spec → plan → implementation cycle. Workday is already done; Phenom / Taleo / SuccessFactors are blacklisted; everything else is a candidate.
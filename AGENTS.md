# Atlas — Agent Instructions

Single source of truth for Cursor, Codex, and Claude Code (CLAUDE.md imports this file). Keep edits here, not in tool-specific copies.

## What this is

Paper-only cross-market prediction-market research system. Watches Kalshi and Polymarket, normalizes contracts, finds economic equivalents, verifies them with deterministic rules, simulates order-book pricing, records paper trades. Full context: `README.md` (architecture, current state), `TODO.md` (active checklist), `docs/ATLAS_TECHNICAL_SPEC.md`.

## Hard invariant — read first

**Atlas is paper-only. Never add, enable, or scaffold a live order-placement path.**

- `ATLAS_TRADING_ENABLED` stays disabled; health endpoint must report `trading_enabled=false`.
- Any future execution capability requires a separate module, explicit promotion gate, default-off flag, and human authorization — do not build it casually or "for later".
- `REVIEW_REQUIRED`, inconclusive, unknown, or non-guaranteed pairs must never become trusted approval labels (`APPROVED_EQUIVALENT`/`APPROVED_INVERSE`). Evidence-backed `REJECTED` labels may derive only from same-canonical-subject review pairs whose terminal settled outcomes diverge on both venues, bounded per event (owner-signed decision: `docs/decisions/2026-08-13-rejected-labels-from-review-pairs.md`).
- Never bypass catalog-ID validation or deterministic verification from model/semantic proposals.
- Never read or print `keys/`, `.env`, or credential values.

## Commands

```bash
uv run pytest                      # full suite (currently ~595 tests, all must pass)
uv run pytest tests/test_X.py -x   # targeted
uv run ruff check .                # lint (line-length 100, py312)
uv run atlas --help                # CLI entry point
docker compose up -d               # postgres:5432 + redis:6379 (local only)
curl -s http://127.0.0.1:8010/health           # expect trading_enabled=false
```

## Runtime

This checkout must live at `~/Atlas`, NOT under the OneDrive Group Container it
previously occupied: macOS blocks launchd-managed services from reading that path,
and `uv` fails there outright because it contains colons. See `deploy/README.md`.

The API (port 8010), the continuous monitor, and a liveness watchdog run as launchd
agents — do not start them by hand, or two monitors will race (`launchctl list |
grep com.atlas`, then `launchctl kickstart -k gui/$(id -u)/com.atlas.<name>` to
restart one). Logs are in `~/Library/Logs/atlas-*.log`.

## Layout

- `atlas/` — core: discovery, normalization, fingerprints, verification, simulation, paper, settlement, learning, worker
- `atlas/venues/`, `atlas/streams/`, `atlas/orderbooks/` — venue adapters and market data
- `apps/api/` — FastAPI service; `apps/dashboard/` — static dashboard (vanilla JS/CSS)
- `tests/` — pytest, `asyncio_mode=auto`
- `data/` — local sqlite/json state (gitignored)

## Conventions

- Python 3.12, pydantic v2, httpx, aiosqlite. Ruff enforced, line length 100.
- Deterministic rules decide truth; AI only proposes. Preserve that separation in any new code.
- Bounded retries/timeouts on all venue requests — never let a stalled catalog hang a loop.
- After changes: run the full test suite and `ruff check` before considering work done.
- Update `TODO.md` checklist when completing or discovering work; update the "Last verified" line in `README.md` only after a full validation run.

## Dashboard checks

Use the Playwright MCP (configured in `.cursor/mcp.json` / `.mcp.json`) to load `apps/dashboard/index.html` against the running API and check console errors, instead of ad-hoc playwright-cli sessions.

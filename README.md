# Atlas

Atlas is a cross-market prediction-market research system. It watches Kalshi and Polymarket markets, normalizes their contract language, identifies possible economic equivalents, verifies those relationships with deterministic rules, simulates executable order-book pricing, and records what a paper trade would have done.

The product thesis is:

> AI finds relationships. Deterministic rules decide whether a relationship is real. Order-book math decides whether the price is actionable. Settlement evidence decides whether the learning label can be trusted.

The long-term product may become a mispricing terminal, alerting service, or data product even if the trading strategy does not survive live execution.

## Current operating boundary

Atlas is **paper-only**. No real order-placement path is enabled.

This is an intentional safety invariant, not a temporary UI setting:

- `ATLAS_TRADING_ENABLED` remains disabled.
- The API health endpoint reports `trading_enabled=false`.
- `atlas.paper` records simulated executions only.
- `atlas.shadow` and `atlas.live_monitor` observe live markets but never submit orders.
- Relayer and execution credentials are not used by v0.1.
- Model or semantic proposals cannot bypass catalog-ID validation or deterministic verification.
- `REVIEW_REQUIRED`, inconclusive, unknown, or non-guaranteed pairs cannot become trusted approval labels. Evidence-backed `REJECTED` labels may derive only from same-canonical-subject review pairs whose terminal settled outcomes diverge on both venues (owner-signed decision, 2026-08-13).
- Public venue requests use bounded retry budgets so a stalled catalog cannot block the monitor indefinitely.
- The continuous monitor catches read-only venue failures, reports `NEVER_EXECUTED`, and retries on the next interval.

Do not add a live order client casually. Any future execution capability must be a separate module with an explicit promotion gate, default-off feature flag, independent risk controls, audit records, and a deliberate human authorization step.

## Where we stand

Last verified: **2026-08-17**.

- **Milestone achieved 2026-08-13: the first real trusted settled pairs.** Three `APPROVED_EQUIVALENT` labels from the settled July 2026 FOMC meeting (maintain, >25bps hike tail, >25bps cut tail), approved by the human-signed-off rounding preimage-equality rule (`docs/decisions/2026-08-12-fed-rounding-preimage-equality.md`) with both legs `GUARANTEED` and consistent terminal outcomes on both venues. Every FOMC meeting (~8/year) can now mint more.
- API: serving locally (`/health` reports `trading_enabled=false`).
- Test suite: `416 passed, 1 warning` at the latest full run; `ruff check .` reports zero issues.
- An approval-frontier watch (`atlas pairs frontier`, also `approval_frontier` in `/api/overview`) ranks blocked pairs by how far they are from approval and flags pairs whose published rules text has changed, so waiting for venue-text alignment is no longer passive. It separates blockers a venue could clear by publishing more text from structural divergences that no amount of waiting fixes, and it reports its own blind spots. The monitor now records a rules baseline for both legs of every blocked candidate (`capture_frontier_rules_evidence`), including Polymarket Global legs the validation universe never sees. Latest scan: 8 blocked, 7 blocked on venue text alone, 0 rules changes in 14 days, **0 pairs with an unmonitored leg** — every blocked pair has a baseline, so the next venue text revision registers as a rules-version change.
- A paper-only gap radar (`atlas gaps scan --live`) now measures live cross-venue price gaps on twin-shaped candidate pairs at executable top-of-book prices, and the dashboard's "$2k paper meter" answers the original bankroll question with recorded assumptions instead of hope. The meter stakes 5% of the current bankroll per opportunity (size-capped by the Kalshi book) so it measures compounding, the radar watches every canonically normalized family on both venues (17 twin-shaped pairs across 9 subjects at the 2026-08-14 scan), and the monitor bursts the read-only radar to a 30-second cadence inside scheduled release windows (`atlas/release_calendar.py`).
- Trusted settlement labels: `72` — 8 `APPROVED_EQUIVALENT` + 64 evidence-backed `REJECTED`. **Both the label-mix and the 50-label volume requirements are satisfied**; the learning loop reports `READY` with no blockers, so the next move is a first training/evaluation experiment against the frozen holdout rather than more accumulation.
- Latest learning readiness check: `training_ready=True`, `labels=72`, `observations=388`.
- Current queue: 12 persisted settlement candidates; all are `BLOCKED`. Discretionary fair-price pairs now carry the terminal gate `STRUCTURALLY_UNREACHABLE_DISCRETIONARY_SETTLEMENT` and sort below every reachable candidate, so the queue surfaces where a first trusted label is possible.
- Scheduled bounded backfills default to guarantee-reachable Polymarket Global tags (`144` Elections, `487` House, `100196` Fed Rates, `101701` CPI) plus explicit Kalshi series scans (`KXFEDDECISION`, `KXFED`, `KXCPIYOY`, `KXCPI`, `KXCPICORE`), all verified against live catalog probes. The batch fetches the tag-independent catalog (Polymarket US closed sweep + Kalshi settled-event scan, ~110s live) **once** and shares it across tags; folding it into each tag's 120s budget had timed out every tag since the feature shipped. First `BATCH_COMPLETE` on 2026-08-17 minted 10 evidence-backed `REJECTED` labels with `paper_only=true`.
- The US CPI family covers all four published variants (headline/core x YoY/MoM) with signed thresholds from published wording only. Every monthly BLS release yields at least two settled exact-complement pairs (headline YoY tail, core MoM tail), each blocked only on the same two venue-text gaps: the divergent missing-data fallback and Kalshi's absent terminal fallback.
- Three canonical macro families now make real cross-venue pairs visible end-to-end with honest published divergences isolated: the settled July 2026 FOMC decision pair (blocked only on Polymarket's published rounding), the open December 2026 fed-funds level overlap, and the settled July 2026 CPI YoY pair — the closest frontier, because both venues publish BLS one-decimal precision there.
- The verifier now recognizes exact threshold-operator complements (`>X` vs `≤X` on identical terms) as `APPROVED_INVERSE` — a gated rule change reviewed and signed off 2026-08-12, verified to change no current verdict. It requires both legs `GUARANTEED` and every other term equal, so the CPI tail pair auto-promotes to the first trusted label if the venues' missing-data fallback texts ever align.
- Execution-ready events: `0`.
- Awaiting-settlement cases: `0`.
- Milestone alerts: none yet, because no candidate has cleared the deterministic gate.
- Learning is intentionally blocked until trusted labels include both approved and rejected outcomes and the minimum label count is met.

The first-trusted-pair milestone was completed on 2026-08-13. The active milestone is now **the balanced trusted dataset** (50+ labels spanning both approved and rejected classes). A milestone pair required that it:

1. passes deterministic verification as `APPROVED_EQUIVALENT` or `APPROVED_INVERSE`;
2. has complete rule and settlement-source evidence for both venues;
3. reaches terminal settlement on both venues; and
4. produces a trusted learning label that is visible in the dashboard and exports.

The project is not waiting for a date. It is waiting for complete external evidence. Continue improving discovery, evidence capture, matching, observability, and paper validation while settlement candidates mature.

## The system loop

```text
Venue catalogs and books
        |
        v
Canonical market normalization
        |
        v
Candidate matching and fingerprints
        |
        v
Deterministic rule verification
        |
        +--> BLOCKED / REVIEW_REQUIRED
        |
        v
Approved-pair registry and guarantee checks
        |
        v
Executable-price simulation
        |
        v
Paper trade and shadow observation
        |
        v
Settlement evidence capture
        |
        v
Cross-venue reconciliation
        |
        v
Trusted label -> evaluation -> future model improvement
```

The matching engine may be permissive in order to discover candidates. The verification, settlement, and learning gates must remain strict.

## Core concepts

### Canonical market

Venue-specific JSON is converted into a stable `Market` representation before downstream logic sees it. Important fields include:

- venue and venue market ID;
- title, description, and explicit resolution text;
- YES/NO outcome labels;
- event subject, action, threshold, unit, geography, and timezone;
- open, close, and resolution times;
- settlement source and raw evidence;
- market status and final outcome;
- normalized order-book data.

### Fingerprint and normalization

`atlas.fingerprints` and `atlas.normalization` extract deterministic economic terms such as event family, date/period, subject, action, threshold, operator, unit, geography, settlement source, and settlement policy.

Normalization is not proof of equivalence. It narrows the search space and makes differences explicit.

### Deterministic verification

`atlas.verification.verify_equivalence` compares canonical terms and returns a match decision. The trusted approval statuses are:

- `APPROVED_EQUIVALENT`: both contracts resolve to the same economic outcome;
- `APPROVED_INVERSE`: one contract's YES corresponds to the other's NO.

Everything else is non-trusted review:

- `REVIEW_REQUIRED` means human or stronger evidence is needed;
- mismatch or unresolved terms remain blocked;
- a lexical title match is never a trusted label;
- a positive paper edge is never proof that the contracts are equivalent.

### Settlement guarantee

`atlas.fingerprints.deterministic_settlement_blockers` and the guarantee assessment keep market identity separate from settlement certainty. A market can look like a match and still be unsafe to treat as guaranteed.

Weather contracts, for example, need explicit binary branches, cancellation or void handling, revision/finality policy, and an authoritative settlement source. Missing rule fields remain blockers; Atlas must not infer them from a title, category, or model output.

### Candidate lifecycle

The persisted settlement queue uses these states:

- `BLOCKED`: verification or guarantee evidence is incomplete;
- `AWAITING_SETTLEMENT`: deterministic and guarantee-complete, but one or both markets are not terminal;
- `SETTLED`: both venues expose terminal outcomes and reconciliation can run.

The dashboard displays the next gate, guarantee reason codes, per-venue evidence completeness, captured source fields, missing required policy fields, rules hashes, lifecycle state, and each venue's settlement time. The catalog report also classifies each shared event as `COMPLETE`, `PARTIAL`, or `UNSPECIFIED` evidence, runs a bounded cross-family detail refresh alongside the weather specialist audit, skips contracts already classified as discretionary/non-guaranteed, and prioritizes the closest evidence-ready blocked candidate for the next research action. Alerts are emitted once when a candidate clears the `DETERMINISTIC_RULE_GATE`, then when it transitions into `AWAITING_SETTLEMENT` or `SETTLED`.

Settlement-ready ranking uses the later of the two venue resolution times as the pair's `settlement_ready_at` deadline. This prevents a pair that cannot settle for days from outranking a deterministic pair that can be reconciled sooner. Review or non-guaranteed pairs remain blocked regardless of how soon their dates are.

The persisted queue preserves that ranking position, so the dashboard, API, and monitor act on the same ordering rather than re-sorting candidates by database insertion time.

### Opportunity and paper execution

`atlas.arbitrage` simulates executable fills across order-book levels, fees, slippage, available size, and inverse-side pricing. It must reject unapproved pairs before calculating an actionable result.

`atlas.paper` records the simulated trade. It does not transmit an order. `atlas.reconcile` compares the paper pair with terminal settlement outcomes and records confirmed, diverged, or pending results.

## Repository map

```text
atlas/
  arbitrage.py       Executable-price and locked-edge calculations
  backfill.py        Bounded historical cross-venue validation
  cli.py             Command-line entry point
  config.py          Environment-backed configuration
  discovery.py       Catalog scanning, matching, ranking, queue states
  evaluation.py      Learning readiness and quality gates
  fees.py            Venue fee calculations
  fingerprints.py    Canonical economic fingerprints and blockers
  learning.py        Trusted-label storage and JSONL exports
  live_monitor.py    Approved-pair observation with paper recording only
  models.py          Pydantic/domain models and statuses
  normalization.py   Family-specific deterministic term extraction
  paper.py           Simulated execution only
  reconcile.py       Paper/settlement outcome reconciliation
  registry.py        Approved-pair registry operations
  semantic.py        Optional proposal layer; never a verifier
  settlement.py      Settlement status and outcome helpers
  shadow.py          Continuous live observation; never executed
  storage.py         SQLite persistence and milestone alerts
  validation.py      Validation universe and settlement reconciliation
  venues/
    kalshi.py        Kalshi REST/WebSocket adapter
    polymarket_us.py Polymarket US adapter
    polymarket_global.py Historical Polymarket Global adapter
    fixtures.py      Offline deterministic fixtures

apps/api/main.py     FastAPI API and dashboard routes
apps/dashboard/      Research dashboard assets
docs/                Technical design and continuation documentation
tests/               Unit, integration, safety, adapter, and dashboard tests
data/                Local SQLite, replay, training, and backup artifacts
TODO.md              Detailed continuation checklist and milestone history
```

## Local setup

Requirements: Python 3.12 or newer.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Never commit `.env`, private keys, API secrets, relayer credentials, database files, or customer/account data. Use `.env.example` only as a variable-name template.

The local default is SQLite. PostgreSQL and Redis variables exist for future deployment, but v0.1 does not require them.

## Run the project

Start the API/dashboard:

```bash
uvicorn apps.api.main:app --reload
```

Then open [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

Verify the runtime:

```bash
curl http://127.0.0.1:8000/health
python3 -m atlas.cli learning status
python3 -m atlas.cli learning readiness
```

The health response must continue to show `"trading_enabled": false`.

### Offline fixture path

Use this path for deterministic development and tests. It does not contact venues.

```bash
atlas markets sync --fixture
atlas books inspect kalshi KALSHI-FED-SEP26
atlas opportunities demo
pytest
```

### Live read-only catalog and shadow path

The live commands read catalogs/order books and persist paper/shadow observations. They do not place orders.

```bash
python3 -m atlas.cli pairs scan --live
python3 -m atlas.cli pairs watch --live --interval 300 --backfill-interval 86400
python3 -m atlas.cli pairs shadow --live --interval 60
```

Run only one continuous monitor per local database. Check the process list and log before starting another one.

### Historical validation path

Keep scans bounded. A slow venue request must become a recorded batch result, not an unbounded process.

```bash
python3 -m atlas.cli learning backfill --live \
  --target 1 \
  --global-pages 1 \
  --candidate-events 50 \
  --market-pairs 500 \
  --resolved-pairs 100
```

Useful targeted probes use the supported Global Polymarket tag override, for example:

```bash
python3 -m atlas.cli learning backfill --live --global-tag-ids 144 --target 1
python3 -m atlas.cli learning backfill --live --global-tag-ids 100196 --target 1
```

Do not loosen caps just to produce labels. The goal is trustworthy evidence, not a large row count.

## Learning and self-improvement

Atlas stores the observation, normalized terms, rule decision, evidence versions, settlement results, and final label. The learning loop is intentionally conservative:

1. Observe and normalize markets.
2. Generate candidates with deterministic or optional semantic proposals.
3. Verify the candidate against canonical IDs and rules.
4. Store approved cases as awaiting settlement.
5. Retrieve final settlement evidence from both venues.
6. Reconcile equivalent/inverse outcomes.
7. Write a trusted `APPROVED_EQUIVALENT`, `APPROVED_INVERSE`, or `REJECTED` label only when settlement evidence supports it.
8. Export training and evaluation data with provenance.
9. Evaluate before changing a normalizer, verifier, or proposal policy.

Inconclusive and review cases remain stored for analysis but are excluded from trusted exports. The system must not self-train on its own guesses.

Check and export learning artifacts:

```bash
python3 -m atlas.cli learning status
python3 -m atlas.cli learning readiness
python3 -m atlas.cli learning export \
  --output data/training/atlas.jsonl \
  --eval-output data/training/atlas-eval.jsonl
```

The current readiness target is at least 50 trusted labels with both approved and rejected examples. Before any fine-tuning experiment, review balance, provenance, duplicate handling, temporal leakage, and the evaluation split.

## Dashboard and API

The dashboard is a visibility layer, not a source of truth. The API and persisted SQLite records are authoritative.

Important endpoints:

- `/health`: service status and paper-only execution flag;
- `/api/overview`: discovery, candidate queue, settlement evidence, learning readiness, shadow observations, and milestone alerts;
- `/`: dashboard HTML;
- `/dashboard.js` and CSS routes: dashboard assets.

The dashboard should make these facts visible without interpretation:

- whether the system is paper-only;
- current catalog freshness and source coverage;
- candidate lifecycle and next gate;
- deterministic decision and guarantee reason codes;
- settlement times for both venues;
- evidence completeness and rule/source hashes;
- paper edge, fees, slippage, and available size;
- trusted-label counts and learning blockers;
- latest milestone transition or an explicit “no transition yet” state.

## What counts as a validated result

A candidate is not validated because:

- titles look similar;
- a model gives high confidence;
- the markets are in the same category;
- a paper trade shows positive edge;
- one venue has settled;
- the dashboard contains a populated row.

Validation requires complete cross-venue evidence, deterministic rule compatibility, terminal outcomes from both venues, and an auditable reconciliation result. The first trusted pair is the next proof point; it is not permission to risk the full account.

## Current blockers and next work

The detailed checklist lives in [`TODO.md`](TODO.md). The immediate sequence is:

1. Keep the monitor and bounded historical probes running.
2. Improve source evidence completeness until candidates can become guarantee-complete.
3. Confirm the first deterministic pair emits `DETERMINISTIC_RULE_GATE` and reaches `AWAITING_SETTLEMENT`.
4. Capture terminal settlement from both venues.
5. Reconcile and expose the first trusted label in the dashboard and exports.
6. Accumulate a balanced trusted dataset.
7. Evaluate normalizer/verifier changes against a frozen holdout.
8. Build live-readiness controls while keeping order placement disabled.

If a venue lacks explicit rules, cancellation terms, finality, or authoritative settlement source, keep the candidate blocked and improve the adapter/evidence path rather than weakening the verifier.

## Staged path beyond paper-only

The project may eventually support live execution, but only as a separately promoted capability:

### Stage 0 — current

Paper and shadow observation only. No order writes.

### Stage 1 — live read-only verification

Verify account permissions, balances, market data, order books, and settlement endpoints. Do not submit orders.

### Stage 2 — controlled demo/sandbox testing

Use venue-supported non-production environments where available. Keep credentials and endpoints explicitly separated.

### Stage 3 — manual-approved canary

One approved pair, minimal notional, both legs, strict stale-book and liquidity checks, deterministic idempotency, pre-trade approval, fill reconciliation, and a kill switch.

### Stage 4 — narrowly automated execution

Only after canary fills and settlement reconcile correctly. Enforce per-trade, per-pair, daily, and total-capital limits; stop on any rule/evidence mismatch.

Moving to a later stage requires an explicit decision record. A future execution module must never be reachable merely because an environment variable was copied into `.env`.

## Risk and research discipline

The original financial target was to attempt to grow $2,000 to $20,000 in 12 months. That is an aspiration, not a guaranteed or validated outcome. No system can promise a 10x return. Live trading can lose principal, and cross-venue execution adds liquidity, timing, counterparty, settlement, fee, and operational risks.

Atlas should optimize first for:

- evidence quality;
- reproducible decisions;
- conservative rejection of uncertain pairs;
- honest paper/live comparison;
- small, auditable experiments;
- preservation of capital and the ability to stop.

Review applicable venue terms, account eligibility, jurisdiction, tax, and legal requirements before any future live trading decision. This repository is research software, not financial or legal advice.

## Validation commands

Run the full suite after changes:

```bash
pytest
ruff check atlas apps tests
python3 -m compileall -q atlas apps tests
node --check apps/dashboard/dashboard.js
```

For a focused change, run the relevant test file first, then the full suite. Changes to normalization, verification, settlement, adapters, storage, learning, or dashboard payloads should receive focused tests and a full regression run.

## Documentation map

- [`docs/ATLAS_TECHNICAL_SPEC.md`](docs/ATLAS_TECHNICAL_SPEC.md): original technical architecture and venue assumptions.
- [`TODO.md`](TODO.md): active milestone checklist, completed work, blockers, and continuation steps.
- [`atlas/models.py`](atlas/models.py): domain statuses and data contracts.
- [`atlas/verification.py`](atlas/verification.py): deterministic equivalence gate.
- [`atlas/fingerprints.py`](atlas/fingerprints.py): normalized terms and settlement blockers.
- [`atlas/storage.py`](atlas/storage.py): persisted observations, candidates, labels, and alerts.
- [`apps/api/main.py`](apps/api/main.py): dashboard API and paper-only health response.

## Resume checklist for the next session

```text
[ ] Read this README and TODO.md.
[ ] Verify the current working tree and .env without printing secrets.
[ ] Confirm only one monitor is running.
[ ] Check /health and /api/overview.
[ ] Run learning status and readiness.
[ ] Inspect candidate lifecycle, guarantee blockers, and settlement times.
[ ] Run one bounded probe or improve the evidence adapter.
[ ] Add a regression test for every behavior change.
[ ] Do not enable order placement.
```

The next meaningful success is not a simulated profit number. It is the first pair whose identity, rules, source evidence, settlement, and reconciliation all agree—and whose complete path is visible and reproducible.

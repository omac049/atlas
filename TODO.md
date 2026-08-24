# Atlas continuation checklist

Last updated: 2026-08-24 (**first contract-intelligence deliverable shipped**: weekly Contract Divergence Report via `atlas intel report` + com.atlas.intel launchd agent; nightly VACUUM added to the backup job; 7 stale worktree branches deleted after verifying content on main; deploy README bootstrap now lists study+intel agents; 600 tests green, lint clean)

Current handoff note (2026-08-20): the runtime has **81 trusted labels** (10 approved, 71 rejected), 388 unlabeled observations, learning readiness `READY` with no blockers. The governing activity is now the **90-day opportunity study** — day 2 of 90, decides 2026-11-17, charter in `docs/NINETY_DAY_STUDY.md`. The verifier and normalizers are **frozen for measurement** while it runs; any rule change needs owner sign-off *plus* an amendment note in the charter. The next dated commitment is **phase 2 by day 31 (2026-09-18)**. The older historical notes below retain prior run counts for provenance; they are not the current state.

Previous entry: 2026-08-17 (adaptive settlement polling integrated: readiness ordering, venue-specific evidence classification, durable pending reasons, next-poll timestamps, bounded retry metadata; 450 tests green; 72 trusted labels)

Previous entry: 2026-08-14 (387 tests green; **50-label balanced-dataset milestone COMPLETE at 52 trusted labels** — payrolls/core-PCE/GDP families shipped from captured real texts before the Kalshi pruning window, the per-event rejection cap is now persisted cross-run, and the backfill pair cap truncates the priority-sorted list so venue ladders can no longer crowd out labelable pairs)

## 2026-08-24 — the pivot gets its first artifact

- [x] **Weekly Contract Divergence Report** (`atlas/intel.py`, `atlas intel
  report`, scheduled Mondays 07:15 via `com.atlas.intel` after the study
  report). Assembles persisted evidence into the thing a person outside the
  repo can read: approved equivalents awaiting settlement, the venue-text
  frontier, a per-family rules-completeness scorecard, settlement-timing
  asymmetries grouped per (subject, early venue) with lock-up ranges, and the
  latest price disagreement per twin pair flagged by venue tradeability and
  the tick/size floors. Read-only; decides nothing; caveats ship in the JSON
  payload, not just the prose, so a machine consumer gets the same honesty
  framing. 5 tests pin the honesty surface (candidates never proven, absent
  measurement never a finding, untradeable never opportunity).
- [x] **Nightly VACUUM in the backup job** (`deploy/atlas_backup.py`), only
  after a snapshot is taken AND verified. The monitor's prune deletes rows but
  SQLite keeps the pages — the file hit 1.05 GB at 76% free before the first
  manual vacuum. 60s busy timeout; a locked database skips gracefully and
  retries the next night.
- [x] **7 stale `worktree-agent-*` branches deleted** (TODO said 3; there were
  7). Verified first: 4 fully merged; the other 3 (core PCE / GDP / payrolls)
  carry one stale commit each whose pinned test files are byte-identical on
  main — dead duplicates, exactly as recorded below.
- [x] `AGENTS.md` test count corrected (~523 -> ~595); `deploy/README.md`
  bootstrap loop now includes `com.atlas.study` (it was installed out-of-band)
  and the new `com.atlas.intel`; both documented in the Files section.

- [ ] Show the divergence report to prospective users during phase-3
  interviews (charter: day 61+, from 2026-10-18); collect what they would pay
  for, not what they say is interesting.
- [ ] The report's price-disagreement section will thin out as Global pairs
  settle — watch that it keeps carrying tradeable-venue rows once the Sep
  releases land.

## 2026-08-20 (b) — the live label loop was structurally dead

- [x] **Root cause:** `scan_pairs` passed **Polymarket US only** to
  `scan_market_pairs`, `review_market_pairs`, `structured_identity_candidates`,
  and `capture_validation_universe` — while every approved pair on record is
  Kalshi x `polymarket_global`. `compatibility_report` and
  `capture_frontier_rules_evidence` already received the combined universe, so
  the queue showed 4 approved pairs while `discovery_scans.approved` read **0
  across 21 consecutive scans**, and 100% of trusted labels came from
  `backfill.py`.
- [x] **Fix:** a single `polymarket_universe = [*polymarket_markets,
  *global_open_markets]` feeds the pairing / review / validation paths.
  Deliberately NOT widened: the enrichment passes and `_record_shadow_observation`
  are bound to the US adapter, and shadow additionally needs an order book the
  Global venue does not expose at all. Pinned by test, including the negative.
- [x] **The trap behind it:** `reconcile_validation_cases` hardcoded
  `polymarket_venue.get_market(...)`. A Global slug means nothing to the US
  gateway, so every Global case would 404, be recorded as
  `VENUE_EVIDENCE_UNAVAILABLE`, and retry **forever** — a permanent stall that
  reads like a venue outage. Reconciliation now resolves the adapter from
  `market.venue`; an adapter without `get_market` (the Global venue exposes
  evidence methods only) carries the persisted market forward while
  `_apply_terminal_settlement` refreshes the terminal evidence reconciliation
  actually adjudicates on. A leg with no adapter gets a new, distinct
  `VENUE_ADAPTER_MISSING` reason and does **not** burn the retry budget —
  never-asked is not asked-and-failed.
- [x] **Verified live:** comparisons 760 -> **1,182**, approved **0 -> 12**
  (FOMC Sep/Oct/Dec 2026 + Jan 2027), validation cases 81 -> **112** including
  the first 12 `source_kind=APPROVED` cases ever created by the live path. All
  31 new cases sit `NOT_CLOSED`, which is correct — those meetings have not
  happened yet. `trading_enabled=false` throughout.
- [x] **The "verified" metric counted the wrong pairs.**
  `if codes and codes <= VENUE_TEXT_ONLY_CODES` required a **non-empty** code
  set, so pairs that cleared the verifier were excluded from the metric named
  "verified" while pairs blocked on wording were counted. Now split into
  `approved_opportunities_total` + `venue_text_only_opportunities_total` ->
  `verified_opportunities_total`. An empty code set alone cannot promote a row:
  the status must also be a trusted approval, so a truncated row cannot sneak
  in through an absent field. Charter amended.

- [ ] **Watch the 12 approved pairs through settlement.** They are the first
  live-path approvals; each needs terminal evidence on both venues before it can
  mint a trusted label. The Sep 16 FOMC meeting is the first to settle.

## 2026-08-20 — the radar was measuring an untradeable venue

- [x] **Root cause, measured not guessed:** all 18,650 gap observations — and
  every one of the 1,490 "executable" ones — priced a `polymarket_global` leg.
  That adapter's own docstring says its markets "can never reach shadow,
  approval, or paper-trading paths that require executable prices", and it is
  the offshore venue. `gaps_scan` (`atlas/cli.py`) built its universe from
  `PolymarketGlobalHistoricalVenue` **only**; Polymarket US was never an input.
  This is why those gaps survive for hours — nobody can close them.
- [x] **Not a pagination bug** (first hypothesis, disproved): the PM-US adapter
  pages correctly and `list_markets()` returns 22,149 live markets with macro
  and politics on pages 0-1, well inside the 20-page cap. It was pure scope.
- [x] **Fix:** `PolymarketUSVenue.list_open_category_markets` + a new
  `GAP_RADAR_PMUS_CATEGORIES = ("macro",)` scope. PM-US legs are priced from the
  gateway's real two-sided book (`/v1/markets/{slug}/book`, keyed `bids` /
  `offers`), so `polymarket_fill_assumed_at_quote` is now per-observation and
  **false** for US rows. `_baskets` records `polymarket_size` and `basket_size`
  (the thinner binding leg); observations carry `polymarket_venue` and
  `tradeable_venue_pair`.
- [x] **Two silent gateway traps found and documented in the adapter:** the
  singular `category=macro` is ignored and returns the unfiltered catalog, and a
  comma-joined `categories=macro,politics` returns **zero** events with HTTP 200.
  Only repeated `categories` params filter. A client-side re-check guards scope
  if the param ever stops working. The scoped sweep went 27.5s -> 3.4s.
- [x] **Fee model:** PM-US publishes a scalar `feeCoefficient` (0.06) and no
  `feeSchedule`; without a branch for it every US quote fell to the max-rate
  fallback and overstated the fee by ~17%.
- [x] **Phantom caught before it could mislead.** With `politics` briefly in
  scope, Kalshi's "Will Democrats win the House" paired with the gateway's joint
  `paccc-balpow-*` contracts and printed gaps of **31.5c and 79.8c**. Cause: the
  joint "R House, D Senate" contract normalizes to `us_house_control|2026` with
  a **non-null and wrong** affirmative outcome (`democratic_party`). The Gamma
  guard relies on joint contracts having a *null* outcome — true there, false
  here. 3 observations recorded and deleted (they postdate the last published
  report, so no artifact contains them); `politics` removed from scope; both the
  defect and the containment pinned by test.
- [x] **Study phase 2 pulled forward from day 31.** `return_on_locked_capital`
  divides the edge by the later leg's horizon — which was already computed and
  bucketed, and which nothing divided by. `distinct_pairs` now sits beside
  `distinct_opportunities` (26 opportunities come from **11 pairs**).
  `meets_go_threshold` became an object of named sub-tests; a sub-test with no
  eligible population reports `null` and does not pass.
- [x] **The paper meter no longer rounds in its own favour.** An opportunity
  with no published depth ran **uncapped** at the full 5% stake — 8 of 26
  opportunities, 35% of recorded profit. Now skipped and counted. The meter
  reads **+$19.29** over 9 days, not +$29.74.
- [x] **Floors added, and they change the reading completely.** Both venues tick
  at 1c, so an edge under one tick is quantization noise; and a gap is worth
  nothing if you cannot take size. `meets_tick_floor` / `meets_size_floor` /
  `best_basket_size` are recorded per observation (`executable_gap` deliberately
  unchanged so the series stays comparable). The live scan now prints depth and
  a `BELOW_FLOOR` marker: on 2026-08-20 **every tradeable executable gap was
  BELOW_FLOOR**, including a 7.8c GDP gap backed by **0.06 contracts** of
  Polymarket depth. The only above-floor row is the Global house-control pair at
  165k contracts — Kalshi-side depth only, on a venue that cannot be traded.
- [x] **Result: GO -> NO-GO on unchanged data.** Frequency still passes
  (73.3/30d vs 10); return on locked capital is **3.38% annualized** vs a 15%
  hurdle; median basket **$459** vs a $500 floor. Tradeable subset: **1.66%**.
- [x] **Owner decision 2026-08-20: execution track dropped, pivot to contract
  intelligence.** Paper-only now stands indefinitely rather than provisionally.
  The staged path in `README.md` is dormant, not pending.

- [ ] **The two new go thresholds are PROVISIONAL** —
  `GO_MIN_ANNUALIZED_RETURN_ON_LOCKED_CAPITAL = 0.15` and
  `GO_MIN_MEDIAN_BASKET_NOTIONAL_USD = 500` are placeholders chosen as the
  risk-free rate plus a risk premium, and the smallest basket worth attention.
  They are reported under `provisional_pending_owner_signoff`. Record a decision
  before day 90 treats either as authoritative.
- [ ] **Election normalizer: chamber attribution is wrong on joint contracts.**
  `paccc-balpow-2026-11-03-rhou-dsen` ("R House, D Senate") reports
  `affirmative_outcome=democratic_party`. It only affects PM-US politics, which
  scope now excludes, but it is a frozen-path defect: fixing it needs owner
  sign-off plus a charter amendment. Re-admitting `politics` to radar scope
  depends on it. Pinned by
  `test_pmus_joint_balance_of_power_misattributes_its_chamber_party`.
- [ ] **Hand-run tradeable snapshot, 2026-08-20 (not yet automated):** across
  FOMC Sep/Oct, CPI YoY, and unemployment — **27 Kalshi x PM-US twin pairs, 0
  executable**, best −0.12c, median −2.12c, baskets costing $1.00-$1.02. The two
  US venues are arbitrage-linked. The near-dated release windows (jobs
  2026-09-04, CPI 2026-09-11, FOMC 2026-09-16) already burst at 30s and are the
  honest test of whether a tradeable gap ever opens.

## Current validated state

*Provenance list — each item was true when checked off, and the invariants still hold.
Run counts and test totals inside it are historical snapshots, **not** current values
(e.g. "172 tests passing" below is from 2026-08-11; the suite is 573 as of 2026-08-20).
For live numbers see the handoff note above, `README.md`, or `/api/overview`.*

- [x] Paper-only policy remains enforced. No order-placement path is enabled.
- [x] API is serving at `http://127.0.0.1:8010/`.
- [x] Continuous live monitor is running with `pairs watch --live`.
- [x] Polymarket US and tagged Polymarket Global historical catalogs are connected.
- [x] Historical settlement evidence is required before creating trusted labels.
- [x] Validation reconciliation refreshes both legs through `get_terminal_settlement_evidence` when available, with the legacy settlement endpoint as a bounded compatibility fallback.
- [x] Kalshi resolved-market terminal results are normalized into structured evidence and persisted beside Polymarket terminal evidence.
- [x] Validation outcomes persist per-leg terminal evidence sources for auditability.
- [x] Settlement polling prioritizes expected readiness and suppresses checks before close/resolution timing.
- [x] Pending settlement cases persist reason codes, next eligible poll time, and bounded retry metadata.
- [x] API overview exposes settlement polling state for operator-visible diagnosis.
- [x] `REVIEW_REQUIRED` pairs remain inconclusive and cannot become training labels.
- [x] Historical scans are bounded by candidate-event, market-pair, and resolved-pair caps.
- [x] Latest bounded probe: 50 candidate events, 500 pairs, 0 approved, 0 rejected, 500 inconclusive.
- [x] Per-tag probes completed for Elections (`144`) and Fed Rates (`100196`); both produced 0 trusted labels.
- [x] Full validation suite: **172 tests passing**, `ruff check .` fully clean (0 issues), all touched modules compile, dashboard `node --check` passes, API paper-only tests (`trading_enabled=false`, `paper_only=true`) green.
- [x] Automated training/evaluation artifacts are generated after completed backfills with provenance and paper-only safeguards.
- [x] Dashboard exposes settlement coverage, confirmation precision, inconclusive rate, learning readiness, and provenance state.
- [x] Settlement rankings expose lifecycle state so open, closed-awaiting-evidence, and settled candidates are not conflated.
- [x] Latest batch scan completed for Crypto (`21`), Weather (`84`), and Commodities (`101031`) without enabling execution.
- [x] Scheduled monitor backfills now use the same bounded per-tag batch policy as manual scans.
- [x] Extended bounded probes completed for Oil (`309`), House control (`487`), Fed Rates (`100196`), and Midterms (`103840`); all produced 0 trusted labels.
- [x] First bounded batch on the re-scoped defaults (`144`/`487`/`100196`, 2026-08-11): `BATCH_COMPLETE`, `paper_only=true`, 0 failed tags, 0 trusted labels — 179 (Elections), 17 (House), 180 (Fed Rates) inconclusive pairs reviewed.
- [x] Per-tag timeout now converts a slow venue request into an explicit batch result instead of hanging the learning loop.
- [x] Settlement-candidate queue is persisted and exposed through the API/dashboard with lifecycle and guarantee states.
- [x] Candidate transitions emit deterministic in-app milestone alerts for the rule gate, `AWAITING_SETTLEMENT`, and `SETTLED` states.
- [x] Queue entries expose per-venue guarantee reason codes and an explicit next gate.
- [x] Queue entries expose per-venue evidence completeness, source presence, captured source fields, missing required policy fields, blockers, and rules hashes.
- [x] Public venue requests enforce a total retry timeout budget so a stalled catalog cannot block the monitor indefinitely.
- [x] Continuous pair monitoring catches venue refresh failures, records a `NEVER_EXECUTED` recovery message, and retries on the next interval.
- [x] Settlement rankings prioritize `SETTLED`, then deterministic/guaranteed pairs by the later of the two venue settlement times.
- [x] Persisted settlement candidates retain discovery ranking order so the dashboard shows the same earliest-settlement priority.
- [x] Polymarket adapter preserves explicit resolution-rule and settlement-source fields for future evidence updates.
- [x] Historical Polymarket Global adapter preserves explicit resolution-rule and settlement-source fields for settled-pair discovery.
- [x] Dashboard redesign (2026-08-11): unified panel/metric-grid design system, sticky topbar with section nav, minimum 10px type with improved contrast, cohort pipeline step cards, settlement guarantee pills, human-readable update timestamps. All element IDs, paper-only safety strings, and tested selectors preserved; 174 tests green.

## Current blockers

*(Verified against the live runtime 2026-08-20.)*

- Reachable candidates remain blocked by published venue-text gaps, especially Kalshi terminal fallback/revision language for CPI and Polymarket rounding/fallback language for FOMC. Current frontier scan: 8 blocked, **6 blocked on venue text alone**, 0 rules changes in 14 days, 0 pairs with an unmonitored leg.
- No candidate is execution-ready; paper-only and never-executed safeguards remain mandatory. Execution-ready events `0`, awaiting-settlement cases `0`.
- **The verifier and normalizers are frozen for measurement until 2026-11-17** (90-day study). A rule change needs owner sign-off *and* a charter amendment note. Verdict-neutral fixes (logging, performance, reporting shape) are exempt but should still be noted if they change a reported metric's shape.
- The next learning decision is evaluation quality, not label accumulation: define family-balanced holdout metrics before training. Do not add labels solely for volume — 81 already clears both the mix and 50-label volume gates.
- ~~Settlement-timing asymmetry is unmeasurable in radar scope~~ **RESOLVED 2026-08-20**: the categorical chamber-control twins are now watched, giving the split 4 asymmetric pairs. See the 2026-08-20 scope entry.
- Polling is adaptive, but venue policy gaps remain external evidence blockers; do not convert retryable/pending states into approvals.

## Active milestone status

- [x] Entered the first-settled-pair milestone.
- [x] Automated live discovery, settlement evidence capture, reconciliation, and paper-only safeguards are operating.
- [x] **First real trusted pairs achieved 2026-08-13 — three `APPROVED_EQUIVALENT` labels** from the settled July 2026 FOMC meeting (`KXFEDDECISION-26JUL-H0` × PM no-change, `-H26` × PM 50+, `-C26` × PM −50+), minted by the signed-off preimage-equality rule on the real bounded backfill (`MILESTONE_IN_PROGRESS`, `new_labels=3`, `trusted_settlement_labels=APPROVED_EQUIVALENT=3`). Both legs `GUARANTEED`, terminal outcomes consistent on both venues, full provenance persisted.
- [x] Current queue is observable: 12 ranked candidates, all `BLOCKED`; none are yet guarantee-complete.
- [x] Milestone closed: three real pairs passed deterministic verification with terminal outcomes published on both venues.
- [x] Balanced trusted dataset milestone closed: 72 trusted labels (8 approved, 64 rejected), with `training_ready=True`.

## Current engineering target — source completeness and transition observability

- [x] Persist structured evidence-field presence without persisting raw venue secrets or oversized payloads.
- [x] Distinguish captured source fields from required policy fields that are still missing.
- [x] Emit a one-time `DETERMINISTIC_RULE_GATE` alert when a candidate first clears both equivalence and guaranteed-settlement checks.
- [x] Keep the existing one-time alerts for `AWAITING_SETTLEMENT` and `SETTLED` queue transitions.
- [x] Add pair-level evidence readiness (`COMPLETE`, `PARTIAL`, or `UNSPECIFIED`) and aggregate counts to the catalog report.
- [x] Prioritize blocked candidates with complete or nearly complete published evidence so the next research action is reproducible.
- [x] Surface the count of evidence-complete shared events in the dashboard.
- [x] Run a bounded cross-family detail refresh in addition to the specialist weather audit.
- [x] Persist cross-family refresh failures and complete-policy counts without inferring missing terms.
- [x] Skip known discretionary/non-guaranteed cross-family events so bounded refresh capacity focuses on unresolved candidates.
- [ ] Find a live candidate whose venue-published policy supplies every required field; do not infer missing cancellation or revision terms.
- [x] Queue triage (2026-08-11): 10 of 12 active candidates are sports spreads blocked by `DISCRETIONARY_FAIR_PRICE_SETTLEMENT` — permanent dead ends for trusted labels. The frontier is the two KSFO weather pairs (ranks #1–2, `rule_distance=1`).
- [x] Capture Kalshi's venue-published binary Yes/No contract structure (`market_type=binary` + published Yes/No sides) as negative-branch evidence, mirroring the existing Polymarket `marketSides` treatment. This clears `MISSING_NEGATIVE_BRANCH` for Kalshi weather candidates without inferring policy. (`atlas/policy_evidence.py`; tests added in `tests/test_policy_evidence.py`.)
- [x] Re-run the full test suite locally: baseline is now **168 passing** (a stale `httpx` import in `tests/test_cli.py::test_safe_live_scan_failure_keeps_monitor_recoverable` was fixed — added `import httpx`). Live `pairs candidates` recomputes evidence through the new extractor: the negative-branch fix is confirmed working — `MISSING_NEGATIVE_BRANCH` no longer appears on the KSFO weather pairs.
- [ ] Remaining true gaps on the KSFO weather pairs after the fix (confirmed live 2026-08-11): the top-4 candidates now block on `REVISION_POLICY_MISMATCH` and `SETTLEMENT_GUARANTEE_UNKNOWN` only. Neither venue publishes cancellation/revision/guarantee terms in its API rules text for weather markets — keep scanning for candidates/venues that do; do not infer.
- [x] **Full weather/climate policy-completeness survey completed** (2026-08-12, read-only, 333 Kalshi climate series + Polymarket weather events): **no policy-complete weather overlap exists today.** Polymarket has upgraded its weather texts (daily-temp markets now publish an explicit revision cutoff; the hottest-year market publishes a terminal fallback chain — the most complete weather text on either venue), but no Kalshi weather family publishes cancellation/void or revision/finality terms in its API rules text, and explicit void disposition is absent on both venues in every family. The KSFO pair's `REVISION_POLICY_MISMATCH` is confirmed genuine and worse than a gap — the published policies are opposite (PM freeze-at-cutoff vs Kalshi latest-version) and the data products differ (Wunderground vs NWS CLI). Best structural candidate anywhere: the hottest-year pair (`KXGTEMP-27-P0` × PM `will-2026-be-the-hottest-year-on-record`), the only overlap where both legs name the identical source (NASA GISS unsmoothed LOTI); still blocked by Kalshi's missing policy text and a published tie-rule divergence.
- [ ] Weather lead #1 — Kalshi publishes per-series `contract_terms_url` PDFs (e.g. `HURRICANE.pdf`, `GLOBALTEMPERATURE.pdf`) that plausibly contain the cancellation/revision clauses missing from the API rules text. Ingesting those PDFs as captured evidence would be new venue-published evidence, not inference — a future adapter-evidence project (bounded fetch, hash the document, extract clauses deterministically).
- [ ] Weather lead #2 — seasonal watch: Kalshi `KXHURCTOT-26DEC01-T6` ("above 6" hurricanes) × Polymarket's "7+" bucket is an exact threshold equivalence (`>6` ≡ `≥7`) on the same NHC subject, recurring every season; today it blocks on a published measurement-period divergence (Jan 1–Dec 1 vs market-creation–Nov 30) plus Kalshi's missing revision/fallback text. Pairs automatically if those texts converge.
- [x] **Separate reachable frontier from structural dead ends in the queue** (`atlas/discovery.py`; tests in `tests/test_arbitrage.py`, now **171 passing**). A `NON_GUARANTEED` (discretionary fair-price) pair can never become a trusted label no matter how much evidence is published, so it is now:
  - given a distinct terminal gate `STRUCTURALLY_UNREACHABLE_DISCRETIONARY_SETTLEMENT` in `_candidate_queue_state` instead of being mislabeled as `CLEAR_DETERMINISTIC_RULE_MISMATCHES` (which implied it was actionable cleanup work);
  - sorted below every reachable-but-incomplete candidate via a new `_reachability_rank` inserted right after `_queue_rank` in both the per-event and final sort keys, so evidence-complete dead ends can no longer float above the real frontier;
  - flagged with a `guarantee_reachable` boolean on each ranked candidate, plus new report aggregates `reachable_blocked_events` and `structurally_unreachable_events` for the dashboard/API.
  Rationale: `UNKNOWN` pairs (weather/economic/election) settle on objective published data and only lack complete policy text, so more venue evidence can promote them to `GUARANTEED`; `NON_GUARANTEED` fair-price pairs (sports spreads) are permanent. This makes the queue self-documenting about where a first trusted label is even possible.
- [x] **Re-scope the scheduled batch default toward guarantee-reachable families** (verified against live catalog, not blind-swapped). Bounded read-only probe (2026-08-11, 100 most-recent closed Polymarket Global markets per tag, 8 tags / 800 markets fingerprinted through `build_fingerprint` + `assess_settlement_guarantee`):
  - Old defaults sampled **0%** guarantee-path families: Crypto `21` = 100% binary, Commodities `101031` = 100% binary, Weather `84` = 100% *moneyline* (recent closed markets under that tag are not even weather-family).
  - Elections `144` = 71% election family; Fed Rates `100196` = 15% economic; House `487` = chamber-control scope (only 13 closed markets, but its `us_house_control` tiebreak policy is a coded `GUARANTEED` path).
  - **Every one of the 800 sampled markets assessed `UNKNOWN`** — no tag yields `GUARANTEED` on the Polymarket Global side yet. The re-scope aims capacity where the coded guarantee paths (chamber-control tiebreak, CPI release policy) *can in principle* fire; it does not manufacture labels.
  - `BATCH_DEFAULT_GLOBAL_TAG_IDS` is now `("144", "487", "100196")` (`atlas/cli.py`), with tests pinning the policy intent (`tests/test_cli.py`; suite now **172 passing**). `--global-tag-ids` overrides are unchanged.
- [x] **Lint baseline cleared to zero** (`ruff check .` → "All checks passed!"): 41 auto-fixes (verbose `Decimal` constructors, import sorting, regex-flag/timeout aliases) plus 8 hand-fixes — month-name lookup reuses the existing `month_numbers` dict in `backfill.py`; date-only `strptime` parses pinned to UTC in `fingerprints.py`/`normalization.py` (callers re-anchor timezone or take `.date()`, so behavior is unchanged); `sorted(...)[0]` → `min(...)` in `enrichment.py`; nested-if collapse in `fingerprints.py`; explicit parens on concatenated regex literals in `policy_evidence.py`.
- [x] **Root-caused and fixed why `GUARANTEED` never fired on real Polymarket Global data** (2026-08-11). Pulled real raw rules text for tags 487/100196/144: Polymarket's boilerplate is `This market will resolve to "Yes" if ... Otherwise, this market will resolve to "No".` — a textbook explicit binary fallback that `has_explicit_binary_fallback()` (`atlas/fingerprints.py`) exists to recognize, yet it returned `False` on that exact text. Root cause: the regexes required `yes`/`no` with no intervening character after `to`/`will`, and Polymarket quotes the outcome word (straight or curly quotes). Fix: optional quote class `["'“”‘’]?` around `yes`/`no` in all four alternatives; verified against real captured market text (both quote styles), the unquoted fixture text (no regression), and fair-price text (still correctly rejected). 2 regression tests added in `tests/test_settlement.py`; suite now **174 passing**, lint still clean.
  - **Empirical impact:** the same 800-market live probe went from **0/800 to 236/800** `GUARANTEED` (Commodities 88%, House 85%, Finance 60%, Crypto 29%, Fed Rates 26%; Elections only 2% — its markets are margin-of-victory spreads, not simple binaries; Weather 0% — it uses the separate stricter policy-evidence path by design).
  - **Post-fix bounded batch (`144`/`487`/`100196`):** still 0 trusted labels — Fed Rates now reviews 252 pairs (up from 180 pre-fix), Elections 179, House timed out at the 120s per-tag guard (recorded, batch continued: `BATCH_PARTIAL_FAILURE`). Single-leg `GUARANTEED` is necessary but not sufficient: a trusted label needs *both* legs `GUARANTEED` on the same settled cross-venue event (`_combined_guarantee`), and `resolved_pairs=0` in every run means no settled cross-venue overlap has cleared deterministic verification yet. The Kalshi side's own guarantee phrasing and actual event overlap are now the binding constraints.
- [x] **Mismatch census on the real backfill path** (2026-08-11, `scripts_diag_mismatch_census.py`, Fed Rates tag `100196`): replicated the exact backfill pairing (10 candidate events from 20,000 Kalshi settled events × 100 Polymarket final binaries → 383 pairs, all `REVIEW_REQUIRED`) and kept the mismatch codes the batch report discards. **`EVENT_SUBJECT_MISMATCH` fires on 383/383 (100%)**, then `MEASUREMENT_PERIOD_MISMATCH` 344, `SETTLEMENT_GUARANTEE_UNKNOWN` 285.
  - Example pairs confirm most are **true negatives** — e.g. `'Will Cindy Hyde-Smith vote for the next Fed Chair nominee?' ↔ 'Will Jerome Powell depart as Fed Chair before May 15?'` — the verifier is correctly refusing them. The system is not broken; the lexical candidate matcher just feeds it mostly-unrelated contracts.
  - **Structural conclusion:** cross-venue `event_subject` values derive from venue-specific tickers/slugs, so generic binary markets can never pass the subject gate. Only families with canonical subjects from `specialized_terms` (`atlas/normalization.py` — e.g. CPI's `us_cpi_yoy|period`) can ever produce a deterministic match. All 10 candidate events were Fed *personnel* markets, none were FOMC rate decisions — the one family where a canonical subject (`us_fomc_decision|meeting`) is well-defined and objective.
- [x] **FOMC-decision canonical family shipped — first cross-venue pair with both legs `GUARANTEED`** (2026-08-11). Overlap probe confirmed the same settled meeting on both venues: Kalshi `KXFEDDECISION-26JUL` / `KXFED-26JUL` (missed by `list_settled_events` paging — 20,000 recent settlements is too shallow to reach July 29) and Polymarket's per-meeting bps buckets (93 rate-phrased markets in 200 recent closed).
  - New `_fomc_decision_bucket_terms` in `atlas/normalization.py`: canonicalizes both venues' phrasings (`does a Hike of 25bps` / `increase interest rates by 25 bps after the July 2026 meeting`) to `us_fomc_rate_decision|YYYY-MM`, scope `fomc_rate_change_bucket`, direction (`hike`→`increase`, 0bps→`maintain`), threshold in bps with exact/open operators (`=`, `>`, `>=` for `50+`), `resolution_source=federal_reserve`, and **outcome-determining policy tokens only** from published text: `no_meeting=no_change_bucket` (Kalshi's canceled-meeting clause ≡ Polymarket's no-statement clause) and `rounding=up_nearest_25bps` (Polymarket only).
  - New `_complete_fomc_decision_policy` guarantee path in `atlas/settlement.py`: a per-meeting bucket with a published no-meeting fallback is `GUARANTEED` (`COMPLETE_FOMC_DECISION_BUCKET_POLICY`). Both real July legs assess `GUARANTEED` — the first time any cross-venue pair has had both legs guaranteed.
  - **Verified on the real settled pair** (K `KXFEDDECISION-26JUL-H25` outcome `no` ↔ PM July 25-bps outcome `no`): mismatches went from 8 (pure parser blindness) to exactly **1** — `SETTLEMENT_POLICY_MISMATCH`, because Polymarket publishes round-up-to-25 and Kalshi does not. That is a genuine published divergence (a hypothetical 12.5bps hike resolves PM-Yes / K-unspecified), so `REVIEW_REQUIRED` is the correct verdict; do not paper over it.
  - Safety verified: `>25bps` vs `50+ bps` do NOT cross-match (`25/>` vs `50/>=`); cumulative `rate cut by <deadline> meeting` contracts are not captured by the bucket trigger.
  - 8 new tests in `tests/test_fomc_decision.py` with the real venue texts frozen as fixtures; suite now **182 passing**, lint clean.
- [x] **Backfill reach closed — bounded Kalshi series-ticker scanning shipped** (2026-08-12):
  - `KalshiVenue.list_settled_events(series_tickers=...)` scans each explicit series (`/events?status=settled&series_ticker=X`, verified live against the API) with its own `SETTLED_SERIES_MAX_PAGES=5` budget before the recent-first scan, deduped by event ticker (`atlas/venues/kalshi.py`).
  - **Requested-series events bypass the lexical candidate gate** (`atlas/backfill.py`): live check showed the July decision event lexically matched only the "dissent" markets — `'Fed decision in July?'` shares just 2 tokens ({fed, july}) with `'…increase interest rates by 25 bps after the July 2026 meeting?'`, below the 3-token floor. Explicitly requested series now pair against the full (tag-scoped) final Polymarket pool and are prepended ahead of lexical candidates so the pair cap cannot crowd them out. Deterministic verification still decides every pair; all existing caps still bound the work.
  - CLI: `--kalshi-series-tickers` on `learning backfill` (default: none) and `backfill-batch`; batch and scheduled-monitor default is `BATCH_DEFAULT_KALSHI_SERIES_TICKERS=("KXFEDDECISION", "KXFED")`. Report/batch report expose `kalshi_series_tickers`, per-series `kalshi_series_event_counts`, and a `KALSHI_SERIES_EVENT_SCAN_EMPTY` blocker.
  - **Verified live on the real backfill path** (2026-08-12, tag `100196` + both series): series events reach the pool (`KXFEDDECISION=14`, `KXFED=5` of 20,067 scanned), and the July pair is now compared end-to-end — the exact-25 hike bucket (K outcome `no` ↔ PM `no`) and exact-25 cut bucket each verify to exactly one mismatch, `SETTLEMENT_POLICY_MISMATCH` (the rounding divergence), `REVIEW_REQUIRED` as required; all cross-bucket pairings are correctly refused on threshold/direction codes. Suite now **197 passing**, lint clean.
- [x] **KXFED upper-bound angle explored and shipped as a canonical family** (2026-08-12, all findings from live venue texts):
  - Landscape: Kalshi `KXFED` (per-meeting) and `KXFEDFUNDSYEAR` (year-end, starts end-of-2027) are strict `>X%` thresholds at 25bp strikes resolved off the Fed's published upper bound; Polymarket's only level-style event (`what-will-the-fed-rate-be-at-the-end-of-2026`, open, no Kalshi year-end-2026 counterpart) lists exact-level buckets (`=X%`) with `≥4.5`/`≤1.0` tails, anchored by published text to the Dec 2026 FOMC meeting with a Dec-31 snapshot fallback. **Polymarket publishes nearest-25bps/away-from-zero rounding on level markets too** — the rounding asymmetry is venue-systematic, not bucket-specific. Kalshi's Sep 2026 decision rules were also re-checked live: still no rounding terms.
  - New `_fed_funds_level_terms` (`atlas/normalization.py`): canonical subject `us_fed_funds_upper_bound|meeting:YYYY-MM-DD` / `|snapshot:YYYY-MM-DD` (anchors namespaced because meeting-time and Dec-31 snapshots are different published measurement events), operators `>`/`>=`/`<=`/`=` from published wording only, policy tokens `no_decision=year_end_rate_snapshot`, `rounding=nearest_25bps[_away_from_zero]`, `single_rate=target_rate_used`. Cumulative "hit X% before <date>" contracts are not captured.
  - New `_complete_fed_funds_level_policy` guarantee path (`atlas/settlement.py`, `COMPLETE_FED_FUNDS_LEVEL_POLICY`): snapshot anchor + published single-rate fallback, or meeting anchor + published no-decision fallback. Verified live: all 21 `KXFEDFUNDSYEAR-28JAN01` legs and all 15 PM end-of-2026 legs assess `GUARANTEED`; Kalshi per-meeting legs stay `UNKNOWN` (no published fallback — honest).
  - Verified live: Kalshi `KXFED-26DEC` and PM end-of-2026 markets share `meeting:2026-12-09` (165 cross-venue subject matches) and verify to `REVIEW_REQUIRED` on exactly the published divergences (`THRESHOLD_OPERATOR_MISMATCH` exact-level vs strict-threshold, `SETTLEMENT_POLICY_MISMATCH` rounding/fallback). 10 new tests with frozen real texts (`tests/test_fed_funds_level.py`); suite **207 passing**, lint clean.
- [x] **Live settlement-candidate discovery now sees the tag-scoped Polymarket Global open catalog** (2026-08-12): the December 2026 fed-funds level event has no US-gateway counterpart (verified live: 0 "upper bound" markets in 22,575 US open markets), so `pairs scan --live` now merges `PolymarketGlobalHistoricalVenue.list_open_markets` (bounded: `LIVE_GLOBAL_TAG_IDS`, 2 pages/tag) into the compatibility/queue computation **only** — global markets have no order books and can never reach shadow, approval, or paper-trading paths; a Gamma outage degrades to the US-only catalog. Verified live read-only: `KXFED-26DEC` × global open catalog ranks `us_fed_funds_upper_bound|meeting:2026-12-09` as `BLOCKED`/reachable with PM `GUARANTEED`, K `UNKNOWN`, honest mismatch codes. Report exposes `polymarket_global_open_markets`/`_tag_ids`.
- [x] **Cross-session handoff applied** (2026-08-12, coordinated with the parallel CPI-family session): `BATCH_DEFAULT_KALSHI_SERIES_TICKERS` now includes `KXCPIYOY` (verified live: `KXCPIYOY-26JUL` settled) and `BATCH_DEFAULT_GLOBAL_TAG_IDS`/`TARGETED_GLOBAL_TAG_IDS` now include CPI tag `101701` (verified live: 80/100 recent closed markets are CPI-family, including the settled July 2026 annual-inflation buckets). The live queue watches the same tags via `LIVE_GLOBAL_TAG_IDS`. Suite **210 passing**, lint clean. Note for a future gated review: `verify_equivalence`'s inverse detection is spread-only today, so an exact `≤X` vs `>X` complement on the same subject (CPI tails, fed-funds tails) still reports `THRESHOLD_OPERATOR_MISMATCH` rather than `APPROVED_INVERSE` — changing that is a verifier-rule change requiring the explicit review gate; do not make it casually.
- [x] **CPI YoY family closed to real venue texts — first real Polymarket legs to clear the CPI guarantee path** (2026-08-12, parallel session, coordinated with the fed-funds session):
  - Live probe found a settled cross-venue CPI overlap: Kalshi `KXCPIYOY-26JUL` (21 strict `>X` strikes, settled off the 3.4% July print released 2026-08-12) ↔ Polymarket "July Inflation US - Annual" (12 one-decimal buckets with `≤3.1`/`≥4.2` tails, PM tag `101701`, closed the same day). Kalshi also lists `KXCPI`/`KXCPICORE` MoM variants (not yet canonicalized).
  - **Key structural finding: both venues publish BLS one-decimal precision on CPI** (`precision=bls_one_decimal` extracted from both real texts) — the venue-systematic rounding divergence that blocks the fed-funds families does not exist here.
  - Normalizer (`atlas/normalization.py` CPI branch): the trigger now recognizes Kalshi's spelled-out windows ("in the twelve months ending July 2026", "for the year ending in July 2026"), with the widened alternatives gated on an explicit BLS mention so non-US annual-inflation contracts (IBGE, NBS) cannot be misfiled; new `_cpi_level_terms` reads Polymarket's postfix tails ("3.1% or less" → `<=`, "or more" → `>=`) and exact one-decimal buckets ("be 3.4%" → `=`) that the generic prefix patterns cannot parse; policy tokens are extracted faithfully from the real texts (`missing=previous_month_figures_at_next_release`, `precision=bls_one_decimal`, `delay=shutdown_extension_release_or_6m`); `revision_policy` is no longer inferred from the mere presence of other tokens; the BLS resolution source is accepted from the full agency name in rules text.
  - Guarantee (`atlas/settlement.py`): `_complete_cpi_release_policy` accepts the real Polymarket token set — a published terminal missing-data fallback (previous month's figures) plus the one-decimal precision clause → real PM CPI legs assess `GUARANTEED` (`COMPLETE_CPI_RELEASE_AND_MISSING_DATA_POLICY`). Kalshi's shutdown clause is a delay extension, not a terminal fallback, so Kalshi CPI legs honestly stay `UNKNOWN`.
  - **Verified on the real settled pair** (PM `≤3.1` outcome No ↔ K `>3.1` outcome yes — an exact logical complement on the same published value, with consistent settled outcomes): `REVIEW_REQUIRED` on exactly three honest codes — `SETTLEMENT_POLICY_MISMATCH` (divergent missing-data branch: PM previous-month figures vs Kalshi extension-only), `THRESHOLD_OPERATOR_MISMATCH` (inverse detection is spread-only today; see the gated-review note above), `SETTLEMENT_GUARANTEE_UNKNOWN` (Kalshi's absent terminal fallback). 7 new tests with frozen real texts (`tests/test_cpi_yoy.py`); suite **217 passing**, lint clean.
- [x] **Gated verifier review completed — threshold-operator complement inverses** (2026-08-12, user signed off via "proceed" after the gate was explicitly surfaced; reverse by removing the `_is_threshold_complement` branch in `atlas/verification.py`): `x > t` vs `x <= t` (and `x >= t` vs `x < t`) at the identical threshold now approve as `APPROVED_INVERSE` with relationship code `THRESHOLD_OPERATOR_COMPLEMENT`. Deliberately narrow: fires only when the ONLY mismatch is the operator (full settlement-policy token set, subject, period, unit, source all already equal), both `affirmative_outcome=predicate_true`, no `threshold_upper`, and the pre-existing both-legs-`GUARANTEED` gate still applies. Gap (`>` vs `<`) and overlap (`>=` vs `<=`) operator pairs are explicitly rejected. 5 new tests (`tests/test_threshold_complement.py`), including end-to-end into `_historical_label` (complementary outcomes → `CONFIRMED` trusted label; agreeing outcomes → `REJECTED`). **Verified live post-change: all 12 real July CPI pairs still `REVIEW_REQUIRED`, 0 approved** — the rule changes no current verdict; it removes the last Atlas-side obstacle so pairs auto-promote the moment venue texts align. Suite **222 passing**, lint clean.
- [x] **CPI reach verified end-to-end on the real backfill path** (2026-08-12, bounded live probes + read-only census after the push):
  - Bounded backfill with `--global-tag-ids 101701 --kalshi-series-tickers KXCPIYOY`: all 21 `KXCPIYOY` settled events reach the pool via the series scan (`kalshi_series_event_counts={'KXCPIYOY': 21}`), 100 tag-101701 final binaries, 26 shared events, 500-pair cap saturated, `EXTERNAL_EVIDENCE_BLOCKED`, `paper_only=true`, 0 labels. Two `KALSHI_EVENT_MARKET_FETCH_FAILED` blockers were recorded as bounded results, not hangs.
  - Full-event census (read-only, `KXCPIYOY-26JUL` × the 100 tag-101701 finals = 2,100 pairs through `verify_equivalence` on the real adapter path): **all 2,100 `REVIEW_REQUIRED`, 0 approved** — the threshold-complement rule flips nothing on real data. The headline settled tail pair (K `>3.1` outcome `yes` ↔ PM `≤3.1` outcome `no`) verifies to exactly `[SETTLEMENT_GUARANTEE_UNKNOWN, SETTLEMENT_POLICY_MISMATCH, THRESHOLD_OPERATOR_MISMATCH]`, matching the frozen-text pins in `tests/test_cpi_yoy.py`. The 448 same-event exact-bucket pairs match on subject/period/scope/source and refuse only on threshold terms plus the same two policy gaps.
  - August is already armed: `KXCPIYOY-26AUG` is open on Kalshi and "August Inflation US - Annual" is open on Polymarket with the identical rules template (verified live: same one-decimal precision clause, same previous-month fallback, tails at `≤2.9`/`≥4.0`), so each monthly release re-creates the frontier pair with no new code.
- [x] **CPI sibling families shipped — headline MoM, core YoY, core MoM from real venue texts** (2026-08-12, all texts captured live):
  - New `_cpi_family_terms` (`atlas/normalization.py`) canonicalizes all four US CPI variants with distinct subjects (`us_cpi_mom`, `us_cpi_core_yoy`, `us_cpi_core_mom`) so families can never cross-match; YoY requires published window markers (`12[- ]month|yoy|twelve months|year ending`), month-anchored change buckets without them are MoM. Signed thresholds from published wording only: Kalshi's signed strikes (`more than -0.1%` → `-0.1`/`>`, generic patterns extended with `more than` + signed capture) and Polymarket's verb-signed monthly buckets (`decrease by 0.7% or more` → `-0.7`/`<=`, `stay flat (0.0%)` → `0.0`/`=`) via `_cpi_change_terms`, which runs before the postfix level patterns so signs cannot be dropped.
  - Honest venue-text gaps preserved, not inferred away: Kalshi headline-MoM rules name only a "Source Agency" (source stays unset → `RESOLUTION_SOURCE_MISMATCH`) and state no adjustment basis (scope `cpi_mom` vs PM's published `cpi_mom_seasonally_adjusted` → `CONTRACT_SCOPE_MISMATCH`). `KXCPICORE` publishes both (BLS + seasonally adjusted), so core-MoM scopes match across venues. Guarantee path accepts only scopes with a published adjustment basis; PM sibling legs assess `GUARANTEED`, Kalshi legs stay `UNKNOWN` (extension-only fallback).
  - **Verified live on the real adapter path: the settled July core-MoM tail pair (K `KXCPICORE-26JUL-T0.0` `>0.0` outcome `yes` ↔ PM "Core CPI MoM 0.0% or less" outcome `no`) is a second exact operator complement, blocking on exactly the same three codes as the headline YoY pair** (`SETTLEMENT_GUARANTEE_UNKNOWN`, `SETTLEMENT_POLICY_MISMATCH`, `THRESHOLD_OPERATOR_MISMATCH`). Every monthly BLS release now yields at least two settled complement pairs (headline YoY tail + core MoM tail) waiting only on the same two venue-text gaps.
  - Batch defaults now scan `KXCPI` + `KXCPICORE` too (`atlas/cli.py`, pins updated). 9 new tests with frozen real texts (`tests/test_cpi_variants.py`); suite **231 passing**, lint clean.
- [x] **Independent adversarial review of the CPI extension + hardening shipped** (2026-08-12): a review agent hunting false-approval paths in commit `f0d151c` confirmed three constructed-but-plausible routes to a false `APPROVED_EQUIVALENT` (loose MoM trigger absorbing unrelated markets that mention CPI; unmodeled directional verbs read unsigned, colliding opposite buckets; the widened generic "more than" pattern letting shared boilerplate shadow real strikes) plus a guarantee bypass (misfiled `cpi_*` legs reaching `GUARANTEED` via the generic yes/no-fallback grant) and jurisdiction-gate holes (bare word "us"; unlisted countries). All fixed the same day: CPI now runs after the more specific macro families; the MoM trigger requires CPI-reference + change-verb + percent in proximity; `_cpi_change_terms` parses titles first (sibling-enumeration immunity), models the full verb vocabulary signed (rise/fall/drop/decline/gain/climb, more-than/at-least/or-more/exact), and fails safe to no-threshold on any unmodeled directional phrasing; generic "more than" reverted (its job moved into the CPI parser); specialized macro scopes (`cpi_*`, `fomc_rate_change_bucket`, `fed_funds_upper_bound_level`) can now earn `GUARANTEED` only through their own complete-policy path (`FAMILY_POLICY_INCOMPLETE` otherwise); US marker requires the dotted form; foreign list extended. Also fixed in passing: the pre-existing YoY verb-signed collision (venue-agnostic since parent commits). 10 adversarial regressions pinned in `tests/test_cpi_hardening.py`; all real July pairs re-verified live post-hardening (same three honest codes, signed strike intact). Suite **248 passing**, lint clean.
- [x] **Gap radar + $2k paper bankroll meter shipped** (2026-08-12): new `atlas/gap_radar.py` — a paper-only measurement instrument that pairs OPEN cross-venue markets by canonical twin shape (identical subject/scope/action/affirmative-outcome/threshold/unit with identical or exact-complement operators), prices locked baskets from executable top-of-book quotes (Kalshi ask+size fields; Gamma bestAsk/bestBid with NO derived as 1−bid, fill-at-quote recorded as an assumption), applies a recorded 2¢/basket fee buffer, and persists every observation with `paper_only`, `trusted=false`, `pair_kind=CANDIDATE_TWIN_SHAPE_NOT_PROVEN`, and the live `verify_equivalence` status + mismatch codes. CLI `gaps scan --live` / `gaps status`; `/api/overview` `gap_radar` block; dashboard `#gap-bankroll` meter + recent-gaps panel. The bankroll summary answers the original $2k→$20k question with data: one opportunity per pair per UTC day, $100/opportunity cap, Kalshi-size cap, all assumptions recorded in the payload.
  - **First-scan phantom caught and pinned:** the initial matcher ignored `affirmative_outcome`, pairing Kalshi hike-25 with Polymarket CUT-25 buckets (same subject/threshold/operator, opposite economics) and reporting phantom 30¢ "gaps". Fixed (direction/scope/action equality required), the 19 polluted observations purged, regression pinned (`test_opposite_direction_fomc_buckets_never_pair`). A live trader chasing those "gaps" would have lost both legs — the paper-first discipline doing exactly its job.
  - **First clean live scan** (2026-08-12): 223 Kalshi open series markets × 521 tag-scoped PM open markets → 10 twin-shaped pairs, 2 executable gaps of exactly 2¢ each (far-month Fed decision pairs, thin books), paper bankroll `$2001.50`. Honest baseline: real gaps exist and they are pennies. Suite **259 passing**, lint clean.
- [x] Taxonomy hygiene fixed (2026-08-12): the whole CPI family is now jurisdiction-gated — contracts naming a non-US jurisdiction (UK, China, Brazil, IPCA/IBGE/ONS/NBS, ...) without any US/BLS marker fall through to generic terms instead of being filed under `us_cpi_*` subjects (`_CPI_FOREIGN_JURISDICTION`); the unhyphenated "12 month period" window variant is also recognized. Pinned by `test_foreign_cpi_with_unhyphenated_window_is_not_misfiled`. No approval risk existed either way (such pairs were already refused on source/guarantee).
- [x] The continuous monitor (`pairs watch --live`) was NOT running at the 2026-08-12 process check despite the state section above; restart it (single instance, per the one-monitor rule) so live queue tracking and scheduled bounded backfills resume. Restarted 2026-08-14, and again 2026-08-17 to load the shared-catalog fix.
- [ ] **CPI remains the closest frontier to the first trusted label.** Remaining gaps are now venue-text only: (1) the divergent missing-data fallback branch (PM previous-month figures vs Kalshi extension-only) — a real published divergence, keep watching for text alignment, do not paper over it; (2) Kalshi publishes no terminal missing-data fallback on CPI, so its leg stays `SETTLEMENT_GUARANTEE_UNKNOWN`. If a future Kalshi CPI rules revision publishes a terminal fallback matching PM's, the settled monthly tail pair approves automatically (`tests/test_threshold_complement.py` pins exactly that scenario). Every monthly CPI release creates a fresh settled overlap; the scheduled batch watches `KXCPIYOY` + tag `101701` automatically.
- [x] **DECIDED 2026-08-13 ("yes on the rounding") and implemented the same day — first three trusted labels minted.** PM's no-change bucket now canonicalizes (maintain/0/`=`), `_is_fomc_preimage_equivalent` approves decision-bucket pairs whose Yes-sets over the raw rate change are identical under each leg's own published rounding (scope-gated, both legs `GUARANTEED`, non-rounding policy tokens equal, 25bp-grid thresholds only), the semantic-flip test is renamed and pinned, and the ±25/level counterexamples plus the Kalshi C26 dropped-">" quirk are pinned in `tests/test_fomc_preimage.py`. Suite **266 passing**, lint clean. Original proposal follows: Full evidence memo: `docs/decisions/2026-08-12-fed-rounding-preimage-equality.md` (2026-08-12, verified live). Summary: three of five per-meeting decision pairs (maintain, both >25bps tails) are provably outcome-identical for every possible raw Fed move under each leg's own published policy — no inference — IF Polymarket's "rounded up to the nearest 25" is read as ceiling-in-magnitude; the exact-±25 pairs and the whole level family are provably divergent and stay `REVIEW_REQUIRED` under every option. If signed off, the already-settled July 2026 meeting yields up to three `APPROVED_EQUIVALENT` pairs — the first trusted labels. Two decision-independent fixes also identified: PM's "no change" bucket is not captured by the FOMC normalizer (pure parser gap), and Kalshi's Cut->25bps rules text drops the ">" (needs a defensive regression test).
- [ ] **Fed-funds families' remaining gap — the venue-systematic rounding divergence:** Polymarket publishes nearest-25bps rounding on both its decision buckets and level markets; Kalshi publishes no rounding terms on either. Decide the policy question (does published-vs-unpublished rounding block equivalence permanently?) or wait for a text revision — the scheduled batch watches Sep/Oct/Dec meetings automatically, and the level family will auto-promote pairs if Kalshi ever publishes rounding/fallback terms or either venue lists a strike/operator-aligned level contract (e.g. a PM `≤X` tail at a Kalshi `>X` strike on the same anchor would be an exact logical inverse — except PM's rounding still applies today).

- [x] **Live queue reach closed + four future Fed meetings queued** (2026-08-13): `pairs scan --live` now merges the bounded Kalshi open-series scan (`list_open_series_markets`, same `BATCH_DEFAULT_KALSHI_SERIES_TICKERS`) into the discovery pool — the recent-first open catalog had the same sports-flood reach gap as the settled scan. Verified live: the settlement rankings now lead with `us_fomc_rate_decision` 2026-09/-10/-12/2027-01 in `OPEN_AWAITING_SETTLEMENT` with next gate `WAIT_FOR_BOTH_TERMINAL_OUTCOMES` (verification + guarantees already passed under the signed-off rule — each meeting is a queued future label batch), followed by all five August CPI variants, KSFO weather, and the Dec fed-level overlap as reachable-blocked. Sports dead-ends no longer occupy the frontier.
- [x] **DECIDED 2026-08-13 ("yes, proceed") and implemented — first five REJECTED labels minted, label mix complete.** Invariant wording amended (AGENTS.md/README); `_historical_label` mints evidence-backed `REJECTED` only for same-canonical-subject review pairs with divergent terminal outcomes; approved-first/complement-first priority sort; `REVIEW_REJECTIONS_PER_EVENT=5` cap enforced on the real run (exactly 5 minted from the July CPI event: K `>4.6`..`>5.0` `no` × PM `=3.4` `Yes`). Trusted labels now 3 `APPROVED_EQUIVALENT` + 5 `REJECTED`; backfill status `MILESTONE_COMPLETE`; readiness blocker reduced to volume only ("need 42 more"). Suite **271 passing**, lint clean. Original proposal follows: Full memo: `docs/decisions/2026-08-13-rejected-labels-from-review-pairs.md`. The proposal conflicts with the LETTER of the AGENTS.md hard invariant ("REVIEW_REQUIRED ... must never become trusted labels") while serving its spirit (no uncertainty laundering; the trust comes from terminal settlement evidence disproving equivalence). Amending hard-invariant text is owner-only. Proposed gate: identical canonical subject + REVIEW_REQUIRED + both-terminal divergent outcomes + full provenance + max 5/event. Without this (or some other REJECTED source), the balanced 50-label dataset can never exist and the learning loop stays blocked forever.
- [ ] Original mapping note — **path to the first REJECTED label (balanced-dataset milestone):** `_resolution_label` (`atlas/validation.py`) mints `REJECTED` when a reconciled pair's settled outcomes diverge under its recorded hypothesis — including non-approved pairs — but the backfill's resolved-pair path only reconciles pairs that cleared deterministic verification, and `validation_cases` is empty, so the review-pair divergence route is dormant. The honest recurring source exists: every monthly CPI tail pair (REVIEW_REQUIRED, inverse-consistent settled outcomes → divergent under the equivalence hypothesis) is a natural evidence-backed REJECTED. Wiring review-pair reconciliation into label creation touches the most sensitive path in the system — design it deliberately (which review pairs qualify, provenance, no self-training leakage) before implementing.

- [x] **History harvest completed 2026-08-13 — the settled archive is fully extracted at 34 trusted labels** (8 `APPROVED_EQUIVALENT` + 26 `REJECTED`, `need 16 more`): four progressively deeper bounded passes (candidate-events up to 40, PM global pages up to 6, pair cap 3000, resolved cap 60 per run) over both macro families. The final deepest pass minted zero new labels — honest convergence, not a cap artifact (rejection caps fired 59+ times across runs doing exactly their bounding job; label dedup held across repeated passes). The remaining 16 labels arrive from the calendar: up to ~3 approvals per FOMC meeting (~8/year) plus capped rejections and potential approvals each monthly CPI release, all watched by the scheduled defaults.

- [x] **Runway research completed (2026-08-13, three read-only census agents) + crypto label pipeline shipped.** Findings, all evidence-backed with verbatim venue texts:
  - **TIME-SENSITIVE: Kalshi prunes settled market details from its public API after ~6 weeks** (verified: events older than ~Jul 2 return zero markets — this is what the `KALSHI_EVENT_MARKET_FETCH_FAILED` blockers were). Deep archives are unharvestable; label flow is calendar-driven plus a trailing ~6-week window. July settlements for U3/payrolls/ISM/GDP-Q2/PCE-June age out soon — harvest each family as soon as its normalization lands. Also: capture election rules/outcomes live before pruning ahead of Nov 2026.
  - **Ranked next families:** (1) **Unemployment U3** (KXU3 × PM monthly; Kalshi names BLS + seasonal adjustment, closing the source/scope gaps that block headline-CPI pairs; complement + grid-preimage approval shapes pending the gated rules; ~5 rej + up to 2 approval-candidates/month; low effort — subject partly exists). (2) **ISM manufacturing + services** (2 events/month; Kalshi "at least 57" ≡ PM "57.0+" is a DIRECT approval shape needing no new verifier rule; ~10 rej + 0–2 appr/month; low effort). (3) Payrolls (~5 rej/month, boundary divergence — no approvals). (4) Core PCE MoM (CPI-shaped complement, young overlap). (5) GDP (quarterly). Dead: jobless claims (PM discontinued after 4 weeks — watch tag 103678 for revival), retail sales (no PM markets). Next releases: ISM Sep 1/3, jobs report Sep 4.
  - **Crypto shipped as a REJECTED-label source** (`test_crypto_overlap.py`, suite 280): PM Binance/Chainlink source tokens + noon-ET candle anchor (`_candle_anchor_timestamp`) make cross-venue crypto pairs same-subject; sources/windows are permanently different so they can NEVER approve (pinned); `CRYPTO_REVIEW_REJECTIONS_PER_RUN=10` bounds the flood on top of the per-event cap. Deliberately NOT in scheduled batch defaults — harvest explicitly with `--global-tag-ids 21 --kalshi-series-tickers KXBTCD,KXETHD`. Crypto is NOT wired into the gap radar: census verdict is that cross-venue crypto gaps are index-basis risk, not arbitrage (three mutually different sources, adjacent windows, $0.01-offset grids); a clearly-labeled basis monitor is possible later if the terminal product wants it.
  - Monitor loop now also runs a gap-radar sweep every interval (`watch_pairs`), so Checkpoint-A evidence accrues ~288 readings/day once the watcher runs.
- [x] **PM-US adapter FIXED and verified live** (2026-08-13, `atlas/venues/polymarket_us.py`, suite 282): closed sweep now sends `closed`-only + `orderBy=id&orderDirection=desc` and terminates only on an empty page (regression-pinned in `tests/test_venue_adapters.py`); open-leg sweep likewise empty-page-terminated; new `list_markets_by_slugs` targeted-lookup helper. Verified live through the fixed adapter: all four targeted macro twins fetched, including the settled July FOMC no-change leg. Two discoveries from the real payloads:
  - **The August CPI pair on PM-US is an IDENTICAL-STRIKE twin** (PM-US `gt2pt9pct` "Above 2.9%" ↔ Kalshi `>2.9%`: same grid, same strict operator, no rounding divergence) and the PM-US CPI text fires the ORIGINAL guarantee tokens (`revision=first_official_release|missing=first_within_3m_else_previous_month` → guarantee-complete leg). Remaining honest gaps to approval are Kalshi-side only (no terminal fallback; no published revision policy → `REVISION_POLICY_MISMATCH`, `SETTLEMENT_POLICY_MISMATCH`). This is the closest-to-approval recurring shape in the system, on the tradeable venue, settling monthly.
  - **PM-US FOMC markets publish a DIFFERENT rounding rule** than PM-Global ("changes smaller than the smallest option of the same direction will be rounded to that smallest option, and changes greater … rounded to the NEAREST displayed option…" — capture the full clause verbatim before tokenizing). The signed-off ceiling-reading preimage table does NOT transfer: only the maintain pair remains provably rounding-immune under nearest-rounding; the tails do not. Needs: a `_fomc_decision_bucket_terms` variant for the PM-US phrasing ("decreases the upper bound … by 25 basis points at the {Month} {Year} FOMC meeting", "does not change the upper bound" maintain form), a DISTINCT rounding token, and a new preimage analysis with owner sign-off before any PM-US FOMC approval. The settled July no-change pair (K H0 `yes` ↔ PM-US `settlement=1`) is the first approval candidate once that lands.
- Original census findings (context for the fix above): **the Polymarket US "emptiness" was our adapter's bug, and the tradeable venue has twin-grade overlap TODAY** (census 2026-08-13, all verbatim-verified live): gateway.polymarket.us actually lists **21,360 open markets / ~400k closed**, including full FOMC-decision bucket ladders (Sep + Oct 2026), CPI YoY strike ladders (August event created 2026-08-13), NFP/U3/GDP, and BTC ladders — with **full unauthenticated order books** (7×11 depth levels, openInterest, settlementPx; exactly what the arbitrage simulation needs) and settled twins (July FOMC resolved, settlement endpoint returns the evidence shape `get_terminal_settlement_evidence` parses). Why we saw 90 markets: (A) the closed-leg filter `active=false&closed=true` selects only manually-DEACTIVATED markets — resolved markets keep `active: true`; (B) short-page termination truncates mid-stream (short page ≠ end on this gateway); (C) bare `orderDirection=desc` is silently ignored without `orderBy=id`; (D) the open-leg 2,000-event cap hides the newest events, where fresh macro ladders land on release days. Fixes: closed sweep `closed=true&orderBy=id&orderDirection=desc&limit=500`, stop on EMPTY page; targeted slug lookups for macro backfill (`rdc-usfed-fomc-{date}-{bucket}`, `cpic-uscpi-{month}-yoy-{release}-gt{X}pt{Y}pct`); raise/paginate the open-event cap; `category`/`tag` params are silently ignored — filter client-side. Normalizer variants needed: PM-US FOMC phrasing uses "basis points" (not "bps") and "at the {Month} {Year} FOMC meeting" + publishes a rounding rule (capture verbatim first); PM-US CPI text fires the existing triggers and publishes a first-print revision freeze (Kalshi is silent — honest new divergence). Payoff: certified twins on the US-REGULATED venue with real books — labels, radar coverage at true depth, the first live-pair candidates for the existing shadow/paper machinery, and a materially different Checkpoint-B posture. Caveat: no official gateway docs exist; all param semantics are empirically probed and could change.
- [x] **U3 + ISM families shipped in parallel worktrees, merged, harvested — 38 of 50 trusted labels** (2026-08-13, suite **307 passing**, lint clean): `_unemployment_rate_terms` (U-3 proximity marker, published-basis scope, PM revision-freeze/terminal-fallback/precision tokens, Kalshi's empty policy left visible) + `_ism_pmi_terms` (published index-phrase trigger, manufacturing/services subjects that never cross-match, `_UNMODELED_BUCKET` fail-safe, expansion-boilerplate guards) with guarantee paths `COMPLETE_UNEMPLOYMENT_RELEASE_POLICY` / `COMPLETE_ISM_RELEASE_AND_MISSING_DATA_POLICY` (PM legs qualify on published tokens; Kalshi legs honestly UNKNOWN — services even resolves via Trading Economics on the event level, preserved as-is), both scopes blocked from the generic binary-fallback grant. 25 new frozen-real-text tests. The ISM identical-threshold pair (K "at least 57" ≡ PM "57.0+") verifies to exactly `[SETTLEMENT_POLICY_MISMATCH, SETTLEMENT_GUARANTEE_UNKNOWN]` — every rule term matches; only Kalshi's unpublished terminal fallback blocks approval. Harvests minted evidence-backed rejections from the July U3 and June/July ISM events (label count 34 → 38; caps bound the rest; PM-final pool now ~1,900/run through the fixed US adapter). Remaining 12 labels: September releases (ISM Sep 1/3, jobs Sep 4, CPI Sep 11, FOMC Sep 16) each mint fresh batches automatically via the running monitor.
- [ ] Crypto harvest mechanics, honest first result (2026-08-13): the first bounded run minted 0 labels — 24 hourly Kalshi events/day drown the single noon overlap event, so the 30-candidate budget never selected a noon event, and only 36 of 200 recent closed PM tag-21 markets were final binaries (mostly non-daily families). The parser-level pipeline is proven by frozen-text tests; to actually mint, candidate selection needs a noon-anchor preference (or a subject-aware candidate rank). Low priority vs U3/ISM — rejections-only yield.

- [x] **Options B + C shipped in parallel worktrees and merged (2026-08-13, suite 330)**: (B) `--kalshi-event-ticker-filter` — explicit-harvest regex scoping the requested-series prepend (e.g. `12$` for noon-ET crypto events); verified live (hour codes confirmed) but crypto still minted 0 — the PM daily finals never reach the tag-21 recent pool (5-minute micro-markets cover <2h of catalog per 200 rows) and the 188-strike ladders blow the pair cap before prioritization; crypto label flow needs a Gamma slug-targeted daily-final lookup (flagged as future work, low priority). (C) `PolymarketUSVenue.list_event_markets` + `--polymarket-us-event-slugs` — the targeted door to settled US-venue ladders; sandbox harvest minted 5 REJECTED from PM-US July CPI (identical-strike pairs, zero false approvals); main-vault June run fetched all 11 June markets but minted 0 under caps (July's event already holds its 5 per the signed cap). **Cross-run cap-compliance note:** the signed decision caps rejections at 5 per shared settled event; the implementation caps per RUN — cross-run accumulation on one event can exceed the memo bound (July us_cpi_yoy holds 5 global-venue + would accept 5 more US-venue pairs). Persisted per-event seeding should close this gap before further same-event harvests; until then, avoid re-harvesting events that already hold 5.
- [x] Vault at **38/50** (8 approved + 30 rejected) as of 2026-08-13. Superseded 2026-08-14: **milestone complete at 52** (8 approved + 44 rejected) — see below.

## 2026-08-14 — balanced-dataset milestone completed (52/50)

- [x] **Per-event rejection cap made cross-run** (`AtlasStore.review_rejection_counts_by_subject` seeds `review_rejections_by_event` in `atlas/backfill.py`): the owner-signed 5-per-event bound previously reset each run; `us_cpi_yoy|2026-07` had already leaked to 6 via exactly that hole (5 global + 1 US-venue). Existing over-cap events now refuse further rejections; 2 regression tests in `tests/test_backfill.py`.
- [x] **Three release families shipped in parallel worktrees from verbatim texts captured 2026-08-14** (all July/June/Q2 settled details harvested before Kalshi's ~6-week pruning): nonfarm payrolls (`us_nonfarm_payrolls|YYYY-MM`, raw-jobs unit, 19 tests), core PCE MoM+YoY (`us_pce_core_*|YYYY-MM`, BEA source, 15 tests — also fixed a live misfiling where Gamma core-PCE YoY texts satisfied the CPI YoY trigger and landed under `us_cpi_core_yoy`), and real GDP growth (`us_real_gdp_growth|YYYY-Qn:vintage` with estimate-vintage anchors, 20 tests). Guarantee paths accept only fully published branch sets (payrolls has no precision clause anywhere, so its complete path is revision+fallback only); all three scopes are blocked from the generic yes/no-fallback grant. Captured texts archived in the session scratchpad (`macro_texts.json`).
- [x] **Backfill pair cap now truncates the PRIORITY-sorted pair list** (`atlas/backfill.py`): verification always ran on every constructed pair before the cap, so arrival-order truncation only crowded labelable pairs out of the labeling window (observed live: 3000/3000 inconclusive on the first payrolls/GDP harvest while the slug-targeted twins sat beyond the cap). Also `_label_priority` gained a tier: approved(0) → complement-shaped review(1) → same-canonical-subject review(2, the only other REJECTED-capable shape) → unrelated(3).
- [x] **Harvests minted 14 labels** (38 → 52; export + manifest refreshed automatically; `training_ready=true`): 4 REJECTED from June payrolls (PM-US ">=" vs Kalshi ">" boundary shape, divergent settled outcomes), 5 REJECTED from Q2 GDP capped at the signed bound (the realized 1.5% exact-print divergence — Kalshi "above 1.5" No vs PM-US "at least 1.5" Yes — is the family's defining specimen), and 5 REJECTED from a same-subject tennis event (see review flag below). July payrolls identical-strike pairs agreed on outcomes → correctly inconclusive (agreement proves nothing); their mismatch codes are purely Kalshi's unpublished revision/missing-data clauses.
- [x] **RESOLVED 2026-08-14 (owner direction: treat sports negatives as an asset): keep them and control the mix via family tagging.** Sports rejections are evidence-backed HARD negatives — the candidate matcher itself selected the pairs as lexically similar, and sports floods the live queue daily, so a learner that has seen them can auto-triage the dominant junk class before it consumes bounded verification capacity. The risk was never the labels; it was curriculum imbalance. Shipped: `example_family()` in `atlas/learning.py` (deterministic, fingerprint-derived: economic/sports/crypto/election/weather/other), a `family` key on every exported JSONL row, and a `label_families` mix in the manifest (current: economic 8 approved + 44 rejected; sports 10 rejected). Training experiments weight/stratify per family at experiment time — no labels discarded, per-event cap still bounds accumulation.
- [ ] Core PCE Gamma legs are normalizer-ready but harvest-unreachable: no Gamma tag ID is known for PCE and the Gamma adapter has no slug-targeted lookup (same gap already flagged for crypto daily finals). The settled June KXPCECORE × Gamma overlap ages out ~mid-September — a small `list_event_markets`-style Gamma addition would harvest it in time.
- [x] Scheduled defaults now watch all three families (`BATCH_DEFAULT_KALSHI_SERIES_TICKERS` += `KXPAYROLLS`, `KXPCECORE`, `KXGDP`); PM-US targeted slugs for reruns: `usnfp-sa-july-2026-08-07`, `uschange-gte-june-2026-07-02`, `us-saa-q2-2026-07-30` (August events `usnfp-sa-august-2026-09-04` and the September calendar arrive via the monitor).
- [x] Continuous monitor restarted 2026-08-14 (detached with nohup, single instance) after being found down at the process check; API serving on port 8010 with `trading_enabled=false`.
- [x] **PM-US FOMC rounding analysis completed (owner-authorized "2 - yes", 2026-08-14) — no approval possible today, and that verdict is the venue's texts, not Atlas** (`docs/decisions/2026-08-14-pmus-fomc-rounding.md`): the settled July event publishes NO rounding clause and NO no-meeting fallback (the census's rounding quote belongs to the Sep/Oct open events), so the PM-US leg stays `UNKNOWN` and the settled no-change pair is honestly inconclusive. Shipped: PM-US decision-bucket phrasings in the normalizer, the distinct `rounding=nearest_bucket_away_from_zero` token, `_fomc_preimage` hardening (unrecognized rounding tokens refuse instead of reading as unrounded), 7 frozen-text pins (`tests/test_fomc_pmus.py`). Future trigger: a PM-US no-meeting clause + a new signed decision would make the maintain pair approvable (preimages provably {0}={0} under the PM-US scheme).
- [x] Gamma slug-targeted harvest door shipped (`PolymarketGlobalHistoricalVenue.list_event_markets`, `--polymarket-global-event-slugs`) and the June core-PCE overlap harvested through it before pruning: 5 REJECTED from `us_pce_core_mom|2026-06`. Vault at **62** (8 approved + 54 rejected).
- [x] **Gap radar upgraded for the growth-rate question — fractional staking, wider universe, release-burst cadence** (2026-08-14, suite **403 passing**, lint clean; paper-only invariant untouched):
  - **Fractional staking**: the $2k meter now stakes `STAKE_FRACTION = 5%` of the current bankroll per opportunity (`atlas/gap_radar.py`) instead of the flat $100 cap, so the meter measures compounding rather than arithmetic accumulation; 5% of the $2,000 start equals the old cap, so recorded history reads identically today. The Kalshi displayed-size cap still binds every stake, and the assumptions block records `stake_fraction_of_bankroll` + `stake_capped_by_kalshi_displayed_size`. Compounding regression pinned in `tests/test_gap_radar.py`.
  - **Wider radar universe, decoupled from batch defaults**: new radar-only scopes `GAP_RADAR_KALSHI_SERIES_TICKERS` (batch defaults + `KXU3`, `KXISMPMI`, `KXUSISMSERV`) and `GAP_RADAR_GLOBAL_TAG_IDS` (Fed Rates 100196, CPI 101701, Inflation 702, jobs 993, unemployment 1624, GDP 370, ISM 105113, PCE 105533) in `atlas/cli.py`, all verified against live catalogs 2026-08-14; Elections/House dropped from the radar (margin spreads can never form twin shapes). First live scan through the wider scope: 400 Kalshi × 478 PM markets, **17 twin-shaped pairs across 9 subjects** — U3, ISM manufacturing (the direct-approval shape), and GDP Q3-advance joined FOMC/CPI on the radar immediately. Scope pinned in `tests/test_cli.py`.
  - **Release-calendar burst mode**: new `atlas/release_calendar.py` (hardcoded UTC release instants for ISM/jobs/CPI/FOMC through Dec 2026, windows −10m/+50m) and `_burst_aware_sleep` in the monitor loop — inside a window the read-only radar scan runs every 30s instead of every 5m; the full pair scan and backfills stay on the base cadence. Cadence-only by construction (a stale entry changes scan frequency, nothing else); CPI entries beyond September must be added from the published BLS schedule. Tests in `tests/test_release_calendar.py`.
  - **Discovery note for the label pipeline**: Gamma tag `105533` (PCE) exists and carries the open core-PCE buckets — the "no Gamma tag ID is known for PCE" gap above is closed for future harvests; jobs tag `993` also carries the monthly unemployment-rate buckets.

## 2026-08-19 — the 90-day study is formalized and running

- [x] **Charter adopted**: `docs/NINETY_DAY_STUDY.md` — 2026-08-19 → 2026-11-17, frozen-rules
  policy (rule changes need the existing owner gate PLUS an amendment note; study baseline commit
  `47adf0c`), metric definitions shared verbatim with `atlas/study.py`, the external review's
  go/no-go thresholds as the day-90 decision rule, and the phase-2 latency-simulation spec (due by
  day 31; needs burst sampling around detected gaps — the 5-minute sweep cannot resolve sub-second
  decay).
- [x] **Instrumentation**: `atlas/study.py` + `atlas gaps study` compute the weekly report from
  persisted observations only (regenerable bit-for-bit), writing dated JSON to `data/study/`;
  `com.atlas.study` runs it Mondays 07:00. 8 tests pin the definitions. Two of my own first-draft
  metrics were dishonest and are fixed with pins: the rate window now spans the retroactive
  observations (a week of data on study day 1 must not be divided by one day), and the go test
  counts VENUE-TEXT-ONLY opportunities (the charter's "verified" precursor), never raw candidates.
- [x] **Day-1 report on real data** (14,677 observations, window 2026-08-12→): 21 candidate
  opportunities, **17 venue-text-only**, verified rate 63.8/30d vs threshold 10, median survival
  **129 minutes** across 21 runs (12 single-sweep), median executable size 33 contracts. Caveat
  recorded in-report: 14,643 of the rows carry the legacy flat-buffer fee model — the first clean
  all-venue-fee week lands 2026-08-26.
- [x] Public repo synced: all local work through the fee model is pushed (`7dd9c66..47adf0c`).

## 2026-08-19 — gap radar fees are now venue-published, not a flat buffer

- [x] **External viability review's fee critique verified and fixed.** The flat 2c/basket buffer
  understated fees exactly where gaps look best: both venues charge quadratic taker fees peaking at
  50c (Kalshi ceil(0.07 x C x P x (1-P)), its /series endpoint publishes fee_type=quadratic,
  fee_multiplier=1 for every default macro series — verified live; Polymarket publishes a
  per-market feeSchedule on the Gamma payload — economics rate 0.05, takerOnly, verified live on
  the tracked macro markets, formula confirmed against the venue's help center). Real cost near
  mids is ~3c/basket, not 2c; at tails it is well under 2c.
- [x] Encoded in `atlas/gap_radar.py`: per-leg fees from the published schedules, recorded on every
  basket (`kalshi_fee`, `polymarket_fee`, `polymarket_fee_basis`). Conservative-by-construction:
  Kalshi ceil applied per CONTRACT (venue ceils per order — ours can only overstate), fees-disabled
  Polymarket markets are free, and a fee-enabled market missing its schedule takes the maximum
  published rate so an absent field can never flatter a gap. 4 fee pins + updated gap-math tests
  (the per-leg model flips which basket is best when a leg trades near $1); suite **469 passing**.
- [x] First live scan under real fees: 17 twin-shaped pairs, **3 executable gaps (2.2c, 4.4c,
  4.0c)** on far-2027 FOMC and Q3-GDP tails — tail-priced legs pay near-zero quadratic fees, so the
  honest model shows MORE edge there than the flat buffer did, while mid-priced "gaps" now clear a
  higher, truthful bar. Historical observations keep the fee model they were recorded under.
- [ ] Remaining fee-model caveats, recorded not hidden: Kalshi fee_multiplier is read as 1
  (verified for the default macro series, not fetched per market at scan time); maker-side pricing
  (both venues charge takers only on our tracked families) would apply if the radar ever modeled
  resting orders; PM-US gateway fees are unmodeled because the radar prices Gamma quotes.

## 2026-08-19 — bug-hunt hardening pass (streams, settlement polling, API, storage, dead code)

- [x] **Streams NO-side complement fixed**: the Kalshi stream's NO-book handling no longer derives
  the wrong side of the complement, so streamed NO quotes match what the REST book reports.
- [x] **Sequence-gap resubscribe**: a detected orderbook sequence gap now triggers a resubscribe
  instead of silently continuing on a stale book.
- [x] **Settlement-polling backoff is actually active**: the bounded retry/backoff metadata is now
  enforced with coherent retry and exhaustion semantics (previously recorded but not honored).
- [x] **`GET /api/overview` made read-only + cached**: the overview no longer mutates state on read,
  and the expensive gap-observation aggregation is cached briefly instead of recomputed per poll.
- [x] **Storage hardening**: init-once schema setup, indexes on the hot query paths, and bounded
  pruning of grown tables.
- [x] **Dead code removed**: `atlas/streams/base.py` (unused `StreamCollector`) and `atlas/worker.py`
  (unused `atlas-worker` console script duplicating live-monitor logic; entry dropped from
  `pyproject.toml`).
- [x] **Fee constant consolidation**: the demo/fixture basket figures (0.83 fees / 0.20 slippage)
  now live once in `atlas/fees.py` (`DEMO_BASKET_FEES`/`DEMO_BASKET_SLIPPAGE`) and are imported by
  the monitor, CLI demo, and API fixture demo — distinct from the venue-published fee schedules in
  `atlas/gap_radar.py`, which are untouched.
- [x] README/TODO port drift fixed (8000 → 8010; the `.claude/launch.json` dev preview stays on 8020
  by design so it cannot collide with the launchd-managed API).

## 2026-08-18 — live pairing gate fixed (paper trading was structurally unreachable)

- [x] **Root-caused why `paper_trades` is empty after 12k+ shadow observations.** Paper trades are
  created only by `run_pair` (`atlas/live_monitor.py`), which the monitor spawns for the `approved`
  pairs returned by `scan_pairs` — and the live scan reported `comparisons=0 approved=0 review=0`
  against 20,336 active Kalshi and 22,275 active Polymarket markets. `scan_market_pairs` bucketed
  candidates on the full 15-field verification key, so a pair had to already agree on
  `settlement_policy`, `revision_policy`, and every threshold field before it was allowed to be
  tested on exactly those fields. Paper trading was unreachable by construction, not unprofitable.
- [x] **Fixed** (`atlas/discovery.py`): new `_fingerprint_pairing_key` buckets on the identity of the
  underlying question (`event_subject`, `contract_scope`, `market_type`, `participants`) and drops
  subjectless contracts so they cannot share an empty bucket. `_fingerprint_verification_key` is
  untouched — it still means "identical on every field" for the `event_compatible` and
  `exact_matches` report metrics. Loosening the bucket cannot manufacture an approval:
  `verify_equivalence` still adjudicates every field. 3 regression tests in `tests/test_arbitrage.py`;
  suite **461 passing**, lint clean.
- [x] **Verified live through the managed monitor**: `comparisons=729 approved=0 review=729` (was
  `0/0/0`), matching the offline measurement exactly. Trusted labels unchanged at 10 approved +
  71 rejected, `trading_enabled=false`, no approvals leaked.
- [ ] **The binding constraint is now venue text, not code.** All 729 live pairs refuse on
  `SETTLEMENT_GUARANTEE_UNKNOWN` + `SETTLEMENT_POLICY_MISMATCH`; 31 sit at an identical strike AND
  operator (e.g. Kalshi "Hike rates by 25bps" x PM-US "25 bps Increase", Sep 2026 FOMC) blocked
  *only* by those two codes — Kalshi publishes no terminal fallback, Polymarket publishes rounding
  terms Kalshi does not. These approve automatically if published text converges; no code change
  needed.
- [ ] **Latent, behind the above:** `run_pair` reads `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`,
  `POLYMARKET_US_API_KEY`, `POLYMARKET_US_API_SECRET` via `os.environ[...]`, none of which are set
  for the launchd monitor, and it is spawned with `asyncio.create_task` — so a `KeyError` would be
  swallowed silently. Before a first live paper trade can be expected, either provide those
  credentials to `com.atlas.monitor` or make the missing-credential path log an explicit blocker.

## 2026-08-17 — scheduled tag backfill fixed (it had never once succeeded)

- [x] **Root cause, measured live, not guessed:** every scheduled `tag_batch` since the feature shipped reported `BATCH_PARTIAL_FAILURE` with all four tags `TIMED_OUT` — 178 runs, 702 tag scans, **zero** completions in the monitor log. Timing probe (2026-08-17): Polymarket US closed sweep 14.9s + terminal-evidence finalization 55.2s + Kalshi settled-event scan 38.7s = **~110s of catalog fetch before a single pair is compared**, against a `BATCH_MAX_TAG_SECONDS = 120` budget. None of that work depends on the Global tag under probe, yet every tag repeated all of it. The Global tag catalog — the only genuinely per-tag fetch — costs 1.9s.
- [x] **Fix:** `SharedBackfillCatalog` + `prefetch_shared_backfill_catalog` (`atlas/backfill.py`) fetch the tag-independent catalog once per batch; `backfill_historical_validation` accepts `shared_catalog=` and consumes it instead of re-scanning. `learning_backfill_batch` prefetches through the patchable `_prefetch_batch_catalog` seam under its own `BATCH_MAX_CATALOG_SECONDS = 300` bound, and the per-tag 120s budget now bounds actual comparison work. A failed prefetch degrades honestly — every tag still runs and pays for its own catalog, exactly as before — and the batch report/log carry a `shared_catalog` status block so this can never fail silently again.
- [x] **Guards, because this path is sensitive:** the shared catalog pins the Kalshi scope it was scanned under and **refuses** a run requesting a different series set rather than silently mismatching evidence. Event-slug harvests always bypass the shared sweep and fetch for themselves — the targeted door reaches markets the plain sweep cannot, so it must never layer onto a cached list. 5 new tests (`tests/test_backfill.py`, `tests/test_cli.py`) cover reuse, scope refusal, slug bypass, single-fetch-per-batch, and failure degradation.
- [x] **New `tests/conftest.py` autouse guard:** the prefetch added a live network seam that the existing batch tests did not stub, and the suite silently started making real venue calls (3.9s → 142.8s). The guard refuses live catalog fetches in tests; suite is back to **408 passed in 3.6s**, lint clean.
- [x] **First working live run (2026-08-17):** `BATCH_COMPLETE`, all four tags green, shared catalog = 1447 PM-US final binaries + 20,309 Kalshi settled events. Minted **10 evidence-backed `REJECTED`** labels (62 → 72) — same-canonical-subject CPI review pairs with divergent terminal outcomes on both venues, `us_cpi_mom|2026-06` and `us_cpi_mom|2026-07` each landing exactly at the signed 5-per-event bound. Verified no event exceeds the cap; the known pre-existing `us_cpi_yoy|2026-07: 6` leak was untouched. Monitor restarted on the fixed code (single instance, nohup); `trading_enabled=false` throughout.
## 2026-08-17 — dashboard leads with a market-watch board

- [x] **The data was already there; nothing surfaced it.** 7,900+ recorded gap observations across
  9 candidate pairs were reduced to a one-line bankroll note and a few recent rows, behind a
  marketing hero. `atlas/watchlist.py` reshapes them into one row per event subject — latest gap,
  change, window high/low, sparkline history — as a pure function over the same observation list
  the bankroll summary already fetches, so the API does no extra I/O.
- [x] **Time windows (`1h / 24h / 7d / all`), because a single delta lies by omission.** Change is
  measured against each window's open, the market-board convention. Live proof it matters:
  `us_fomc_rate_decision|2026-12` reads WIDENING on 1h and 24h but NARROWING over 7d. Sub-noise
  movement reports `FLAT` so quote timing does not fake activity, and a window with no readings
  reports `NO_DATA` rather than borrowing numbers from outside it, which would make a stale pair
  look freshly observed.
- [x] **History is downsampled, not truncated** — keeping the newest N would silently redraw a
  7-day sparkline as a 2-hour one. Both ends of the window are always preserved.
- [x] **Safety framing held through the redesign:** every row carries the deterministic verdict
  verbatim and stays labelled a candidate. An executable gap on a `REVIEW_REQUIRED` pair is a
  research signal, not a trade — a board that looked like a trading screen would be the easiest
  way to forget that. Pinned by `test_dashboard_watch_board_never_presents_candidates_as_tradeable`.
- [x] **Dashboard shell now served `no-store`.** The shell carries the asset version query strings,
  so a cached copy pinned the browser to stale CSS/JS and the page silently stopped matching the
  code — which is exactly what happened mid-verification. Assets stay cacheable.
- [x] Verified live in-browser: window switching (ranges widen correctly with the window), both
  filter groups, both sort directions, no console errors. Suite **433 passing**, lint clean,
  dashboard `node --check` passes.
- [x] **Per-pair drill-down shipped:** clicking (or keyboard-activating) a row expands both full
  contract titles + market IDs, the mismatch codes behind the verdict, a per-window open/change/
  low/high/scans/exec table, and the pair's executable episodes. Delegated handlers so it survives
  the 15s re-render; `aria-expanded` and Enter/Space support because the verdict codes are the most
  useful thing on the board and should not be mouse-only.
- [x] **Crossing alerts shipped — as episodes, not threshold edges.** First implementation counted
  every rising edge and immediately proved itself unusable: live pairs flicker across the
  executable line on nearly every scan (`us_fomc_rate_decision|2027-01` produced **155 rising
  edges in five days, always at the same gap**), so the strip would have fired 8 identical alerts
  in 24h and buried the next real one. Edges within `CROSSING_COOLDOWN_MINUTES = 30` now fold into
  one episode reporting when it opened, when it was last seen, its peak gap, and its scan count.
  155 edges → 5 episodes for that pair; the strip currently shows **1** live episode.
  The window filter keys on *last activity*, not episode start — a pair executable since yesterday
  is the most current alert there is, and filtering on start time hid exactly that case (caught in
  review: the first version showed 0 alerts while a pair was executable). Every alert carries the
  deterministic verdict and the words "research signal, not a trade".
- [x] **Logomark rebuilt as the thesis:** two venue legs (Kalshi green, Polymarket blue) rise
  toward one event and stop short of meeting — the unclosed apex is the gap. Split crossbar repeats
  the spread; radar sweep is the monitor scanning both catalogs. Inline SVG themed off the existing
  CSS tokens, `prefers-reduced-motion` respected, favicon redrawn to match at 16px.
- [x] **Dated latent bug found and fixed while planning the next step: the watch board would have
  silently frozen in ~25 days.** `AtlasStore.all_gap_observations` selected
  `ORDER BY created_at ASC LIMIT 50000`, which keeps the **oldest** 50,000 rows and drops
  everything after. Callers need ascending order (the bankroll meter compounds chronologically),
  so the ASC was deliberate — but combined with the cap it means that once the table passes 50k the
  board stops seeing new observations entirely while still rendering as live. At the measured
  ~1,660 observations/day (8,302 recorded over five days) that lands around 2026-09-11. Now selects
  the newest rows first and restores ascending order; pinned by a regression test that fails against
  the old query.
- [x] **A capped load now reports itself.** `build_watchlist(..., total_observations=...)` sets
  `history_truncated` when fewer rows were loaded than exist, and the board replaces its footer with
  an explicit warning — otherwise the ALL window and every all-time high/low would be computed from
  a slice and labelled all-time. Currently `false` (8,302 of 8,302).
- [ ] Still open: per-pair daily rollups so the ALL window need not load the full table on every
  request (`/api/overview` measured at ~1.1s, polled every 15s). Deferred deliberately — it touches
  `atlas/storage.py`, which a concurrent session is actively editing (settlement polling).

## 2026-08-17 — approval-frontier watch (`atlas pairs frontier`)

Every blocked pair is waiting for a venue to publish terms it does not publish today, and the
standing rule is to wait for that text rather than infer it. That waiting was **passive**: the
store has recorded a `rules_hash` per evidence snapshot all along, but nothing ever said "a venue
republished its terms — re-check this blocker", so a cleared blocker could go unnoticed until the
settled overlap aged out of the catalog.

- [x] **`atlas/frontier.py` + `atlas pairs frontier` + `approval_frontier` in `/api/overview`.**
  Read-only reporting over evidence already in the store; it never approves, never relaxes a
  mismatch, and never feeds a verdict. `AtlasStore.rules_version_history` returns the ordered
  distinct published-rules versions per market.
- [x] **Blockers are split into `text_clearable_codes` vs `structural_codes`** so the report never
  implies a venue could fix a real divergence by publishing more text. A different strike
  (`THRESHOLD_OPERATOR_MISMATCH`, `SIGNED_LINE_MISMATCH`, `CONTRACT_SCOPE_MISMATCH`) is simply not
  the same bet, and no amount of waiting changes it. Ranking puts moved text first, then
  text-only-blocked pairs, then fewest remaining mismatches.
- [x] **First live report: 8 blocked, 6 blocked on venue text alone, 0 rules changes in the last
  14 days** — so nothing on the frontier has moved, which is now a measured fact instead of an
  assumption.
- [x] **Blind spot the report surfaced, and then closed the same day: 3 of the 8 blocked pairs had a
  leg with NO recorded rules baseline** (`us_nonfarm_payrolls|2026-08` Kalshi leg; both
  `us_cpi_*|2026-08` pairs on both legs) — precisely the high-value macro frontier. With no baseline
  there is nothing to diff, so a text change on those legs would never have been detected, and
  "we're watching for alignment" was not true for them.
  **Root cause:** `capture_validation_universe` only snapshots markets that are already `GUARANTEED`
  or that appear in a review pair, and it is never handed the Polymarket Global catalog at all.
  Blocked frontier pairs fail both tests by construction — a pair is blocked *because* a leg's
  guarantee is unknown — so the exact markets the project most wants to watch were the ones it was
  not watching.
  **Fix:** `capture_frontier_rules_evidence` (`atlas/frontier.py`) snapshots both legs of every
  blocked candidate regardless of guarantee status, wired into the monitor's scan right after the
  settlement rankings are saved, where the Global catalog is in scope. Legs absent from the current
  scan are counted as `frontier_legs_unavailable` rather than silently dropped. Observation only —
  a snapshot grants no guarantee and changes no verdict.
  **Verified live:** `frontier_evidence: legs=16 unavailable=0`, and the frontier report now reports
  `unmonitored=0`. Every blocked pair has a baseline on both legs, so the next venue text revision
  on any of them will register as a rules-version change.
- [x] **Monitor log was block-buffered — found while verifying the above, and worth its own entry.**
  The monitor writes stdout to `data/atlas-monitor.log`, which Python block-buffers when it is a
  file. A restarted monitor ran **~19 minutes of completed cycles (2:56 of CPU) while writing zero
  bytes**, so the log showed only the previous process's output and looked as if the new code were
  not running. Nothing was wrong with the monitor; the log simply lagged reality by many minutes.
  That directly undermines the frontier watch — a `PUBLISHED RULES CHANGED` line is worthless
  sitting in an unflushed buffer. Restarted with `PYTHONUNBUFFERED=1` and the line appeared within
  one cycle (~3.7 min). `deploy/com.atlas.monitor.plist` now defines the monitor durably with that
  variable set (file added; loading it into launchd is a separate owner action). **Always start the
  monitor with `PYTHONUNBUFFERED=1`.**

## 2026-08-17 — holdout baseline: `training_ready=true` is measuring volume, not signal

Ran the baseline characterization of the frozen holdout **before** spending anything on a
training run. The result argues against running one yet, and the reason is structural.

- [x] **The verifier/truth matrix is completely degenerate.** Across all 72 trusted labels there
  is not one disagreement: `APPROVED_EQUIVALENT` verdict → `APPROVED_EQUIVALENT` truth (8/8), and
  `REVIEW_REQUIRED` verdict → `REJECTED` truth (64/64). Three cells, zero off-diagonal. The label
  is a **deterministic function of the verifier's own verdict**, because the labeling pipeline only
  mints a label where the verifier has already decided. A model trained on this can learn exactly
  one thing — to imitate the verifier — and nothing about where the verifier is wrong, since the
  vault contains no such case by construction.
- [x] **The holdout cannot measure the thing that matters.** 15 rows, **2** positives. Any approval
  metric computed from it is noise. Majority-class baseline (answer `REJECTED` every time) scores
  **86.7%** on the holdout and 89.5% on train, so a headline accuracy in the high 80s would mean
  the model learned nothing.
- [x] **The one number that is genuinely good: approval precision 8/8.** Every pair the verifier
  approved settled consistently on both venues. Zero false approvals — the only error class that
  could ever cost real money. That is a property of the deterministic rules, not of any model.
- [ ] **Reframe before training.** The model's job was never to replace the verifier; it is to
  triage which candidate pairs deserve bounded verification capacity (sports floods the live queue
  daily — that is why the hard negatives were kept). Evaluate against *that* objective — junk
  filtered per unit of verification spend, measured per family — not approve/reject accuracy. Until
  the objective and metric are restated, more labels of the current shape add volume, not signal.
- [ ] **What would add real signal:** labeled pairs where verdict and settlement truth *diverge*.
  Honest catch — outcome agreement never proves equivalence (already noted for the July payrolls
  identical-strike pairs), so those cannot be minted as trusted positives. The ceiling is real, not
  a bug, and it is another reason the approval frontier (venue-text alignment) is the higher-value
  work.

- [ ] Label mix is now 8 approved / 64 rejected. The negative class grows automatically on every release while approvals stay gated on venue-text alignment — check the `label_families` mix before any training run, and prefer approval-frontier work (venue-text watches) over more rejection volume.

## Historical milestone notes (superseded by the current handoff at the top)

The next milestone is one settled cross-venue pair that passes deterministic verification as either:

- `APPROVED_EQUIVALENT`, or
- `APPROVED_INVERSE`

Do not treat a lexical match, a `REVIEW_REQUIRED` pair, or a divergent outcome alone as a trusted label.

## Step 1 — verify the runtime

```bash
curl http://127.0.0.1:8010/health
python3 -m atlas.cli learning status
python3 -m atlas.cli learning readiness
```

Confirm `paper_only=true`, no execution status, and that only one continuous monitor is running.

## Step 2 — add bounded per-tag scanning

- [x] Add a CLI override for Global Polymarket tag IDs instead of using only the default tag tuple.
- [x] Include the selected tag IDs in the historical backfill report and dashboard.
- [x] Keep each scan bounded:

```bash
python3 -m atlas.cli learning backfill --live \
  --target 1 \
  --global-pages 1 \
  --candidate-events 50 \
  --market-pairs 500 \
  --resolved-pairs 100
```

- [x] Run the Elections probe (`--global-tag-ids 144`): 179 inconclusive pairs, 0 trusted labels.
- [x] Run the Fed Rates probe (`--global-tag-ids 100196`): 158 inconclusive pairs, 0 trusted labels.
- [x] Run bounded probes for Crypto (`21`), Weather (`84`), and Commodities (`101031`).
- [x] Record scanned tags and outcomes in the machine-readable `tag_batch_report`; all targeted probes completed with 0 trusted labels.
- [x] Verify bounded runs completed without unexpected rows; caps remain enforced, and slow venue requests are now time-limited.
- [x] Ensure the continuous monitor uses bounded backfills; scheduled runs are capped at 50 candidate events, 500 market pairs, and 100 resolved pairs per tag.

## Step 3 — validate the first positive pair

- [ ] Confirm both markets are closed/settled with terminal Yes/No evidence.
- [ ] Confirm both settlement rules pass `verify_equivalence`.
- [ ] Confirm the pair is `APPROVED_EQUIVALENT` or `APPROVED_INVERSE`.
- [ ] Store the settlement evidence, rules hashes, source venue, market IDs, and timestamps.
- [ ] Verify the dashboard shows the pair as trusted historical evidence.

## Step 4 — build a balanced learning set

- [ ] Accumulate at least 50 trusted labels total.
- [ ] Require both `APPROVED_EQUIVALENT` and `REJECTED` labels.
- [x] Never convert inconclusive or review pairs into labels.
- [x] Run and persist the automatic export after backfill:

```bash
python3 -m atlas.cli learning status
python3 -m atlas.cli learning readiness
python3 -m atlas.cli learning export \
  --output data/training/atlas.jsonl \
  --eval-output data/training/atlas-eval.jsonl
```

- [ ] Review label balance, provenance, and evaluation split before any fine-tuning experiment.

## Step 5 — improve the self-learning loop

- [x] Add deterministic `learning_loop_status()` with explicit label-mix, minimum-label, and paper-only gates.
- [x] Persist every observation, verifier decision, settlement outcome, and final label.
- [x] Generate periodic training/evaluation exports automatically after completed backfills.
- [x] Track precision, coverage, inconclusive rate, and label-mix readiness on the dashboard.
- [x] Keep model/semantic proposals behind catalog-ID validation and deterministic verification.
- [x] Require an explicit review gate before changing normalization or verifier rules.

## Step 6 — paper trading only

- [x] Continue live shadow observations with `LIVE SHADOW TEST / NEVER EXECUTED`.
- [x] Simulate executable prices, fees, slippage, and available size only after a pair is approved.
- [x] Reconcile paper-trade outcomes against actual settlements.
- [x] Do not enable live orders, relayers, or execution credentials as part of validation.

## Completion criteria for the next stage

- [ ] At least one real approved settled pair is visible in the dashboard.
- [ ] The pair has complete cross-venue settlement evidence and matching rules hashes.
- [ ] No review/inconclusive pairs are present in the trusted training set.
- [x] The API, dashboard, and exported JSONL agree on the trusted-label counts: 0 trusted labels in each.
- [x] Paper-only and never-executed safeguards remain true.

## 2026-08-18 — gap observations queryable by column; ALL window no longer capped

- [x] **Growth re-measured: ~3,280 observations/day, not the 1,660 estimated yesterday** (11,039
  rows, latest 3 minutes old, monitor healthy). That moves the 50k load cap from ~25 days out to
  **~12 days**, so the ALL window was about to quietly become "the newest 50k rows".
- [x] **Promoted the queried fields out of JSON into columns** (`event_subject`, `best_gap`,
  `executable`) with indexes on subject and `created_at`, migrated and backfilled in
  `initialize()`. The payload stays the source of truth; the columns are a queryable projection.
  Backfill runs once (0.170s over 11k rows); later startups pay 0.001s because the index turns the
  "anything left?" probe into a seek. The positional `INSERT ... VALUES (?, ?, ?)` was rewritten
  with an explicit column list — it would have broken the moment the columns were added.
- [x] **`AtlasStore.gap_subject_aggregates()` reads all-time extremes over every row**, regardless
  of any load cap: **0.015s vs 0.411s** for the same aggregate via `json_extract` (27×).
- [x] **`build_watchlist(..., subject_aggregates=...)` sources the ALL window and row-level
  widest/narrowest from that aggregate**, falling back to the loaded slice when none is supplied.
  Open and change still come from loaded rows — the aggregate records extremes and counts, not the
  first reading, and inventing one would be worse than reporting the slice's.
- [x] Cross-checked against the live database: aggregates match a raw Decimal recomputation on
  low/high/count/executable for all 9 subjects across 11,039 observations — **0 mismatches**.
  Suite **458 passing**, lint clean, dashboard `node --check` passes.
- [ ] Follow-up now unblocked: bound the raw load to a recent window (the bounded windows only need
  ~7d) so `/api/overview` stops loading the full table. Measured 0.94s; the remaining cost is the
  JSON parse of every loaded row.

## 2026-08-20 — settlement-timing curve reporting corrected; docs re-synced

- [x] **Confirmed the curve's zeros were data age, not a broken pipeline.** Every gap
  observation recorded since 2026-08-19T23:25Z carries the `settlement_timing`
  annotation with a parsed horizon: 2,856 of 2,856 annotated rows have one, i.e.
  **100% coverage since the feature shipped**. The horizon math is right — it takes
  the later of the two legs' anchors (`max` over `resolution_time`, else
  `close_time`), which is the honest capital lock-up for a paired position.
- [x] **Fixed a real measurement-honesty bug: two unrelated populations were pooled.**
  `observations_without_horizon` counted 15,527, which read as "most of our data has
  no settlement horizon". In fact those rows simply predate the annotation. They are
  now `unannotated_observations`, split by *key presence* (not truthiness), leaving
  `observations_without_horizon` for annotated rows whose venues published no anchor
  — currently 0. This is the same rule `fee_model_rows` already applies to its two
  fee models. Pooling them would have understated coverage for the whole study,
  because the pre-annotation block never shrinks. The four counts now reconcile
  exactly to `observations_reviewed` (verified live: 18,383).
- [x] **Fixed a second one: an unmeasurable comparison was presenting as a result.**
  `asymmetric_median_gap: null` beside a populated `symmetric_median_gap` invited the
  day-90 reading "asymmetry was measured and didn't matter". It was never measured.
  The report now emits `asymmetry_measured` plus `asymmetry_blind_spot`
  (`NO_ANNOTATED_OBSERVATIONS` / `NO_WATCHED_PAIR_PUBLISHES_EARLY_DETERMINATION`),
  and `annotated_observations` / `annotated_pairs` to show the eligible population.
- [x] Dashboard surfaces the gap-vs-lock-up table and renders the blind spot as an
  amber caution block, not a neutral footnote — a GO/NO-GO panel must not hide an
  untested dimension. Verified live in the browser against the running API; zero
  console errors. (New CSS: `.study-subhead`, `.study-note`, `.study-note--caution`.)
- [x] Charter amended (`docs/NINETY_DAY_STUDY.md`): metric definitions for settlement
  horizon and annotated observation, the separation rule, the known blind spot, and a
  dated amendment note recording that no go/no-go metric moved and the deterministic
  verifier is byte-unchanged. 4 new tests; suite 569 → **573 passing**, lint clean.
- [x] README/TODO re-synced to the live runtime (81 labels, 573 tests, 6-of-8 frontier
  pairs blocked on venue text alone) and the study is now described as the governing
  milestone with its frozen-rules constraint.

### RESOLVED 2026-08-20 — the asymmetry split now has an eligible population

Root cause was two independent gates, not one:

1. **Scope** — the radar's Kalshi series list and Polymarket tag list simply did
   not include chamber control. The recorded rationale ("Elections/House stay out:
   margin-of-victory spreads produce no twin shapes") was true of *spreads* but
   never evaluated for *categorical* contracts.
2. **The matcher, which was the real blocker** — `match_twin_shapes` required a
   numeric threshold and operator on both legs. Chamber control has neither
   (`threshold=None`, verified live). Adding the scope alone would have produced
   **zero** pairs.

- [x] `_twin_shape` in `atlas/gap_radar.py` now recognizes a second twin kind:
  categorical twins pair on identical subject/action/scope plus a **non-null,
  equal** affirmative outcome, with neither leg publishing a threshold. A
  threshold contract is never compared to a categorical one.
- [x] **Not done deliberately:** opposing parties are NOT an inverse shape. Ties
  and third outcomes exist — which is why both venues publish tiebreak clauses —
  so calling them complements would be inference. Pinned by test.
- [x] **Trap guarded:** Polymarket's "2026 Balance of Power: D Senate, D House"
  joint contracts normalize to the *house-control* subject with no affirmative
  outcome. Requiring non-null-and-equal keeps a joint bet from pairing with a
  House-only bet. Found empirically on the live catalog; pinned by test.
- [x] Radar scope gained Kalshi `CONTROLH`/`CONTROLS` + Polymarket tag `144`.
  Tag `487` stays out — the live probe found 0 party_control markets under it.
- [x] **Quarantined from go/no-go** (`POST_START_SCOPE_FAMILIES` in
  `atlas/study.py`): measured in full under `post_start_scope`, held out of the
  opportunity counts, both rates, `meets_go_threshold`, and the weekly table —
  but INCLUDED in the settlement-timing curve, which is not a go/no-go input and
  is the whole reason the family was added. Charter amended.
- [x] Verified live 2026-08-20: radar 17 → **21 twin-shaped pairs**; frozen-scope
  headline byte-identical before and after (24 opportunities, 66.7 verified/30d,
  GO); curve went from `asymmetry_measured: false` to **4 asymmetric pairs vs
  3,043 symmetric observations**. Dashboard renders both blocks, 0 console errors.
  581 tests green, lint clean.

**Early signal, do not over-read:** asymmetric median gap +0.29¢ vs symmetric
−3.0¢. That is 4 observations from a single scan. It is directionally consistent
with asymmetric pairs trading wider (carry compensation), but it is nowhere near
evidence. The point of the 90 days is to let this accumulate.

- [ ] **These pairs must stay `REVIEW_REQUIRED`.** Kalshi may settle on a media-call
  consensus months before the Polymarket twin's official sources resolve, so the
  basket is not truly locked. The asymmetry is a caution signal; it can never
  promote a pair to a trusted label.
- [ ] Polymarket publishes no displayed size on these legs, so the 163,706-contract
  median is Kalshi-side only. Do not present it as two-legged depth.
- [ ] When the 2026 contracts settle, the asymmetric population empties unless the
  2028 contracts get a Polymarket counterpart. The blind-spot reporting stays in
  place for exactly that reason — watch for it to re-fire.

### Next dated commitment — study phase 2 by day 31 (2026-09-18)

- [ ] Replay each executable observation under simulated execution delays of 250ms,
  500ms, 1s, and 2s using the next recorded quotes; require both legs to remain
  fillable.
- [ ] Measure legging/partial-fill exposure and report **return on locked capital
  until settlement**, not profit per basket — the horizon buckets now feed this
  directly.
- [ ] Add burst sampling around detected gaps: the 5-minute sweep cannot resolve
  sub-second decay. Instrumentation only — not a rule change, so no sign-off gate,
  but note it in the charter.
- [ ] Watch the fee-model split converge: 14,643 rows still carry the legacy flat 2¢
  buffer against 3,723 venue-published. Per-week comparisons must keep them separate
  until the legacy rows age out of the reporting window.

### Housekeeping

- [ ] Delete 3 stale `worktree-agent-*` branches (core PCE, GDP, payrolls) — their
  families are already on `main` with tests; the branches are dead duplicates.

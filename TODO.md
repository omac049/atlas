# Atlas continuation checklist

Last updated: 2026-08-11 (172 tests green, lint zero; queue reachability shipped; batch default re-scoped to guarantee-reachable tags 144/487/100196 after live probe)

## Current validated state

- [x] Paper-only policy remains enforced. No order-placement path is enabled.
- [x] API is serving at `http://127.0.0.1:8000/`.
- [x] Continuous live monitor is running with `pairs watch --live`.
- [x] Polymarket US and tagged Polymarket Global historical catalogs are connected.
- [x] Historical settlement evidence is required before creating trusted labels.
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

## Current blockers

- No trusted settled cross-venue pair has been found yet; the latest batch produced 0 approved and 0 rejected labels.
- The learning loop remains intentionally blocked at `LABEL_MIX_BLOCKED` because it has 0 trusted labels and 279 unlabeled observations at the latest check.
- The catalog evidence currently shows no cross-venue settled overlap for Crypto or Weather, and capped inconclusive review for Commodities.

## Active milestone status

- [x] Entered the first-settled-pair milestone.
- [x] Automated live discovery, settlement evidence capture, reconciliation, and paper-only safeguards are operating.
- [ ] First real trusted pair: `0` execution-ready events, `0` awaiting-settlement cases, and `0` trusted labels at the latest check.
- [x] Current queue is observable: 12 ranked candidates, all `BLOCKED`; none are yet guarantee-complete.
- [ ] Keep the milestone open until one real pair passes deterministic verification and both venues publish terminal outcomes.

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

## Next milestone: first trusted positive label

The next milestone is one settled cross-venue pair that passes deterministic verification as either:

- `APPROVED_EQUIVALENT`, or
- `APPROVED_INVERSE`

Do not treat a lexical match, a `REVIEW_REQUIRED` pair, or a divergent outcome alone as a trusted label.

## Step 1 — verify the runtime

```bash
curl http://127.0.0.1:8000/health
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

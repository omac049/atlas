# NFL player-prop family — design

Date: 2026-09-24 · Status: implemented (plan: `2026-09-24-nfl-player-prop-family-plan.md`). Live proof: 66,665 markets, 0 fingerprints changed outside the family, 4,460 claimed, 680 twin pairs all showing exactly the three true codes.
Owner sign-off for the frozen-path change: given in chat 2026-09-24 ("yes, teach the rules player props", option A "recognize and explain", approach 1, NFL only).

## In plain words

Atlas can't read NFL player bets today. A true twin such as "Malik Nabers 100+ receiving
yards" on both venues comes back with **8** mismatch reasons, most of them false; Kalshi's
version is even misread as a moneyline. This adds a reader for these bets so a twin reads as
the same bet and gets the **3** reasons that are actually true: the payout rules differ, Kalshi
names no stats source, and the payout isn't guaranteed. **Nothing becomes approvable.** A pair
could only ever be approved if both venues published complete, identical rules — today none do.

## Goal and non-goals

**Goal.** Deterministically recognize single-game NFL player "N+ stat" markets on Kalshi and
Polymarket US, give twins the same canonical fingerprint on every field that really matches,
and turn each venue's edge-case fine print into explicit settlement-policy tokens so the
existing verifier names the real differences.

**Non-goals.**
- No approvals. No new approval rule in `atlas/verification.py`; `verify_equivalence` and its
  field list are unchanged.
- No other sports or leagues. No first-touchdown, longest-play, fantasy-point, season-long, or
  team-level markets.
- No change to how any existing (non-player-prop) market is fingerprinted — proven on the live
  catalogs (see Testing).
- No gap-radar or go/no-go scope change: the family is quarantined.

## Why it can't be approved (evidence, 2026-09-24, live rules text)

Pair: `kalshi:KXNFLRECYDS-26SEP27TENNYG-NYGMNABERS1-100` vs
`polymarket_us:astatc-nfl-ten-nyg-2026-09-27-recyd-malnab-gte100`.

| Branch | Kalshi | Polymarket US |
|---|---|---|
| Active, no snap | fair market price **before game start** | **last** fair market price |
| Inactive | not stated | last fair market price ("must participate") |
| Overtime | not stated | included |
| Post-game stat corrections | not stated | excluded |
| Postponement | not stated ("originally scheduled") | not rescheduled within 2 days → last fair price |
| Source | none named | official box score / relevant governing body |

The hard invariant forbids trusted labels for non-guaranteed or unknown branches, and treating
Kalshi's silence as agreement would be inference. `assess_settlement_guarantee` already marks
any fair-price clause `NON_GUARANTEED`.

## Scope: markets recognized

Single-game player stat ladders with an integer "N+" line.

| Canonical stat | Kalshi series | Polymarket `sportsMarketType` |
|---|---|---|
| `receiving_yards` | `KXNFLRECYDS` | `football_player_receiving_yards` |
| `receptions` | `KXNFLREC` | `football_player_receptions` |
| `rushing_yards` | `KXNFLRSHYDS` | `football_player_rushing_yards` |
| `rushing_attempts` | `KXNFLRSHATT` | `football_player_rushing_attempts` |
| `passing_yards` | `KXNFLPASSYDS` | `football_player_passing_yards` |
| `passing_attempts` | `KXNFLPASSATT` | `football_player_passing_attempts` |
| `passing_completions` | `KXNFLPASSCOMP` | `football_player_passing_completions` |
| `passing_touchdowns` | `KXNFLPASSTDS` | `football_player_passing_touchdowns` |
| `interceptions_thrown` | `KXNFLPASSINT` | `football_player_interceptions_thrown` |
| `scrimmage_yards` | `KXNFLRRYDS` ("rushing and receiving yards combined") | `football_player_scrimmage_yards` |

**Definition gate (resolved while planning).** `scrimmage_yards` passed: Kalshi "rushing and
receiving yards combined" = Polymarket "scrimmage yards (rushing yards + receiving yards)".
`touchdowns` failed: Polymarket says "excluding passing touchdowns", Kalshi is silent. It is left
out, so the family covers **10 stats**.

Polymarket markets are identified by the `astatc-nfl-{away}-{home}-{YYYY-MM-DD}-...` slug with a
date; season-long slugs (`astatc-nfl-passyds-2027…`, `…ou`) are out of scope. Kalshi markets by
series prefix above.

## Design

### 1. Reader: `_nfl_player_prop_terms(market)` in `atlas/normalization.py`

Added to the `specialized_terms` dispatch **first**, so it claims these markets before
`_economic_terms`. It returns `{}` for anything outside the scope table, which leaves every
other market on its current path.

Canonical fields:

- **Game key:** `YYYY-MM-DD` plus both team codes, sorted.
  - Kalshi: the codes are parsed from the event ticker (`26SEP27TENNYG`) by splitting against a
    32-team code table.
  - Polymarket: the codes come from the slug.
  - Aliases: `JAC→JAX` (seen live), `WSH→WAS`, `LA→LAR`.
- **Player:** the name is lowercased, dotted suffixes are normalized ("Jr.", "Sr.", "III"), and
  punctuation is collapsed.
  - Kalshi: the name comes from the title prefix before `:`.
  - Polymarket: the name comes from `metadata.playerName`, falling back to the title.
- **Fields:**
  - `event_subject = nfl_player_stat|{date}|{team_a}-{team_b}|{player}|{stat}`
  - `event_action = records`
  - `market_type = player_prop`
  - `contract_scope = nfl_player_game` (not `full_game`: the lock keys on this scope, because the
    generic sports fallback already emits `market_type=player_prop` for non-NFL markets)
  - `affirmative_outcome = {player}`
  - `threshold = N`, `threshold_operator = ">="`, `threshold_unit = {stat}`
  - `measurement_period = {date}`
  - `event_date = {date}`
- **Line:** read from the title "N+".
  - Kalshi's `floor_strike` (N−0.5, `strike_type=greater`) is cross-checked; if the two disagree,
    the reader returns `{}`.
  - Polymarket's `line` is cross-checked the same way.
- **Participants:** `[{player}]`. This needs the one shared-code change: `build_fingerprint` uses
  `specialized.get("participants")` when the reader supplies it. No existing reader supplies it,
  so no current fingerprint changes.
- **Resolution source:**
  - Kalshi: left as the existing sentinel `unknown`, because it names none. Keeping `unknown` means
    the existing missing-source checks still fire.
  - Polymarket: `official_box_score` when the rules say "official box score", otherwise the text
    as today.

### 2. Settlement-policy tokens

The reader parses five branches from each venue's text into `settlement_policy`: a sorted,
`;`-joined token string.

| Branch | Values |
|---|---|
| `no_snap` | `fair_price_pregame`, `fair_price_last`, `unstated` |
| `inactive` | `fair_price_pregame`, `fair_price_last`, `unstated` |
| `overtime` | `included`, `excluded`, `unstated` |
| `stat_corrections` | `included`, `excluded`, `unstated` |
| `postponement` | `fair_price_last_after_2d` (or `…_after_{n}d`), `unstated` |

- Each branch is matched by explicit phrases pinned to the live text. A phrase that isn't
  recognized yields `unstated`. The reader never guesses.
- For the real twin, the tokens differ, so the verifier reports `SETTLEMENT_POLICY_MISMATCH`.

### 3. Safety lock: `atlas/settlement.py`

- Add a complete-policy check for scope `nfl_player_game`: all five branch tokens must be present,
  none `unstated`, and a named resolution source.
- If it fails, add the scope to the existing *incomplete-policy forces UNKNOWN* list
  (`FAMILY_POLICY_INCOMPLETE`), so the generic sports grants (`cancel=half`, explicit
  yes-else-no) can never make a partly stated player prop `GUARANTEED`.
- The fair-price rule still yields `NON_GUARANTEED` first, exactly as today.
- Net effect: a pair can only reach approval if both venues state all five branches identically
  **and** without a fair-price outcome. No venue does today.

### 4. Study quarantine and records

- **`atlas/study.py`:** `POST_START_SCOPE_FAMILIES["nfl_player_stat"] = "2026-09-24"`.
- **`docs/NINETY_DAY_STUDY.md`:** append an amendment in the existing format. Header:
  "**2026-09-24 — NFL player-prop family recognized (frozen-path change; quarantined from
  go/no-go; no approval path).**" Sub-bullets cover:
  - What changed: the reader, the participants hook, and the safety lock.
  - Which metrics it can move: none of the go/no-go inputs, because it is quarantined and the
    radar is macro-only. What it does move:
    - Review candidate lists and counts in `scan:`, since player-prop twins now group by event.
    - Learning data: same-canonical-subject player-prop review pairs whose settled outcomes
      diverge on both venues can become evidence-backed `REJECTED` labels, under the existing
      2026-08-13 decision.
  - What was deliberately not done: no approval rule, no verifier change, no other sports.
- **`docs/decisions/2026-09-24-nfl-player-prop-family.md`:** a short owner-decision record
  (sign-off quote, option A, the evidence table above).
- **`atlas/learning.py`:** add `player_prop` and `team_total` to `_SPORTS_MARKET_TYPES`. Today
  the fingerprint emits both but `example_family` files them as `other`.
- **`TODO.md`:** add an entry. The README "Last verified" line changes only after a full
  validation run.

## Data flow

Venue adapters (unchanged) → `Market` → `specialized_terms` → new reader returns canonical
fields + policy tokens → `build_fingerprint` → `verify_equivalence` (unchanged) and
`assess_settlement_guarantee` (+ lock) → `REVIEW_REQUIRED` with the three true codes →
candidates/review lists. Validation capture only takes pairs `GUARANTEED` on both legs
(`atlas/validation.py`), so player props add no settlement-polling load.

## Error handling

- The reader is pure and never raises.
- Any parse doubt (unknown team code, title/strike disagreement, unmapped stat, missing date)
  returns `{}`, which falls back to today's behaviour for that market.
- Unrecognized fine print becomes an `unstated` token, never an assumed value.

## Testing

Real venue text is embedded in the tests (captured 2026-09-24). Tests:

1. **The real twin** (Nabers 100+ receiving yards, Kalshi vs Polymarket):
   - Every canonical field matches except `settlement_policy` and `resolution_source`.
   - The decision is `REVIEW_REQUIRED` with exactly `SETTLEMENT_POLICY_MISMATCH`,
     `RESOLUTION_SOURCE_MISMATCH` and `NON_GUARANTEED_SETTLEMENT`.
2. **Different line** (100+ vs 90+): adds `THRESHOLD_MISMATCH`.
3. **Different player, same game and stat:** `EVENT_SUBJECT_MISMATCH`.
4. **Policy tokens:** each branch value is parsed from the real text of both venues; unrecognized
   text becomes `unstated`.
5. **Safety lock:** a synthetic player prop whose text states all five branches without fair price
   can be `GUARANTEED`; one with any branch `unstated` is `UNKNOWN` (`FAMILY_POLICY_INCOMPLETE`),
   even when it has an explicit yes-else-no sentence.
6. **Parsing edge cases:** `JAC`/`JAX` alias; a title/strike disagreement returns `{}`; season-long
   slugs are ignored.
7. **Quarantine:** `nfl_player_stat` observations are counted under `post_start_scope` and never
   under the go/no-go.
8. **Learning:** `player_prop` maps to the `sports` family.
9. **Live no-regression proof** (run once, recorded in the amendment, not a unit test):
   - Fingerprint all live markets with and without the change.
   - Zero fingerprint changes outside the new family.
   - Report how many markets the family claims and how many twin pairs now show exactly the three
     expected codes.

Full suite and `ruff check .` must pass.

## Open risks

- **Venue text drift:** a rewording turns a branch `unstated`. That is safe (more mismatch, never
  less), but the explanation degrades until phrases are added.
- **Team-code table maintenance:** relocations and renames need an update. An unknown code falls
  back to `{}`, which is safe.
- **Definition gate:** resolved. `touchdowns` is excluded, so the family covers 10 stats.

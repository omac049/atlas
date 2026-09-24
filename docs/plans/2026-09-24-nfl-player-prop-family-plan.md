# NFL Player-Prop Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministically recognize single-game NFL player "N+ stat" markets on Kalshi and
Polymarket US so a true twin reads as the same bet and the verifier names only the three real
differences. Nothing becomes approvable.

**Architecture:** A new family reader, `_nfl_player_prop_terms`, is added first in
`specialized_terms` (`atlas/normalization.py`).
- It returns canonical fields plus a five-branch settlement-policy string.
- `build_fingerprint` gains one hook: it takes `participants` from the reader when the reader
  supplies it.
- `assess_settlement_guarantee` gains a complete-policy path and adds the family's scope to the
  existing "incomplete forces UNKNOWN" lock.
- The family is quarantined in `atlas/study.py`. `verify_equivalence` is untouched.

**Tech Stack:** Python 3.12, pydantic v2, pytest (`asyncio_mode=auto`), ruff (line length 100).
Run tools through `.venv/bin/…` — `uv` fails on this machine (see memory note).

**Spec:** `docs/plans/2026-09-24-nfl-player-prop-family-design.md` — read it first. This plan
implements it with three refinements found while planning. Task 5 writes them back into the spec:
- Scope is **10 stats**. `touchdowns` failed the definition gate: Polymarket says "excluding
  passing touchdowns" and Kalshi says nothing.
- `contract_scope` is `nfl_player_game`, not `full_game`. The safety lock keys on this scope. The
  generic sports fallback already emits `market_type="player_prop"` for non-NFL markets, so keying
  the lock on market type would change existing verdicts.
- Kalshi's source stays the existing sentinel `unknown`, not a new `unstated`. That way
  `MISSING_RESOLUTION_SOURCE` and `deterministic_settlement_blockers` keep treating it as missing.

## Global Constraints

- Atlas is paper-only. Never add an order path. `trading_enabled=false`.
- Do not change `atlas/verification.py`. No new approval rule.
- The reader must return `{}` for every market outside the scope table. No existing market's
  fingerprint or settlement status may change, and Task 5 proves this on live catalogs.
- Anything the reader can't parse is `unstated`, or `{}` for identity fields. The reader never
  infers.
- Frozen-rules policy: owner sign-off was given in chat on 2026-09-24. An amendment note goes in
  `docs/NINETY_DAY_STUDY.md` (Task 5).
- After every task: `.venv/bin/pytest -q` and `.venv/bin/ruff check .` must both pass.
- Never read or print `.env` or `keys/`.

## Review Focus

These five inputs are not exercised by the spec's tests, listed with the behaviour a reasonable
person would expect (most likely first). Each has a test in the owning task.

1. **Apostrophes and suffixes in player names.** "Ja'Marr Chase" and "Michael Penix Jr." on both
   venues must produce the same player key. (Task 1: `test_player_key_normalizes_punctuation`)
2. **Two-letter team codes and ambiguous splits.** Kalshi `KCMIA`, `LVNO` and `NEJAC` must split
   correctly, and an unsplittable code must return `{}`, not a guess.
   (Task 1: `test_team_code_split`)
3. **Polymarket `line` as a float.** `100.0` must equal title `100+`. `line: 95` against title
   `100+` must return `{}`. (Task 1: `test_polymarket_line_cross_check`)
4. **Kalshi strike disagreements.** A missing `floor_strike` is accepted on the title alone. A
   `floor_strike` that disagrees with the title returns `{}`.
   (Task 1: `test_kalshi_strike_cross_check`)
5. **Out-of-scope lookalikes.** Kalshi `KXNFLTD` (touchdowns), `KXNFLFIRSTTD`, and Polymarket
   season-long `astatc-nfl-passyds-2027…` slugs all return `{}`.
   (Task 1: `test_out_of_scope_markets_are_ignored`)

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `atlas/normalization.py` | Modify | New reader, team/player helpers, and `nfl_prop_settlement_policy`; add the reader first in `specialized_terms` |
| `atlas/fingerprints.py` | Modify (~2 lines) | Participants hook |
| `atlas/settlement.py` | Modify | Complete-policy check and lock scope |
| `atlas/study.py` | Modify (1 line) | Quarantine entry |
| `atlas/learning.py` | Modify (1 line) | Add `player_prop` and `team_total` to `_SPORTS_MARKET_TYPES` |
| `tests/test_nfl_player_props.py` | Create | All family tests, built from verbatim 2026-09-24 venue text |
| `tests/test_study.py`, `tests/test_learning_artifacts.py` | Modify | Quarantine and learning-family tests |
| `docs/NINETY_DAY_STUDY.md`, `docs/decisions/2026-09-24-nfl-player-prop-family.md`, `TODO.md`, the spec | Modify / Create | Records |

---

### Task 1: Reader: canonical identity fields and participants hook

**Files:**
- Modify: `atlas/normalization.py`. Add the import of `VenueName`, the new block after
  `_number`, and the dispatch in `specialized_terms` (currently around line 95).
- Modify: `atlas/fingerprints.py:14-23`, the `build_fingerprint` preamble.
- Create: `tests/test_nfl_player_props.py`

**Interfaces:**
- Produces:
  - `NFL_PLAYER_PROP_SCOPE: str = "nfl_player_game"`
  - `NFL_PROP_POLICY_BRANCHES: tuple[str, ...]`
  - `_nfl_player_prop_terms(market: Market) -> dict[str, object]`
  - `nfl_prop_settlement_policy(text: str) -> str` (stub in this task; real in Task 2)
  - `_nfl_player_key(name: str) -> str`
  - `_nfl_split_teams(codes: str) -> tuple[str, str] | None`
  - `_nfl_team(code: str) -> str | None`
- Consumes: `Market`, `VenueName` (`atlas/models.py`), `KalshiVenue._normalize_market`,
  `PolymarketUSVenue._normalize_market`.

- [ ] **Step 1: Write the failing tests** (create `tests/test_nfl_player_props.py`)

```python
"""NFL single-game player props: recognized and explained, never approvable.

Venue text is verbatim from the live catalogs on 2026-09-24 (Malik Nabers, Titans at Giants).
Design: docs/plans/2026-09-24-nfl-player-prop-family-design.md
"""

from decimal import Decimal

import pytest

from atlas.fingerprints import build_fingerprint
from atlas.normalization import (
    _nfl_player_key,
    _nfl_player_prop_terms,
    _nfl_split_teams,
)
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_us import PolymarketUSVenue

KALSHI_SECONDARY = (
    "The following market refers to {player} in the Tennessee vs New York G Pro Football game "
    "originally scheduled for Sep 27, 2026. If {player} is active but never takes a snap, the "
    "market settles to the fair market price before game start. Once {player} takes at least one "
    "snap, even if nullified by penalty, the market settles based on receiving yards recorded.\n\n"
    "Kalshi is not affiliated, associated, authorized, endorsed by, or in any way officially "
    "connected with the Governing League. All trademarks, logos, and brand names are the property "
    "of their respective owners."
)
POLYMARKET_DESCRIPTION = (
    "This market will settle to Yes if {player} records at least {line} receiving yards in the "
    "Tennessee Titans vs New York Giants professional football game scheduled for Sep 27, 2026. "
    "Overtime is included if played. The listed player must participate in the game by taking at "
    "least one snap on offense, defense, or special teams, with a snap nullified by penalty "
    "counting; otherwise, the market will settle to the last fair market price. The market will "
    "settle based on the official box score at the conclusion of the game. For purposes of this "
    "market, stat corrections enforced after the game has been completed will not count. If the "
    "game is delayed, postponed, or suspended and not rescheduled to a date within two days of the "
    "originally scheduled date, the market will settle to the last fair market price. Outcome "
    "sourced from the relevant governing body."
)


def kalshi_record(player="Malik Nabers", line=100, event="KXNFLRECYDS-26SEP27TENNYG", **extra):
    record = {
        "ticker": f"{event}-NYG{player.split()[-1].upper()}-{line}",
        "event_ticker": event,
        "title": f"{player}: {line}+ receiving yards",
        "yes_sub_title": f"{player}: {line}+",
        "no_sub_title": f"{player}: {line}+",
        "rules_primary": (
            f"If {player} records {line}+ receiving yards in the Tennessee vs New York G Pro "
            "Football game originally scheduled for Sep 27, 2026, then the market resolves to Yes."
        ),
        "rules_secondary": KALSHI_SECONDARY.format(player=player),
        "strike_type": "greater",
        "floor_strike": line - 0.5,
        "close_time": "2026-09-29T17:00:00Z",
        "expected_expiration_time": "2026-09-27T23:00:00Z",
        "status": "active",
        "market_type": "binary",
        "early_close_condition": "This market will close and expire early if the event occurs.",
    }
    record.update(extra)
    return record


def polymarket_record(player="Malik Nabers", line=100, slug_game="ten-nyg-2026-09-27", **extra):
    abbreviation = (player.split()[0][:3] + player.split()[-1][:3]).lower()
    record = {
        "slug": f"astatc-nfl-{slug_game}-recyd-{abbreviation}-gte{line}",
        "title": f"{player} {line}+ receiving yards",
        "question": f"Will {player} record {line}+ receiving yards?",
        "description": POLYMARKET_DESCRIPTION.format(player=player, line=line),
        "sportsMarketType": "football_player_receiving_yards",
        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_PROP",
        "marketType": "props",
        "line": line,
        "metadata": {"lineLabel": f"{line}+", "playerName": player, "statLabel": "Rec Yds"},
        "gameStartTime": "2026-09-27T17:00:00Z",
        "startDate": "2026-09-24T20:45:09Z",
        "endDate": "2026-10-11T17:00:00Z",
        "status": "MARKET_STATUS_OPEN",
        "active": True,
        "closed": False,
        "outcomes": '["No","Yes"]',
    }
    record.update(extra)
    return record


def kalshi(**kwargs):
    return KalshiVenue._normalize_market(kalshi_record(**kwargs))


def polymarket(**kwargs):
    return PolymarketUSVenue._normalize_market(polymarket_record(**kwargs))


def test_twin_gets_the_same_canonical_identity():
    k, p = build_fingerprint(kalshi()), build_fingerprint(polymarket())
    expected_subject = "nfl_player_stat|2026-09-27|nyg-ten|malik nabers|receiving_yards"
    for fp in (k, p):
        assert fp.event_subject == expected_subject
        assert fp.event_action == "records"
        assert fp.market_type == "player_prop"
        assert fp.contract_scope == "nfl_player_game"
        assert fp.affirmative_outcome == "malik nabers"
        assert fp.participants == ["malik nabers"]
        assert fp.threshold == Decimal(100)
        assert fp.threshold_upper is None
        assert fp.threshold_operator == ">="
        assert fp.threshold_unit == "receiving_yards"
        assert fp.measurement_period == "2026-09-27"
    assert k.resolution_source == "unknown"  # Kalshi names no source
    assert p.resolution_source == "official_box_score"


def test_player_key_normalizes_punctuation():
    assert _nfl_player_key("Ja'Marr Chase") == _nfl_player_key("JaMarr Chase") == "jamarr chase"
    assert _nfl_player_key("Michael Penix Jr.") == "michael penix jr"
    assert _nfl_player_key("Kyle Pitts Sr.") != _nfl_player_key("Kyle Pitts")  # never merge


def test_team_code_split():
    assert _nfl_split_teams("TENNYG") == ("nyg", "ten")
    assert _nfl_split_teams("KCMIA") == ("kc", "mia")
    assert _nfl_split_teams("LVNO") == ("lv", "no")
    assert _nfl_split_teams("NEJAC") == ("jax", "ne")  # Kalshi JAC == Polymarket JAX
    assert _nfl_split_teams("LARDEN") == ("den", "lar")
    assert _nfl_split_teams("XXXYYY") is None


def test_jacksonville_alias_matches_across_venues():
    k = kalshi(event="KXNFLRECYDS-26SEP27NEJAC")
    p = polymarket(slug_game="ne-jax-2026-09-27")
    assert build_fingerprint(k).event_subject == build_fingerprint(p).event_subject


def test_polymarket_line_cross_check():
    as_float = PolymarketUSVenue._normalize_market({**polymarket_record(), "line": 100.0})
    assert _nfl_player_prop_terms(as_float)["threshold"] == Decimal(100)
    mismatched = PolymarketUSVenue._normalize_market({**polymarket_record(), "line": 95})
    assert _nfl_player_prop_terms(mismatched) == {}


def test_kalshi_strike_cross_check():
    no_strike = {k: v for k, v in kalshi_record().items() if k != "floor_strike"}
    assert _nfl_player_prop_terms(KalshiVenue._normalize_market(no_strike))["threshold"] == 100
    wrong = kalshi_record(floor_strike=89.5)
    assert _nfl_player_prop_terms(KalshiVenue._normalize_market(wrong)) == {}


@pytest.mark.parametrize(
    "market",
    [
        lambda: kalshi(event="KXNFLTD-26SEP27TENNYG"),
        lambda: kalshi(event="KXNFLFIRSTTD-26SEP27TENNYG"),
        lambda: PolymarketUSVenue._normalize_market(
            {**polymarket_record(), "slug": "astatc-nfl-passyds-2027-malnab-gte4000"}
        ),
        lambda: PolymarketUSVenue._normalize_market(
            {**polymarket_record(), "sportsMarketType": "football_player_touchdowns"}
        ),
    ],
)
def test_out_of_scope_markets_are_ignored(market):
    assert _nfl_player_prop_terms(market()) == {}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nfl_player_props.py -q`
Expected: collection error `ImportError: cannot import name '_nfl_player_key'`.

- [ ] **Step 3: Implement the reader.** In `atlas/normalization.py`:
  - Change the import to `from atlas.models import Market, VenueName`.
  - Add `_nfl_player_prop_terms` as the **first** entry of the `specialized_terms` tuple.
  - Add this block after `_number`:

```python
# NFL single-game player stat ladders — recognized and explained, never approvable.
# Design: docs/plans/2026-09-24-nfl-player-prop-family-design.md. Every venue today settles
# at least one branch at a "fair price" or leaves it unstated, which settlement.py treats as
# non-guaranteed; this reader only makes a twin *read* as a twin.
NFL_PLAYER_PROP_SCOPE = "nfl_player_game"
NFL_PROP_POLICY_BRANCHES = ("inactive", "no_snap", "overtime", "postponement", "stat_corrections")
_NFL_KALSHI_SERIES = {
    "KXNFLRECYDS": "receiving_yards",
    "KXNFLREC": "receptions",
    "KXNFLRSHYDS": "rushing_yards",
    "KXNFLRSHATT": "rushing_attempts",
    "KXNFLPASSYDS": "passing_yards",
    "KXNFLPASSATT": "passing_attempts",
    "KXNFLPASSCOMP": "passing_completions",
    "KXNFLPASSTDS": "passing_touchdowns",
    "KXNFLPASSINT": "interceptions_thrown",
    # "rushing and receiving yards combined" == Polymarket "scrimmage yards (rushing + receiving)".
    "KXNFLRRYDS": "scrimmage_yards",
}
_NFL_POLYMARKET_TYPES = {f"football_player_{stat}": stat for stat in _NFL_KALSHI_SERIES.values()}
_NFL_TEAMS = frozenset({
    "ari", "atl", "bal", "buf", "car", "chi", "cin", "cle", "dal", "den", "det", "gb", "hou",
    "ind", "jax", "kc", "lac", "lar", "lv", "mia", "min", "ne", "no", "nyg", "nyj", "phi", "pit",
    "sea", "sf", "tb", "ten", "was",
})
_NFL_TEAM_ALIASES = {"jac": "jax", "wsh": "was", "la": "lar"}
_NFL_KALSHI_TITLE = re.compile(r"^(?P<player>[^:]+):\s*(?P<line>\d+)\+\s")
_NFL_POLYMARKET_TITLE = re.compile(r"^(?P<player>.+?)\s+(?P<line>\d+)\+\s")
_NFL_POLYMARKET_SLUG = re.compile(
    r"^astatc-nfl-(?P<a>[a-z]+)-(?P<b>[a-z]+)-(?P<date>\d{4}-\d{2}-\d{2})-"
)


def _nfl_team(code: str) -> str | None:
    code = _NFL_TEAM_ALIASES.get(code.lower(), code.lower())
    return code if code in _NFL_TEAMS else None


def _nfl_split_teams(codes: str) -> tuple[str, str] | None:
    """Split Kalshi's concatenated team codes; ambiguous or unknown -> None."""
    splits = {
        tuple(sorted((a, b)))
        for i in range(2, len(codes) - 1)
        if (a := _nfl_team(codes[:i])) and (b := _nfl_team(codes[i:]))
    }
    return splits.pop() if len(splits) == 1 else None


def _nfl_player_key(name: str) -> str:
    cleaned = re.sub(r"[.'’]", "", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", cleaned).strip()


def _nfl_kalshi_prop(market: Market) -> tuple[str, tuple[str, str], str, str, int] | None:
    raw = market.raw_market_json
    series, _, suffix = str(raw.get("event_ticker") or "").partition("-")
    stat = _NFL_KALSHI_SERIES.get(series)
    title = _NFL_KALSHI_TITLE.match(market.title)
    if not stat or not title or len(suffix) < 11:
        return None
    try:
        game_date = datetime.strptime(suffix[:7], "%y%b%d").date().isoformat()
    except ValueError:
        return None
    teams = _nfl_split_teams(suffix[7:])
    line = int(title["line"])
    floor = raw.get("floor_strike")
    if floor is not None and (
        raw.get("strike_type") != "greater" or Decimal(str(floor)) != line - Decimal("0.5")
    ):
        return None
    if teams is None:
        return None
    return game_date, teams, _nfl_player_key(title["player"]), stat, line


def _nfl_polymarket_prop(market: Market) -> tuple[str, tuple[str, str], str, str, int] | None:
    raw = market.raw_market_json
    stat = _NFL_POLYMARKET_TYPES.get(str(raw.get("sportsMarketType") or ""))
    slug = _NFL_POLYMARKET_SLUG.match(str(raw.get("slug") or ""))
    title = _NFL_POLYMARKET_TITLE.match(market.title)
    if not stat or not slug or not title:
        return None
    a, b = _nfl_team(slug["a"]), _nfl_team(slug["b"])
    line = int(title["line"])
    raw_line = raw.get("line")
    if not a or not b or (raw_line is not None and Decimal(str(raw_line)) != line):
        return None
    metadata = raw.get("metadata")
    name = metadata.get("playerName") if isinstance(metadata, dict) else None
    return slug["date"], tuple(sorted((a, b))), _nfl_player_key(str(name or title["player"])), stat, line


def _nfl_player_prop_terms(market: Market) -> dict[str, object]:
    if market.venue == VenueName.KALSHI:
        parsed = _nfl_kalshi_prop(market)
    elif market.venue == VenueName.POLYMARKET_US:
        parsed = _nfl_polymarket_prop(market)
    else:
        return {}
    if parsed is None:
        return {}
    game_date, (team_a, team_b), player, stat, line = parsed
    text = " ".join(f"{market.raw_rules_text} {market.description or ''}".lower().split())
    terms: dict[str, object] = {
        "event_subject": f"nfl_player_stat|{game_date}|{team_a}-{team_b}|{player}|{stat}",
        "event_date": game_date,
        "event_action": "records",
        "market_type": "player_prop",
        "contract_scope": NFL_PLAYER_PROP_SCOPE,
        "affirmative_outcome": player,
        "participants": [player],
        "threshold": Decimal(line),
        "threshold_upper": None,
        "threshold_operator": ">=",
        "threshold_unit": stat,
        "measurement_period": game_date,
        "settlement_policy": nfl_prop_settlement_policy(text),
    }
    if "official box score" in text:
        terms["resolution_source"] = "official_box_score"
    return terms


def nfl_prop_settlement_policy(text: str) -> str:
    """Stub until Task 2: every branch unstated."""
    return ";".join(f"{branch}=unstated" for branch in NFL_PROP_POLICY_BRANCHES)
```

  Confirm `datetime` is already imported at the top of the file (`from datetime import UTC, datetime`).
  If a line exceeds 100 characters (for example the `return slug["date"], …` line), wrap it in
  parentheses.

- [ ] **Step 4: Participants hook.** In `atlas/fingerprints.py` `build_fingerprint`, replace:

```python
    participants = sorted(_participants(market))
    specialized = specialized_terms(market)
```

with:

```python
    specialized = specialized_terms(market)
    # Only the NFL player-prop reader supplies participants today; others keep the fallback.
    participants = sorted(specialized.get("participants") or _participants(market))
```

- [ ] **Step 5: Run the family tests**

Run: `.venv/bin/pytest tests/test_nfl_player_props.py -q`
Expected: all pass.

- [ ] **Step 6: Full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: all pass. Existing tests must be untouched and green.

- [ ] **Step 7: Commit**

```bash
git add atlas/normalization.py atlas/fingerprints.py tests/test_nfl_player_props.py
git commit -m "Recognize NFL single-game player props: canonical identity fields

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Settlement-policy tokens (the explanation)

**Files:**
- Modify: `atlas/normalization.py`. Replace the `nfl_prop_settlement_policy` stub.
- Test: `tests/test_nfl_player_props.py`

**Interfaces:**
- Consumes: `NFL_PROP_POLICY_BRANCHES` and `_nfl_player_prop_terms` (Task 1).
- Produces: `nfl_prop_settlement_policy(text: str) -> str`, a `;`-joined `branch=value` string
  in `NFL_PROP_POLICY_BRANCHES` order. The values are `fair_price_pregame`, `fair_price_last`,
  `fair_price_last_after_{n}d`, `included`, `excluded` and `unstated`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from atlas.normalization import nfl_prop_settlement_policy
from atlas.verification import verify_equivalence

KALSHI_POLICY = (
    "inactive=unstated;no_snap=fair_price_pregame;overtime=unstated;"
    "postponement=unstated;stat_corrections=unstated"
)
POLYMARKET_POLICY = (
    "inactive=fair_price_last;no_snap=fair_price_last;overtime=included;"
    "postponement=fair_price_last_after_2d;stat_corrections=excluded"
)


def test_policy_tokens_from_real_text():
    assert build_fingerprint(kalshi()).settlement_policy == KALSHI_POLICY
    assert build_fingerprint(polymarket()).settlement_policy == POLYMARKET_POLICY


def test_unrecognized_fine_print_is_unstated_never_guessed():
    assert nfl_prop_settlement_policy("some new wording about overtime rules") == (
        "inactive=unstated;no_snap=unstated;overtime=unstated;"
        "postponement=unstated;stat_corrections=unstated"
    )


def test_real_twin_gets_exactly_the_three_true_reasons():
    decision = verify_equivalence(kalshi(), polymarket(), "twin").decision
    assert decision.status.value == "REVIEW_REQUIRED"
    assert sorted(decision.mismatch_codes) == [
        "NON_GUARANTEED_SETTLEMENT",
        "RESOLUTION_SOURCE_MISMATCH",
        "SETTLEMENT_POLICY_MISMATCH",
    ]


def test_different_line_adds_threshold_mismatch():
    codes = verify_equivalence(kalshi(line=90), polymarket(line=100), "x").decision.mismatch_codes
    assert "THRESHOLD_MISMATCH" in codes


def test_different_player_is_a_different_subject():
    other = polymarket(player="Isaiah Likely", line=100)
    codes = verify_equivalence(kalshi(), other, "x").decision.mismatch_codes
    assert "EVENT_SUBJECT_MISMATCH" in codes
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nfl_player_props.py -q -k "policy or reasons or line_adds or different_player"`
Expected: `test_policy_tokens_from_real_text` and `test_real_twin_gets_exactly_the_three_true_reasons`
FAIL. The stub returns all `unstated`, so both policies are equal and there is no
`SETTLEMENT_POLICY_MISMATCH`.

- [ ] **Step 3: Implement.** Replace the stub in `atlas/normalization.py`:

```python
_NFL_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7}


def nfl_prop_settlement_policy(text: str) -> str:
    """Five edge-case branches from one venue's fine print. Unrecognized -> `unstated`.

    `text` is lowercased with whitespace collapsed. Phrases are pinned to the venues'
    live wording on 2026-09-24; a rewording degrades to `unstated` (more mismatch, never less).
    """
    policy = dict.fromkeys(NFL_PROP_POLICY_BRANCHES, "unstated")
    if "active but never takes a snap" in text and "fair market price before game start" in text:
        policy["no_snap"] = "fair_price_pregame"
    if re.search(
        r"must participate in the game by taking at least one snap[^.]*otherwise, "
        r"the market will settle to the last fair market price",
        text,
    ):
        policy["no_snap"] = policy["inactive"] = "fair_price_last"
    if "overtime is included" in text:
        policy["overtime"] = "included"
    elif re.search(r"overtime (?:is not|will not be) (?:included|counted)", text):
        policy["overtime"] = "excluded"
    if "stat corrections enforced after the game has been completed will not count" in text:
        policy["stat_corrections"] = "excluded"
    if match := re.search(
        r"not rescheduled to a date within (\w+) days? of the originally scheduled date, "
        r"the market will settle to the last fair market price",
        text,
    ):
        days = _NFL_WORD_NUMBERS.get(match[1]) or (int(match[1]) if match[1].isdigit() else None)
        if days:
            policy["postponement"] = f"fair_price_last_after_{days}d"
    return ";".join(f"{branch}={policy[branch]}" for branch in NFL_PROP_POLICY_BRANCHES)
```

- [ ] **Step 4: Run the family tests**

Run: `.venv/bin/pytest tests/test_nfl_player_props.py -q`
Expected: all pass.
- If the twin test shows an extra code (for example `REVISION_POLICY_MISMATCH` or
  `GEOGRAPHY_MISMATCH`), print both fingerprints and set that field in the reader's `terms`,
  **only** if both venues' raw values are genuinely the same thing.
- Otherwise the extra code is a true difference: record it in the spec and update the expected
  list. Do not force a match.

- [ ] **Step 5: Full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add atlas/normalization.py tests/test_nfl_player_props.py
git commit -m "Parse NFL player-prop settlement branches into explicit policy tokens

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Safety lock in settlement

**Files:**
- Modify: `atlas/settlement.py`.
  - Import `NFL_PLAYER_PROP_SCOPE` and `NFL_PROP_POLICY_BRANCHES` from `atlas.normalization`.
  - Add `_complete_nfl_player_prop_policy`.
  - Call it after the `_complete_gdp_release_policy` block.
  - Add the scope to the `specialized_scope` set.
- Test: `tests/test_nfl_player_props.py`

**Interfaces:**
- Consumes: `NFL_PLAYER_PROP_SCOPE` and `NFL_PROP_POLICY_BRANCHES` (Task 1).
- Produces: reason code `COMPLETE_NFL_PLAYER_PROP_POLICY`. Incomplete NFL props get `UNKNOWN`
  with `FAMILY_POLICY_INCOMPLETE`.

- [ ] **Step 1: Write the failing tests** (append)

```python
from atlas.fingerprints import has_explicit_binary_fallback
from atlas.settlement import assess_settlement_guarantee

NO_FAIR_PRICE_RULES = (
    "If Malik Nabers records 100+ receiving yards in the Tennessee vs New York G Pro Football "
    "game originally scheduled for Sep 27, 2026, then the market resolves to Yes. "
    "Otherwise, the market resolves to No."
)


def _no_fair_price_market():
    return kalshi(rules_primary=NO_FAIR_PRICE_RULES, rules_secondary="")


def test_real_legs_stay_non_guaranteed():
    for market in (kalshi(), polymarket()):
        assert assess_settlement_guarantee(market)["status"] == "NON_GUARANTEED"


def test_lock_blocks_generic_yes_no_grant_when_branches_are_unstated():
    market = _no_fair_price_market()
    text = f"{market.raw_rules_text} {market.description or ''}"
    assert has_explicit_binary_fallback(text)  # the generic grant WOULD fire without the lock
    result = assess_settlement_guarantee(market)
    assert result["status"] == "UNKNOWN"
    assert "FAMILY_POLICY_INCOMPLETE" in result["reason_codes"]


def test_all_branches_stated_without_fair_price_can_be_guaranteed():
    market = _no_fair_price_market()
    complete = build_fingerprint(market).model_copy(update={
        "settlement_policy": (
            "inactive=no;no_snap=no;overtime=included;postponement=no;stat_corrections=excluded"
        ),
        "resolution_source": "official_box_score",
    })
    result = assess_settlement_guarantee(market, fingerprint=complete)
    assert result == {"status": "GUARANTEED", "reason_codes": ["COMPLETE_NFL_PLAYER_PROP_POLICY"]}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_nfl_player_props.py -q -k "lock or guaranteed or non_guaranteed"`
Expected:
- `test_lock_blocks…` FAILs with status `GUARANTEED` (reason `EXPLICIT_YES_NO_FALLBACK`).
- `test_all_branches…` FAILs.
- If `has_explicit_binary_fallback` is False for `NO_FAIR_PRICE_RULES`, adjust that sentence
  until it is True. The test must prove the lock is what blocks.

- [ ] **Step 3: Implement.** In `atlas/settlement.py`, first add the import:

```python
from atlas.normalization import NFL_PLAYER_PROP_SCOPE, NFL_PROP_POLICY_BRANCHES
```

Add the function next to the other `_complete_*` helpers:

```python
def _complete_nfl_player_prop_policy(fingerprint: ContractFingerprint) -> bool:
    """All five edge-case branches stated (none `unstated`) and a named source."""
    if fingerprint.contract_scope != NFL_PLAYER_PROP_SCOPE or not fingerprint.settlement_policy:
        return False
    tokens = dict(
        token.split("=", 1) for token in fingerprint.settlement_policy.split(";") if "=" in token
    )
    return (
        set(tokens) == set(NFL_PROP_POLICY_BRANCHES)
        and "unstated" not in tokens.values()
        and fingerprint.resolution_source not in {"", "unknown"}
    )
```

Add this block after the `_complete_gdp_release_policy` block:

```python
    if _complete_nfl_player_prop_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_NFL_PLAYER_PROP_POLICY"],
        }
```

Extend the lock set:

```python
    } or specialized_scope in {
        "fomc_rate_change_bucket",
        "fed_funds_upper_bound_level",
        "ism_manufacturing_pmi",
        "ism_services_pmi",
        NFL_PLAYER_PROP_SCOPE,
    }:
```

- [ ] **Step 4: Run the family tests**

Run: `.venv/bin/pytest tests/test_nfl_player_props.py -q`
Expected: all pass.

- [ ] **Step 5: Full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: all pass. A circular import at startup would appear here. `normalization` imports only
`models`, so none is expected.

- [ ] **Step 6: Commit**

```bash
git add atlas/settlement.py tests/test_nfl_player_props.py
git commit -m "Lock NFL player props to UNKNOWN unless every settlement branch is stated

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Study quarantine and learning family

**Files:**
- Modify: `atlas/study.py:89-92` (`POST_START_SCOPE_FAMILIES`)
- Modify: `atlas/learning.py:210` (`_SPORTS_MARKET_TYPES`)
- Test: `tests/test_study.py` (after line 366). Reuse its `_observation` helper, which accepts
  `pair=`, `gap=` and `subject=`.
- Test: `tests/test_learning_artifacts.py`

**Interfaces:**
- Consumes: the `nfl_player_stat|…` event-subject prefix (Task 1).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the failing tests**

In `tests/test_study.py`:

```python
def test_nfl_player_props_are_quarantined_from_the_go_threshold():
    macro = _observation("2026-09-24T10:00:00+00:00", pair="m1", gap="0.03")
    prop = _observation(
        "2026-09-24T10:00:00+00:00", pair="p1", gap="0.05",
        subject="nfl_player_stat|2026-09-27|nyg-ten|malik nabers|receiving_yards",
    )
    report = study_report([macro, prop], today=date(2026, 9, 24))
    assert report["distinct_opportunities"] == 1
    assert report["post_start_scope"]["family_executable_observations"] == {"nfl_player_stat": 1}
```

In `tests/test_learning_artifacts.py`:

```python
def test_player_props_and_team_totals_are_sports():
    def example(market_type):
        return {"label": "REJECTED", "payload": {"decision": {
            "fingerprint_a": {"market_type": market_type, "event_subject": "x|2026-09-27"}
        }}}

    assert example_family(example("player_prop")) == "sports"
    assert example_family(example("team_total")) == "sports"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_study.py tests/test_learning_artifacts.py -q -k "nfl_player or player_props"`
Expected: both FAIL. Study returns `distinct_opportunities == 2`; learning returns `"other"`.

- [ ] **Step 3: Implement**

`atlas/study.py`:

```python
POST_START_SCOPE_FAMILIES: dict[str, str] = {
    "us_house_control": "2026-08-20",
    "us_senate_control": "2026-08-20",
    # Recognized, never approvable (docs/decisions/2026-09-24-nfl-player-prop-family.md).
    "nfl_player_stat": "2026-09-24",
}
```

`atlas/learning.py`:

```python
_SPORTS_MARKET_TYPES = {"spread", "moneyline", "total", "sports", "player_prop", "team_total"}
```

- [ ] **Step 4: Run to verify they pass, then run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: all pass. If an existing learning test pinned a `team_total` or `player_prop` example as
`other`, that expectation is exactly what the spec changes. Update it and name it in the Task 5
amendment.

- [ ] **Step 5: Commit**

```bash
git add atlas/study.py atlas/learning.py tests/test_study.py tests/test_learning_artifacts.py
git commit -m "Quarantine NFL player props from the go/no-go; file props as sports in learning

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Live no-regression proof and records

**Files:**
- Create: `docs/decisions/2026-09-24-nfl-player-prop-family.md`
- Modify: `docs/NINETY_DAY_STUDY.md` (append under `## Amendments`),
  `docs/plans/2026-09-24-nfl-player-prop-family-design.md`, `TODO.md`

**Interfaces:**
- Consumes: everything above. Produces the proof numbers quoted in the amendment.

- [ ] **Step 1: Run the live proof** (scratchpad script, not committed; reads public catalogs only)

```python
# save as <scratchpad>/prop_proof.py and run: .venv/bin/python <scratchpad>/prop_proof.py
import asyncio
import collections

import atlas.fingerprints as F
import atlas.normalization as N
from atlas.settlement import assess_settlement_guarantee
from atlas.verification import verify_equivalence
from atlas.venues.kalshi import KalshiVenue
from atlas.venues.polymarket_us import PolymarketUSVenue


def snapshot(markets):
    F._FINGERPRINT_CACHE.clear()
    return {
        m.market_id: (F.build_fingerprint(m).model_dump_json(),
                      assess_settlement_guarantee(m)["status"])
        for m in markets
    }


async def main():
    kalshi = await KalshiVenue(fixture=False).list_markets()
    poly = await PolymarketUSVenue(fixture=False).list_markets()
    markets = kalshi + poly
    claimed = {m.market_id for m in markets if N._nfl_player_prop_terms(m)}
    after = snapshot(markets)
    real = N._nfl_player_prop_terms
    N._nfl_player_prop_terms = lambda m: {}
    before = snapshot(markets)
    N._nfl_player_prop_terms = real
    F._FINGERPRINT_CACHE.clear()
    changed = [i for i in after if i not in claimed and after[i] != before[i]]
    print(f"markets={len(markets)} claimed={len(claimed)} changed_outside_family={len(changed)}")
    by_key = collections.defaultdict(list)
    for m in markets:
        if m.market_id in claimed:
            fp = F.build_fingerprint(m)
            by_key[(fp.event_subject, fp.threshold)].append(m)
    twins = [ms for ms in by_key.values() if {m.venue for m in ms} == {"kalshi", "polymarket_us"}]
    codes = collections.Counter()
    for ms in twins:
        k = next(m for m in ms if m.venue == "kalshi")
        p = next(m for m in ms if m.venue == "polymarket_us")
        codes[tuple(sorted(verify_equivalence(k, p, "proof").decision.mismatch_codes))] += 1
    print(f"twin_pairs={len(twins)}")
    for combo, n in codes.most_common(5):
        print(n, combo)


asyncio.run(main())
```

Expected:
- `changed_outside_family=0`. **If not 0, stop.** Inspect the changed IDs; that is a regression.
- `claimed` is in the thousands.
- The most common twin code combination is exactly
  `('NON_GUARANTEED_SETTLEMENT', 'RESOLUTION_SOURCE_MISMATCH', 'SETTLEMENT_POLICY_MISMATCH')`.
- Record all printed numbers.

- [ ] **Step 2: Write the decision record** `docs/decisions/2026-09-24-nfl-player-prop-family.md`:

```markdown
# 2026-09-24 — NFL player props: recognize and explain, no approval path

**Decision (owner, in chat, 2026-09-24):** "yes, teach the rules player props" → option A
"recognize and explain"; approach 1 (new family reader); NFL only.

**Why no approval path:** on live text, Kalshi settles a no-snap player at the fair price
*before game start* while Polymarket uses the *last* fair price, and Kalshi does not state
inactive, overtime, stat-correction, or postponement rules. Treating silence as agreement is
inference, which the hard invariant forbids. Evidence table: the design spec,
docs/plans/2026-09-24-nfl-player-prop-family-design.md.

**What it does:** twins share a canonical fingerprint; the verifier reports the three true
differences (settlement policy, resolution source, non-guaranteed settlement). A pair could
only become approvable if both venues state all five branches identically with a named source
and no fair-price outcome.
```

- [ ] **Step 3: Append the amendment** to `docs/NINETY_DAY_STUDY.md` under `## Amendments`, in
  the existing dated-bullet format. Fill `<…>` with Step 1's numbers:

```markdown
- **2026-09-24 — NFL player-prop family recognized (frozen-path change; quarantined from
  go/no-go; no approval path).** Owner sign-off:
  `docs/decisions/2026-09-24-nfl-player-prop-family.md`.
  - **What changed:** a new reader (`_nfl_player_prop_terms`, first in `specialized_terms`)
    for single-game NFL player "N+" ladders in 10 stats; `build_fingerprint` takes
    participants from a reader when supplied (only this one does); `settlement.py` locks the
    `nfl_player_game` scope to UNKNOWN unless all five edge-case branches are stated.
    `verify_equivalence` is byte-unchanged.
  - **Proof on the live catalogs, same day:** <markets> markets fingerprinted with and without
    the reader — **<changed> changed outside the family**; the reader claims <claimed>;
    <twins> cross-venue twin pairs, <n> of them with exactly
    SETTLEMENT_POLICY / RESOLUTION_SOURCE / NON_GUARANTEED (previously 8 codes each).
  - **Which metrics it can move:** none of the go/no-go inputs (quarantined in
    `POST_START_SCOPE_FAMILIES`; the radar is macro-only). It moves review-candidate counts in
    `scan:` and may add evidence-backed REJECTED sports labels under the 2026-08-13 decision
    when both venues' settled outcomes diverge; learning exports now file `player_prop` and
    `team_total` as `sports` instead of `other`.
  - **Deliberately not done:** no approval rule; no other sports; `touchdowns` excluded (the
    venues define it differently: Polymarket excludes passing TDs, Kalshi is silent).
```

- [ ] **Step 4: Update the spec** (`docs/plans/2026-09-24-nfl-player-prop-family-design.md`) with
  the three refinements from this plan's header:
  - 10 stats (drop the `touchdowns` row and note why).
  - `contract_scope = nfl_player_game`, with the lock keyed on the scope.
  - The Kalshi source stays `unknown`.
  - Also change its Status line to "implemented".

- [ ] **Step 5: TODO entry.** Add a dated section at the top of `TODO.md`:

```markdown
## 2026-09-24 — NFL player props recognized (explain-only)

- [x] New family reader, policy tokens, safety lock, study quarantine, learning family. Live
  proof: <changed> fingerprints changed outside the family; <twins> twin pairs now show the
  three true reasons. Records: decision + charter amendment.
- [ ] Watch for venue rewordings: any branch turning `unstated` degrades the explanation (safe).
- [ ] If Kalshi ever states overtime/corrections/postponement, re-run the proof; approval still
  needs identical non-fair-price branches on both venues.
```

- [ ] **Step 6: Full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check .`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add docs/decisions/2026-09-24-nfl-player-prop-family.md docs/NINETY_DAY_STUDY.md \
  docs/plans/2026-09-24-nfl-player-prop-family-design.md docs/plans/2026-09-24-nfl-player-prop-family-plan.md TODO.md
git commit -m "Record NFL player-prop family: decision, charter amendment, live proof

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Pushing and opening the PR happen only when the owner asks.

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

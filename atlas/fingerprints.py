import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal

from atlas.models import ContractFingerprint, Market
from atlas.normalization import specialized_terms

_FINGERPRINT_CACHE: dict[str, tuple[tuple, ContractFingerprint]] = {}
_FINGERPRINT_CACHE_LIMIT = 100_000


def build_fingerprint(market: Market) -> ContractFingerprint:
    signature = _fingerprint_signature(market)
    cached = _FINGERPRINT_CACHE.get(market.market_id)
    if cached and cached[0] == signature:
        return cached[1]
    event_date = _event_date(market) or (
        market.resolution_time.isoformat() if market.resolution_time else None
    )
    participants = sorted(_participants(market))
    specialized = specialized_terms(market)
    event_date = str(specialized.get("event_date") or event_date) if event_date else specialized.get("event_date")
    event_subject = str(
        specialized.get("event_subject")
        or _canonical_event_subject(market, event_date, participants)
    )
    rules_hash = hashlib.sha256(market.raw_rules_text.encode()).hexdigest() if market.raw_rules_text else None
    fingerprint = ContractFingerprint(
        event_subject=event_subject,
        event_action=str(specialized.get("event_action") or _canonical_action(market)),
        event_date=event_date,
        market_type=str(specialized.get("market_type") or _market_family(market)),
        contract_scope=specialized.get("contract_scope") or _contract_scope(market),
        affirmative_outcome=specialized.get("affirmative_outcome") or _affirmative_outcome(market),
        signed_line=_signed_line(market),
        settlement_policy=specialized.get("settlement_policy") or _settlement_policy(market),
        threshold=specialized.get("threshold", market.threshold),
        threshold_upper=specialized.get("threshold_upper", market.threshold_upper),
        threshold_operator=specialized.get("threshold_operator", market.threshold_operator),
        threshold_unit=specialized.get("threshold_unit", market.threshold_unit),
        measurement_period=specialized.get("measurement_period")
        or _canonical_measurement_period(market, event_date),
        geography=specialized.get("geography")
        or (_clean(market.geography) if market.geography else None),
        revision_policy=specialized.get("revision_policy")
        or (_clean(market.revision_policy) if market.revision_policy else None),
        resolution_source=str(
            specialized.get("resolution_source")
            or _canonical_resolution_source(market.resolution_source)
        ),
        participants=participants,
        rules_hash=rules_hash,
    )
    if len(_FINGERPRINT_CACHE) >= _FINGERPRINT_CACHE_LIMIT:
        _FINGERPRINT_CACHE.clear()
    _FINGERPRINT_CACHE[market.market_id] = (signature, fingerprint)
    return fingerprint


def _fingerprint_signature(market: Market) -> tuple:
    raw = market.raw_market_json
    raw_semantics = {
        key: raw.get(key)
        for key in (
            "question",
            "sportsMarketType",
            "sportsMarketTypeV2",
            "yes_sub_title",
            "outcome_title",
            "marketSides",
            "event_ticker",
            "floor_strike",
            "cap_strike",
            "strike_type",
        )
    }
    return (
        market.venue.value,
        market.title,
        market.subtitle,
        market.description,
        market.outcome_yes_label,
        market.event_action,
        market.measurement_period,
        market.resolution_time.isoformat() if market.resolution_time else None,
        market.market_type,
        market.threshold,
        market.threshold_upper,
        market.threshold_operator,
        market.threshold_unit,
        market.geography,
        market.revision_policy,
        market.resolution_source,
        tuple(market.participants),
        market.raw_rules_text,
        json.dumps(raw_semantics, sort_keys=True, default=str),
    )


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().strip())


def _canonical_resolution_source(value: str) -> str:
    cleaned = _clean(value)
    if "state electoral" in cleaned and "house of representatives" in cleaned:
        return "state_electoral_authorities+us_house"
    if "state electoral" in cleaned and re.search(r"\b(?:u\.?s\.?|united states) senate\b", cleaned):
        return "state_electoral_authorities+us_senate"
    for authority in (
        "atp",
        "wta",
        "nfl",
        "nba",
        "mlb",
        "nhl",
        "fifa",
        "uefa",
        "ncaa",
        "pga",
        "ufc",
    ):
        if re.search(rf"\b{authority}\b", cleaned):
            return authority
    return cleaned


def _event_date(market: Market) -> str | None:
    text = f"{market.raw_rules_text} {market.description or ''}"
    match = re.search(r"(?:scheduled for|originally scheduled for) ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    if match:
        for date_format in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return (
                    datetime.strptime(match.group(1), date_format)
                    .replace(tzinfo=UTC)
                    .date()
                    .isoformat()
                )
            except ValueError:
                continue
    value = market.measurement_period
    return value.split("T", 1)[0] if value and "T" in value else value


def _canonical_measurement_period(market: Market, event_date: str | None) -> str | None:
    """Treat intraday sports start times as scheduling metadata, not rule windows."""
    if _market_family(market) in {
        "moneyline",
        "spread",
        "team_total",
        "player_prop",
    }:
        return event_date
    return market.measurement_period


def _market_family(market: Market) -> str:
    if family := specialized_terms(market).get("market_type"):
        return str(family)
    text = _market_text(market)
    raw_family = _clean(market.market_type or "")
    if raw_family == "election" or _election_chamber(market):
        return "election"
    if raw_family in {"totals", "team_total"}:
        return "team_total"
    if raw_family in {"spreads", "spread"}:
        return "spread"
    if raw_family in {"props", "player_prop"}:
        return "player_prop"
    if re.search(r"\b(over|under)\s+[0-9.]+\s+(goals?|runs?|points?|yards?)", text):
        return "team_total"
    if re.search(r"\b[0-9]+(?:\.[0-9]+)?\s*\+\s*(hits?|runs?|rbis?|assists?|rebounds?|strikeouts?)\b", text):
        return "player_prop"
    if re.search(
        r"\bwin\b.*\b(?:at least\s+)?[0-9.]+\s+more\s+(?:games?|points?|sets?|goals?|runs?)\b",
        text,
    ):
        return "spread"
    if re.search(r"\b(wins?|beat)\b.*\b(by|more than)\b", text):
        return "spread"
    if "win" in text or "winner" in text:
        return "moneyline"
    return market.market_type or "binary"


def _canonical_action(market: Market) -> str:
    if _election_chamber(market):
        return "party_control"
    return _market_family(market)


def _contract_scope(market: Market) -> str | None:
    if chamber := _election_chamber(market):
        return f"us_{chamber}_control"
    text = _market_text(market)
    if "full match" in text or re.search(r"\bfull\b.{0,120}\bmatch\b", text):
        return "full_match"
    for name, pattern in (
        ("map", r"\bmap\s*(\d+)\b"),
        ("set", r"\bset\s*(\d+)\b"),
        ("quarter", r"\b(?:quarter|q)\s*(\d+)\b"),
        ("period", r"\bperiod\s*(\d+)\b"),
    ):
        if match := re.search(pattern, text):
            return f"{name}_{match.group(1)}"
    if "first half" in text:
        return "first_half"
    if "second half" in text:
        return "second_half"
    raw_type = _clean(
        str(
            market.raw_market_json.get("sportsMarketType")
            or market.raw_market_json.get("sportsMarketTypeV2")
            or ""
        )
    )
    if "match_winner" in raw_type or _market_family(market) == "moneyline":
        return "full_match"
    return None


def _affirmative_outcome(market: Market) -> str | None:
    if _election_chamber(market):
        text = _market_text(market)
        for party, canonical in (
            ("democratic", "democratic_party"),
            ("republican", "republican_party"),
        ):
            if re.search(rf"\b{party}(?: party)?\b", text):
                return canonical
    label = _clean(market.outcome_yes_label)
    if label not in {"yes", "no", ""}:
        return label
    title = _clean(market.title)
    if match := re.match(r"will (.+?) win\b", title):
        return match.group(1)
    if match := re.search(r":\s*(.+?) wins?\b", title):
        return match.group(1)
    rules = _clean(f"{market.raw_rules_text} {market.description or ''}")
    if match := re.search(r"settle(?:s|d)? to yes if (.+?) wins?\b", rules):
        return match.group(1)
    return None


def _signed_line(market: Market) -> Decimal | None:
    raw = market.raw_market_json
    if market.venue.value == "kalshi":
        text = str(raw.get("yes_sub_title") or market.subtitle or "")
    else:
        side = next(
            (item for item in raw.get("marketSides", []) if item.get("long") is True),
            {},
        )
        text = str(side.get("description") or "")
    match = re.search(r"(?<![0-9])([+-][0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return Decimal(match.group(1))


def _settlement_policy(market: Market) -> str | None:
    text = _market_text(market)
    if not text:
        return None
    if _election_chamber(market):
        return _election_settlement_policy(market)
    policies: list[str] = []
    cancellation_text = any(
        word in text
        for word in (
            "cancellation",
            "canceled",
            "cancelled",
            "walkover",
            "does not begin",
            "does not occur",
        )
    )
    if "fair market price" in text or "fair price" in text:
        if cancellation_text:
            policies.append("cancel=fair_price")
    elif cancellation_text and re.search(r"(?:\$?0\.50|half)", text):
        policies.append("cancel=half")
    if "within two weeks" in text:
        policies.append("postpone=14d")
    if "unconditionally settled" in text and ("fair market price" in text or "fair price" in text):
        policies.append("retirement=conditional_else_fair")
    if re.search(r"tie.*(?:\$?0\.50|fair market price)", text):
        policies.append("tie=half")
    if has_explicit_binary_fallback(text):
        policies.append("binary=explicit_yes_else_no")
    return "|".join(policies) or None


_QUOTE = "[\"'“”‘’]?"


def has_explicit_binary_fallback(text: str) -> bool:
    """Recognize a complete Yes/No rule despite harmless venue wording differences.

    Some venues (e.g. Polymarket Global) quote the outcome word: `resolve to "Yes" if`.
    The optional quote class here tolerates that without loosening what counts as an
    explicit fallback otherwise.
    """
    normalized = _clean(text)
    yes_rule = bool(
        re.search(
            rf"(?:settle|resolve)(?:s|d)?(?: to)? {_QUOTE}yes{_QUOTE} if", normalized
        )
        or re.search(
            rf"\bif\b.{{1,1200}}(?:(?:the|this) market )?(?:will )?"
            rf"(?:settle|resolve)(?:s|d)?(?: to)? {_QUOTE}yes\b",
            normalized,
        )
        or re.search(
            rf"then[\s,:;-]*(?:(?:the|this) )?market (?:will )?"
            rf"(?:settle|resolve)(?:s|d)?(?: to)? {_QUOTE}yes\b",
            normalized,
        )
    )
    no_rule = bool(
        re.search(
            rf"otherwise[\s,:;-]*(?:(?:the|this) market )?(?:will )?"
            rf"(?:settle|resolve)(?:s|d)?(?: to)? {_QUOTE}no\b",
            normalized,
        )
    )
    return yes_rule and no_rule


def _participants(market: Market) -> list[str]:
    if market.participants:
        return [_clean(value) for value in market.participants]
    text = f"{market.title} {market.raw_rules_text}"
    match = re.search(r"(?:the |between )([A-Za-z .'-]+?)\s+vs\.?\s+([A-Za-z .'-]+?)(?: professional| game| match| originally| after|$)", text, re.IGNORECASE)
    if match:
        return sorted({_clean(match.group(1)), _clean(match.group(2))})
    return []


def deterministic_settlement_blockers(market: Market) -> list[str]:
    """Return explicit reasons a contract is not ready for deterministic comparison."""
    fingerprint = build_fingerprint(market)
    blockers: list[str] = []
    for field in (
        "event_subject",
        "event_date",
        "market_type",
        "contract_scope",
        "affirmative_outcome",
        "settlement_policy",
        "resolution_source",
    ):
        value = getattr(fingerprint, field)
        if value is None or value in {"", "unknown"}:
            blockers.append(f"MISSING_{field.upper()}")
    if fingerprint.settlement_policy and "fair_price" in fingerprint.settlement_policy:
        blockers.append("NON_GUARANTEED_SETTLEMENT_POLICY")
    return blockers


def _canonical_event_subject(
    market: Market, event_date: str | None, participants: list[str]
) -> str:
    if chamber := _election_chamber(market):
        cycle = _election_cycle(market)
        return f"us_{chamber}_control|{cycle or event_date or 'unknown'}"
    if participants and event_date:
        return "|".join(participants) + "|" + str(event_date)
    return _clean(market.event_action) + "|" + str(event_date)


def _market_text(market: Market) -> str:
    raw = market.raw_market_json
    return _clean(
        " ".join(
            str(value)
            for value in (
                market.title,
                market.subtitle or "",
                raw.get("question") or "",
                market.description or "",
                market.raw_rules_text,
                market.event_subject,
                market.event_action,
                market.venue_market_id,
            )
            if value
        )
    )


def _election_chamber(market: Market) -> str | None:
    text = _market_text(market)
    house = r"(?:u\.?s\.?\s+house|united states house(?: of representatives)?|house of representatives)"
    senate = r"(?:u\.?s\.?\s+senate|united states senate)"
    control = r"(?:wins?|winning|winner|party)?\s*control(?:led)?"
    if re.search(rf"(?:{control}.{{0,60}}{house}|{house}.{{0,60}}{control})", text):
        return "house"
    if re.search(rf"(?:{control}.{{0,60}}{senate}|{senate}.{{0,60}}{control})", text):
        return "senate"
    return None


def _election_cycle(market: Market) -> str | None:
    text = _market_text(market)
    if match := re.search(r"\b(20\d{2})\b.{0,40}\b(?:midterm|election)\b", text):
        return match.group(1)
    if match := re.search(r"\b(?:midterm|election)\b.{0,40}\b(20\d{2})\b", text):
        return match.group(1)
    # Kalshi's chamber-control listings say "win the House in 2026" without the
    # word election/midterm anywhere in title or rules (CONTROLH-2026, verified
    # live 2026-08-19); without this the subject falls back to close_time and
    # can never pair with the Polymarket cycle-stamped twin.
    if match := re.search(r"\b(?:in|after)\s+(?:the\s+)?(20\d{2})\b", text):
        return match.group(1)
    if market.measurement_period and (
        match := re.search(r"\b(20\d{2})\b", market.measurement_period)
    ):
        return match.group(1)
    return None


def _election_settlement_policy(market: Market) -> str | None:
    text = _market_text(market)
    policies: list[str] = []
    for marker, pattern in (
        ("control=majority_voting_seats", r"majority of (?:the chamber.s )?voting seats"),
        ("runoffs=included", r"runoff(?: result|s for qualifying elections?)"),
        ("seat_attribution=elected_party", r"attributed to the party under which the member was elected"),
        ("independents=formal_caucus", r"independents who formally caucus"),
        ("independent_deadline=jan4_2359_et", r"january 4th, 11:59 pm et"),
        ("tie=first_elected_speaker_party", r"first elected speaker of the house"),
        ("tie=half_seats_plus_vp", r"half of the voting seats and holds the vice presidency"),
        ("ambiguous=first_president_pro_tempore_party", r"first selected president pro tempore"),
        ("senate_vacancies=most_recent_holder_party", r"vacant seats.{0,180}most recent member to hold that seat"),
        ("senate_uncontested=election_day_party", r"seats not contested.{0,180}date of the election"),
    ):
        if re.search(pattern, text):
            policies.append(marker)
    return "|".join(policies) or None

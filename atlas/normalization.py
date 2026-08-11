import re
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from atlas.models import Market

MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
MONTHS.update({name[:3].lower(): index for name, index in list(MONTHS.items())})

STATE_CODES = {
    "alabama": "al",
    "alaska": "ak",
    "arizona": "az",
    "arkansas": "ar",
    "california": "ca",
    "colorado": "co",
    "connecticut": "ct",
    "delaware": "de",
    "florida": "fl",
    "georgia": "ga",
    "hawaii": "hi",
    "idaho": "id",
    "illinois": "il",
    "indiana": "in",
    "iowa": "ia",
    "kansas": "ks",
    "kentucky": "ky",
    "louisiana": "la",
    "maine": "me",
    "maryland": "md",
    "massachusetts": "ma",
    "michigan": "mi",
    "minnesota": "mn",
    "mississippi": "ms",
    "missouri": "mo",
    "montana": "mt",
    "nebraska": "ne",
    "nevada": "nv",
    "new hampshire": "nh",
    "new jersey": "nj",
    "new mexico": "nm",
    "new york": "ny",
    "north carolina": "nc",
    "north dakota": "nd",
    "ohio": "oh",
    "oklahoma": "ok",
    "oregon": "or",
    "pennsylvania": "pa",
    "rhode island": "ri",
    "south carolina": "sc",
    "south dakota": "sd",
    "tennessee": "tn",
    "texas": "tx",
    "utah": "ut",
    "vermont": "vt",
    "virginia": "va",
    "washington": "wa",
    "west virginia": "wv",
    "wisconsin": "wi",
    "wyoming": "wy",
}

STATION_ALIASES = {
    "cliokc": "kokc",
    "clihou": "khou",
    "clisat": "ksat",
    "clinyc": "knyc",
    "climia": "kmia",
    "climdw": "kmdw",
    "clilax": "klax",
    "clisfo": "ksfo",
}


def specialized_terms(market: Market) -> dict[str, object]:
    for normalizer in (
        _economic_terms,
        _weather_terms,
        _crypto_terms,
        _election_terms,
    ):
        if terms := normalizer(market):
            return terms
    return {}


def _economic_terms(market: Market) -> dict[str, object] | None:
    text = _text(market)
    market_source = _market_source(market)
    period = _month_period(text, market.venue_market_id)
    threshold, upper, operator = _threshold_terms(market, text)
    if re.search(r"\b(?:cpi yoy|consumer price index|inflation).{0,100}\b(?:12-month|yoy)\b", text):
        if not period:
            return None
        policies = []
        if "subsequent revisions" in text and "not be considered" in text:
            policies.append("revision=first_official_release")
        if "first figure officially published" in text and "within three months" in text:
            policies.append("missing=first_within_3m_else_previous_month")
        return {
            "event_subject": f"us_cpi_yoy|{period}",
            "event_date": period,
            "event_action": "published_value",
            "market_type": "economic",
            "contract_scope": "cpi_yoy_not_seasonally_adjusted",
            "affirmative_outcome": "predicate_true",
            "threshold": threshold,
            "threshold_upper": upper,
            "threshold_operator": operator,
            "threshold_unit": "percent",
            "measurement_period": period,
            "geography": "us",
            "resolution_source": (
                "us_bls_cpi"
                if "bls" in text or "bureau of labor statistics" in market_source
                else None
            ),
            "revision_policy": "first_official_release" if policies else None,
            "settlement_policy": "|".join(policies) or None,
        }
    unemployment = re.search(r"\b(canada|u\.?s\.?|united states) unemployment rate\b", text)
    if unemployment and period:
        jurisdiction = "ca" if unemployment.group(1) == "canada" else "us"
        source = (
            "statistics_canada"
            if "statistics canada" in text or "statistics canada" in market_source
            else "trading_economics"
            if "trading economics" in market_source
            else "us_bls"
            if "bls" in text or "bureau of labor statistics" in market_source
            else _named_authority_source(market.resolution_source)
        )
        return {
            "event_subject": f"{jurisdiction}_unemployment_rate|{period}",
            "event_date": period,
            "event_action": "published_value",
            "market_type": "economic",
            "contract_scope": "unemployment_rate",
            "affirmative_outcome": "predicate_true",
            "threshold": threshold,
            "threshold_upper": upper,
            "threshold_operator": operator,
            "threshold_unit": "percent",
            "measurement_period": period,
            "geography": jurisdiction,
            "resolution_source": source,
        }
    if (
        "federal reserve" in text
        and "upper bound" in text
        and re.search(r"\b(?:increases?|decreases?)\b", text)
    ):
        direction = "decrease" if "decrease" in text else "increase"
        horizon = _deadline(text) or _date_from_market(market)
        return {
            "event_subject": f"us_fed_target_upper_bound|{horizon or 'unknown'}",
            "event_date": horizon,
            "event_action": direction,
            "market_type": "economic",
            "contract_scope": "federal_funds_target_upper_bound",
            "affirmative_outcome": "predicate_true",
            "measurement_period": horizon,
            "geography": "us",
            "resolution_source": "federal_reserve",
        }
    return None


def _weather_terms(market: Market) -> dict[str, object] | None:
    text = _text(market)
    if not (
        "temperature recorded" in text
        and ("national weather service" in text or "the weather company" in text)
    ):
        return None
    date = _iso_date(text) or _date_from_market(market)
    station = _station(text)
    threshold, upper, operator = _threshold_terms(market, text)
    action = (
        "daily_max_temperature"
        if re.search(r"\b(?:maximum|highest) temperature\b", text)
        else "daily_min_temperature"
        if re.search(r"\b(?:minimum|lowest) temperature\b", text)
        else "point_temperature"
    )
    period = (
        date if action.startswith("daily_") else _exact_timestamp(text) or market.measurement_period
    )
    source = (
        "nws_climatological_report_daily"
        if "national weather service" in text
        else f"weather_company:{station or 'unknown'}"
    )
    revision = (
        "latest_final_report"
        if "latest version" in text or "official and final value" in text
        else None
    )
    scope = f"{action}|station={station or 'unknown'}"
    return {
        "event_subject": f"weather_temperature|{station or 'unknown'}|{period or 'unknown'}",
        "event_date": date,
        "event_action": action,
        "market_type": "weather",
        "contract_scope": scope,
        "affirmative_outcome": "predicate_true",
        "threshold": threshold,
        "threshold_upper": upper,
        "threshold_operator": operator,
        "threshold_unit": "fahrenheit",
        "measurement_period": period,
        "geography": station,
        "resolution_source": source,
        "revision_policy": revision,
    }


def _crypto_terms(market: Market) -> dict[str, object] | None:
    text = _text(market)
    asset = _crypto_asset(text, market.venue_market_id)
    source = _crypto_source(text)
    price_context = bool(
        re.search(
            r"\b(?:price of (?:bitcoin|btc|ethereum|ether|eth|xrp|dogecoin|doge|solana)"
            r"|(?:bitcoin|btc|ethereum|ether|eth|xrp|dogecoin|doge|solana) price)\b",
            text,
        )
    )
    if not asset or (not source and not price_context):
        return None
    threshold, upper, operator = _threshold_terms(market, text)
    any_point = "at any point" in text or "trimmed mean crosses" in text
    direction = "simple average" in text and "at least the simple average" in text
    action = "price_direction" if direction else "price_crossed" if any_point else "price_at"
    period = _deadline(text) if any_point else _exact_timestamp(text) or market.measurement_period
    method = (
        "60s_trimmed_mean_20pct"
        if "excluding the top 20% and bottom 20%" in text
        else "60s_simple_average"
        if "sixty seconds" in text or "60 rti prices" in text
        else "unknown_method"
    )
    return {
        "event_subject": f"crypto_price|{asset}|{period or 'unknown'}",
        "event_date": period,
        "event_action": action,
        "market_type": "crypto",
        "contract_scope": f"{action}|{method}",
        "affirmative_outcome": "predicate_true",
        "threshold": threshold,
        "threshold_upper": upper,
        "threshold_operator": operator,
        "threshold_unit": "usd",
        "measurement_period": period,
        "geography": asset,
        "resolution_source": source,
    }


def _election_terms(market: Market) -> dict[str, object] | None:
    text = _text(market)
    if not re.search(
        r"\b(?:election|nomination|nominee|primary|race|drop(?:s|ped)? out)\b",
        text,
    ):
        return None
    office, jurisdiction = _election_office(text, market.venue_market_id)
    if not office or not jurisdiction:
        return None
    question = str(market.raw_market_json.get("question") or "").lower()
    withdrawal = bool(re.search(r"\bdrop(?:s|ped)? out\b|\bwithdraw(?:s|n|al)?\b", text))
    runoff = "qualify for the runoff" in text or "qualify for a runoff" in text
    vote_share = "popular vote" in text and ("percentage" in text or "%" in text)
    primary = "election winner" not in question and ("primary" in text or "nomination" in text)
    date = _deadline(text) if withdrawal else _iso_date(text) or _date_from_market(market)
    action = (
        "candidate_withdrawal"
        if withdrawal
        else "runoff_qualification"
        if runoff
        else "primary_vote_share"
        if vote_share
        else "nomination_winner"
        if primary
        else "election_winner"
    )
    threshold, upper, operator = _threshold_terms(market, text)
    source = _election_source(market, text)
    outcome = (
        _election_candidate(market, text)
        if withdrawal or runoff or vote_share
        else _election_outcome(market, text)
    )
    scope = f"{office}:{jurisdiction}"
    if vote_share:
        scope += "|popular_valid_vote_share"
    return {
        "event_subject": f"us_election|{action}|{office}:{jurisdiction}|{date or 'unknown'}",
        "event_date": date,
        "event_action": action,
        "market_type": "election",
        "contract_scope": scope,
        "affirmative_outcome": outcome,
        "threshold": threshold if vote_share else None,
        "threshold_upper": upper if vote_share else None,
        "threshold_operator": operator if vote_share else None,
        "threshold_unit": "percent" if vote_share else None,
        "measurement_period": date,
        "geography": jurisdiction,
        "resolution_source": source,
    }


def _text(market: Market) -> str:
    raw = market.raw_market_json
    return re.sub(
        r"\s+",
        " ",
        " ".join(
            str(value)
            for value in (
                market.title,
                market.subtitle or "",
                raw.get("question") or "",
                raw.get("yes_sub_title") or "",
                market.description or "",
                market.raw_rules_text,
                market.venue_market_id,
            )
            if value
        ).lower(),
    ).strip()


def _number(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def _threshold_terms(
    market: Market, text: str
) -> tuple[Decimal | None, Decimal | None, str | None]:
    raw = market.raw_market_json
    source_texts = [value for value in (market.raw_rules_text.lower().strip(), text) if value]
    for source_text in source_texts:
        if match := re.search(
            r"\bbetween\s+\$?([0-9][0-9,.]*)\s*(?:°?f|fahrenheit|%)?\s*"
            r"(?:and|to|-)\s*\$?([0-9][0-9,.]*)",
            source_text,
        ):
            return _number(match.group(1)), _number(match.group(2)), "between_inclusive"
    if str(raw.get("strike_type") or "").lower() == "between":
        lower, upper = raw.get("floor_strike"), raw.get("cap_strike")
        return (
            Decimal(str(lower)) if lower is not None else market.threshold,
            Decimal(str(upper)) if upper is not None else market.threshold_upper,
            "between_inclusive",
        )
    patterns = (
        (">=", r"\b(?:at least|or above)\s+\$?([0-9][0-9,.]*)"),
        ("<=", r"\b(?:at most|or below)\s+\$?([0-9][0-9,.]*)"),
        (">", r"\b(?:above|greater than)\s+\$?([0-9][0-9,.]*)"),
        ("<", r"\b(?:below|less than)\s+\$?([0-9][0-9,.]*)"),
    )
    for source_text in source_texts:
        for operator, pattern in patterns:
            if match := re.search(pattern, source_text):
                return _number(match.group(1)), None, operator
    return market.threshold, market.threshold_upper, market.threshold_operator


def _month_period(text: str, identifier: str) -> str | None:
    for pattern in (
        r"(?:ending in|for|in)\s+([a-z]+)\s+(20\d{2})",
        r"\b([a-z]+)\s+(20\d{2})\b",
    ):
        if match := re.search(pattern, text):
            month = MONTHS.get(match.group(1).lower())
            if month:
                return f"{match.group(2)}-{month:02d}"
    if match := re.search(r"-(20\d{2})-(\d{2})-\d{2}", identifier):
        return f"{match.group(1)}-{match.group(2)}"
    return None


def _iso_date(text: str) -> str | None:
    if match := re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text):
        return match.group(1)
    if match := re.search(r"\b([a-z]+)\s+(\d{1,2}),\s*(20\d{2})\b", text):
        month = MONTHS.get(match.group(1))
        if month:
            return f"{match.group(3)}-{month:02d}-{int(match.group(2)):02d}"
    return None


def _date_from_market(market: Market) -> str | None:
    value = market.measurement_period
    return value.split("T", 1)[0] if value and "T" in value else value


def _deadline(text: str) -> str | None:
    if match := re.search(
        r"(?:by|before|until)\s+(?:\d{1,2}:\d{2}\s*(?:am|pm)\s*(?:et|edt|est)\s+on\s+)?([a-z]+\s+\d{1,2},\s*20\d{2})",
        text,
    ):
        return _date_text_to_iso(match.group(1))
    return None


def _exact_timestamp(text: str) -> str | None:
    match = re.search(
        r"(?:at|before)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(et|edt|est).*?"
        r"(?:on\s+)?([a-z]+\s+\d{1,2},\s*20\d{2})",
        text,
    )
    if not match:
        return None
    date = _parse_date_text(match.group(5))
    hour = int(match.group(1)) % 12 + (12 if match.group(3) == "pm" else 0)
    local = date.replace(
        hour=hour, minute=int(match.group(2) or 0), tzinfo=ZoneInfo("America/New_York")
    )
    return local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


def _date_text_to_iso(value: str) -> str:
    return _parse_date_text(value).date().isoformat()


def _parse_date_text(value: str) -> datetime:
    """Parse a calendar date; pinned to UTC (callers re-anchor or take .date())."""
    normalized = re.sub(r"\bSept\b", "Sep", value.title())
    for date_format in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(normalized, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"unsupported market date: {value}")


def _station(text: str) -> str | None:
    codes = re.findall(r"\b(?:cli[a-z]{3}|k[a-z]{3})\b", text)
    if not codes:
        return None
    code = codes[0].lower()
    return STATION_ALIASES.get(code, code)


def _crypto_asset(text: str, identifier: str) -> str | None:
    joined = f"{text} {identifier.lower()}"
    for pattern, asset in (
        (r"\b(?:bitcoin|btc|brti)\b", "btc"),
        (r"\b(?:ethereum|ether|eth|erti|ethusdrti)\b", "eth"),
        (r"\b(?:xrp|xrpusdrti)\b", "xrp"),
        (r"\b(?:dogecoin|doge)\b", "doge"),
        (r"\b(?:solana|sol)\b", "sol"),
    ):
        if re.search(pattern, joined):
            return asset
    return None


def _crypto_source(text: str) -> str | None:
    for token, source in (
        ("ethusdrti", "cf_benchmarks_ethusdrti"),
        ("xrpusdrti", "cf_benchmarks_xrpusdrti"),
        ("dogeusd_rti", "cf_benchmarks_dogeusd_rti"),
        ("brti", "cf_benchmarks_brti"),
        ("erti", "cf_benchmarks_erti"),
    ):
        if token in text:
            return source
    if "cf benchmarks" in text:
        return "cf_benchmarks_unknown_index"
    return None


def _election_office(text: str, identifier: str) -> tuple[str | None, str | None]:
    joined = f"{text} {identifier.lower()}"
    district = re.search(r"\b([a-z]{2})-(\d{1,2})\b", joined)
    if district:
        return "house", f"{district.group(1)}-{int(district.group(2)):02d}"
    state = next(
        (code for name, code in STATE_CODES.items() if re.search(rf"\b{name}\b", joined)), None
    )
    if state is None:
        slug = re.search(r"\b(?:usgub|usse)-([a-z]{2})\b", identifier.lower())
        state = slug.group(1) if slug else None
    if "governor" in joined or "governorship" in joined or "usgub" in joined:
        return "governor", state
    if re.search(r"\b(?:u\.?s\.? senator|senate election)\b", joined) or "usse" in joined:
        return "senate", state
    return None, None


def _election_outcome(market: Market, text: str) -> str | None:
    if "democratic party nominee wins" in text or re.search(
        r"\bdemocratic party\b", market.title.lower()
    ):
        return "democratic_party"
    if "republican party nominee wins" in text or re.search(
        r"\brepublican party\b", market.title.lower()
    ):
        return "republican_party"
    label = market.raw_market_json.get("yes_sub_title") or market.outcome_yes_label
    if str(label).lower() not in {"yes", "no", ""}:
        return re.sub(r"\s+", " ", str(label).lower().strip())
    if match := re.search(r"\bif (.+?) wins? (?:the )?nomination\b", text):
        return match.group(1).strip()
    return None


def _election_candidate(market: Market, text: str) -> str | None:
    title = market.title.lower()
    for pattern in (
        r"\bwill (.+?) (?:drop out|receive|qualify)",
        r"\bvote received by (.+?) in\b",
        r"\bif (.+?) (?:drops out|withdraws?)\b",
        r"\bif (.+?) is announced to qualify\b",
    ):
        if match := re.search(pattern, f"{title} {text}"):
            return re.sub(r"\s+", " ", match.group(1).strip())
    return None


def _election_source(market: Market, text: str) -> str | None:
    if "party authorities" in text:
        return "relevant_party_authorities"
    if "government authorities" in text:
        return "relevant_government_authorities"
    source = _market_source(market)
    if match := re.search(r"([a-z ]+) secretary of state", source):
        state = STATE_CODES.get(match.group(1).strip())
        return f"state_election_authority:{state or match.group(1).strip().replace(' ', '_')}"
    return _named_authority_source(market.resolution_source)


def _market_source(market: Market) -> str:
    return re.sub(r"\s+", " ", str(market.resolution_source or "").lower().strip())


def _named_authority_source(value: object) -> str | None:
    source = re.sub(r"\s+", " ", str(value or "").lower().strip())
    if source in {"", "unknown"}:
        return None
    parts = sorted(
        re.sub(r"[^a-z0-9]+", "_", item).strip("_") for item in source.split("|") if item.strip()
    )
    return "named_sources:" + "+".join(parts)

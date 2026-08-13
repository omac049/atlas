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
    if fomc := _fomc_decision_bucket_terms(text):
        return fomc
    if level := _fed_funds_level_terms(text):
        return level
    # CPI runs after the more specific macro families so a fed/unemployment
    # contract whose commentary merely mentions CPI cannot be captured here.
    if cpi := _cpi_family_terms(
        (market.title or "").lower(), text, market_source, period, threshold, upper, operator
    ):
        return cpi
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


def _fomc_decision_bucket_terms(text: str) -> dict[str, object] | None:
    """Canonicalize per-meeting FOMC rate-change bucket contracts across venues.

    Kalshi phrases the bucket as `the Federal Reserve does a Hike of 25bps` /
    `Hike rates by 25bps at their July 2026 meeting`; Polymarket as `increase
    interest rates by 25 bps after the July 2026 meeting`. Both are anchored to a
    single scheduled meeting, unlike cumulative `rate cut by <deadline>` contracts,
    so the trigger requires both a bucket phrase and a per-meeting reference.
    """
    bucket = re.search(
        r"(?:federal reserve|fed)(?:’s| will|'s)?\s+"
        r"(?:does a\s+|will\s+)?(hike|cut|increase|decrease)s?"
        r"(?:\s+of|\s+(?:interest\s+)?rates?\s+by)?\s+"
        r"(>?)(\d+(?:\.\d+)?)(\+?)\s*bps",
        text,
    )
    no_change = None
    if not bucket:
        # Polymarket's zero bucket carries no bps phrase at all: "Will there be
        # no change in Fed interest rates after the September 2026 meeting?"
        no_change = re.search(
            r"\bno change in (?:the )?fed(?:eral reserve)?(?:['’]s)?\s+(?:interest\s+)?rates?\b",
            text,
        )
        if not no_change:
            return None
    meeting = re.search(
        r"(?:at their|after the|for their|at the)\s+"
        rf"({'|'.join(MONTHS)})\.?\s+(20\d{{2}})\s+meeting",
        text,
    ) or re.search(
        rf"(?:on|meeting scheduled for)\s+({'|'.join(MONTHS)})\.?\s+\d{{1,2}}"
        rf"(?:\s*-\s*\d{{1,2}})?,\s+(20\d{{2}})",
        text,
    )
    if not meeting:
        return None
    period = f"{meeting.group(2)}-{MONTHS[meeting.group(1)]:02d}"
    if no_change:
        magnitude = Decimal(0)
        direction = "maintain"
        operator = "="
    else:
        magnitude = Decimal(bucket.group(3))
        direction = {"hike": "increase", "cut": "decrease"}.get(bucket.group(1), bucket.group(1))
        if magnitude == 0:
            direction = "maintain"
        operator = ">" if bucket.group(2) else ">=" if bucket.group(4) else "="
    # Only outcome-determining policies become tokens; structural notes like
    # "mutually exclusive" are already encoded by the exact-match operator.
    policies = []
    if re.search(
        r"meeting is canceled and does not occur.{0,120}maintains rate.{0,60}resolve to yes", text
    ) or re.search(r"no statement is released.{0,120}no change.{0,60}bracket", text):
        policies.append("no_meeting=no_change_bucket")
    if re.search(r"rounded up to the nearest 25", text):
        policies.append("rounding=up_nearest_25bps")
    return {
        "event_subject": f"us_fomc_rate_decision|{period}",
        "event_date": period,
        "event_action": "rate_change_bucket",
        "market_type": "economic",
        "contract_scope": "fomc_rate_change_bucket",
        "affirmative_outcome": direction,
        "threshold": magnitude,
        "threshold_operator": operator,
        "threshold_unit": "bps",
        "measurement_period": period,
        "geography": "us",
        "resolution_source": "federal_reserve",
        "settlement_policy": "|".join(policies) or None,
    }


def _fed_funds_level_terms(text: str) -> dict[str, object] | None:
    """Canonicalize fed funds target upper-bound *level* contracts across venues.

    Kalshi phrases the level as a strict threshold — per-meeting KXFED (`be above
    3.50% following the Fed's Dec 9, 2026 meeting`) or year-end KXFEDFUNDSYEAR
    (`in effect at 11:59 PM ET on December 31, 2027 be above 5.75%`). Polymarket
    lists exact-level buckets with tail operators (`be [≥|≤]3.5% at the end of
    2026`) whose published rules anchor resolution to the December FOMC meeting
    with a Dec-31 snapshot fallback. Anchors are namespaced (`meeting:` vs
    `snapshot:`) because those are different published measurement events.
    """
    level = re.search(
        r"upper bound of the (?:target )?(?:range for the )?(?:target )?federal funds "
        r"(?:rate|range)\b.{0,80}?\bbe\s+(above\s+|greater than\s+|≥\s*|≤\s*)?"
        r"(\d+(?:\.\d+)?)\s*%",
        text,
    )
    if not level:
        return None
    month_names = "|".join(MONTHS)
    meeting = re.search(
        rf"following the fed(?:eral reserve)?['’]?s?\s+({month_names})\.?\s+"
        r"(\d{1,2}),\s*(20\d{2})\s+meeting",
        text,
    ) or re.search(
        rf"meeting,? currently scheduled for\s+({month_names})\.?\s+"
        r"(?:\d{1,2}\s*-\s*)?(\d{1,2}),\s*(20\d{2})",
        text,
    )
    snapshot = re.search(
        rf"in effect at 11:59 pm et on\s+({month_names})\.?\s+(\d{{1,2}}),\s*(20\d{{2}})",
        text,
    )
    if meeting:
        anchor_date = f"{meeting.group(3)}-{MONTHS[meeting.group(1)]:02d}-{int(meeting.group(2)):02d}"
        anchor = f"meeting:{anchor_date}"
    elif snapshot:
        anchor_date = (
            f"{snapshot.group(3)}-{MONTHS[snapshot.group(1)]:02d}-{int(snapshot.group(2)):02d}"
        )
        anchor = f"snapshot:{anchor_date}"
    else:
        return None
    qualifier = (level.group(1) or "").strip()
    operator = {
        "above": ">",
        "greater than": ">",
        "≥": ">=",
        "≤": "<=",
    }.get(qualifier, "=")
    # Only outcome-determining published policies become tokens.
    policies = []
    if re.search(
        r"no fomc decision.{0,160}?resolve according to the upper bound of the target "
        r"federal funds range at that time",
        text,
    ):
        policies.append("no_decision=year_end_rate_snapshot")
    if "rounded to the nearest 25 basis points" in text:
        policies.append(
            "rounding=nearest_25bps_away_from_zero"
            if "rounded away from zero" in text
            else "rounding=nearest_25bps"
        )
    if re.search(
        r"single target rate rather than a target range.{0,40}?target rate will be used", text
    ):
        policies.append("single_rate=target_rate_used")
    return {
        "event_subject": f"us_fed_funds_upper_bound|{anchor}",
        "event_date": anchor_date,
        "event_action": "published_level",
        "market_type": "economic",
        "contract_scope": "fed_funds_upper_bound_level",
        "affirmative_outcome": "predicate_true",
        "threshold": Decimal(level.group(2)),
        "threshold_operator": operator,
        "threshold_unit": "percent",
        "measurement_period": anchor,
        "geography": "us",
        "resolution_source": "federal_reserve" if "federal reserve" in text else None,
        "settlement_policy": "|".join(policies) or None,
    }


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
    period = (
        _deadline(text)
        if any_point
        else _exact_timestamp(text)
        or _candle_anchor_timestamp(text, market.venue_market_id)
        or market.measurement_period
    )
    method = (
        "60s_trimmed_mean_20pct"
        if "excluding the top 20% and bottom 20%" in text
        else "60s_simple_average"
        if "sixty seconds" in text or "60 rti prices" in text
        else "1m_candle_close"
        if "1 minute candle" in text and "close" in text
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


# Non-US jurisdictions whose CPI/inflation contracts must not be filed under a
# us_cpi_* subject. Contracts naming one of these without any US/BLS marker fall
# through to generic terms; misfiled pairs were already refused on source and
# guarantee gates, so this is taxonomy honesty rather than a label-safety fix.
_CPI_FOREIGN_JURISDICTION = re.compile(
    r"\b(?:uk|united kingdom|britain|british|china|chinese|brazil|brazilian|canada|"
    r"canadian|eurozone|euro area|european|germany|france|india|japan|mexico|korea|"
    r"australia|argentina|turkey|russia|poland|sweden|norway|switzerland|colombia|"
    r"chile|peru|indonesia|philippines|thailand|vietnam|south africa|nigeria|egypt|"
    r"ipca|ibge|ons|nbs|indec|tuik|rosstat)\b"
)

_CPI_YOY_TRIGGER = re.compile(
    r"\b(?:cpi yoy|consumer price index|inflation).{0,100}"
    r"\b(?:12[- ]month|yoy|twelve months|year ending)\b"
)


def _cpi_family_terms(
    title: str,
    text: str,
    market_source: str,
    period: str | None,
    threshold: Decimal | None,
    upper: Decimal | None,
    operator: str | None,
) -> dict[str, object] | None:
    """US CPI family: headline/core x YoY/MoM, from published wording only.

    The YoY window is announced explicitly ("12-month", "twelve months ending",
    "year ending in"). Absent those markers, a CPI change bucket anchored to a
    single month ("increases by more than 0.1% (single-decimal) in July 2026",
    "one-month percent change") is the month-over-month series. Core variants
    are announced by the published "less/excluding food and energy" phrasing.
    Adjustment basis is encoded in scope only when the venue publishes it —
    Kalshi's headline-MoM rules state no basis, and that stays visible as a
    scope difference rather than being inferred away.
    """
    yoy = _CPI_YOY_TRIGGER.search(text)
    mom = None
    if not yoy:
        # The bare-co-occurrence form ("cpi" anywhere + "rise" anywhere) captured
        # unrelated markets whose commentary mentions CPI; require the CPI
        # reference, the change verb, and a percent value in proximity.
        mom = (
            re.search(r"\bone-month percent(?:age)? change\b", text)
            or re.search(r"\bmonthly inflation\b", text)
            or re.search(
                r"\b(?:consumer price index|cpi)\b.{0,160}"
                r"\b(?:increases? by|rises?)\b.{0,40}-?[0-9]+(?:\.[0-9]+)?\s*%",
                text,
            )
        )
    if not (yoy or mom) or not period:
        return None
    us_context = (
        "bureau of labor statistics" in text
        or re.search(r"\bbls\b", text)
        or "bureau of labor statistics" in market_source
        # Dotted form only: the bare word "us" ("contact us") is not a marker.
        or re.search(r"\bu\.s\.?(?![a-z])|\bunited states\b", text)
    )
    if _CPI_FOREIGN_JURISDICTION.search(text) and not us_context:
        return None
    core = bool(
        "less food and energy" in text
        or "excluding food and energy" in text
        or re.search(r"\bcore cpi\b|\bcpi core\b|\bcore inflation\b", text)
    )
    if yoy:
        subject_base = "us_cpi_core_yoy" if core else "us_cpi_yoy"
        scope = (
            "cpi_core_yoy_not_seasonally_adjusted"
            if core
            else "cpi_yoy_not_seasonally_adjusted"
        )
    else:
        subject_base = "us_cpi_core_mom" if core else "us_cpi_mom"
        scope = ("cpi_core_mom" if core else "cpi_mom") + (
            "_seasonally_adjusted" if "seasonally adjusted" in text else ""
        )
    # Title first: the title always states the market's OWN bucket, while
    # descriptions may enumerate sibling buckets whose phrasings would match.
    signed_change = _cpi_change_terms(title)
    if signed_change is None:
        signed_change = _cpi_change_terms(text)
    if signed_change is _UNPARSEABLE_CHANGE:
        # A directional phrasing we do not model: refuse a threshold rather
        # than risk reading it unsigned. No threshold means the leg can never
        # be guarantee-complete, so the pair can never approve.
        threshold, upper, operator = None, None, None
    elif signed_change is not None:
        threshold, upper, operator = signed_change
    else:
        threshold, upper, operator = _cpi_level_terms(text, threshold, upper, operator)
    policies = []
    if "subsequent revisions" in text and "not be considered" in text:
        policies.append("revision=first_official_release")
    if "first figure officially published" in text and "within three months" in text:
        policies.append("missing=first_within_3m_else_previous_month")
    if "most recent previous month" in text and "not released" in text:
        policies.append("missing=previous_month_figures_at_next_release")
    if (
        "one-decimal place value" in text
        or "one decimal point" in text
        or "single-decimal" in text
    ):
        policies.append("precision=bls_one_decimal")
    if "government shutdown" in text and "expiration date will be extended" in text:
        policies.append("delay=shutdown_extension_release_or_6m")
    return {
        "event_subject": f"{subject_base}|{period}",
        "event_date": period,
        "event_action": "published_value",
        "market_type": "economic",
        "contract_scope": scope,
        "affirmative_outcome": "predicate_true",
        "threshold": threshold,
        "threshold_upper": upper,
        "threshold_operator": operator,
        "threshold_unit": "percent",
        "measurement_period": period,
        "geography": "us",
        "resolution_source": (
            "us_bls_cpi"
            if "bls" in text
            or "bureau of labor statistics" in text
            or "bureau of labor statistics" in market_source
            else None
        ),
        "revision_policy": (
            "first_official_release"
            if "revision=first_official_release" in policies
            else None
        ),
        "settlement_policy": "|".join(policies) or None,
    }


# Sentinel: a directional phrasing was present but not modeled — the caller must
# refuse a threshold rather than let an unsigned reading through.
_UNPARSEABLE_CHANGE: tuple = ("UNPARSEABLE",)

_CPI_DOWN_VERB = r"(?:decreases?|falls?|drops?|declines?)"
_CPI_UP_VERB = r"(?:increases?|rises?|gains?|climbs?)"
_CPI_VALUE = r"(-?[0-9]+(?:\.[0-9]+)?)"


def _cpi_change_terms(text: str) -> tuple | None:
    """Signed change phrasings, or None when no directional phrasing is present.

    The sign lives in the verb ("decrease by 0.7% or more" is a change of -0.7
    or lower; "increases by more than 3.1%" is strictly above +3.1). Any
    directional phrasing outside this vocabulary returns ``_UNPARSEABLE_CHANGE``
    so the caller drops the threshold entirely — an unsigned misreading of a
    signed bucket must never produce a comparable fingerprint.
    """
    strict = r"(?:\s+by)?\s+(?:more than|above|over)\s+"
    at_least = r"(?:\s+by)?\s+at least\s+"
    for pattern, sign, operator in (
        (_CPI_DOWN_VERB + strict + _CPI_VALUE + r"\s*%", -1, "<"),
        (_CPI_UP_VERB + strict + _CPI_VALUE + r"\s*%", 1, ">"),
        (_CPI_DOWN_VERB + at_least + _CPI_VALUE + r"\s*%", -1, "<="),
        (_CPI_UP_VERB + at_least + _CPI_VALUE + r"\s*%", 1, ">="),
        (_CPI_DOWN_VERB + r"\s+by\s+" + _CPI_VALUE + r"\s*%\s*or\s+more\b", -1, "<="),
        (_CPI_UP_VERB + r"\s+by\s+" + _CPI_VALUE + r"\s*%\s*or\s+more\b", 1, ">="),
        (_CPI_DOWN_VERB + r"\s+by\s+" + _CPI_VALUE + r"\s*%(?!\s*or\b)", -1, "="),
        (_CPI_UP_VERB + r"\s+by\s+" + _CPI_VALUE + r"\s*%(?!\s*or\b)", 1, "="),
    ):
        if match := re.search(r"\b" + pattern, text):
            return sign * _number(match.group(1)), None, operator
    if re.search(r"\bstays? flat \(0\.0%\)", text):
        return Decimal("0.0"), None, "="
    if re.search(
        r"\b(?:" + _CPI_DOWN_VERB + "|" + _CPI_UP_VERB + r")\s+(?:by\s+)?"
        r"(?:more than\s+|above\s+|over\s+|at least\s+)?-?[0-9]",
        text,
    ):
        return _UNPARSEABLE_CHANGE
    return None


def _cpi_level_terms(
    text: str,
    threshold: Decimal | None,
    upper: Decimal | None,
    operator: str | None,
) -> tuple[Decimal | None, Decimal | None, str | None]:
    """Threshold phrasings specific to published CPI level buckets.

    Polymarket phrases its tails postfix ("3.1% or less", "4.2% or more") and its
    interior buckets as exact one-decimal levels ("be 3.4% in July"), none of which
    the generic prefix patterns in ``_threshold_terms`` can read. The exact-level
    branch only fires when the generic pass found no operator, so Kalshi's strict
    "above 3.1%" phrasing keeps its ``>`` reading.
    """
    value = r"(-?[0-9]+(?:\.[0-9]+)?)"
    if match := re.search(value + r"\s*%\s*or\s+(?:less|lower)\b", text):
        return _number(match.group(1)), None, "<="
    if match := re.search(value + r"\s*%\s*or\s+(?:more|higher)\b", text):
        return _number(match.group(1)), None, ">="
    if operator is None and (match := re.search(r"\bbe\s+" + value + r"\s*%", text)):
        return _number(match.group(1)), None, "="
    return threshold, upper, operator


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
        (">=", r"\b(?:at least|or above)\s+\$?(-?[0-9][0-9,.]*)"),
        ("<=", r"\b(?:at most|or below)\s+\$?(-?[0-9][0-9,.]*)"),
        (">", r"\b(?:above|greater than)\s+\$?(-?[0-9][0-9,.]*)"),
        ("<", r"\b(?:below|less than)\s+\$?(-?[0-9][0-9,.]*)"),
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
    # Polymarket publishes per-pair exchange candles or Chainlink streams; these
    # are genuinely different indices from CF Benchmarks, so distinct source
    # tokens keep cross-venue crypto pairs honestly non-equivalent forever.
    if "binance" in text:
        if "btc/usdt" in text:
            return "binance_btcusdt_1m_close"
        if "eth/usdt" in text:
            return "binance_ethusdt_1m_close"
        return "binance_unknown_pair"
    if "chainlink" in text:
        if "btc/usd" in text:
            return "chainlink_btcusd_stream"
        if "eth/usd" in text:
            return "chainlink_ethusd_stream"
        return "chainlink_unknown_stream"
    return None


def _candle_anchor_timestamp(text: str, identifier: str = "") -> str | None:
    """Polymarket's candle anchor: '…1 minute candle for BTC/USDT 12:00 in the
    ET timezone (noon) on the date specified in the title…' with the calendar
    date carried by the title ('… on August 14?'); year from an explicit year
    in text or from the venue identifier/slug."""
    clock = re.search(
        r"1 minute candle for \w+/\w+\s+(\d{1,2}):(\d{2}) in the et timezone", text
    )
    if not clock:
        return None
    date = re.search(rf"\bon\s+({'|'.join(MONTHS)})\.?\s+(\d{{1,2}})(?:,\s*(20\d{{2}}))?", text)
    if not date:
        return None
    year = date.group(3)
    if not year:
        year_match = re.search(r"\b(20\d{2})\b", text) or re.search(
            r"(20\d{2})", identifier.lower()
        )
        year = year_match.group(1) if year_match else None
    if not year:
        return None
    local = _parse_date_text(f"{date.group(1)} {date.group(2)}, {year}").replace(
        hour=int(clock.group(1)) % 24,
        minute=int(clock.group(2)),
        tzinfo=ZoneInfo("America/New_York"),
    )
    return local.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


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

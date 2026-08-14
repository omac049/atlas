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
    if unemployment := _unemployment_rate_terms(
        market, (market.title or "").lower(), text, market_source, period, threshold, upper, operator
    ):
        return unemployment
    if fomc := _fomc_decision_bucket_terms(text):
        return fomc
    if level := _fed_funds_level_terms(text):
        return level
    if ism := _ism_pmi_terms(market, text, market_source, period):
        return ism
    if payrolls := _payrolls_terms(market, text, market_source):
        return payrolls
    # Core PCE must run before CPI: its texts publish "excluding food and
    # energy" and "core inflation ... 12-month" wording that satisfies the CPI
    # triggers, while its own trigger requires a published personal-consumption-
    # expenditures reference that no CPI text carries.
    if pce := _pce_core_terms(
        (market.title or "").lower(), text, market_source, period, threshold, upper, operator
    ):
        return pce
    if gdp := _gdp_growth_terms(market, text, market_source):
        return gdp
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


# The U-3 designation adjacent to an unemployment-rate reference is the published
# BLS series marker (Kalshi: "unemployment rate (U-3)"; Polymarket: "official
# unemployment rate denoted as U-3"). Proximity is required so a distant U-3
# mention in commentary cannot re-file an unrelated market under the US subject.
_U3_SERIES_MARKER = re.compile(r"\bunemployment rate\b.{0,40}?\bu-3\b")


def _unemployment_rate_terms(
    market: Market,
    title: str,
    text: str,
    market_source: str,
    period: str | None,
    threshold: Decimal | None,
    upper: Decimal | None,
    operator: str | None,
) -> dict[str, object] | None:
    """US U-3 (and explicitly named foreign) unemployment-rate contracts.

    Kalshi's KXU3 rules publish the series and basis inline ("the seasonally
    adjusted unemployment rate (U-3) reported by the Bureau of Labor Statistics
    in the Employment Situation Report") with a strict "above X%" strike and no
    revision, missing-data, or precision clauses — that absence stays visible as
    an empty settlement policy. Polymarket publishes the same subject plus a
    first-release revision freeze, a terminal last-available-month fallback
    (added to its template after Feb 2025 — older events lack it and must not
    look complete), and the one-decimal precision clause, with exact one-decimal
    buckets ("be 4.1%", "be exactly 4.0%") and tails ("be ≤3.9%", "greater than
    or equal to 4.3%"). The measurement basis enters the scope only when the
    text publishes it; contracts naming a non-US jurisdiction without the U-3
    marker are never filed under the US subject.
    """
    explicit = re.search(r"\b(canada|u\.?s\.?|united states) unemployment rate\b", text)
    if not period:
        return None
    if explicit and explicit.group(1) == "canada":
        jurisdiction = "ca"
    elif explicit or (
        _U3_SERIES_MARKER.search(text) and not _CPI_FOREIGN_JURISDICTION.search(text)
    ):
        jurisdiction = "us"
    else:
        return None
    scope = "unemployment_rate"
    if (
        jurisdiction == "us"
        and _U3_SERIES_MARKER.search(text)
        and "seasonally adjusted unemployment rate" in text
    ):
        scope = "unemployment_rate_u3_seasonally_adjusted"
    bls_named = bool(
        "bureau of labor statistics" in text
        or re.search(r"\bbls\b", text)
        or "bureau of labor statistics" in market_source
    )
    source = (
        "statistics_canada"
        if "statistics canada" in text or "statistics canada" in market_source
        else "trading_economics"
        if "trading economics" in market_source
        else "us_bls_employment_situation"
        if bls_named and "employment situation report" in text
        else "us_bls"
        if bls_named
        else _named_authority_source(market.resolution_source)
    )
    # Title first: the title always states the market's OWN bucket, while
    # descriptions may enumerate sibling buckets whose phrasings would match.
    title_level = _cpi_level_terms(title, None, None, None)
    if title_level[2] is not None:
        threshold, upper, operator = title_level
    else:
        threshold, upper, operator = _cpi_level_terms(text, threshold, upper, operator)
    # Only published outcome-determining clauses become tokens; Kalshi's KXU3
    # rules publish none, so its legs keep an empty policy.
    policies = []
    if re.search(r"revisions to the data after the first release will not count", text):
        policies.append("revision=first_official_release")
    if re.search(
        r"no data for the specified month is released by the date the next month", text
    ) and "resolve based on data from the last available month" in text:
        policies.append("missing=last_available_month_at_next_release")
    if "one decimal point" in text and "level of precision" in text:
        policies.append("precision=bls_one_decimal")
    return {
        "event_subject": f"{jurisdiction}_unemployment_rate|{period}",
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
        "geography": jurisdiction,
        "resolution_source": source,
        "revision_policy": (
            "first_official_release"
            if "revision=first_official_release" in policies
            else None
        ),
        "settlement_policy": "|".join(policies) or None,
    }


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
    if not bucket:
        # Polymarket US decision buckets phrase the change against the upper
        # bound with "basis points" spelled out: "increases the upper bound of
        # the target federal funds rate by 50 basis points or more at the July
        # 2026 FOMC meeting" (captured live 2026-08-14).
        bucket_us = re.search(
            r"(increase|decrease)s? the upper bound of the target federal funds rate "
            r"by (>?)(\d+(?:\.\d+)?)\s*basis points( or more)?",
            text,
        )
        bucket = bucket_us
    no_change = None
    if not bucket:
        # Polymarket's zero bucket carries no bps phrase at all: Gamma's "no
        # change in Fed interest rates after the September 2026 meeting" and
        # PM-US's "does not change the upper bound of the target federal funds
        # rate at the July 2026 FOMC meeting".
        no_change = re.search(
            r"\bno change in (?:the )?fed(?:eral reserve)?(?:['’]s)?\s+(?:interest\s+)?rates?\b",
            text,
        ) or re.search(
            r"\bdoes not change the upper bound of the target federal funds rate\b",
            text,
        )
        if not no_change:
            return None
    meeting = re.search(
        r"(?:at their|after the|for their|at the)\s+"
        rf"({'|'.join(MONTHS)})\.?\s+(20\d{{2}})\s+"
        r"(?:federal open market committee\s*)?(?:\(?fomc\)?\s+)?meeting",
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
    elif re.search(
        r"changes smaller than the smallest option of the same direction.{0,40}"
        r"rounded to that smallest option.{0,160}rounded to the nearest displayed option"
        r".{0,80}rounded away from zero",
        text,
    ):
        # Polymarket US publishes a DIFFERENT scheme than Gamma's round-up
        # clause (captured live 2026-08-14 on the Sep/Oct 2026 events; the
        # settled July event publishes no rounding clause at all). The distinct
        # token keeps the signed round-up preimage table from ever applying.
        policies.append("rounding=nearest_bucket_away_from_zero")
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


# The index name must appear as one published phrase ("ISM Manufacturing PMI",
# "US ISM services PMI", "ISM Manufacturing Purchasing Managers' Index") — mere
# co-occurrence of "ism" and "pmi" elsewhere in a description must not trigger.
_ISM_PMI_INDEX = re.compile(
    r"\bism\s+(manufacturing|services)\s+(?:pmi\b|purchasing managers['’]?s?\s+index)"
)

# Sentinel: a comparison phrasing was present but not modeled — the caller must
# refuse the threshold rather than fall through to shared boilerplate text.
_UNMODELED_BUCKET: tuple = ("UNMODELED_BUCKET",)

_ISM_BUCKET_VALUE = r"([0-9]{1,3}(?:\.[0-9])?)"


def _ism_pmi_terms(
    market: Market, text: str, market_source: str, period: str | None
) -> dict[str, object] | None:
    """US ISM PMI family (manufacturing + services), from published wording only.

    Kalshi's manufacturing series (KXISMPMI) publishes "at least X" integer
    strikes and names both the source and precision inline ("as published by
    ISM (one decimal place)") but NO missing-release fallback; its services
    series (KXUSISMSERV) is an older template ("is above 54") that publishes no
    source, precision, or fallback in the rules at all — the event-level
    settlement source is Trading Economics, not ISM. Polymarket lists
    one-decimal brackets ("between 49.0 and 49.9") with "below X" / "at least
    X" tails and publishes the ISM Report On Business source, one-decimal
    precision, and a terminal previous-month missing-release fallback. Each of
    those absences stays visible as a fingerprint difference rather than being
    inferred away.

    Thresholds are parsed from the market's OWN bucket texts only (title, then
    Kalshi's strike subtitle, then rules) — never from the generic prefix pass,
    whose patterns would read Polymarket's shared "a reading above 50 indicates
    expansion" boilerplate as a strike — and an unmodeled comparison phrasing
    refuses the threshold outright so the leg can never look
    guarantee-complete. Manufacturing and services carry distinct subjects and
    scopes; a text naming both indices is ambiguous and is not captured.
    """
    indices = set(_ISM_PMI_INDEX.findall(text))
    if len(indices) != 1 or not period:
        return None
    index = indices.pop()
    bucket = None
    for candidate in (
        (market.title or "").lower(),
        str(market.raw_market_json.get("yes_sub_title") or "").lower(),
        (market.raw_rules_text or "").lower(),
    ):
        bucket = _ism_bucket_terms(candidate)
        if bucket is not None:
            break
    if bucket is None or bucket is _UNMODELED_BUCKET:
        threshold, upper, operator = None, None, None
    else:
        threshold, upper, operator = bucket
    source = (
        # Both venues that resolve off ISM name the "Report On Business" in
        # their published rules; Kalshi's services rules name nothing, so the
        # venue-level Trading Economics settlement source stays visible.
        "ism_report_on_business"
        if "report on business" in text or "as published by ism" in text
        else "trading_economics"
        if "trading economics" in text or "trading economics" in market_source
        else None
    )
    policies = []
    if "most recent previous month" in text and "not released" in text:
        policies.append("missing=previous_month_figures_at_next_release")
    if re.search(r"one decimal (?:place|point)", text):
        policies.append("precision=ism_one_decimal")
    return {
        "event_subject": f"us_ism_{index}_pmi|{period}",
        "event_date": period,
        "event_action": "published_value",
        "market_type": "economic",
        "contract_scope": f"ism_{index}_pmi",
        "affirmative_outcome": "predicate_true",
        "threshold": threshold,
        "threshold_upper": upper,
        "threshold_operator": operator,
        "threshold_unit": "index_points",
        "measurement_period": period,
        "geography": "us",
        "resolution_source": source,
        "settlement_policy": "|".join(policies) or None,
    }


def _ism_bucket_terms(text: str) -> tuple | None:
    """Published PMI bucket phrasings for one candidate text, or a refusal.

    Returns ``None`` when the text carries no comparison at all (the caller
    tries the next candidate text), a ``(threshold, upper, operator)`` tuple
    for a modeled phrasing, and ``_UNMODELED_BUCKET`` when a comparison phrase
    is present but unmodeled — the caller then drops the threshold entirely
    instead of falling through to later texts. The ``indicates`` lookaheads
    keep Polymarket's shared expansion/contraction explainer ("a reading above
    50 indicates expansion... below 50 indicates contraction") from ever being
    read as a strike should rules text be reached.
    """
    if not text:
        return None
    if match := re.search(
        rf"\bbetween\s+{_ISM_BUCKET_VALUE}\s+and\s+{_ISM_BUCKET_VALUE}\b", text
    ):
        return _number(match.group(1)), _number(match.group(2)), "between_inclusive"
    for operator, pattern in (
        (">=", rf"\bat least\s+{_ISM_BUCKET_VALUE}\b"),
        (">=", rf"\b{_ISM_BUCKET_VALUE}\s*\+"),
        ("<", rf"(?:\bbelow\s+|\bless than\s+|<\s*){_ISM_BUCKET_VALUE}\b(?!\s*indicates)"),
        (">", rf"(?:\babove\s+|\bgreater than\s+){_ISM_BUCKET_VALUE}\b(?!\s*indicates)"),
    ):
        if match := re.search(pattern, text):
            return _number(match.group(1)), None, operator
    if re.search(
        r"\b(?:at least|at most|above|below|between|greater than|less than"
        r"|or more|or less|or higher|or lower)\b",
        text,
    ) and re.search(r"[0-9]", text):
        return _UNMODELED_BUCKET
    return None


# The published series phrase ("total non-farm payroll employment", "the change
# in total U.S. nonfarm payroll employment", "nonfarm payrolls") — a market that
# merely says "jobs" never triggers; the payroll series must be named.
_PAYROLLS_SERIES = re.compile(r"\bnon-?farm payrolls?\b")

# Raw job counts: comma thousands separators, ASCII or unicode minus, and the
# venue "k" shorthand ("50k" on Polymarket-Global bucket titles).
_PAYROLLS_NUM = r"([-−]?[0-9][0-9,]*)\s*(k\b)?"


def _payrolls_number(value: str, suffix: str | None) -> Decimal:
    number = _number(value.replace("−", "-"))
    return number * 1000 if suffix else number


def _payrolls_terms(
    market: Market, text: str, market_source: str
) -> dict[str, object] | None:
    """US nonfarm-payrolls change contracts, from published wording only.

    Kalshi's KXPAYROLLS rules publish a strict strike ("the increase in total
    non-farm payroll employment is above 90000 as reported by the Bureau of
    Labor Statistics Monthly Employment Situation Report for the month of July
    2026") and NO revision, missing-data, or precision clauses — that absence
    stays visible as an empty settlement policy. (Known metadata quirk: the
    KXPAYROLLS series-level settlement-source URL points at the BLS PPI release
    page; the rules text names the Employment Situation Report and that text is
    what is tokenized here.) Polymarket-US publishes the same subject with
    strict "Above X" outcomes on its July/August events but "At least X" (>=)
    on its June event — the operator is read per event, never assumed — plus a
    first-print revision exclusion and a terminal three-month previous-month
    fallback. Polymarket-Global lists range buckets ("between 0 and 50k",
    "lose jobs") whose boundary membership is determined only by its published
    exact-boundary-to-higher-bracket rule, alongside a last-available-month
    fallback and no revision clause. The reference month comes from the
    published "for (the month of) July 2026" / "in July 2026" wording, never
    from a release date alone; no venue here publishes a seasonal-adjustment
    basis in its rules text (Polymarket-US only marks "sa" in the slug), so the
    scope stays the bare series. Buckets are parsed title-first so sibling
    enumerations can never re-strike a leg, and unmodeled directional
    phrasings refuse the threshold outright.
    """
    if not _PAYROLLS_SERIES.search(text):
        return None
    bls_named = bool(
        "bureau of labor statistics" in text
        or re.search(r"\bbls\b", text)
        or "bureau of labor statistics" in market_source
    )
    us_context = bls_named or bool(
        # Dotted form only: the bare word "us" ("contact us") is not a marker.
        re.search(r"\bu\.s\.?(?![a-z])|\bunited states\b", text)
    )
    if _CPI_FOREIGN_JURISDICTION.search(text) and not us_context:
        return None
    month_names = "|".join(MONTHS)
    period_match = re.search(
        rf"(?:for the month of|for|in)\s+({month_names})\s+(20\d{{2}})\b", text
    )
    if not period_match:
        # The reference month must come from published text; the release date
        # or the venue identifier alone must never supply it.
        return None
    period = f"{period_match.group(2)}-{MONTHS[period_match.group(1)]:02d}"
    bucket = None
    for candidate in (
        (market.title or "").lower(),
        str(market.raw_market_json.get("yes_sub_title") or "").lower(),
        (market.subtitle or "").lower(),
        (market.raw_rules_text or "").lower(),
    ):
        bucket = _payrolls_bucket_terms(candidate)
        if bucket is not None:
            break
    # A range bucket's boundary membership exists only through the published
    # exact-boundary rule ("falls exactly between two brackets ... higher range
    # bracket" -> [L, U)); without that clause adjacent integer buckets share
    # their endpoints ambiguously and the threshold is refused, not inferred.
    boundary_rule = bool(
        "falls exactly between two brackets" in text and "higher range bracket" in text
    )
    if bucket is None or bucket is _UNMODELED_BUCKET:
        threshold, upper, operator = None, None, None
    else:
        threshold, upper, operator = bucket
        if operator == "range":
            if boundary_rule:
                operator = "between_left_inclusive"
            else:
                threshold, upper, operator = None, None, None
    source = (
        "us_bls_employment_situation"
        if bls_named and re.search(r"employment situation (?:report|summary)", text)
        else "us_bls"
        if bls_named
        else _named_authority_source(market.resolution_source)
    )
    # Only published outcome-determining clauses become tokens; Kalshi's
    # KXPAYROLLS rules publish none, so its legs keep an empty policy.
    policies = []
    if "subsequent revisions" in text and "not be considered" in text:
        policies.append("revision=first_official_release")
    if "released within three months" in text and "most recent previous month" in text:
        policies.append("missing=previous_month_within_3m")
    if re.search(
        r"no data for the specified month is released by the date the next month", text
    ) and "resolve based on data from the last available month" in text:
        policies.append("missing=last_available_month_at_next_release")
    if boundary_rule:
        policies.append("boundary=exact_to_higher_bracket")
    return {
        "event_subject": f"us_nonfarm_payrolls|{period}",
        "event_date": period,
        "event_action": "published_value",
        "market_type": "economic",
        "contract_scope": "nonfarm_payrolls_seasonally_adjusted"
        if "seasonally adjusted" in text
        else "nonfarm_payrolls",
        "affirmative_outcome": "predicate_true",
        "threshold": threshold,
        "threshold_upper": upper,
        "threshold_operator": operator,
        "threshold_unit": "jobs",
        "measurement_period": period,
        "geography": "us",
        "resolution_source": source,
        "revision_policy": (
            "first_official_release"
            if "revision=first_official_release" in policies
            else None
        ),
        "settlement_policy": "|".join(policies) or None,
    }


def _payrolls_bucket_terms(text: str) -> tuple | None:
    """Published payrolls bucket phrasings for one candidate text, or a refusal.

    Returns ``None`` when the text carries no comparison at all (the caller
    tries the next candidate text), a ``(threshold, upper, operator)`` tuple
    for a modeled phrasing (ranges carry the sentinel operator ``"range"`` for
    the caller to resolve against the published boundary rule), and
    ``_UNMODELED_BUCKET`` when a directional or comparison phrase is present
    but unmodeled — the caller then drops the threshold entirely instead of
    falling through to later texts. "lose" phrasings carry the sign: "lose
    more than 50k jobs" is a change below -50,000 and "lose between 0 and 50k
    jobs" is the negated range.
    """
    if not text:
        return None
    if match := re.search(
        rf"\blose\s+between\s+{_PAYROLLS_NUM}\s+and\s+{_PAYROLLS_NUM}\s+jobs\b", text
    ):
        low = _payrolls_number(match.group(1), match.group(2))
        high = _payrolls_number(match.group(3), match.group(4))
        return -high, -low, "range"
    if match := re.search(rf"\bbetween\s+{_PAYROLLS_NUM}\s+and\s+{_PAYROLLS_NUM}\b", text):
        return (
            _payrolls_number(match.group(1), match.group(2)),
            _payrolls_number(match.group(3), match.group(4)),
            "range",
        )
    if match := re.search(rf"\blose\s+more\s+than\s+{_PAYROLLS_NUM}\s+jobs\b", text):
        return -_payrolls_number(match.group(1), match.group(2)), None, "<"
    if match := re.search(rf"\bat least\s+{_PAYROLLS_NUM}", text):
        return _payrolls_number(match.group(1), match.group(2)), None, ">="
    if match := re.search(rf"\babove\s+{_PAYROLLS_NUM}", text):
        return _payrolls_number(match.group(1), match.group(2)), None, ">"
    if re.search(r"\blose\s+jobs\b", text):
        return Decimal(0), None, "<"
    if re.search(
        r"\b(?:at least|at most|above|below|between|more than|less than|fewer than"
        r"|or more|or fewer|or less|lose|shed|drop|decline|fall)\b",
        text,
    ) and re.search(r"[0-9]", text):
        return _UNMODELED_BUCKET
    return None




# The GDP reference must be a published index phrase ("real GDP", "GDP growth")
# anchored to a quarterly period — mere co-occurrence of "gdp" and a quarter
# elsewhere in a description must not trigger.
_GDP_GROWTH_TRIGGER = re.compile(
    r"\b(?:real gdp|gdp growth)\b.{0,120}?\bq[1-4]\s+(?:of\s+)?20\d{2}\b"
)

# A title naming a different published series is that market's own identity: a
# payrolls/CPI/PCE contract whose commentary mentions GDP growth must keep its
# family rather than being re-filed under the US GDP subject.
_GDP_COMPETING_TITLE_SERIES = re.compile(
    r"\b(?:payrolls?|cpi|consumer price|inflation|unemployment|pce|ism|pmi)\b"
)

_GDP_QUARTER = re.compile(r"\bq([1-4])\s+(?:of\s+)?(20\d{2})\b")

_GDP_VALUE = r"(-?[0-9]+(?:\.[0-9]+)?)"


def _gdp_growth_terms(
    market: Market, text: str, market_source: str
) -> dict[str, object] | None:
    """US real GDP growth family (quarterly, BEA), from published wording only.

    Kalshi's KXGDP series publishes strict "more than X" strikes against "the
    BEA's seasonally adjusted and annualized Advance Estimate" plus a
    one-decimal Expiration Value note, but NO revision clause and NO
    missing-release fallback — those absences stay visible. Polymarket US
    publishes ">= / above" outcomes ("At least 1.5%", "Above 1.5%") with an
    explicit revision exclusion and a terminal three-month previous-quarter
    fallback. Polymarket's Gamma events list half-open range buckets ("between
    1.5% and 2.0%") whose published exact-boundary rule sends a value landing
    exactly between two brackets to the higher bracket — that rule becomes a
    settlement-policy token, never an inferred operator change. The published
    estimate vintage (Advance/Second/Third) anchors the subject so different
    vintages can never cross-match, and the seasonally-adjusted-annualized
    basis enters the scope only when the text publishes it. Foreign GDP
    contracts (UK, China, ...) are never filed under the US subject.
    """
    if not _GDP_GROWTH_TRIGGER.search(text):
        return None
    title = (market.title or "").lower()
    if _GDP_COMPETING_TITLE_SERIES.search(title) and "gdp" not in title:
        return None
    quarter = _GDP_QUARTER.search(text)
    if quarter is None:
        return None
    us_context = (
        "bureau of economic analysis" in text
        or re.search(r"\bbea\b", text)
        or "bureau of economic analysis" in market_source
        or re.search(r"\bu\.s\.?(?![a-z])|\bunited states\b|\bus gdp\b", text)
    )
    if not us_context or _CPI_FOREIGN_JURISDICTION.search(text):
        return None
    # Vintage anchor: only a published estimate name may anchor the subject.
    # Advance is checked first because revision/fallback clauses on legs that
    # settle off the Advance Estimate mention the Second/Third Estimates.
    vintage = (
        "advance"
        if "advance estimate" in text
        else "second"
        if "second estimate" in text
        else "third"
        if "third estimate" in text
        else None
    )
    period = f"{quarter.group(2)}-Q{quarter.group(1)}"
    anchor = f"{period}:{vintage}" if vintage else period
    scope = (
        "real_gdp_growth_saar"
        if "seasonally adjusted and annualized" in text
        or "seasonally adjusted annualized" in text
        else "real_gdp_growth"
    )
    # Title first: the title always states the market's OWN bucket, while
    # descriptions may enumerate sibling buckets whose phrasings would match.
    bucket = None
    raw = market.raw_market_json
    for candidate in (
        title,
        str(raw.get("yes_sub_title") or "").lower(),
        str(raw.get("outcome_title") or "").lower(),
        (market.subtitle or "").lower(),
        (market.raw_rules_text or "").lower(),
    ):
        bucket = _gdp_bucket_terms(candidate)
        if bucket is not None:
            break
    if bucket is None or bucket is _UNMODELED_BUCKET:
        threshold, upper, operator = None, None, None
    else:
        threshold, upper, operator = bucket
    source = (
        "us_bea_gdp"
        if "bureau of economic analysis" in text
        or re.search(r"\bbea\b", text)
        or "bureau of economic analysis" in market_source
        else None
    )
    # Only published outcome-determining clauses become tokens; Kalshi's KXGDP
    # rules publish no revision or missing-data branch, so those stay absent.
    policies = []
    if re.search(
        r"subsequent revisions to this figure.{0,80}?not be considered", text
    ) or re.search(r"revisions to gdp report data.{0,80}?not be considered", text):
        policies.append("revision=first_official_release")
    if (
        re.search(r"no qualifying (?:gdp )?figure.{0,60}?within three months", text)
        and "most recent previous quarter" in text
    ):
        policies.append("missing=first_within_3m_else_previous_quarter")
    elif (
        "no official estimate is released by the date the next quarter" in text
        and "most recent previous figure" in text
    ):
        policies.append("missing=first_else_most_recent_at_next_release")
    if "falls exactly between two brackets" in text and "higher range bracket" in text:
        policies.append("boundary=exact_value_to_higher_bracket")
    if "expiration value is the one-decimal value published by the bea" in text:
        policies.append("precision=bea_one_decimal")
    return {
        "event_subject": f"us_real_gdp_growth|{anchor}",
        "event_date": anchor,
        "event_action": "published_value",
        "market_type": "economic",
        "contract_scope": scope,
        "affirmative_outcome": "predicate_true",
        "threshold": threshold,
        "threshold_upper": upper,
        "threshold_operator": operator,
        "threshold_unit": "percent",
        "measurement_period": anchor,
        "geography": "us",
        "resolution_source": source,
        "revision_policy": (
            "first_official_release"
            if "revision=first_official_release" in policies
            else None
        ),
        "settlement_policy": "|".join(policies) or None,
    }


def _gdp_bucket_terms(text: str) -> tuple | None:
    """Published GDP bucket phrasings for one candidate text, or a refusal.

    Returns ``None`` when the text carries no comparison at all (the caller
    tries the next candidate text), a ``(threshold, upper, operator)`` tuple
    for a modeled phrasing, and ``_UNMODELED_BUCKET`` when a comparison phrase
    is present but unmodeled — the caller then drops the threshold entirely so
    the leg can never look guarantee-complete. Every modeled pattern requires
    the percent sign, so Kalshi's bare "increases by more than 4.0" rules
    clause (reached only when no earlier candidate carried the bucket) and
    boilerplate numbers can never be read as a strike. Range buckets keep the
    distinct "between" operator: with Gamma's published exact-boundary rule
    they are half-open intervals, not the inclusive-both-ends buckets other
    families publish on a one-decimal grid.
    """
    if not text:
        return None
    if match := re.search(
        rf"\bbetween\s+{_GDP_VALUE}\s*%?\s+and\s+{_GDP_VALUE}\s*%", text
    ):
        return _number(match.group(1)), _number(match.group(2)), "between"
    for operator, pattern in (
        (">=", rf"\bat least\s+{_GDP_VALUE}\s*%"),
        (">", rf"\b(?:above|more than|greater than)\s+{_GDP_VALUE}\s*%"),
        ("<", rf"\b(?:below|less than)\s+{_GDP_VALUE}\s*%"),
    ):
        if match := re.search(pattern, text):
            return _number(match.group(1)), None, operator
    if re.search(
        r"\b(?:at least|at most|above|below|between|greater than|less than"
        r"|more than|or more|or less|or higher|or lower|exactly)\b",
        text,
    ) and re.search(r"[0-9]", text):
        return _UNMODELED_BUCKET
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

# Core PCE triggers. Both venues publish "excluding food and energy" exactly
# like core CPI, so PCE capture requires the published Personal Consumption
# Expenditures index name adjacent to its change window (or the venue's own
# "Core PCE MoM/YoY" market naming). A CPI contract whose commentary merely
# mentions the PCE index has no such adjacency and keeps its CPI subject; a
# PCE text is kept out of the CPI family purely by dispatch order.
_PCE_INDEX = r"personal consumption expenditures price index"
_PCE_YOY_TRIGGER = re.compile(
    rf"\bcore pce yoy\b"
    rf"|\b{_PCE_INDEX}\b.{{0,120}}\b(?:12[- ]month|yoy|twelve months|year ending)\b"
    rf"|\b(?:12[- ]month|twelve months)\b.{{0,120}}\b{_PCE_INDEX}\b"
)
_PCE_MOM_TRIGGER = re.compile(
    rf"\bcore pce mom\b"
    rf"|\bmonth-over-month percent(?:age)? change\b.{{0,60}}\b{_PCE_INDEX}\b"
    rf"|\b{_PCE_INDEX}\b.{{0,120}}\b(?:1-month|one-month)\b"
)


def _pce_core_terms(
    title: str,
    text: str,
    market_source: str,
    period: str | None,
    threshold: Decimal | None,
    upper: Decimal | None,
    operator: str | None,
) -> dict[str, object] | None:
    """US core PCE (MoM and YoY), from published wording only.

    Kalshi's KXPCECORE rules publish a strict "above X%" ladder on the
    "(single-decimal) month-over-month percent change in the Personal
    Consumption Expenditures Price Index excluding food and energy ...
    according to the Bureau of Economic Analysis" with NO revision clause, NO
    missing-data fallback, and NO adjustment basis — each absence stays
    visible rather than being inferred. Polymarket's Gamma template publishes
    exact one-decimal grid buckets with "or less"/"or more" tails, names the
    BEA Personal Income and Outlays report, "seasonally adjusted", the
    one-decimal precision clause, and a terminal previous-month missing-data
    fallback (and no revision clause). The YoY events exist only on Gamma; the
    window markers keep MoM and YoY subjects from ever cross-matching. Only
    core contracts are modeled — no venue lists a headline-PCE market, so a
    text without the published core phrasing is not captured here.
    """
    yoy = _PCE_YOY_TRIGGER.search(text)
    mom = None if yoy else _PCE_MOM_TRIGGER.search(text)
    if not (yoy or mom) or not period:
        return None
    core = bool(
        "less food and energy" in text
        or "excluding food and energy" in text
        or re.search(r"\bcore pce\b|\bpce core\b", text)
    )
    if not core:
        return None
    us_context = (
        "bureau of economic analysis" in text
        or re.search(r"\bbea\b", text)
        or "bureau of economic analysis" in market_source
        # Dotted form only: the bare word "us" ("contact us") is not a marker.
        or re.search(r"\bu\.s\.?(?![a-z])|\bunited states\b", text)
    )
    if _CPI_FOREIGN_JURISDICTION.search(text) and not us_context:
        return None
    subject_base = "us_pce_core_yoy" if yoy else "us_pce_core_mom"
    scope = ("pce_core_yoy" if yoy else "pce_core_mom") + (
        # Basis enters the scope only when published: Gamma names "seasonally
        # adjusted"; Kalshi's rules state no basis and keep the bare scope.
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
        title_level = _cpi_level_terms(title, None, None, None)
        if title_level[2] is not None:
            threshold, upper, operator = title_level
        else:
            threshold, upper, operator = _cpi_level_terms(text, threshold, upper, operator)
    # Only published outcome-determining clauses become tokens; Kalshi
    # publishes only the single-decimal precision, so its legs carry that
    # token alone and stay honestly incomplete.
    policies = []
    if "most recent previous month" in text and "not released" in text:
        policies.append("missing=previous_month_figures_at_next_release")
    if "single-decimal" in text or (
        "one decimal point" in text and "level of precision" in text
    ):
        policies.append("precision=bea_one_decimal")
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
            "us_bea_pce"
            if "bureau of economic analysis" in text
            or re.search(r"\bbea\b", text)
            or "bureau of economic analysis" in market_source
            else None
        ),
        "settlement_policy": "|".join(policies) or None,
    }


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
    """Threshold phrasings specific to published one-decimal level buckets.

    Polymarket phrases its tails postfix ("3.1% or less", "4.2% or more"), with
    symbols ("be ≤3.9%", "be ≥4.7%"), or spelled out ("less than or equal to
    3.9%", "greater than or equal to 4.3%"), and its interior buckets as exact
    one-decimal levels ("be 3.4% in July", "be exactly 4.0%"), none of which the
    generic prefix patterns in ``_threshold_terms`` can read (they require a
    digit immediately after "greater than"/"less than", so the spelled-out
    "or equal to" forms fall through to here). The bare exact-level branch
    only fires when the generic pass found no operator, so Kalshi's strict
    "above 3.1%" phrasing keeps its ``>`` reading.
    """
    value = r"(-?[0-9]+(?:\.[0-9]+)?)"
    if match := re.search(value + r"\s*%\s*or\s+(?:less|lower)\b", text):
        return _number(match.group(1)), None, "<="
    if match := re.search(value + r"\s*%\s*or\s+(?:more|higher)\b", text):
        return _number(match.group(1)), None, ">="
    if match := re.search(r"\bless than or equal to\s+" + value + r"\s*%", text):
        return _number(match.group(1)), None, "<="
    if match := re.search(r"\bgreater than or equal to\s+" + value + r"\s*%", text):
        return _number(match.group(1)), None, ">="
    if match := re.search(r"\bbe\s*≤\s*" + value + r"\s*%", text):
        return _number(match.group(1)), None, "<="
    if match := re.search(r"\bbe\s*≥\s*" + value + r"\s*%", text):
        return _number(match.group(1)), None, ">="
    if match := re.search(r"\bbe\s+exactly\s+" + value + r"\s*%", text):
        return _number(match.group(1)), None, "="
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

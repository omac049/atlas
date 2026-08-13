from enum import StrEnum

from atlas.fingerprints import build_fingerprint, has_explicit_binary_fallback
from atlas.models import ContractFingerprint, Market
from atlas.policy_evidence import parse_market_policy_evidence


class GuaranteeStatus(StrEnum):
    GUARANTEED = "GUARANTEED"
    NON_GUARANTEED = "NON_GUARANTEED"
    UNKNOWN = "UNKNOWN"


def assess_settlement_guarantee(
    market: Market, fingerprint: ContractFingerprint | None = None
) -> dict[str, object]:
    """Classify whether a contract has deterministic rather than discretionary settlement."""
    fingerprint = fingerprint or build_fingerprint(market)
    text = f"{market.raw_rules_text} {market.description or ''}".lower()

    if any(
        marker in text
        for marker in (
            "fair market price",
            "last fair market price",
            "fair price",
            "sole discretion of the exchange",
        )
    ) or (fingerprint.settlement_policy and "fair_price" in fingerprint.settlement_policy):
        return {
            "status": GuaranteeStatus.NON_GUARANTEED.value,
            "reason_codes": ["DISCRETIONARY_FAIR_PRICE_SETTLEMENT"],
        }

    if _complete_chamber_control_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_CHAMBER_CONTROL_TIEBREAK_POLICY"],
        }

    if _complete_cpi_release_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_CPI_RELEASE_AND_MISSING_DATA_POLICY"],
        }

    if _complete_unemployment_release_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_UNEMPLOYMENT_RELEASE_POLICY"],
        }

    if _complete_fomc_decision_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_FOMC_DECISION_BUCKET_POLICY"],
        }

    if _complete_fed_funds_level_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_FED_FUNDS_LEVEL_POLICY"],
        }

    if _complete_ism_release_policy(fingerprint):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["COMPLETE_ISM_RELEASE_AND_MISSING_DATA_POLICY"],
        }

    if fingerprint.market_type == "weather":
        evidence = parse_market_policy_evidence(market)
        if evidence.complete:
            return {
                "status": GuaranteeStatus.GUARANTEED.value,
                "reason_codes": ["COMPLETE_WEATHER_SETTLEMENT_POLICY"],
                "policy_evidence": evidence.model_dump(mode="json"),
            }
        return {
            "status": GuaranteeStatus.UNKNOWN.value,
            "reason_codes": evidence.blockers,
            "policy_evidence": evidence.model_dump(mode="json"),
        }

    specialized_scope = str(fingerprint.contract_scope or "")
    if specialized_scope.startswith(("cpi_", "unemployment_rate")) or specialized_scope in {
        "fomc_rate_change_bucket",
        "fed_funds_upper_bound_level",
        "ism_manufacturing_pmi",
        "ism_services_pmi",
    }:
        # Specialized macro families may earn GUARANTEED only through their own
        # complete-policy path above. The generic yes/no-fallback grant below
        # would otherwise let a misfiled or partially-parsed leg reach approval.
        reasons = ["FAMILY_POLICY_INCOMPLETE"]
        if fingerprint.resolution_source in {"", "unknown"}:
            reasons.append("MISSING_RESOLUTION_SOURCE")
        return {"status": GuaranteeStatus.UNKNOWN.value, "reason_codes": reasons}

    if fingerprint.settlement_policy and "cancel=half" in fingerprint.settlement_policy:
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["EXPLICIT_HALF_PAYOUT_EXCEPTION_POLICY"],
        }

    if has_explicit_binary_fallback(text):
        return {
            "status": GuaranteeStatus.GUARANTEED.value,
            "reason_codes": ["EXPLICIT_YES_NO_FALLBACK"],
        }

    reasons = ["NO_EXPLICIT_EXCEPTION_FALLBACK"]
    if fingerprint.resolution_source in {"", "unknown"}:
        reasons.append("MISSING_RESOLUTION_SOURCE")
    if not fingerprint.settlement_policy:
        reasons.append("UNPARSED_SETTLEMENT_POLICY")
    return {"status": GuaranteeStatus.UNKNOWN.value, "reason_codes": reasons}


def _complete_chamber_control_policy(fingerprint: ContractFingerprint) -> bool:
    if fingerprint.event_action != "party_control" or not fingerprint.settlement_policy:
        return False
    policies = set(fingerprint.settlement_policy.split("|"))
    if fingerprint.contract_scope == "us_house_control":
        return {
            "control=majority_voting_seats",
            "tie=first_elected_speaker_party",
        } <= policies
    if fingerprint.contract_scope == "us_senate_control":
        return {
            "control=majority_voting_seats",
            "tie=half_seats_plus_vp",
            "ambiguous=first_president_pro_tempore_party",
        } <= policies
    return False


def _complete_fomc_decision_policy(fingerprint: ContractFingerprint) -> bool:
    """A per-meeting FOMC rate bucket is deterministic when the venue publishes what
    happens if the meeting produces no decision (canceled meeting / no statement)."""
    if (
        fingerprint.market_type != "economic"
        or fingerprint.contract_scope != "fomc_rate_change_bucket"
        or fingerprint.resolution_source != "federal_reserve"
        or fingerprint.threshold is None
        or fingerprint.threshold_operator is None
        or fingerprint.threshold_unit != "bps"
        or not fingerprint.measurement_period
        or not fingerprint.settlement_policy
    ):
        return False
    return "no_meeting=no_change_bucket" in fingerprint.settlement_policy.split("|")


def _complete_fed_funds_level_policy(fingerprint: ContractFingerprint) -> bool:
    """A fed-funds upper-bound level contract is deterministic when the published
    anchor cannot fail to produce a value: a year-end snapshot with a published
    single-rate fallback, or a meeting anchor with a published no-decision fallback."""
    if (
        fingerprint.market_type != "economic"
        or fingerprint.contract_scope != "fed_funds_upper_bound_level"
        or fingerprint.resolution_source != "federal_reserve"
        or fingerprint.threshold is None
        or fingerprint.threshold_operator is None
        or fingerprint.threshold_unit != "percent"
        or not fingerprint.measurement_period
        or not fingerprint.settlement_policy
    ):
        return False
    policies = set(fingerprint.settlement_policy.split("|"))
    if fingerprint.measurement_period.startswith("snapshot:"):
        return "single_rate=target_rate_used" in policies
    if fingerprint.measurement_period.startswith("meeting:"):
        return "no_decision=year_end_rate_snapshot" in policies
    return False


def _complete_unemployment_release_policy(fingerprint: ContractFingerprint) -> bool:
    """A U-3 unemployment leg is deterministic only when the venue publishes the
    complete release policy: the first-release revision freeze, the terminal
    last-available-month fallback, and the one-decimal precision clause, all on
    the published seasonally-adjusted U-3 basis anchored to the BLS Employment
    Situation Report. Polymarket's current template publishes all three; Kalshi's
    KXU3 rules publish none (and pre-fallback Polymarket templates lack the
    terminal branch), so those legs stay honestly UNKNOWN."""
    if (
        fingerprint.market_type != "economic"
        or fingerprint.contract_scope != "unemployment_rate_u3_seasonally_adjusted"
        or fingerprint.resolution_source != "us_bls_employment_situation"
        or fingerprint.threshold is None
        or fingerprint.threshold_operator is None
        or fingerprint.threshold_unit != "percent"
        or not fingerprint.measurement_period
        or not fingerprint.settlement_policy
    ):
        return False
    return {
        "revision=first_official_release",
        "missing=last_available_month_at_next_release",
        "precision=bls_one_decimal",
    } <= set(fingerprint.settlement_policy.split("|"))


def _complete_ism_release_policy(fingerprint: ContractFingerprint) -> bool:
    """An ISM PMI leg is deterministic only when its published terms cover every
    branch: the ISM Report On Business named as source, the published one-decimal
    precision, and a terminal missing-release fallback (previous month's figures).
    Kalshi's manufacturing rules publish source and precision but no fallback, and
    its services rules publish none of the three — both honestly stay UNKNOWN."""
    if (
        fingerprint.market_type != "economic"
        or fingerprint.contract_scope not in {"ism_manufacturing_pmi", "ism_services_pmi"}
        or fingerprint.resolution_source != "ism_report_on_business"
        or fingerprint.threshold is None
        or fingerprint.threshold_operator is None
        or fingerprint.threshold_unit != "index_points"
        or not fingerprint.measurement_period
        or not fingerprint.settlement_policy
    ):
        return False
    policies = set(fingerprint.settlement_policy.split("|"))
    return {
        "missing=previous_month_figures_at_next_release",
        "precision=ism_one_decimal",
    } <= policies


def _complete_cpi_release_policy(fingerprint: ContractFingerprint) -> bool:
    if (
        fingerprint.market_type != "economic"
        # Scopes with a published adjustment basis only; a leg whose venue does
        # not publish its basis (Kalshi headline MoM: bare "cpi_mom") cannot be
        # guarantee-complete no matter how complete its other policy tokens are.
        or fingerprint.contract_scope
        not in {
            "cpi_yoy_not_seasonally_adjusted",
            "cpi_core_yoy_not_seasonally_adjusted",
            "cpi_mom_seasonally_adjusted",
            "cpi_core_mom_seasonally_adjusted",
        }
        or fingerprint.resolution_source != "us_bls_cpi"
        or fingerprint.threshold is None
        or fingerprint.threshold_operator is None
        or fingerprint.threshold_unit != "percent"
        or not fingerprint.measurement_period
        or not fingerprint.settlement_policy
    ):
        return False
    policies = set(fingerprint.settlement_policy.split("|"))
    if {
        "revision=first_official_release",
        "missing=first_within_3m_else_previous_month",
    } <= policies:
        return True
    # A published terminal missing-data fallback (resolve on the most recent prior
    # month's figures) plus the published BLS one-decimal precision clause determines
    # the outcome in every branch, including a release that never happens. A published
    # delay-extension alone (Kalshi's shutdown clause) leaves the never-released
    # branch unspecified and must NOT satisfy this path.
    return {
        "missing=previous_month_figures_at_next_release",
        "precision=bls_one_decimal",
    } <= policies

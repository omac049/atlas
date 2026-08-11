from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VenueName(StrEnum):
    KALSHI = "kalshi"
    POLYMARKET_US = "polymarket_us"
    POLYMARKET_GLOBAL = "polymarket_global"


class MarketStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    PAUSED = "paused"
    SETTLED = "settled"


class MatchStatus(StrEnum):
    REJECTED = "REJECTED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED_EQUIVALENT = "APPROVED_EQUIVALENT"
    APPROVED_INVERSE = "APPROVED_INVERSE"


class ContractFingerprint(BaseModel):
    schema_version: str = "1.2"
    event_subject: str
    event_action: str
    event_date: str | None = None
    market_type: str | None = None
    contract_scope: str | None = None
    affirmative_outcome: str | None = None
    signed_line: Decimal | None = None
    settlement_policy: str | None = None
    threshold: Decimal | None = None
    threshold_upper: Decimal | None = None
    threshold_operator: str | None = None
    threshold_unit: str | None = None
    measurement_period: str | None = None
    geography: str | None = None
    revision_policy: str | None = None
    resolution_source: str
    participants: list[str] = Field(default_factory=list)
    rules_hash: str | None = None

    def digest(self) -> str:
        payload = self.model_dump_json(exclude={"rules_hash"}, exclude_none=True, by_alias=True)
        return sha256(payload.encode()).hexdigest()


class EquivalenceDecision(BaseModel):
    verification_version: str = "1.2"
    status: MatchStatus
    mismatch_codes: list[str] = Field(default_factory=list)
    relationship_codes: list[str] = Field(default_factory=list)
    fingerprint_a: ContractFingerprint
    fingerprint_b: ContractFingerprint


class OrderBookLevel(BaseModel):
    price: Decimal = Field(ge=0, le=1)
    quantity: Decimal = Field(gt=0)


class OrderBook(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    venue: VenueName
    market_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    yes_bids: list[OrderBookLevel] = Field(default_factory=list)
    yes_asks: list[OrderBookLevel] = Field(default_factory=list)
    no_bids: list[OrderBookLevel] = Field(default_factory=list)
    no_asks: list[OrderBookLevel] = Field(default_factory=list)
    sequence: int | None = None

    def asks_for(self, side: str) -> list[OrderBookLevel]:
        return self.yes_asks if side.upper() == "YES" else self.no_asks


class Market(BaseModel):
    market_id: str
    venue: VenueName
    venue_market_id: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    outcome_yes_label: str = "Yes"
    outcome_no_label: str = "No"
    category: str | None = None
    open_time: datetime | None = None
    close_time: datetime | None = None
    resolution_time: datetime | None = None
    resolution_source: str
    resolution_text: str
    event_subject: str
    event_action: str
    threshold: Decimal | None = None
    threshold_upper: Decimal | None = None
    threshold_operator: str | None = None
    threshold_unit: str | None = None
    measurement_period: str | None = None
    geography: str | None = None
    timezone: str | None = None
    revision_policy: str | None = None
    market_type: str | None = None
    participants: list[str] = Field(default_factory=list)
    status: MarketStatus = MarketStatus.ACTIVE
    volume: Decimal = Decimal(0)
    open_interest: Decimal = Decimal(0)
    raw_market_json: dict[str, Any] = Field(default_factory=dict)
    raw_rules_text: str = ""

    def fingerprint(self) -> str:
        values = [
            self.event_subject.lower().strip(),
            self.event_action.lower().strip(),
            str(self.threshold),
            self.threshold_operator or "",
            self.threshold_unit or "",
            self.measurement_period or "",
            self.resolution_source.lower().strip(),
            self.revision_policy or "",
        ]
        return "|".join(values)


class ContractPair(BaseModel):
    pair_id: str
    market_a: Market
    market_b: Market
    status: MatchStatus
    match_confidence: Decimal
    differences: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    decision: EquivalenceDecision | None = None


class PaperTradeRecord(BaseModel):
    trade_id: str
    opportunity_id: str
    status: str
    simulated_profit: Decimal
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Opportunity(BaseModel):
    opportunity_id: str
    pair_id: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    leg_a_venue: VenueName
    leg_a_market_id: str
    leg_a_side: str
    leg_a_average_price: Decimal
    leg_b_venue: VenueName
    leg_b_market_id: str
    leg_b_side: str
    leg_b_average_price: Decimal
    contracts: Decimal
    gross_cost: Decimal
    fees: Decimal
    slippage: Decimal
    net_cost: Decimal
    guaranteed_payout: Decimal
    expected_profit: Decimal
    expected_roi: Decimal
    rule_check: str
    status: str = "DETECTED"

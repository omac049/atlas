from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from atlas.models import Opportunity


@dataclass(frozen=True)
class PaperTrade:
    opportunity_id: str
    status: str
    simulated_profit: Decimal
    created_at: datetime


class PaperExecutor:
    def execute(self, opportunity: Opportunity) -> PaperTrade:
        return PaperTrade(
            opportunity.opportunity_id,
            opportunity.status,
            opportunity.expected_profit,
            datetime.now(UTC),
        )

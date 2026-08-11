###### ATLAS v0.1 — Cross-Market Prediction-Market Mispricing Engine

## 1. Objective

Atlas continuously monitors prediction-market venues and searches for economically equivalent contracts whose executable prices create a positive locked or near-locked return after:

- exchange fees
- available liquidity
- slippage
- timing/latency
- contract-resolution differences
- execution failure risk
- settlement horizon

Atlas v0.1 is **paper-trading only**.

No real orders may be transmitted by v0.1.

The system should be designed so live execution can later be enabled through a separate, explicit execution module.

---

# 2. Initial Venues

## Venue A — Kalshi

Use:

REST:\
`https://external-api.kalshi.com/trade-api/v2`

WebSocket:\
`wss://external-api-ws.kalshi.com/trade-api/ws/v2`

Kalshi WebSockets require authentication and can provide an initial order-book snapshot followed by incremental `orderbook_delta` updates. citeturn253977search2turn253977search3turn253977search7

Important Kalshi behavior:

Kalshi's order-book API exposes YES bids and NO bids rather than conventional bid/ask arrays.

Therefore:

YES ask = 1.00 − best NO bid

NO ask = 1.00 − best YES bid

Do not treat missing explicit asks as missing liquidity. citeturn253977search0turn253977search1

---

## Venue B — Polymarket US

Use its current U.S. API.

Market WebSocket:

`wss://api.polymarket.us/v1/ws/markets`

Polymarket US currently supports full market-data subscriptions, lighter price subscriptions and trade notifications. Its REST market API exposes market metadata, books, BBO data and settlement information. citeturn287940search0turn287940search3turn287940search4

The L2 order-book interface exposes aggregated bids/offers and quantities, which Atlas should use when estimating executable size rather than relying solely on displayed best prices. citeturn287940search1

---

# 3. System Architecture

```text
                 ┌─────────────────┐
                 │ Market Discovery │
                 └────────┬────────┘
                          │
                ┌─────────▼─────────┐
                │ Contract Normalizer│
                └─────────┬─────────┘
                          │
                ┌─────────▼──────────┐
                │ Candidate Matcher   │
                └─────────┬──────────┘
                          │
                 AI proposes matches
                          │
                ┌─────────▼──────────┐
                │ Deterministic Rule │
                │ Verifier           │
                └─────────┬──────────┘
                          │
                    Approved pairs
                          │
         ┌────────────────┴────────────────┐
         │                                 │
┌────────▼────────┐                ┌───────▼─────────┐
│ Kalshi Book     │                │ Polymarket Book │
│ Collector       │                │ Collector       │
└────────┬────────┘                └────────┬────────┘
         └────────────────┬────────────────┘
                          │
                ┌─────────▼──────────┐
                │ Opportunity Engine │
                └─────────┬──────────┘
                          │
                ┌─────────▼──────────┐
                │ Paper Executor     │
                └─────────┬──────────┘
                          │
                ┌─────────▼──────────┐
                │ Analytics / Logs   │
                └────────────────────┘
```

---

# 4. Recommended Stack

Backend:

**Python 3.12 + FastAPI**

Python is preferable because later research components, simulations and statistical analysis can share the same codebase.

Async stack:

- `asyncio`
- `httpx`
- `websockets`
- `pydantic`

Database:

**PostgreSQL**

Cache / transient book state:

**Redis**

Frontend:

**Next.js**

Infrastructure for MVP:

- Docker
- PostgreSQL
- Redis
- one inexpensive VPS/cloud instance

Avoid Kubernetes, microservices and expensive observability platforms.

Atlas should begin as a **modular monolith**.

---

# 5. Repository Structure

```text
atlas/
│
├── apps/
│   ├── api/
│   ├── worker/
│   └── dashboard/
│
├── atlas/
│   ├── venues/
│   │   ├── base.py
│   │   ├── kalshi.py
│   │   └── polymarket_us.py
│   │
│   ├── discovery/
│   ├── normalization/
│   ├── matching/
│   ├── verification/
│   ├── orderbooks/
│   ├── fees/
│   ├── arbitrage/
│   ├── simulation/
│   ├── risk/
│   └── analytics/
│
├── tests/
├── migrations/
├── docker/
└── config/
```

The venue interface is critical.

Every exchange adapter must implement the same abstract interface:

```python
class PredictionVenue:
    async def list_markets(self): ...

    async def get_market(self, market_id): ...

    async def get_orderbook(self, market_id): ...

    async def stream_orderbook(self, market_ids): ...

    async def get_rules(self, market_id): ...

    async def get_fee(self, trade): ...

    async def get_settlement(self, market_id): ...
```

That lets us add future venues without rewriting Atlas.

---

# 6. Canonical Market Schema

Never let downstream logic operate directly on exchange-specific JSON.

Normalize every market into:

```text
market_id
venue
venue_market_id

title
subtitle
description

outcome_yes_label
outcome_no_label

category

open_time
close_time
resolution_time

resolution_source
resolution_text

event_subject
event_action

threshold
threshold_operator
threshold_unit

geography

timezone

status

yes_best_bid
yes_best_ask
no_best_bid
no_best_ask

volume
open_interest

raw_market_json
raw_rules_text

retrieved_at
```

Example normalized event:

```text
subject:
Federal Reserve

action:
raises federal funds target

threshold:
25

threshold_unit:
basis_points

deadline:
2026-09-30

resolution_source:
Federal Reserve
```

This canonical representation becomes the heart of matching.

---

# 7. Order Book Schema

Store books by venue and market.

```text
orderbook_snapshot_id
venue
market_id
timestamp

side
price
quantity
level
```

Maintain the latest book in Redis.

Persist periodic snapshots plus detected-opportunity snapshots into PostgreSQL.

Do NOT store every order-book tick indefinitely during MVP.

---

# 8. Contract Matching Pipeline

This is Atlas's most important component.

There are four stages.

## Stage 1 — Candidate generation

Generate potential matches using:

- normalized title similarity
- extracted named entities
- dates
- numerical thresholds
- categories
- semantic embeddings

Example:

Kalshi:

"Will CPI exceed 3.0% in September?"

Polymarket:

"US CPI above 3% for September 2026?"

Candidate similarity may be high.

AI is allowed to propose this pair.

AI is **not allowed to approve it**.

---

# 9. AI Contract Parser

Ask the model to convert contract language into structured JSON.

Example output:

```json
{
  "subject": "US CPI",
  "predicate": "greater_than",
  "threshold": 3.0,
  "unit": "percent",
  "measurement_period": "September 2026",
  "release_source": "BLS",
  "deadline": null,
  "revision_policy": "initial_release",
  "ambiguities": []
}
```

Perform separately for both contracts.

Models should also return:

```text
semantic_match_probability
identified_differences[]
ambiguities[]
```

But this probability is informational.

It cannot authorize an opportunity.

---

# 10. Deterministic Match Verification

A candidate must pass every required field.

Required equality checks:

### Event subject

Must represent the same real-world event.

### Resolution source

Example:

BLS vs another source = reject unless explicitly proven equivalent.

### Measurement period

September vs September release date = potentially different.

### Threshold operator

`>` is NOT identical to `>=`.

### Threshold

3.00 and 3.0 are identical.

3.0 and 3.1 are not.

### Time cutoff

Midnight UTC and midnight ET may produce different results.

### Revision policy

Initial economic release vs later revised figure can differ.

### Cancellation / postponement

Sports and event contracts may handle postponed events differently.

### Exceptional resolution

"Official source unavailable" clauses must be compared.

---

# 11. Match Status

Every candidate receives one of:

```text
REJECTED
REVIEW_REQUIRED
APPROVED_EQUIVALENT
APPROVED_INVERSE
```

Only:

`APPROVED_EQUIVALENT`

or

`APPROVED_INVERSE`

may reach the arbitrage engine.

For v0.1, I recommend **manual human approval of every new contract pair**.

After approval, Atlas can monitor that pair automatically.

This is much safer than trusting automated semantic matching during the research phase.

---

# 12. Match Fingerprint

Create a deterministic fingerprint:

```text
event_subject
predicate
threshold
measurement_period
resolution_source
deadline
```

Hash it.

Markets sharing the same fingerprint become natural matching candidates.

This eventually reduces dependence on AI.

---

# 13. Core Arbitrage Structures

Start with exactly two strategies.

## Strategy A

Buy YES on Venue A.

Buy NO on Venue B.

If both contracts resolve identically:

```text
cost =
A_yes_ask
+
B_no_ask
+
fees
+
slippage

profit =
1.00 - cost
```

---

## Strategy B

Buy NO on Venue A.

Buy YES on Venue B.

```text
cost =
A_no_ask
+
B_yes_ask
+
fees
+
slippage

profit =
1.00 - cost
```

---

# 14. Return Calculation

For `n` contracts:

```text
gross_payout = n

purchase_cost =
cost_leg_a
+
cost_leg_b

net_profit =
gross_payout
-
purchase_cost
-
fees
-
estimated_execution_penalty
```

Then:

```text
ROI = net_profit / purchase_cost
```

Never calculate opportunity value using midpoint prices.

Only use prices Atlas could realistically consume from the order book.

---

# 15. Depth-Aware Execution

Suppose:

```text
Venue A YES asks

0.41 × 20
0.42 × 50
0.44 × 200
```

And Venue B NO:

```text
0.54 × 30
0.55 × 100
```

Atlas should calculate arbitrage independently at each cumulative size.

Possible output:

```text
10 contracts → 4.2% expected net
20 contracts → 4.1%
30 contracts → 3.4%
50 contracts → 2.0%
100 contracts → negative
```

Therefore:

```text
maximum_profitable_size = 30
```

Not 100.

---

# 16. Fee Engine

Fees MUST be venue adapters, never hard-coded inside arbitrage logic.

```python
fee = venue.calculate_fee(contracts, price, liquidity_role, market_type)
```

Current Polymarket US documentation uses:

```text
Fee = theta × contracts × price × (1-price)
```

and presently documents separate taker fees and maker rebates. citeturn287940search2

Kalshi publishes its own current fee schedule, which should be represented in configuration rather than permanently coded because schedules can change. citeturn739138search24

Store:

```text
fee_schedule_version
effective_date
source_url
```

Every simulated trade should reference the fee version used.

---

# 17. Opportunity Object

Every detected opportunity should become:

```json
{
  "opportunity_id": "...",
  "pair_id": "...",

  "detected_at": "...",

  "leg_a": {
    "venue": "kalshi",
    "market": "...",
    "side": "YES",
    "average_execution_price": 0.41
  },

  "leg_b": {
    "venue": "polymarket_us",
    "market": "...",
    "side": "NO",
    "average_execution_price": 0.54
  },

  "contracts": 50,

  "gross_cost": 47.50,
  "fees": 0.83,
  "slippage": 0.20,

  "net_cost": 48.53,
  "guaranteed_payout": 50,

  "expected_profit": 1.47,
  "expected_roi": 0.0303,

  "match_confidence": 1.0,
  "rule_check": "PASS"
}
```

---

# 18. Opportunity Thresholds

Initial defaults:

```text
minimum net ROI:        3.0%
minimum absolute profit: $1
minimum depth/leg:      $25
maximum horizon:        30 days
maximum pair risk:      $100
```

These are research thresholds, not assertions that any opportunity is profitable.

Make every threshold configurable.

---

# 19. Paper Trading Simulator

This is where we're going to be much stricter than most toy backtests.

When Atlas detects an opportunity:

1. save both books
2. record timestamp
3. introduce simulated latency
4. fetch/advance the book again
5. determine whether both legs remained executable
6. simulate fills only against available liquidity
7. apply fees
8. record partial fills
9. record failures
10. track market to settlement

Run multiple latency scenarios:

```text
50 ms
100 ms
250 ms
500 ms
1000 ms
2000 ms
```

This lets us discover how latency-sensitive the strategy actually is.

---

# 20. Failure Simulation

Atlas MUST simulate:

```text
LEG_A_FILLED_LEG_B_FAILED
LEG_B_FILLED_LEG_A_FAILED

PARTIAL_FILL_A
PARTIAL_FILL_B

PRICE_MOVED_A
PRICE_MOVED_B

MARKET_PAUSED

MARKET_CLOSED

DATA_STALE

API_ERROR
```

The ugly cases matter more than the successful cases.

---

# 21. Opportunity Lifetime

Measure:

```text
detected_at
first_profitable_at
last_profitable_at

duration_ms
```

Then analyze:

```text
median opportunity lifetime
P10
P50
P90
```

If most opportunities last 100 milliseconds and our system takes 500 milliseconds to react, Atlas has discovered something interesting but not something tradable.

---

# 22. Phantom Arbitrage Metric

This should be one of the core dashboard numbers.

```text
phantom_rate =
opportunities_detected_but_not_executable
/
all_opportunities_detected
```

Example:

```text
Detected: 1,000
Actually executable: 83

Phantom rate: 91.7%
```

That's incredibly important.

---

# 23. Database Tables

Minimum schema:

```text
venues

markets

market_rules

market_snapshots

orderbook_snapshots

contract_candidates

contract_pairs

contract_verifications

opportunities

paper_orders

paper_fills

paper_positions

settlements

fee_schedules

system_events

strategy_metrics
```

---

# 24. Dashboard

The initial UI should contain only four screens.

## Live Opportunities

```text
PAIR
EDGE
SIZE
LIFETIME
STATUS
```

## Contract Matches

```text
Kalshi market
Polymarket market
match status
rule differences
human approval
```

## Paper Trades

```text
timestamp
size
predicted profit
simulated realized profit
fill status
```

## Analytics

```text
detected opportunities
executable opportunities
phantom rate
gross theoretical profit
realistic simulated profit
failed hedge losses
average edge
median lifetime
capital utilization
```

---

# 25. Alerts

For v0.1:

Console + dashboard.

Optional:

Telegram/Discord notification when:

```text
net ROI >= 5%
AND
size >= $50
AND
pair approved
```

The alert should NOT place orders.

---

# 26. Safety Architecture

Live execution code should physically not exist in the first deployment.

Create:

```text
PaperExecutor
```

but no:

```text
LiveExecutor
```

until the research gate is passed.

The repository should also contain:

```text
TRADING_ENABLED=false
```

Even after live execution exists later, it should require:

```text
TRADING_ENABLED=true

AND

LIVE_CONFIRMATION_TOKEN=<manual secret>
```

---

# 27. Data Integrity Safeguards

Reject opportunity if:

```text
book age > configured maximum

sequence gap detected

market status != ACTIVE

contract pair unapproved

fee schedule unavailable

rule text changed

settlement deadline changed

venue API timestamp inconsistent

either book empty
```

If an order-book sequence gap occurs, throw away local state and request a new snapshot.

Kalshi specifically provides snapshot-plus-delta WebSocket behavior, making sequence integrity important when maintaining local books. citeturn253977search3

---

# 28. Contract Mutation Detection

Every time market metadata/rules are refreshed:

```text
hash(normalized_rules_text)
```

Compare against previous hash.

If different:

```text
pair.status = REVIEW_REQUIRED
```

Atlas immediately stops generating trades for that pair.

---

# 29. Logging

Every important decision should be explainable later.

Example:

```text
2026-08-09T21:41:03Z

PAIR:
ABC123

MATCH:
PASS

Kalshi YES:
0.4125

PM NO:
0.5480

Gross:
0.9605

Fees:
0.0118

Slippage:
0.0031

Net:
0.9754

Edge:
2.46%

ACTION:
REJECT

REASON:
EDGE_BELOW_MINIMUM
```

This dataset becomes the research asset.

---

# 30. Phase-One Success Criteria

Atlas does NOT progress to live capital merely because its theoretical P&L is positive.

Require at least:

```text
500+ candidate market pairs examined

100+ validated equivalent pairs observed

100+ apparent opportunities

30+ realistically executable simulated trades

positive net P&L after all simulated fees

positive net P&L after failed-leg simulation

phantom rate measured

zero known contract-equivalence failures
```

More important:

At least one strategy must remain profitable under:

```text
2× estimated latency

and

1.5× estimated slippage
```

That is our robustness test.

---

# 31. Kill Criteria

Pause the strategy if:

```text
realistic simulated EV <= 0

or

>95% of apparent opportunities are non-executable

or

profitable opportunities require unattainable latency

or

contract matching creates material unresolved basis risk

or

fees eliminate nearly all observed spread
```

Killing Atlas B1 does **not** mean killing Atlas.

We keep the data infrastructure and test another strategy.

---

# 32. Phase Two Possibilities

Only after v0.1.

Potential strategies:

### Maker + Taker Arbitrage

Rest a maker order on one exchange.

When filled, immediately hedge as a taker elsewhere.

Polymarket US currently documents maker rebates, which makes this worth testing later. citeturn287940search2

### Multi-outcome Arbitrage

Example:

```text
Candidate A
Candidate B
Candidate C
Candidate D
```

If complete mutually exclusive outcome baskets can be purchased for less than $1 after costs, buy basket.

### Logical Arbitrage

Example:

```text
P(A) = .70

P(A AND B) = .75
```

That violates probability constraints.

### Temporal Mispricing

Related contracts with different expiry windows.

### Structural Market-Making

Use Atlas's cross-venue fair-value estimate to provide liquidity.

### Directional Models

Much later.

Not the priority.

---

# 33. Development Milestones

## Milestone 1

Kalshi market discovery + REST order books.

Acceptance test:

Atlas can continuously retrieve and normalize 100 markets.

## Milestone 2

Polymarket US discovery + books.

Acceptance test:

Same canonical schema works for both venues.

## Milestone 3

WebSocket collectors.

Acceptance test:

Books remain synchronized for one hour without sequence corruption.

## Milestone 4

Candidate matching.

Acceptance test:

System identifies obvious equivalent markets.

## Milestone 5

Human verification UI.

Acceptance test:

A human can approve/reject a pair in under 30 seconds.

## Milestone 6

Arbitrage calculator.

Acceptance test:

Known synthetic test cases produce mathematically correct edge calculations.

## Milestone 7

Depth-aware simulation.

Acceptance test:

Large orders properly consume multiple book levels.

## Milestone 8

Paper executor.

Acceptance test:

Every detected opportunity can be reconstructed from stored data.

## Milestone 9

Analytics.

Acceptance test:

Dashboard reports realistic P&L rather than theoretical P&L.

---

# 34. First AI Coding-Agent Prompt

Build ATLAS v0.1 as a modular Python application.

Begin ONLY with the data layer.

Implement:

1. PredictionVenue abstract class.
2. KalshiVenue adapter.
3. PolymarketUSVenue adapter.
4. Canonical Market Pydantic model.
5. Canonical OrderBook model.
6. REST market discovery.
7. REST order-book retrieval.
8. PostgreSQL persistence.
9. Unit tests using recorded fixtures.
10. CLI command:

`atlas markets sync`

and:

`atlas books inspect <venue> <market_id>`

Requirements:

- Python 3.12.
- FastAPI-compatible architecture.
- asyncio for network operations.
- Decimal for all monetary calculations.
- Never use floating-point arithmetic for prices or fees.
- Exchange-specific JSON must never escape the venue adapter.
- API credentials must come exclusively from environment variables.
- Do not implement trading.
- Do not implement AI matching yet.
- Do not implement frontend yet.
- Write deterministic unit tests before proceeding.
- Include README setup instructions.
- Include Docker Compose for PostgreSQL and Redis.

Stop after this milestone and report:

- files created
- tests executed
- API assumptions
- unresolved questions
- sample normalized market from each venue

Do not proceed to subsequent milestones automatically.

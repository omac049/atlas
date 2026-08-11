import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import aiosqlite

from atlas.models import ContractPair, Market, Opportunity, OrderBook, PaperTradeRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
  market_id TEXT PRIMARY KEY, venue TEXT NOT NULL, venue_market_id TEXT NOT NULL,
  title TEXT NOT NULL, payload_json TEXT NOT NULL, retrieved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT NOT NULL,
  venue TEXT NOT NULL, timestamp TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS opportunities (
  opportunity_id TEXT PRIMARY KEY, pair_id TEXT NOT NULL, detected_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract_pairs (
  pair_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL,
  approved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trades (
  trade_id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, status TEXT NOT NULL,
  simulated_profit TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_scans (
  scan_id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT NOT NULL,
  kalshi_active INTEGER NOT NULL, polymarket_active INTEGER NOT NULL,
  comparisons INTEGER NOT NULL, approved INTEGER NOT NULL, review INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_proposals (
  proposal_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlement_candidates (
  candidate_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, active INTEGER NOT NULL,
  lifecycle_status TEXT NOT NULL, guarantee_status TEXT NOT NULL,
  pair_status TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS milestone_alerts (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,
  queue_status TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(candidate_id, queue_status)
);
CREATE TABLE IF NOT EXISTS learning_examples (
  example_id TEXT PRIMARY KEY, label TEXT NOT NULL, created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trade_outcomes (
  trade_id TEXT PRIMARY KEY, status TEXT NOT NULL, resolved_at TEXT NOT NULL,
  outcome_a TEXT, outcome_b TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_reports (
  report_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_observations (
  observation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_evidence_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT NOT NULL,
  venue TEXT NOT NULL, observed_at TEXT NOT NULL, evidence_hash TEXT NOT NULL,
  rules_hash TEXT, status TEXT NOT NULL, outcome TEXT, reason TEXT NOT NULL,
  payload_json TEXT NOT NULL, UNIQUE(market_id, evidence_hash)
);
CREATE TABLE IF NOT EXISTS validation_cases (
  pair_id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, decision_status TEXT NOT NULL,
  guarantee_a TEXT NOT NULL, guarantee_b TEXT NOT NULL, tracking_status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_checked_at TEXT,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS validation_outcomes (
  pair_id TEXT PRIMARY KEY, resolved_at TEXT NOT NULL, relationship_status TEXT NOT NULL,
  outcome_a TEXT, outcome_b TEXT, trusted_label TEXT, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS historical_backfills (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  status TEXT NOT NULL, payload_json TEXT NOT NULL
);
"""


class AtlasStore:
    def __init__(self, path: str = "data/atlas.sqlite3"):
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def save_markets(self, markets: list[Market]) -> None:
        await self.initialize()
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(
                "INSERT OR REPLACE INTO markets VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        m.market_id,
                        m.venue.value,
                        m.venue_market_id,
                        m.title,
                        m.model_dump_json(),
                        now,
                    )
                    for m in markets
                ],
            )
            await db.commit()

    async def save_orderbook(self, book: OrderBook) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO orderbook_snapshots (market_id, venue, timestamp, payload_json) VALUES (?, ?, ?, ?)",
                (
                    book.market_id,
                    book.venue.value,
                    book.timestamp.isoformat(),
                    book.model_dump_json(),
                ),
            )
            await db.commit()

    async def save_opportunity(self, opportunity: Opportunity) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO opportunities VALUES (?, ?, ?, ?)",
                (
                    opportunity.opportunity_id,
                    opportunity.pair_id,
                    opportunity.detected_at.isoformat(),
                    opportunity.model_dump_json(),
                ),
            )
            await db.commit()

    async def latest_orderbooks(self, limit: int = 20) -> list[OrderBook]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM orderbook_snapshots ORDER BY snapshot_id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [OrderBook.model_validate_json(row[0]) for row in rows]

    async def latest_opportunity(self) -> Opportunity | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM opportunities ORDER BY detected_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        return Opportunity.model_validate_json(row[0]) if row else None

    async def save_pair(self, pair: ContractPair) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO contract_pairs VALUES (?, ?, ?, datetime('now'))",
                (pair.pair_id, pair.status.value, pair.model_dump_json()),
            )
            await db.commit()

    async def get_pair(self, pair_id: str) -> ContractPair | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM contract_pairs WHERE pair_id = ?", (pair_id,)
            )
            row = await cursor.fetchone()
        return ContractPair.model_validate_json(row[0]) if row else None

    async def latest_pair(self) -> ContractPair | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM contract_pairs ORDER BY approved_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        return ContractPair.model_validate_json(row[0]) if row else None

    async def paper_trade_summary(self) -> dict[str, int | str | None]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            count_cursor = await db.execute("SELECT COUNT(*) FROM paper_trades")
            count = (await count_cursor.fetchone())[0]
            latest_cursor = await db.execute(
                "SELECT status, created_at FROM paper_trades ORDER BY created_at DESC LIMIT 1"
            )
            latest = await latest_cursor.fetchone()
        return {
            "count": count,
            "latest_status": latest[0] if latest else None,
            "latest_created_at": latest[1] if latest else None,
        }

    async def save_paper_trade(self, trade: PaperTradeRecord) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO paper_trades VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trade.trade_id,
                    trade.opportunity_id,
                    trade.status,
                    str(trade.simulated_profit),
                    trade.created_at.isoformat(),
                    trade.model_dump_json(),
                ),
            )
            await db.commit()

    async def save_candidate_proposals(self, proposals: list[dict[str, object]]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM candidate_proposals")
            now = datetime.now(UTC).isoformat()
            await db.executemany(
                "INSERT INTO candidate_proposals VALUES (?, ?, ?)",
                [
                    (f"{item['kalshi_market_id']}::{item['polymarket_market_id']}", now, json.dumps(item))
                    for item in proposals
                ],
            )
            await db.executemany(
                "INSERT OR REPLACE INTO learning_examples VALUES (?, ?, ?, ?)",
                [
                    (
                        f"observation:{item['kalshi_market_id']}::{item['polymarket_market_id']}",
                        "UNLABELED",
                        now,
                        json.dumps({"type": "candidate_observation", "proposal": item}),
                    )
                    for item in proposals
                ],
            )
            await db.commit()

    async def latest_candidate_proposals(self, limit: int = 25) -> list[dict[str, object]]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM candidate_proposals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [json.loads(row[0]) for row in rows]

    async def save_settlement_candidates(
        self, candidates: list[dict[str, object]]
    ) -> None:
        """Persist the current ranked queue without promoting review pairs."""
        await self.initialize()
        observed_at = datetime.now(UTC).isoformat()
        ranked_candidates = [
            {**item, "ranking_position": index + 1}
            for index, item in enumerate(candidates)
        ]
        async with aiosqlite.connect(self.path) as db:
            previous_rows = await (
                await db.execute(
                    "SELECT candidate_id, payload_json FROM settlement_candidates WHERE active = 1"
                )
            ).fetchall()
            previous = {row[0]: json.loads(row[1]) for row in previous_rows}
            await db.execute("UPDATE settlement_candidates SET active = 0")
            await db.executemany(
                """INSERT OR REPLACE INTO settlement_candidates
                (candidate_id, observed_at, active, lifecycle_status,
                 guarantee_status, pair_status, payload_json)
                VALUES (?, ?, 1, ?, ?, ?, ?)""",
                [
                    (
                        f"{item['kalshi_market_id']}::{item['polymarket_market_id']}",
                        observed_at,
                        str(item.get("lifecycle_status", "UNKNOWN")),
                        str(item.get("guarantee_status", "UNKNOWN")),
                        str(item.get("pair_status", "REVIEW_REQUIRED")),
                        json.dumps(item),
                    )
                    for item in ranked_candidates
                ],
            )
            for item in ranked_candidates:
                candidate_id = f"{item['kalshi_market_id']}::{item['polymarket_market_id']}"
                queue_status = str(item.get("queue_status", "BLOCKED"))
                prior = previous.get(candidate_id, {})
                deterministic = (
                    str(item.get("pair_status"))
                    in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
                    and str(item.get("guarantee_status")) == "GUARANTEED"
                )
                was_deterministic = (
                    str(prior.get("pair_status"))
                    in {"APPROVED_EQUIVALENT", "APPROVED_INVERSE"}
                    and str(prior.get("guarantee_status")) == "GUARANTEED"
                )
                if deterministic and not was_deterministic:
                    alert = {
                        "alert_type": "MILESTONE_TRANSITION",
                        "transition_kind": "DETERMINISTIC_RULE_GATE",
                        "candidate_id": candidate_id,
                        "queue_status": "DETERMINISTIC",
                        "observed_at": observed_at,
                        "next_gate": item.get("next_gate"),
                        "pair_status": item.get("pair_status"),
                        "guarantee_status": item.get("guarantee_status"),
                        "settlement_ready_at": item.get("settlement_ready_at"),
                        "kalshi_market_id": item.get("kalshi_market_id"),
                        "polymarket_market_id": item.get("polymarket_market_id"),
                    }
                    await db.execute(
                        """INSERT OR IGNORE INTO milestone_alerts
                        (candidate_id, queue_status, created_at, payload_json)
                        VALUES (?, ?, ?, ?)""",
                        (candidate_id, "DETERMINISTIC", observed_at, json.dumps(alert)),
                    )
                if queue_status not in {"AWAITING_SETTLEMENT", "SETTLED"}:
                    continue
                if prior.get("queue_status") == queue_status:
                    continue
                alert = {
                    "alert_type": "MILESTONE_TRANSITION",
                    "transition_kind": "QUEUE_STATUS",
                    "candidate_id": candidate_id,
                    "queue_status": queue_status,
                    "observed_at": observed_at,
                    "next_gate": item.get("next_gate"),
                    "pair_status": item.get("pair_status"),
                    "guarantee_status": item.get("guarantee_status"),
                    "kalshi_market_id": item.get("kalshi_market_id"),
                    "polymarket_market_id": item.get("polymarket_market_id"),
                }
                await db.execute(
                    """INSERT OR IGNORE INTO milestone_alerts
                    (candidate_id, queue_status, created_at, payload_json)
                    VALUES (?, ?, ?, ?)""",
                    (candidate_id, queue_status, observed_at, json.dumps(alert)),
                )
            await db.commit()

    async def latest_settlement_candidates(
        self, limit: int = 25
    ) -> list[dict[str, object]]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT payload_json FROM settlement_candidates
                    WHERE active = 1""",
                )
            ).fetchall()
        candidates = [json.loads(row[0]) for row in rows]
        candidates.sort(
            key=lambda item: (
                int(item.get("ranking_position", 10**9)),
                str(item.get("settlement_ready_at") or "9999"),
                str(item.get("candidate_id") or ""),
            )
        )
        return candidates[:limit]

    async def latest_milestone_alerts(self, limit: int = 10) -> list[dict[str, object]]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT payload_json FROM milestone_alerts
                    ORDER BY alert_id DESC LIMIT ?""",
                    (limit,),
                )
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def save_agent_run(self, payload: dict[str, object]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO agent_runs (created_at, payload_json) VALUES (?, ?)",
                (datetime.now(UTC).isoformat(), json.dumps(payload)),
            )
            await db.commit()

    async def latest_agent_run(self) -> dict[str, object] | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT payload_json FROM agent_runs ORDER BY run_id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def save_learning_example(self, example_id: str, label: str, payload: dict) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO learning_examples VALUES (?, ?, ?, ?)",
                (example_id, label, datetime.now(UTC).isoformat(), json.dumps(payload)),
            )
            await db.commit()

    async def labeled_learning_examples(self) -> list[dict[str, object]]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT example_id, label, payload_json FROM learning_examples
                WHERE label IN ('APPROVED_EQUIVALENT', 'REJECTED') ORDER BY created_at"""
            )
            rows = await cursor.fetchall()
        examples = [
            {"example_id": row[0], "label": row[1], "payload": json.loads(row[2])}
            for row in rows
        ]
        return [
            example
            for example in examples
            if example["payload"].get("evidence", {}).get("settlement_verified") is True
        ]

    async def learning_counts(self) -> dict[str, int]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT label, COUNT(*) FROM learning_examples GROUP BY label"
            )
            rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    async def trusted_learning_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for example in await self.labeled_learning_examples():
            label = str(example["label"])
            counts[label] = counts.get(label, 0) + 1
        return counts

    async def save_market_evidence_snapshots(
        self, snapshots: list[dict[str, object]]
    ) -> dict[str, int]:
        await self.initialize()
        if not snapshots:
            return {"observed": 0, "new_versions": 0}
        async with aiosqlite.connect(self.path) as db:
            before = (
                await (
                    await db.execute("SELECT COUNT(*) FROM market_evidence_snapshots")
                ).fetchone()
            )[0]
            await db.executemany(
                """INSERT OR IGNORE INTO market_evidence_snapshots
                (market_id, venue, observed_at, evidence_hash, rules_hash, status,
                 outcome, reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["market_id"],
                        item["venue"],
                        item["observed_at"],
                        item["evidence_hash"],
                        item.get("rules_hash"),
                        item["status"],
                        item.get("outcome"),
                        item["reason"],
                        json.dumps(item["payload"]),
                    )
                    for item in snapshots
                ],
            )
            await db.commit()
            after = (
                await (
                    await db.execute("SELECT COUNT(*) FROM market_evidence_snapshots")
                ).fetchone()
            )[0]
        return {"observed": len(snapshots), "new_versions": after - before}

    async def save_validation_case(self, case: dict[str, object]) -> bool:
        await self.initialize()
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO validation_cases
                (pair_id, source_kind, decision_status, guarantee_a, guarantee_b,
                 tracking_status, created_at, updated_at, last_checked_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    case["pair_id"],
                    case["source_kind"],
                    case["decision_status"],
                    case["guarantee_a"],
                    case["guarantee_b"],
                    case.get("tracking_status", "AWAITING_SETTLEMENT"),
                    now,
                    now,
                    json.dumps(case["payload"]),
                ),
            )
            await db.commit()
        return cursor.rowcount > 0

    async def pending_validation_cases(
        self, limit: int = 20
    ) -> list[dict[str, object]]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT pair_id, source_kind, decision_status, guarantee_a,
                    guarantee_b, tracking_status, last_checked_at, payload_json
                    FROM validation_cases WHERE tracking_status = 'AWAITING_SETTLEMENT'
                    ORDER BY created_at LIMIT ?""",
                    (limit,),
                )
            ).fetchall()
        return [
            {
                "pair_id": row[0],
                "source_kind": row[1],
                "decision_status": row[2],
                "guarantee_a": row[3],
                "guarantee_b": row[4],
                "tracking_status": row[5],
                "last_checked_at": row[6],
                "payload": json.loads(row[7]),
            }
            for row in rows
        ]

    async def mark_validation_checked(
        self, pair_id: str, tracking_status: str = "AWAITING_SETTLEMENT"
    ) -> None:
        await self.initialize()
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE validation_cases SET tracking_status = ?, updated_at = ?,
                last_checked_at = ? WHERE pair_id = ?""",
                (tracking_status, now, now, pair_id),
            )
            await db.commit()

    async def save_validation_outcome(self, outcome: dict[str, object]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO validation_outcomes
                (pair_id, resolved_at, relationship_status, outcome_a, outcome_b,
                 trusted_label, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    outcome["pair_id"],
                    outcome["resolved_at"],
                    outcome["relationship_status"],
                    outcome.get("outcome_a"),
                    outcome.get("outcome_b"),
                    outcome.get("trusted_label"),
                    json.dumps(outcome),
                ),
            )
            await db.execute(
                """UPDATE validation_cases SET tracking_status = 'RESOLVED',
                updated_at = ?, last_checked_at = ? WHERE pair_id = ?""",
                (outcome["resolved_at"], outcome["resolved_at"], outcome["pair_id"]),
            )
            await db.commit()

    async def validation_summary(self) -> dict[str, object]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            evidence = await (
                await db.execute(
                    """SELECT COUNT(*), COUNT(DISTINCT market_id)
                    FROM market_evidence_snapshots"""
                )
            ).fetchone()
            cases = await (
                await db.execute(
                    """SELECT COUNT(*),
                    SUM(CASE WHEN tracking_status = 'AWAITING_SETTLEMENT' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN tracking_status = 'RESOLVED' THEN 1 ELSE 0 END)
                    FROM validation_cases"""
                )
            ).fetchone()
            outcomes = await (
                await db.execute(
                    """SELECT
                    SUM(CASE WHEN relationship_status = 'CONFIRMED' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN relationship_status = 'DIVERGED' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN relationship_status = 'INCONCLUSIVE' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN trusted_label IS NOT NULL THEN 1 ELSE 0 END)
                    FROM validation_outcomes"""
                )
            ).fetchone()
        versions, markets = int(evidence[0] or 0), int(evidence[1] or 0)
        return {
            "evidence_versions": versions,
            "markets_tracked": markets,
            "rule_changes": max(0, versions - markets),
            "cases": int(cases[0] or 0),
            "awaiting_settlement": int(cases[1] or 0),
            "resolved_cases": int(cases[2] or 0),
            "confirmed": int(outcomes[0] or 0),
            "diverged": int(outcomes[1] or 0),
            "inconclusive": int(outcomes[2] or 0),
            "trusted_labels": int(outcomes[3] or 0),
        }

    async def save_historical_backfill(self, report: dict[str, object]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO historical_backfills
                (created_at, status, payload_json) VALUES (?, ?, ?)""",
                (
                    str(report.get("completed_at") or datetime.now(UTC).isoformat()),
                    str(report.get("status") or "UNKNOWN"),
                    json.dumps(report),
                ),
            )
            await db.commit()

    async def latest_historical_backfill(self) -> dict[str, object] | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    """SELECT payload_json FROM historical_backfills
                    ORDER BY run_id DESC LIMIT 1"""
                )
            ).fetchone()
        return json.loads(row[0]) if row else None

    async def pending_trade_contexts(self) -> list[dict[str, object]]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT t.trade_id, o.pair_id, p.payload_json
                FROM paper_trades t
                JOIN opportunities o ON o.opportunity_id = t.opportunity_id
                JOIN contract_pairs p ON p.pair_id = o.pair_id
                WHERE t.trade_id NOT IN (SELECT trade_id FROM paper_trade_outcomes)"""
            )
            rows = await cursor.fetchall()
        return [{"trade_id": row[0], "pair_id": row[1], "pair_json": json.loads(row[2])} for row in rows]

    async def save_paper_trade_outcome(self, trade_id: str, status: str, outcome_a: str | None, outcome_b: str | None) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO paper_trade_outcomes VALUES (?, ?, ?, ?, ?, ?)",
                (
                    trade_id, status, datetime.now(UTC).isoformat(), outcome_a, outcome_b,
                    json.dumps({"trade_id": trade_id, "status": status, "outcome_a": outcome_a, "outcome_b": outcome_b}),
                ),
            )
            await db.commit()

    async def save_catalog_report(self, report: dict[str, object]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO catalog_reports (created_at, payload_json) VALUES (?, ?)",
                (datetime.now(UTC).isoformat(), json.dumps(report)),
            )
            await db.commit()

    async def latest_catalog_report(self) -> dict[str, object] | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute(
                "SELECT payload_json FROM catalog_reports ORDER BY report_id DESC LIMIT 1"
            )).fetchone()
        return json.loads(row[0]) if row else None

    async def save_shadow_observation(self, observation: dict[str, object]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO shadow_observations VALUES (?, ?, ?)",
                (
                    observation["observation_id"],
                    observation["created_at"],
                    json.dumps(observation),
                ),
            )
            await db.commit()

    async def latest_shadow_observation(self) -> dict[str, object] | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            row = await (
                await db.execute(
                    "SELECT payload_json FROM shadow_observations ORDER BY created_at DESC LIMIT 1"
                )
            ).fetchone()
        return json.loads(row[0]) if row else None

    async def shadow_validation_summary(self, limit: int = 5000) -> dict[str, object]:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    "SELECT payload_json FROM shadow_observations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
        observations = [json.loads(row[0]) for row in rows]
        edges: list[Decimal] = []
        unit_costs: list[Decimal] = []
        blocker_counts: dict[str, int] = {}
        for observation in observations:
            quote = observation.get("best_direction", {})
            edge = Decimal(str(quote.get("raw_edge_if_complementary", "0")))
            contracts = Decimal(str(quote.get("contracts", "0")))
            gross_cost = Decimal(str(quote.get("gross_cost", "0")))
            edges.append(edge)
            if contracts > 0:
                unit_costs.append(gross_cost / contracts)
            for blocker in observation.get("blockers", []):
                blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        positive = sum(edge > 0 for edge in edges)
        return {
            "observation_count": len(observations),
            "unique_pairs": len({item.get("pair_id") for item in observations}),
            "positive_edge_count": positive,
            "positive_edge_rate": str(Decimal(positive) / len(edges)) if edges else "0",
            "best_unit_cost": str(min(unit_costs)) if unit_costs else None,
            "best_raw_edge": str(max(edges)) if edges else None,
            "latest_created_at": observations[0].get("created_at") if observations else None,
            "blocker_counts": blocker_counts,
        }

    async def save_discovery_scan(self, result: dict[str, int]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT INTO discovery_scans
                (scanned_at, kalshi_active, polymarket_active, comparisons, approved, review)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(UTC).isoformat(), result["kalshi_active"],
                    result["polymarket_active"], result["comparisons"],
                    result["approved"], result["review"],
                ),
            )
            await db.commit()

    async def latest_discovery_scan(self) -> dict[str, int | str] | None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT scanned_at, kalshi_active, polymarket_active, comparisons, approved, review
                FROM discovery_scans ORDER BY scan_id DESC LIMIT 1"""
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return {
            "scanned_at": row[0], "kalshi_active": row[1],
            "polymarket_active": row[2], "comparisons": row[3],
            "approved": row[4], "review": row[5],
        }

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import ClassVar

import aiosqlite

from atlas.models import ContractPair, Opportunity, OrderBook, PaperTradeRecord

# How long raw order-book snapshots stay useful, and how many of the
# newest-row-only report tables `prune()` keeps around for debugging.
PRUNE_ORDERBOOK_MAX_AGE_DAYS = 30
PRUNE_KEEP_NEWEST = 20

SCHEMA = """
-- LEGACY, UNREAD: nothing in Atlas writes or reads the markets table any more
-- (`save_markets` was removed 2026-08-19; zero SELECTs existed anywhere).
-- Existing rows are left in place deliberately — no destructive migration.
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
CREATE TABLE IF NOT EXISTS gap_observations (
  observation_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
-- Capacity samples live OUTSIDE gap_observations on purpose. The 90-day
-- study's observation stream is frozen for measurement; folding a new field
-- into it mid-flight would break the week-over-week comparison the study
-- exists to make. These rows are a separate artifact, joined by pair and time
-- only when a report asks for them.
CREATE TABLE IF NOT EXISTS capacity_samples (
  sample_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
  release_window TEXT, kalshi_market_id TEXT NOT NULL,
  polymarket_market_id TEXT NOT NULL, event_subject TEXT,
  profitable_contracts REAL, total_profit_usd REAL,
  top_of_book_contracts REAL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capacity_window ON capacity_samples(release_window, captured_at);
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
  pending_reason TEXT, next_poll_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 5, last_retry_at TEXT,
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
-- Additive indexes for the measured hot paths. `latest_orderbooks` orders by
-- snapshot_id, which is the rowid primary key and needs no extra index; the
-- timestamp index serves prune()'s age-cutoff delete instead. The
-- market_evidence index mirrors rules_version_history exactly: filter by
-- market_id, group by (market_id, rules_hash), MIN/MAX(observed_at).
-- The validation_cases index lives in initialize() because its columns are
-- added by post-schema migrations on older databases.
CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_timestamp
  ON orderbook_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_market_evidence_market_rules
  ON market_evidence_snapshots(market_id, rules_hash, observed_at);
"""


def _as_float(value: object) -> float | None:
    """Gaps are stored as exact decimal strings; the column is a sortable mirror."""
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _outcome_settled_same_day(payload: dict[str, object]) -> bool:
    """Whether both legs of a resolved outcome settled on the same UTC day.

    Outcomes recorded before the flag existed still carry both timestamps, so
    fall back to comparing their ISO date prefixes.
    """
    same_day = payload.get("settled_same_day")
    if isinstance(same_day, bool):
        return same_day
    left = str(payload.get("kalshi_settled_at") or "")[:10]
    right = str(payload.get("polymarket_settled_at") or "")[:10]
    return not left or not right or left == right


class AtlasStore:
    # DB paths whose schema + migrations have already run in this process.
    # Every public method calls initialize(), and call sites construct fresh
    # AtlasStore instances constantly, so without this guard the full ~20
    # statement executescript and every PRAGMA migration probe re-ran on every
    # single storage call. The guard is per resolved path: the FIRST
    # initialization of a path always runs in full (the additive migration
    # pattern below must still fire for existing databases), and each distinct
    # path (e.g. per-test tmp_path stores) initializes independently. A plain
    # set is enough — everything runs on one event loop, and a rare
    # interleaved double-initialization is idempotent.
    _initialized_paths: ClassVar[set[str]] = set()

    def __init__(self, path: str = "data/atlas.sqlite3"):
        self.path = Path(path)

    async def initialize(self) -> None:
        key = str(self.path.resolve())
        if key in AtlasStore._initialized_paths:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            columns = {
                row[1]
                for row in await (await db.execute("PRAGMA table_info(validation_cases)")).fetchall()
            }
            migrations = {
                "pending_reason": "ALTER TABLE validation_cases ADD COLUMN pending_reason TEXT",
                "next_poll_at": "ALTER TABLE validation_cases ADD COLUMN next_poll_at TEXT",
                "retry_count": (
                    "ALTER TABLE validation_cases ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
                ),
                "max_retries": (
                    "ALTER TABLE validation_cases ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 5"
                ),
                "last_retry_at": "ALTER TABLE validation_cases ADD COLUMN last_retry_at TEXT",
            }
            for column, statement in migrations.items():
                if column not in columns:
                    await db.execute(statement)
            # Created here rather than in SCHEMA: on pre-migration databases
            # next_poll_at only exists after the ALTERs above have run.
            # Matches pending_validation_cases exactly (tracking_status
            # equality filter, then the next_poll_at due check).
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_validation_cases_tracking_poll "
                "ON validation_cases(tracking_status, next_poll_at)"
            )
            await self._migrate_gap_observation_columns(db)
            await db.commit()
        AtlasStore._initialized_paths.add(key)

    async def _migrate_gap_observation_columns(self, db: aiosqlite.Connection) -> None:
        """Promote the queried gap fields out of the JSON payload into columns.

        The watch board asks the same three questions of every observation —
        which subject, what gap, was it executable — and answering them with
        `json_extract` meant re-parsing every payload on every request (measured
        0.41s across 11k rows, growing ~3.3k/day). The payload stays the source of
        truth; these columns are a queryable projection of it.
        """
        gap_columns = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(gap_observations)")).fetchall()
        }
        for column, statement in {
            "event_subject": "ALTER TABLE gap_observations ADD COLUMN event_subject TEXT",
            "best_gap": "ALTER TABLE gap_observations ADD COLUMN best_gap REAL",
            "executable": "ALTER TABLE gap_observations ADD COLUMN executable INTEGER",
        }.items():
            if column not in gap_columns:
                await db.execute(statement)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_gap_observations_subject "
            "ON gap_observations(event_subject)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_gap_observations_created "
            "ON gap_observations(created_at)"
        )
        # Backfill once. The index above makes the "is there anything left?" probe
        # a seek rather than a scan, so later startups pay almost nothing.
        pending = await (
            await db.execute("SELECT 1 FROM gap_observations WHERE event_subject IS NULL LIMIT 1")
        ).fetchone()
        if pending:
            await db.execute(
                """UPDATE gap_observations SET
                       event_subject = json_extract(payload_json, '$.event_subject'),
                       best_gap = CAST(json_extract(payload_json, '$.best_gap') AS REAL),
                       executable = CASE
                           WHEN json_extract(payload_json, '$.executable_gap') IN (1, 'true')
                           THEN 1 ELSE 0 END
                   WHERE event_subject IS NULL"""
            )

    async def prune(self, *, now: datetime | None = None) -> dict[str, int]:
        """Delete stale operational rows; returns rows deleted per table.

        Removes only reproducible operational exhaust:
        - ``orderbook_snapshots`` older than ``PRUNE_ORDERBOOK_MAX_AGE_DAYS``
        - ``catalog_reports``, ``discovery_scans``, and ``agent_runs`` beyond
          the newest ``PRUNE_KEEP_NEWEST`` rows (only their newest row is ever
          read; the rest is kept purely as a short debugging tail)

        The evidence/label chain is NEVER touched: market_evidence_snapshots,
        validation_cases, validation_outcomes, learning_examples, paper_trades,
        paper_trade_outcomes, and opportunities are the system's ground truth
        and must stay append-only.

        DELETE frees pages inside the file but never shrinks it; run a
        one-time manual ``VACUUM`` against the database to reclaim disk.
        """
        await self.initialize()
        cutoff = (
            (now or datetime.now(UTC)) - timedelta(days=PRUNE_ORDERBOOK_MAX_AGE_DAYS)
        ).isoformat()
        deleted: dict[str, int] = {}
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "DELETE FROM orderbook_snapshots WHERE timestamp < ?", (cutoff,)
            )
            deleted["orderbook_snapshots"] = cursor.rowcount
            for table, id_column in (
                ("catalog_reports", "report_id"),
                ("discovery_scans", "scan_id"),
                ("agent_runs", "run_id"),
            ):
                cursor = await db.execute(
                    f"DELETE FROM {table} WHERE {id_column} NOT IN "
                    f"(SELECT {id_column} FROM {table} ORDER BY {id_column} DESC LIMIT ?)",
                    (PRUNE_KEEP_NEWEST,),
                )
                deleted[table] = cursor.rowcount
            await db.commit()
        return deleted

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

    async def recent_trusted_labels(self, limit: int = 8) -> list[dict[str, object]]:
        """Compact, newest-first view of trusted labels (never UNLABELED observations)."""
        await self.initialize()
        limit = max(1, min(int(limit), 20))
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT example_id, label, created_at, payload_json
                    FROM learning_examples WHERE label != 'UNLABELED'
                    ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                )
            ).fetchall()
        summaries: list[dict[str, object]] = []
        for example_id, label, created_at, payload_json in rows:
            payload = json.loads(payload_json)
            market_a = payload.get("market_a") or {}
            market_b = payload.get("market_b") or {}
            evidence = payload.get("evidence") or {}
            summaries.append(
                {
                    "pair_id": payload.get("pair_id") or example_id,
                    "label": label,
                    "created_at": created_at,
                    "title_a": market_a.get("title"),
                    "venue_a": market_a.get("venue"),
                    "title_b": market_b.get("title"),
                    "venue_b": market_b.get("venue"),
                    "source_kind": evidence.get("source_kind"),
                    "relationship_status": evidence.get("relationship_status"),
                    "outcome_a": evidence.get("outcome_a"),
                    "outcome_b": evidence.get("outcome_b"),
                    "settlement_verified": evidence.get("settlement_verified") is True,
                }
            )
        return summaries

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

    async def review_rejection_counts_by_subject(self) -> dict[str, int]:
        """Persisted review-pair rejection counts per canonical event subject.

        The owner-signed 5-per-event bound (2026-08-13 decision) must hold across
        runs, so the backfill seeds its per-event counter from these counts
        instead of starting every run at zero.
        """
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT json_extract(vc.payload_json,
                        '$.pair.decision.fingerprint_a.event_subject'), COUNT(*)
                    FROM validation_outcomes vo
                    JOIN validation_cases vc ON vo.pair_id = vc.pair_id
                    WHERE vo.trusted_label = 'REJECTED'
                      AND vc.decision_status = 'REVIEW_REQUIRED'
                    GROUP BY 1"""
                )
            ).fetchall()
        return {str(subject or ""): int(count) for subject, count in rows}

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
                 tracking_status, created_at, updated_at, last_checked_at,
                 pending_reason, next_poll_at, retry_count, max_retries, last_retry_at,
                 payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                (
                    case["pair_id"],
                    case["source_kind"],
                    case["decision_status"],
                    case["guarantee_a"],
                    case["guarantee_b"],
                    case.get("tracking_status", "AWAITING_SETTLEMENT"),
                    now,
                    now,
                    case.get("pending_reason"),
                    case.get("next_poll_at"),
                    max(0, int(case.get("retry_count", 0))),
                    max(1, min(int(case.get("max_retries", 5)), 100)),
                    case.get("last_retry_at"),
                    json.dumps(case["payload"]),
                ),
            )
            await db.commit()
        return cursor.rowcount > 0

    async def pending_validation_cases(
        self, limit: int = 20, *, due_only: bool = False, now: datetime | None = None
    ) -> list[dict[str, object]]:
        await self.initialize()
        now_iso = (now or datetime.now(UTC)).isoformat()
        async with aiosqlite.connect(self.path) as db:
            due_clause = "AND (next_poll_at IS NULL OR next_poll_at <= ?)" if due_only else ""
            params: tuple[object, ...] = (now_iso, limit) if due_only else (limit,)
            rows = await (
                await db.execute(
                    f"""SELECT pair_id, source_kind, decision_status, guarantee_a,
                    guarantee_b, tracking_status, last_checked_at, pending_reason,
                    next_poll_at, retry_count, max_retries, last_retry_at, payload_json
                    FROM validation_cases WHERE tracking_status = 'AWAITING_SETTLEMENT'
                    {due_clause}
                    ORDER BY CASE WHEN next_poll_at IS NULL THEN 0 ELSE 1 END,
                             next_poll_at, created_at LIMIT ?""",
                    params,
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
                "pending_reason": row[7],
                "next_poll_at": row[8],
                "retry_count": int(row[9] or 0),
                "max_retries": int(row[10] or 5),
                "last_retry_at": row[11],
                "poll_eligible": row[8] is None or row[8] <= now_iso,
                "payload": json.loads(row[12]),
            }
            for row in rows
        ]

    async def mark_validation_checked(
        self,
        pair_id: str,
        tracking_status: str = "AWAITING_SETTLEMENT",
        *,
        pending_reason: str | None = None,
        next_poll_at: str | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        increment_retry: bool = False,
    ) -> None:
        """Record one settlement poll for a validation case.

        ``retry_count`` counts consecutive evidence *failures*, not polls:
        it moves only when ``increment_retry`` is set (incremented, capped at
        ``max_retries``, stamping ``last_retry_at``) or when an explicit
        ``retry_count`` value is passed (e.g. ``0`` to reset after a clean
        answer). A plain check leaves it untouched.
        """
        await self.initialize()
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE validation_cases SET tracking_status = ?, updated_at = ?,
                last_checked_at = ?, pending_reason = COALESCE(?, pending_reason),
                next_poll_at = COALESCE(?, next_poll_at),
                retry_count = CASE WHEN ? THEN MIN(retry_count + 1, max_retries)
                                   WHEN ? IS NULL THEN retry_count
                                   ELSE MIN(?, max_retries) END,
                max_retries = CASE WHEN ? IS NULL THEN max_retries
                                   ELSE MAX(1, MIN(?, 100)) END,
                last_retry_at = CASE WHEN ? THEN ? ELSE last_retry_at END
                WHERE pair_id = ?""",
                (
                    tracking_status,
                    now,
                    now,
                    pending_reason,
                    next_poll_at,
                    int(increment_retry),
                    retry_count,
                    max(0, int(retry_count)) if retry_count is not None else None,
                    max_retries,
                    max(1, min(int(max_retries), 100)) if max_retries is not None else None,
                    int(increment_retry),
                    now,
                    pair_id,
                ),
            )
            await db.commit()

    async def update_validation_pending(
        self,
        pair_id: str,
        *,
        pending_reason: str,
        next_poll_at: str,
    ) -> None:
        """Record why a case was deferred without pretending it was polled."""
        await self.initialize()
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE validation_cases SET updated_at = ?, pending_reason = ?,
                next_poll_at = ? WHERE pair_id = ?""",
                (now, pending_reason, next_poll_at, pair_id),
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

    async def rules_version_history(
        self, market_ids: list[str]
    ) -> dict[str, list[dict[str, object]]]:
        """Ordered distinct published-rules versions per market, oldest first.

        Read-only. Every blocked frontier pair is waiting on a venue to publish
        different terms, so the arrival of a new `rules_hash` is the only honest
        signal that a blocker is worth re-checking. Never used to change a verdict.
        """
        await self.initialize()
        if not market_ids:
            return {}
        placeholders = ",".join("?" for _ in market_ids)
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    f"""SELECT market_id, rules_hash, MIN(observed_at), MAX(observed_at),
                        COUNT(*)
                        FROM market_evidence_snapshots
                        WHERE market_id IN ({placeholders}) AND rules_hash IS NOT NULL
                        GROUP BY market_id, rules_hash
                        ORDER BY market_id, MIN(observed_at)""",
                    market_ids,
                )
            ).fetchall()
        history: dict[str, list[dict[str, object]]] = {}
        for market_id, rules_hash, first_seen, last_seen, observations in rows:
            history.setdefault(str(market_id), []).append(
                {
                    "rules_hash": str(rules_hash),
                    "first_observed_at": str(first_seen),
                    "last_observed_at": str(last_seen),
                    "observations": int(observations),
                }
            )
        return history

    async def settlement_lag_stats(self) -> dict[str, object]:
        """Aggregate the observed settlement-timing asymmetry across resolved pairs.

        Read-only observability over recorded validation outcomes. Lags are
        signed seconds following ``atlas.validation.settlement_lag_observation``:
        positive means the Kalshi leg settled EARLIER than its Polymarket twin.
        ``median_lag_seconds`` keeps that sign; ``max_lag_seconds`` reports the
        largest magnitude observed, so it is never negative. Outcomes without a
        computable lag are skipped rather than counted as zero.
        """
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute("SELECT payload_json FROM validation_outcomes")
            ).fetchall()
        lags: list[float] = []
        different_day = 0
        first_venue_counts: dict[str, int] = {}
        for (payload_json,) in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            lag = payload.get("settlement_lag_seconds")
            if isinstance(lag, bool) or not isinstance(lag, int | float):
                continue
            lags.append(float(lag))
            if not _outcome_settled_same_day(payload):
                different_day += 1
            first_venue = payload.get("first_settled_venue")
            if first_venue:
                key = str(first_venue)
                first_venue_counts[key] = first_venue_counts.get(key, 0) + 1
        return {
            "pairs_with_lag": len(lags),
            "median_lag_seconds": float(median(lags)) if lags else None,
            "max_lag_seconds": max((abs(lag) for lag in lags), default=None),
            "different_day_pairs": different_day,
            "first_settled_venue_counts": first_venue_counts,
        }

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
                    SUM(CASE WHEN tracking_status = 'RESOLVED' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN tracking_status = 'AWAITING_SETTLEMENT'
                              AND (next_poll_at IS NULL OR next_poll_at <= datetime('now'))
                             THEN 1 ELSE 0 END),
                    SUM(CASE WHEN tracking_status = 'EVIDENCE_EXHAUSTED'
                             THEN 1 ELSE 0 END)
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
            "settlement_lag": await self.settlement_lag_stats(),
            "evidence_versions": versions,
            "markets_tracked": markets,
            "rule_changes": max(0, versions - markets),
            "cases": int(cases[0] or 0),
            "awaiting_settlement": int(cases[1] or 0),
            "resolved_cases": int(cases[2] or 0),
            "poll_eligible": int(cases[3] or 0),
            "retry_exhausted": int(cases[4] or 0),
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

    async def recent_historical_backfills(self, limit: int = 6) -> list[dict[str, object]]:
        """Return the most recent persisted backfill reports, newest first."""
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT payload_json FROM historical_backfills
                    ORDER BY run_id DESC LIMIT ?""",
                    (max(1, min(int(limit), 20)),),
                )
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

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

    async def save_gap_observation(self, observation: dict[str, object]) -> None:
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO gap_observations
                   (observation_id, created_at, payload_json,
                    event_subject, best_gap, executable)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    observation["observation_id"],
                    observation["observed_at"],
                    json.dumps(observation),
                    observation.get("event_subject"),
                    _as_float(observation.get("best_gap")),
                    1 if observation.get("executable_gap") else 0,
                ),
            )
            await db.commit()

    async def save_capacity_sample(self, sample: dict[str, object]) -> None:
        """Record one ladder-walk measurement for one pair at one moment."""
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO capacity_samples
                   (sample_id, captured_at, release_window, kalshi_market_id,
                    polymarket_market_id, event_subject, profitable_contracts,
                    total_profit_usd, top_of_book_contracts, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sample["sample_id"],
                    sample["captured_at"],
                    sample.get("release_window"),
                    sample["kalshi_market_id"],
                    sample["polymarket_market_id"],
                    sample.get("event_subject"),
                    _as_float(sample.get("profitable_contracts")),
                    _as_float(sample.get("total_profit_usd")),
                    _as_float(sample.get("top_of_book_contracts")),
                    json.dumps(sample),
                ),
            )
            await db.commit()

    async def capacity_window_summary(self) -> dict[str, object]:
        """Peak and typical deployable capacity, split by release window.

        The quiet baseline is the ``release_window IS NULL`` bucket, so a
        release peak is always read against the calm market that produced the
        study's $2.88 median rather than against nothing.
        """
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    """SELECT COALESCE(release_window, '_quiet') AS window_name,
                              COUNT(*) AS samples,
                              SUM(CASE WHEN total_profit_usd > 0 THEN 1 ELSE 0 END) AS with_capacity,
                              MAX(total_profit_usd) AS max_profit_usd,
                              MAX(profitable_contracts) AS max_contracts
                       FROM capacity_samples GROUP BY window_name ORDER BY window_name"""
                )
            ).fetchall()
        return {
            "windows": [
                {
                    "window": row["window_name"],
                    "samples": row["samples"],
                    "samples_with_capacity": row["with_capacity"],
                    "max_profit_usd": row["max_profit_usd"],
                    "max_profitable_contracts": row["max_contracts"],
                }
                for row in rows
            ]
        }

    async def recent_gap_observations(self, limit: int = 20) -> list[dict[str, object]]:
        await self.initialize()
        limit = max(1, min(int(limit), 200))
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    "SELECT payload_json FROM gap_observations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def all_gap_observations(self, limit: int = 50000) -> list[dict[str, object]]:
        """Gap observations oldest-first, keeping the NEWEST `limit` rows.

        Callers need ascending order (the bankroll meter compounds chronologically),
        but a plain `ORDER BY created_at ASC LIMIT n` keeps the *oldest* n and drops
        everything after it. At the observed ~1.7k rows/day that would have silently
        frozen the watch board on month-old data while it still read as live. Select
        the newest rows first, then restore ascending order.
        """
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT payload_json FROM (
                           SELECT payload_json, created_at FROM gap_observations
                           ORDER BY created_at DESC LIMIT ?
                       ) ORDER BY created_at ASC""",
                    (limit,),
                )
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def gap_subject_aggregates(self) -> dict[str, dict[str, object]]:
        """All-time per-subject extremes, over every row regardless of load caps.

        The watch board's ALL window must not quietly become "the newest N rows"
        once `all_gap_observations` hits its cap. This reads the promoted columns,
        so it stays an indexed aggregate instead of re-parsing every payload.
        """
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            rows = await (
                await db.execute(
                    """SELECT event_subject, COUNT(*), SUM(COALESCE(executable, 0)),
                              MIN(best_gap), MAX(best_gap),
                              MIN(created_at), MAX(created_at)
                       FROM gap_observations
                       WHERE event_subject IS NOT NULL AND best_gap IS NOT NULL
                       GROUP BY event_subject"""
                )
            ).fetchall()
        return {
            str(subject): {
                "observations": int(count),
                "executable_observations": int(executable or 0),
                "low": low,
                "high": high,
                "first_observed_at": str(first_at),
                "last_observed_at": str(last_at),
            }
            for subject, count, executable, low, high, first_at, last_at in rows
        }

    async def gap_observation_count(self) -> int:
        """Total recorded observations, so a truncated load can say so."""
        await self.initialize()
        async with aiosqlite.connect(self.path) as db:
            row = await (await db.execute("SELECT COUNT(*) FROM gap_observations")).fetchone()
        return int(row[0]) if row else 0

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

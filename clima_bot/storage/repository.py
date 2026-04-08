from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))


def _json_loads(value: Any) -> Any:
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(str(value))


class ClimaBotRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                leaderboard_limit INTEGER NOT NULL,
                shortlisted_count INTEGER NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analysis_candidates (
                run_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                proxy_wallet TEXT NOT NULL,
                user_name TEXT NOT NULL,
                score REAL NOT NULL,
                passed INTEGER NOT NULL,
                reject_reason TEXT NOT NULL,
                rationale TEXT NOT NULL,
                profile_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                verified_badge INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, proxy_wallet)
            );
            CREATE TABLE IF NOT EXISTS tracked_wallets (
                proxy_wallet TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 0,
                approved INTEGER NOT NULL DEFAULT 1,
                active INTEGER NOT NULL DEFAULT 1,
                paused INTEGER NOT NULL DEFAULT 0,
                profile_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                selection_json TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS copied_orders (
                order_id TEXT PRIMARY KEY,
                trade_hash TEXT NOT NULL UNIQUE,
                proxy_wallet TEXT NOT NULL,
                market_id TEXT NOT NULL,
                position_key TEXT NOT NULL,
                asset_symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                exchange_order_id TEXT NOT NULL,
                price_limit REAL NOT NULL,
                size INTEGER NOT NULL,
                notional_usd REAL NOT NULL,
                realized_pnl_usd REAL NOT NULL DEFAULT 0,
                is_open_position INTEGER NOT NULL DEFAULT 1,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wallet_performance_snapshots (
                proxy_wallet TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS engine_state (
                state_id TEXT PRIMARY KEY,
                running INTEGER NOT NULL,
                mode TEXT NOT NULL,
                status_text TEXT NOT NULL,
                last_sync_at TEXT,
                last_error TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS event_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                tag TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                meta TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def save_analysis_run(self, run: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO analysis_runs (
                run_id, category, leaderboard_limit, shortlisted_count, summary, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run["run_id"],
                run["category"],
                int(run["leaderboard_limit"]),
                int(run["shortlisted_count"]),
                str(run["summary"]),
                _json_dumps(run.get("metadata", {})),
                str(run["created_at"]),
            ),
        )
        self.conn.execute("DELETE FROM analysis_candidates WHERE run_id = ?", (run["run_id"],))
        for candidate in candidates:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO analysis_candidates (
                    run_id, rank, proxy_wallet, user_name, score, passed, reject_reason, rationale,
                    profile_json, metrics_json, verified_badge, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    int(candidate["rank"]),
                    str(candidate["proxy_wallet"]),
                    str(candidate["user_name"]),
                    float(candidate["score"]),
                    1 if bool(candidate["passed"]) else 0,
                    str(candidate.get("reject_reason") or ""),
                    str(candidate["rationale"]),
                    _json_dumps(candidate.get("profile", {})),
                    _json_dumps(candidate.get("metrics", {})),
                    1 if bool(candidate.get("verified_badge")) else 0,
                    str(candidate["created_at"]),
                ),
            )
        self.conn.commit()

    def get_latest_analysis(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM analysis_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return {"run": None, "candidates": []}
        candidates = self.conn.execute(
            "SELECT * FROM analysis_candidates WHERE run_id = ? ORDER BY rank ASC, score DESC",
            (row["run_id"],),
        ).fetchall()
        return {
            "run": self._analysis_run_row(row),
            "candidates": [self._analysis_candidate_row(item) for item in candidates],
        }

    def upsert_wallet(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO tracked_wallets (
                proxy_wallet, user_name, score, approved, active, paused, profile_json, metrics_json,
                selection_json, approved_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(proxy_wallet) DO UPDATE SET
                user_name = excluded.user_name,
                score = excluded.score,
                approved = excluded.approved,
                active = excluded.active,
                paused = excluded.paused,
                profile_json = excluded.profile_json,
                metrics_json = excluded.metrics_json,
                selection_json = excluded.selection_json,
                updated_at = excluded.updated_at
            """,
            (
                str(payload["proxy_wallet"]),
                str(payload.get("user_name") or payload["proxy_wallet"]),
                float(payload.get("score") or 0),
                1 if bool(payload.get("approved", True)) else 0,
                1 if bool(payload.get("active", True)) else 0,
                1 if bool(payload.get("paused", False)) else 0,
                _json_dumps(payload.get("profile", {})),
                _json_dumps(payload.get("metrics", {})),
                _json_dumps(payload.get("selection", {})),
                str(payload.get("approved_at") or now),
                now,
            ),
        )
        self.conn.commit()
        return self.get_wallet(str(payload["proxy_wallet"])) or {}

    def get_wallet(self, proxy_wallet: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM tracked_wallets WHERE proxy_wallet = ?",
            (proxy_wallet,),
        ).fetchone()
        return self._wallet_row(row) if row else None

    def list_wallets(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tracked_wallets ORDER BY active DESC, paused ASC, score DESC, approved_at DESC"
        ).fetchall()
        return [self._wallet_row(row) for row in rows]

    def set_wallet_paused(self, proxy_wallet: str, paused: bool) -> dict[str, Any] | None:
        self.conn.execute(
            "UPDATE tracked_wallets SET paused = ?, active = ?, updated_at = ? WHERE proxy_wallet = ?",
            (1 if paused else 0, 0 if paused else 1, datetime.now(UTC).isoformat(), proxy_wallet),
        )
        self.conn.commit()
        return self.get_wallet(proxy_wallet)

    def remove_wallet(self, proxy_wallet: str) -> None:
        self.conn.execute("DELETE FROM tracked_wallets WHERE proxy_wallet = ?", (proxy_wallet,))
        self.conn.commit()

    def count_wallets(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS total FROM tracked_wallets").fetchone()
        return int(row["total"]) if row else 0

    def record_order(self, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO copied_orders (
                order_id, trade_hash, proxy_wallet, market_id, position_key, asset_symbol, direction,
                action, status, exchange_order_id, price_limit, size, notional_usd, realized_pnl_usd,
                is_open_position, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload["order_id"]),
                str(payload["trade_hash"]),
                str(payload["proxy_wallet"]),
                str(payload["market_id"]),
                str(payload["position_key"]),
                str(payload.get("asset_symbol") or "WEATHER"),
                str(payload["direction"]),
                str(payload["action"]),
                str(payload["status"]),
                str(payload.get("exchange_order_id") or ""),
                float(payload["price_limit"]),
                int(payload["size"]),
                float(payload["notional_usd"]),
                float(payload.get("realized_pnl_usd") or 0.0),
                1 if bool(payload.get("is_open_position", True)) else 0,
                _json_dumps(payload.get("metadata", {})),
                str(payload.get("created_at") or datetime.now(UTC).isoformat()),
            ),
        )
        self.conn.commit()

    def has_trade_hash(self, trade_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM copied_orders WHERE trade_hash = ? LIMIT 1",
            (trade_hash,),
        ).fetchone()
        return row is not None

    def list_orders(self, *, proxy_wallet: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if proxy_wallet:
            rows = self.conn.execute(
                "SELECT * FROM copied_orders WHERE proxy_wallet = ? ORDER BY created_at DESC LIMIT ?",
                (proxy_wallet, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM copied_orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._order_row(row) for row in rows]

    def upsert_wallet_performance(self, proxy_wallet: str, snapshot: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO wallet_performance_snapshots (proxy_wallet, snapshot_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(proxy_wallet) DO UPDATE SET
                snapshot_json = excluded.snapshot_json,
                updated_at = excluded.updated_at
            """,
            (proxy_wallet, _json_dumps(snapshot), now),
        )
        self.conn.commit()

    def list_wallet_performance(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM wallet_performance_snapshots").fetchall()
        return {str(row["proxy_wallet"]): _json_loads(row["snapshot_json"]) or {} for row in rows}

    def update_engine_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO engine_state (state_id, running, mode, status_text, last_sync_at, last_error, metadata_json, updated_at)
            VALUES ('singleton', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state_id) DO UPDATE SET
                running = excluded.running,
                mode = excluded.mode,
                status_text = excluded.status_text,
                last_sync_at = excluded.last_sync_at,
                last_error = excluded.last_error,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                1 if bool(payload.get("running", False)) else 0,
                str(payload.get("mode") or "paper"),
                str(payload.get("status_text") or ""),
                payload.get("last_sync_at"),
                str(payload.get("last_error") or ""),
                _json_dumps(payload.get("metadata", {})),
                now,
            ),
        )
        self.conn.commit()
        return self.get_engine_state()

    def get_engine_state(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM engine_state WHERE state_id = 'singleton'").fetchone()
        if row is None:
            return {
                "running": False,
                "mode": "paper",
                "status_text": "idle",
                "last_sync_at": None,
                "last_error": "",
                "metadata": {},
                "updated_at": None,
            }
        return {
            "running": bool(row["running"]),
            "mode": str(row["mode"]),
            "status_text": str(row["status_text"]),
            "last_sync_at": row["last_sync_at"],
            "last_error": str(row["last_error"]),
            "metadata": _json_loads(row["metadata_json"]) or {},
            "updated_at": row["updated_at"],
        }

    def append_log(self, level: str, tag: str, title: str, detail: str = "", meta: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO event_logs (level, tag, title, detail, meta, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (level, tag, title, detail, meta, datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def list_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM event_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "level": str(row["level"]),
                "tag": str(row["tag"]),
                "title": str(row["title"]),
                "detail": str(row["detail"]),
                "meta": str(row["meta"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ][::-1]

    def _analysis_run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": str(row["run_id"]),
            "category": str(row["category"]),
            "leaderboard_limit": int(row["leaderboard_limit"]),
            "shortlisted_count": int(row["shortlisted_count"]),
            "summary": str(row["summary"]),
            "metadata": _json_loads(row["metadata_json"]) or {},
            "created_at": str(row["created_at"]),
        }

    def _analysis_candidate_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": str(row["run_id"]),
            "rank": int(row["rank"]),
            "proxy_wallet": str(row["proxy_wallet"]),
            "user_name": str(row["user_name"]),
            "score": float(row["score"]),
            "passed": bool(row["passed"]),
            "reject_reason": str(row["reject_reason"]),
            "rationale": str(row["rationale"]),
            "profile": _json_loads(row["profile_json"]) or {},
            "metrics": _json_loads(row["metrics_json"]) or {},
            "verified_badge": bool(row["verified_badge"]),
            "created_at": str(row["created_at"]),
        }

    def _wallet_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "proxy_wallet": str(row["proxy_wallet"]),
            "user_name": str(row["user_name"]),
            "score": float(row["score"]),
            "approved": bool(row["approved"]),
            "active": bool(row["active"]),
            "paused": bool(row["paused"]),
            "profile": _json_loads(row["profile_json"]) or {},
            "metrics": _json_loads(row["metrics_json"]) or {},
            "selection": _json_loads(row["selection_json"]) or {},
            "approved_at": str(row["approved_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _order_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "order_id": str(row["order_id"]),
            "trade_hash": str(row["trade_hash"]),
            "proxy_wallet": str(row["proxy_wallet"]),
            "market_id": str(row["market_id"]),
            "position_key": str(row["position_key"]),
            "asset_symbol": str(row["asset_symbol"]),
            "direction": str(row["direction"]),
            "action": str(row["action"]),
            "status": str(row["status"]),
            "exchange_order_id": str(row["exchange_order_id"]),
            "price_limit": float(row["price_limit"]),
            "size": int(row["size"]),
            "notional_usd": float(row["notional_usd"]),
            "realized_pnl_usd": float(row["realized_pnl_usd"]),
            "is_open_position": bool(row["is_open_position"]),
            "metadata": _json_loads(row["metadata_json"]) or {},
            "created_at": str(row["created_at"]),
        }

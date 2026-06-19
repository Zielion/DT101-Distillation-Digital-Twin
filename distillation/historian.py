from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .tags import coerce_tag_value


class TagBus:
    def __init__(self) -> None:
        self.tags: dict[str, Any] = {}
        self.last_update: datetime = datetime.now(timezone.utc)

    def publish(self, tags: dict[str, Any]) -> None:
        self.tags.update(tags)
        self.last_update = datetime.now(timezone.utc)

    def snapshot(self) -> dict[str, Any]:
        return dict(self.tags)


class Historian:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tag_history (
                    timestamp TEXT NOT NULL,
                    tick INTEGER,
                    tag TEXT NOT NULL,
                    value TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tag_history)")}
            if "tick" not in columns:
                conn.execute("ALTER TABLE tag_history ADD COLUMN tick INTEGER")
            missing_timestamps = conn.execute(
                "SELECT DISTINCT timestamp FROM tag_history WHERE tick IS NULL ORDER BY timestamp ASC"
            ).fetchall()
            next_tick = int(
                conn.execute("SELECT COALESCE(MAX(tick), -1) + 1 FROM tag_history").fetchone()[0]
            )
            for timestamp_row in missing_timestamps:
                conn.execute(
                    "UPDATE tag_history SET tick = ? WHERE timestamp = ? AND tick IS NULL",
                    (next_tick, timestamp_row[0]),
                )
                next_tick += 1
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_history_time ON tag_history(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_history_tick ON tag_history(tick)")

    def write(self, timestamp: datetime, tags: dict[str, Any], tick: int) -> None:
        rows = [(timestamp.isoformat(), int(tick), tag, str(coerce_tag_value(value))) for tag, value in tags.items()]
        with self._connect() as conn:
            conn.executemany("INSERT INTO tag_history(timestamp, tick, tag, value) VALUES (?, ?, ?, ?)", rows)

    def latest_tick(self) -> int | None:
        with self._connect() as conn:
            value = conn.execute("SELECT MAX(tick) FROM tag_history").fetchone()[0]
        return None if value is None else int(value)

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tag_history")

    def query(
        self,
        tag_names: list[str] | None = None,
        seconds: int = 120,
        ticks: int | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        filters: list[str] = []
        if ticks is not None:
            latest_tick = self.latest_tick()
            if latest_tick is None:
                return []
            filters.append("tick >= ?")
            params.append(max(0, latest_tick - int(ticks)))
        else:
            since = datetime.now(timezone.utc) - timedelta(seconds=seconds)
            filters.append("timestamp >= ?")
            params.append(since.isoformat())
        if tag_names:
            placeholders = ",".join("?" for _ in tag_names)
            filters.append(f"tag IN ({placeholders})")
            params.extend(tag_names)
        where_clause = " AND ".join(filters)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT timestamp, tick, tag, value FROM tag_history WHERE {where_clause} ORDER BY tick ASC, timestamp ASC, tag ASC",
                params,
            ).fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "tick": int(row["tick"]),
                "tag": row["tag"],
                "value": _parse_value(row["value"]),
            }
            for row in rows
        ]


def _parse_value(value: str) -> float | str | bool:
    if value == "True":
        return True
    if value == "False":
        return False
    try:
        return float(value)
    except ValueError:
        return value

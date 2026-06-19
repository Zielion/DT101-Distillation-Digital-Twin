import sqlite3
from datetime import datetime, timedelta, timezone

from distillation.historian import Historian


def test_historian_writes_and_queries_recent_tag_values(tmp_path):
    db_path = tmp_path / "history.sqlite"
    historian = Historian(db_path)
    timestamp = datetime.now(timezone.utc)

    historian.write(timestamp, {"DT101.PV.TOP_TEMP": 78.4, "DT101.STATE.MODE": "NORMAL_OPERATION"}, tick=0)
    rows = historian.query(["DT101.PV.TOP_TEMP", "DT101.STATE.MODE"], ticks=60)

    assert len(rows) == 2
    assert {row["tick"] for row in rows} == {0}
    values = {row["tag"]: row["value"] for row in rows}
    assert values["DT101.PV.TOP_TEMP"] == 78.4
    assert values["DT101.STATE.MODE"] == "NORMAL_OPERATION"


def test_historian_queries_a_simulated_tick_window(tmp_path):
    historian = Historian(tmp_path / "history.sqlite")
    timestamp = datetime.now(timezone.utc)
    for tick in (0, 1, 5):
        historian.write(timestamp + timedelta(seconds=tick), {"DT101.PV.TOP_TEMP": 70.0 + tick}, tick=tick)

    rows = historian.query(["DT101.PV.TOP_TEMP"], ticks=2)

    assert [row["tick"] for row in rows] == [5]
    assert historian.latest_tick() == 5


def test_historian_migrates_legacy_timestamp_rows_to_ticks(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tag_history (timestamp TEXT NOT NULL, tag TEXT NOT NULL, value TEXT NOT NULL)")
        conn.executemany(
            "INSERT INTO tag_history(timestamp, tag, value) VALUES (?, ?, ?)",
            [
                ("2026-01-01T00:00:00+00:00", "A", "1"),
                ("2026-01-01T00:00:00+00:00", "B", "2"),
                ("2026-01-01T00:00:01+00:00", "A", "3"),
            ],
        )

    historian = Historian(db_path)
    rows = historian.query(ticks=10)

    assert [row["tick"] for row in rows] == [0, 0, 1]
    assert historian.latest_tick() == 1


def test_historian_clear_removes_rows_without_deleting_database(tmp_path):
    historian = Historian(tmp_path / "history.sqlite")
    historian.write(datetime.now(timezone.utc), {"A": 1.0}, tick=0)

    historian.clear()

    assert historian.query(ticks=10) == []
    assert historian.latest_tick() is None

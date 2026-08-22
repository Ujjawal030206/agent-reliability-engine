"""SQLite-backed run history, for the Reliability Scorecard + Regression Tracker."""

import sqlite3
import json
import time
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "runs.db")


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            agent_version TEXT,
            timestamp REAL,
            score REAL,
            total_scenarios INTEGER,
            passed INTEGER,
            failed INTEGER,
            results_json TEXT
        )
    """)
    return conn


def save_run(run_id: str, agent_version: str, score: float, total: int,
             passed: int, failed: int, results: list):
    conn = _connect()
    conn.execute(
        "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, agent_version, time.time(), score, total, passed, failed, json.dumps(results)),
    )
    conn.commit()
    conn.close()


def get_all_runs():
    conn = _connect()
    rows = conn.execute(
        "SELECT run_id, agent_version, timestamp, score, total_scenarios, passed, failed "
        "FROM runs ORDER BY timestamp ASC"
    ).fetchall()
    conn.close()
    cols = ["run_id", "agent_version", "timestamp", "score", "total_scenarios", "passed", "failed"]
    return [dict(zip(cols, r)) for r in rows]


def get_run_results(run_id: str):
    conn = _connect()
    row = conn.execute("SELECT results_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    return json.loads(row[0]) if row else []

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from factory.bus.schemas import GateStatus, StageStatus


class PipelineState:
    """SQLite-backed pipeline state persistence.

    Schema:
        runs: run_id, pipeline_name, status, created_at, updated_at
        stages: run_id, stage_name, status, started_at, completed_at, retry_count, error
        gates: run_id, stage_name, status, reviewed_at, feedback
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    pipeline_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    current_stage TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stages (
                    run_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    started_at TEXT,
                    completed_at TEXT,
                    retry_count INTEGER DEFAULT 0,
                    error TEXT,
                    PRIMARY KEY (run_id, stage_name)
                );
                CREATE TABLE IF NOT EXISTS gates (
                    run_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewed_at TEXT,
                    feedback TEXT,
                    PRIMARY KEY (run_id, stage_name)
                );
            """)

    def create_run(self, run_id: str, pipeline_name: str) -> None:
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, pipeline_name, status, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?)",
                (run_id, pipeline_name, now, now),
            )

    def update_run_status(self, run_id: str, status: str, current_stage: Optional[str] = None) -> None:
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            if current_stage:
                conn.execute(
                    "UPDATE runs SET status = ?, current_stage = ?, updated_at = ? WHERE run_id = ?",
                    (status, current_stage, now, run_id),
                )
            else:
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (status, now, run_id),
                )

    def get_run(self, run_id: str) -> Optional[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def set_stage_status(self, run_id: str, stage_name: str, status: StageStatus,
                         error: Optional[str] = None) -> None:
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            existing = conn.execute(
                "SELECT status FROM stages WHERE run_id = ? AND stage_name = ?",
                (run_id, stage_name),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE stages SET status = ?, error = ?,
                       completed_at = CASE WHEN ? IN ('completed', 'rejected', 'failed') THEN ? ELSE completed_at END
                       WHERE run_id = ? AND stage_name = ?""",
                    (status.value, error, status.value, now, run_id, stage_name),
                )
            else:
                conn.execute(
                    "INSERT INTO stages (run_id, stage_name, status, started_at, completed_at, error) VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, stage_name, status.value, now,
                     now if status in (StageStatus.COMPLETED, StageStatus.REJECTED, StageStatus.FAILED) else None,
                     error),
                )

    def get_stage_status(self, run_id: str, stage_name: str) -> Optional[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM stages WHERE run_id = ? AND stage_name = ?",
                (run_id, stage_name),
            ).fetchone()
            return dict(row) if row else None

    def set_gate_status(self, run_id: str, stage_name: str, status: GateStatus,
                        feedback: Optional[str] = None) -> None:
        now = datetime.now().isoformat()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gates (run_id, stage_name, status, reviewed_at, feedback)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, stage_name, status.value, now, feedback),
            )

    def get_gate_status(self, run_id: str, stage_name: str) -> Optional[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? AND stage_name = ?",
                (run_id, stage_name),
            ).fetchone()
            return dict(row) if row else None

    def get_all_stages(self, run_id: str) -> list[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM stages WHERE run_id = ? ORDER BY started_at",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_gates(self, run_id: str) -> list[dict]:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? ORDER BY reviewed_at",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

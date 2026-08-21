from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def vacancy_markdown_url(vacancy_id: str) -> str:
    return f"[https://hh.ru/vacancy/](https://hh.ru/vacancy/){vacancy_id}"


@dataclass
class TokenStore:
    path: Path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if "obtained_at" not in token:
            token["obtained_at"] = utc_now()
        self.path.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")
        self.path.chmod(0o600)


class ApplicationLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.history_path = path.with_name("history.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                vacancy_id TEXT NOT NULL,
                resume_id TEXT NOT NULL,
                status TEXT NOT NULL,
                vacancy_name TEXT,
                employer_name TEXT,
                url TEXT,
                reason TEXT,
                response_status INTEGER,
                response_body TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (vacancy_id, resume_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_usage (
                day TEXT PRIMARY KEY,
                calls INTEGER NOT NULL DEFAULT 0,
                approved INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def _record_history(self, entry: dict[str, Any]) -> None:
        try:
            history = json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else []
            if not isinstance(history, list):
                history = []
        except json.JSONDecodeError:
            history = []
        history.append(entry)
        self.history_path.write_text(json.dumps(history[-1000:], ensure_ascii=False, indent=2), encoding="utf-8")

    def was_processed(self, vacancy_id: str, resume_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT status FROM applications
            WHERE vacancy_id = ? AND resume_id = ?
            """,
            (vacancy_id, resume_id),
        ).fetchone()
        return row is not None and row["status"] in {"applied", "already_applied"}

    def was_vacancy_processed(self, vacancy_id: str) -> bool:
        row = self.conn.execute(
            """
            SELECT status FROM applications
            WHERE vacancy_id = ?
              AND status IN ('applied', 'already_applied')
            LIMIT 1
            """,
            (vacancy_id,),
        ).fetchone()
        return row is not None

    def llm_calls_today(self, day: str | None = None) -> int:
        day = day or datetime.now().date().isoformat()
        row = self.conn.execute("SELECT calls FROM llm_usage WHERE day = ?", (day,)).fetchone()
        return int(row["calls"]) if row else 0

    def record_llm_call(self, status: str, day: str | None = None) -> None:
        day = day or datetime.now().date().isoformat()
        normalized = status.lower()
        approved = 1 if normalized == "approved" else 0
        skipped = 1 if normalized == "skip" else 0
        errors = 1 if normalized not in {"approved", "skip"} else 0
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO llm_usage (day, calls, approved, skipped, errors, updated_at)
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                calls=llm_usage.calls + 1,
                approved=llm_usage.approved + excluded.approved,
                skipped=llm_usage.skipped + excluded.skipped,
                errors=llm_usage.errors + excluded.errors,
                updated_at=excluded.updated_at
            """,
            (day, approved, skipped, errors, now),
        )
        self.conn.commit()

    def record(
        self,
        vacancy: dict[str, Any],
        resume_id: str,
        status: str,
        reason: str = "",
        response_status: int | None = None,
        response_body: str = "",
    ) -> None:
        now = utc_now()
        employer = vacancy.get("employer") or {}
        vacancy_id = str(vacancy["id"])
        url = vacancy_markdown_url(vacancy_id)
        self.conn.execute(
            """
            INSERT INTO applications (
                vacancy_id, resume_id, status, vacancy_name, employer_name, url,
                reason, response_status, response_body, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vacancy_id, resume_id) DO UPDATE SET
                status=excluded.status,
                reason=excluded.reason,
                response_status=excluded.response_status,
                response_body=excluded.response_body,
                updated_at=excluded.updated_at
            """,
            (
                vacancy_id,
                resume_id,
                status,
                vacancy.get("name", ""),
                employer.get("name", ""),
                url,
                reason,
                response_status,
                response_body[:4000],
                now,
                now,
            ),
        )
        self.conn.commit()
        self._record_history(
            {
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "status": status,
                "vacancy_name": vacancy.get("name", ""),
                "employer_name": employer.get("name", ""),
                "url": url,
                "reason": reason,
                "response_status": response_status,
                "response_body": response_body[:4000],
                "updated_at": now,
            }
        )

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT vacancy_id, status, vacancy_name, employer_name, reason, updated_at
            FROM applications
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def close(self) -> None:
        self.conn.close()

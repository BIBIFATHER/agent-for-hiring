from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
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
                vacancy_fingerprint TEXT,
                status TEXT NOT NULL,
                vacancy_name TEXT,
                employer_name TEXT,
                url TEXT,
                reason TEXT,
                decision_reason TEXT,
                agent_decision TEXT,
                user_decision TEXT,
                fit_score INTEGER,
                cover_letter TEXT,
                hh_negotiation_id TEXT,
                response_status INTEGER,
                response_body TEXT,
                funnel_status TEXT,
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
        for column, ddl in [
            ("vacancy_fingerprint", "TEXT"),
            ("decision_reason", "TEXT"),
            ("agent_decision", "TEXT"),
            ("user_decision", "TEXT"),
            ("fit_score", "INTEGER"),
            ("cover_letter", "TEXT"),
            ("hh_negotiation_id", "TEXT"),
            ("funnel_status", "TEXT"),
        ]:
            try:
                self.conn.execute(f"ALTER TABLE applications ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError:
                pass
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_fingerprint ON applications(vacancy_fingerprint)")
        self.conn.commit()

    @staticmethod
    def _normalize(value: Any) -> str:
        return " ".join(str(value or "").lower().split())

    @classmethod
    def compute_vacancy_fingerprint(cls, vacancy: dict[str, Any]) -> str:
        employer = vacancy.get("employer") or {}
        salary = vacancy.get("salary") or {}
        location = vacancy.get("area") or vacancy.get("city") or ""
        description = str(vacancy.get("description") or vacancy.get("snippet") or vacancy.get("responsibility") or "")
        payload = " | ".join(
            [
                cls._normalize(employer.get("name", "") if isinstance(employer, dict) else employer),
                cls._normalize(vacancy.get("name", "")),
                cls._normalize(location.get("name", "") if isinstance(location, dict) else location),
                cls._normalize(json.dumps(salary, sort_keys=True, ensure_ascii=False)),
                hashlib.sha256(description.encode("utf-8")).hexdigest(),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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

    def status_for(self, vacancy_id: str, resume_id: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT status FROM applications
            WHERE vacancy_id = ? AND resume_id = ?
            """,
            (vacancy_id, resume_id),
        ).fetchone()
        return str(row["status"]) if row is not None else None

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

    def was_fingerprint_processed(self, vacancy_fingerprint: str) -> bool:
        row = self.conn.execute(
            """
            SELECT status FROM applications
            WHERE vacancy_fingerprint = ?
              AND status IN ('applied', 'already_applied', 'invitation', 'interview', 'rejected', 'offer')
            LIMIT 1
            """,
            (vacancy_fingerprint,),
        ).fetchone()
        return row is not None

    def was_fingerprint_seen(self, vacancy_fingerprint: str) -> bool:
        row = self.conn.execute(
            """
            SELECT status FROM applications
            WHERE vacancy_fingerprint = ?
            LIMIT 1
            """,
            (vacancy_fingerprint,),
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
        decision_reason: str = "",
        agent_decision: str = "",
        user_decision: str = "",
        fit_score: int | None = None,
        cover_letter: str = "",
        hh_negotiation_id: str = "",
        response_status: int | None = None,
        response_body: str = "",
        funnel_status: str = "",
    ) -> None:
        now = utc_now()
        employer = vacancy.get("employer") or {}
        vacancy_id = str(vacancy["id"])
        url = vacancy_markdown_url(vacancy_id)
        vacancy_fingerprint = self.compute_vacancy_fingerprint(vacancy)
        self.conn.execute(
            """
            INSERT INTO applications (
                vacancy_id, resume_id, vacancy_fingerprint, status, vacancy_name, employer_name, url,
                reason, decision_reason, agent_decision, user_decision, fit_score, cover_letter, hh_negotiation_id,
                response_status, response_body, funnel_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vacancy_id, resume_id) DO UPDATE SET
                vacancy_fingerprint=excluded.vacancy_fingerprint,
                status=excluded.status,
                reason=excluded.reason,
                decision_reason=excluded.decision_reason,
                agent_decision=excluded.agent_decision,
                user_decision=excluded.user_decision,
                fit_score=excluded.fit_score,
                cover_letter=excluded.cover_letter,
                hh_negotiation_id=excluded.hh_negotiation_id,
                response_status=excluded.response_status,
                response_body=excluded.response_body,
                funnel_status=excluded.funnel_status,
                updated_at=excluded.updated_at
            """,
            (
                vacancy_id,
                resume_id,
                vacancy_fingerprint,
                status,
                vacancy.get("name", ""),
                employer.get("name", ""),
                url,
                reason,
                decision_reason,
                agent_decision,
                user_decision,
                fit_score,
                cover_letter[:4000],
                hh_negotiation_id,
                response_status,
                response_body[:4000],
                funnel_status or status,
                now,
                now,
            ),
        )
        self.conn.commit()
        self._record_history(
            {
                "vacancy_id": vacancy_id,
                "resume_id": resume_id,
                "vacancy_fingerprint": vacancy_fingerprint,
                "status": status,
                "vacancy_name": vacancy.get("name", ""),
                "employer_name": employer.get("name", ""),
                "url": url,
                "reason": reason,
                "decision_reason": decision_reason,
                "agent_decision": agent_decision,
                "user_decision": user_decision,
                "fit_score": fit_score,
                "cover_letter": cover_letter[:4000],
                "hh_negotiation_id": hh_negotiation_id,
                "response_status": response_status,
                "response_body": response_body[:4000],
                "funnel_status": funnel_status or status,
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

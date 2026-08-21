from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from typing import Any

from .browser_apply import (
    build_search_url,
    ensure_playwright,
    extract_cards,
    goto_with_retry,
    launch_browser_context,
    load_search_profiles,
)
from .cover_letter import load_rules, mass_card_relevance_decision
from .storage import ApplicationLog


TERMINAL_STATUSES = {
    "applied",
    "already_applied",
    "manual_required",
    "skipped",
    "error_terminal",
}

AGE_BUCKETS = ("0-7", "8-14", "15-30", "31-45", "46-60", "unknown")


@dataclass
class BackfillStats:
    total_cards_seen: int = 0
    unique_vacancy_ids: set[str] = field(default_factory=set)
    already_known: set[str] = field(default_factory=set)
    terminal_known: set[str] = field(default_factory=set)
    fresh_unique: set[str] = field(default_factory=set)
    likely_apply: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    errors: int = 0
    by_query: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    by_age: Counter[str] = field(default_factory=Counter)
    query_hits_by_vacancy: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    queue: dict[str, dict[str, Any]] = field(default_factory=dict)


def browser_backfill_search(settings: Any, log: ApplicationLog) -> dict[str, Any]:
    profiles = load_search_profiles(settings)
    rules = load_rules(settings.cover_letter_rules_file)
    stats = BackfillStats()
    sync_playwright = ensure_playwright()

    with sync_playwright() as pw:
        context, temp_dir = launch_browser_context(pw, settings, headless=settings.browser_headless)
        try:
            page = context.new_page()
            for profile in profiles:
                query_title = str(profile["title"])
                print(f"Backfill profile: {query_title}")
                for page_index in range(settings.search_pages):
                    search_url = build_search_url(settings, page_index, profile)
                    if not goto_with_retry(page, search_url):
                        stats.errors += 1
                        stats.by_query[query_title]["errors"] += 1
                        print(f"ERROR search page {page_index}: navigation failed")
                        break
                    page.wait_for_timeout(1800)
                    cards = extract_cards(page)
                    if not cards:
                        print(f"No vacancies found on page {page_index}.")
                        break
                    for card in cards:
                        record_card(stats, log, profile, card, rules)
        finally:
            context.close()
            if temp_dir is not None:
                temp_dir.cleanup()

    return write_outputs(settings, stats)


def record_card(
    stats: BackfillStats,
    log: ApplicationLog,
    profile: dict[str, Any],
    card: dict[str, Any],
    rules: dict[str, Any],
) -> None:
    vacancy_id = str(card["id"])
    query_title = str(profile["title"])
    resume_key = str(profile["resume_id"])
    stats.total_cards_seen += 1
    stats.unique_vacancy_ids.add(vacancy_id)
    stats.query_hits_by_vacancy[vacancy_id].add(query_title)
    query_counter = stats.by_query[query_title]
    query_counter["cards_seen"] += 1

    first_seen_this_run = vacancy_id not in stats.fresh_unique and vacancy_id not in stats.already_known
    existing = existing_state(log, vacancy_id, resume_key)
    if existing:
        stats.already_known.add(vacancy_id)
        query_counter["already_known"] += 1
        if existing in TERMINAL_STATUSES:
            stats.terminal_known.add(vacancy_id)
            query_counter["terminal_known"] += 1
        return

    if not first_seen_this_run:
        query_counter["duplicate_in_run"] += 1
        return

    vacancy = vacancy_from_card(card)
    fingerprint = log.compute_vacancy_fingerprint(vacancy)
    if log.was_fingerprint_seen(fingerprint):
        stats.already_known.add(vacancy_id)
        query_counter["already_known_fingerprint"] += 1
        return

    stats.fresh_unique.add(vacancy_id)
    query_counter["fresh_unique"] += 1
    bucket = age_bucket(card.get("published_at", ""))
    stats.by_age[bucket] += 1
    query_counter[f"age_{bucket}"] += 1

    decision = mass_card_relevance_decision(vacancy, rules)
    if decision.status == "SKIP":
        stats.skipped.add(vacancy_id)
        query_counter["skipped"] += 1
        query_counter[f"skip_reason:{decision.reason}"] += 1
        return

    stats.likely_apply.add(vacancy_id)
    query_counter["likely_apply"] += 1
    stats.queue[vacancy_id] = {
        "vacancy_id": vacancy_id,
        "title": card.get("name", ""),
        "employer": card.get("employer", ""),
        "url": card.get("url", ""),
        "salary": card.get("salary", ""),
        "snippet": card.get("snippet", ""),
        "published_at": card.get("published_at", ""),
        "age_bucket": bucket,
        "query": query_title,
        "resume_id": resume_key,
        "decision": "likely_apply",
        "decision_reason": decision.reason,
        "vacancy_fingerprint": fingerprint,
    }


def existing_state(log: ApplicationLog, vacancy_id: str, resume_id: str) -> str | None:
    row = log.conn.execute(
        """
        SELECT status FROM applications
        WHERE vacancy_id = ?
          AND (resume_id = ? OR status IN ('applied', 'already_applied'))
        ORDER BY
          CASE WHEN status IN ('applied', 'already_applied') THEN 0 ELSE 1 END,
          updated_at DESC
        LIMIT 1
        """,
        (vacancy_id, resume_id),
    ).fetchone()
    return str(row["status"]) if row is not None else None


def vacancy_from_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(card["id"]),
        "name": card.get("name", ""),
        "employer": {"name": card.get("employer", "")},
        "alternate_url": card.get("url", ""),
        "snippet": card.get("snippet", ""),
        "salary": card.get("salary", ""),
        "published_at": card.get("published_at", ""),
    }


def age_bucket(publication_text: str) -> str:
    age_days = publication_age_days(publication_text)
    if age_days is None:
        return "unknown"
    if age_days <= 7:
        return "0-7"
    if age_days <= 14:
        return "8-14"
    if age_days <= 30:
        return "15-30"
    if age_days <= 45:
        return "31-45"
    if age_days <= 60:
        return "46-60"
    return "unknown"


def publication_age_days(publication_text: str) -> int | None:
    text = str(publication_text or "").strip().lower()
    if not text:
        return None
    if "сегодня" in text:
        return 0
    if "вчера" in text:
        return 1
    match = re.search(r"(\d{1,2})\s+([а-яё]+)", text)
    if not match:
        return None
    day = int(match.group(1))
    months = {
        "января": 1,
        "февраля": 2,
        "марта": 3,
        "апреля": 4,
        "мая": 5,
        "июня": 6,
        "июля": 7,
        "августа": 8,
        "сентября": 9,
        "октября": 10,
        "ноября": 11,
        "декабря": 12,
    }
    month = months.get(match.group(2))
    if not month:
        return None
    now = datetime.now(timezone.utc).date()
    year = now.year
    published = datetime(year, month, day, tzinfo=timezone.utc).date()
    if published > now:
        published = datetime(year - 1, month, day, tzinfo=timezone.utc).date()
    return (now - published).days


def write_outputs(settings: Any, stats: BackfillStats) -> dict[str, Any]:
    outputs_dir = Path("outputs")
    outputs_dir.mkdir(parents=True, exist_ok=True)
    queue_path = outputs_dir / "backfill_queue_60d.json"
    report_path = outputs_dir / "backfill_report_60d.json"
    overlaps = {
        vacancy_id: sorted(queries)
        for vacancy_id, queries in stats.query_hits_by_vacancy.items()
        if len(queries) > 1
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "search_profiles_file": str(settings.search_profiles_file),
        "search_pages": settings.search_pages,
        "total_cards_seen": stats.total_cards_seen,
        "unique_vacancy_ids": len(stats.unique_vacancy_ids),
        "already_known": len(stats.already_known),
        "terminal_known": len(stats.terminal_known),
        "fresh_unique": len(stats.fresh_unique),
        "likely_apply": len(stats.likely_apply),
        "skipped": len(stats.skipped),
        "errors": stats.errors,
        "age_breakdown": {bucket: int(stats.by_age.get(bucket, 0)) for bucket in AGE_BUCKETS},
        "query_overlap_count": len(overlaps),
        "query_overlaps": overlaps,
        "by_query": {query: dict(counter) for query, counter in stats.by_query.items()},
        "queue_path": str(queue_path),
    }
    queue_path.write_text(
        json.dumps(list(stats.queue.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Backfill summary: "
        f"total_cards_seen={report['total_cards_seen']}, "
        f"unique_vacancy_ids={report['unique_vacancy_ids']}, "
        f"already_known={report['already_known']}, "
        f"fresh_unique={report['fresh_unique']}, "
        f"likely_apply={report['likely_apply']}, "
        f"skipped={report['skipped']}, "
        f"errors={report['errors']}, "
        f"query_overlap_count={report['query_overlap_count']}, "
        f"queue={queue_path}"
    )
    return report

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from .hh_api import HHApiError, HHClient
from .cover_letter import evaluate_vacancy, load_rules
from .storage import ApplicationLog


@dataclass
class ApplyStats:
    seen: int = 0
    new: int = 0
    applied: int = 0
    dry_run: int = 0
    skipped: int = 0
    errors: int = 0


def pick_resume_id(client: HHClient, configured_resume_id: str) -> str:
    if configured_resume_id:
        return configured_resume_id
    resumes = client.resumes_mine()
    if not resumes:
        raise SystemExit("No resumes found in HH account.")
    print("Available resumes:")
    for resume in resumes:
        print(f"- {resume.get('id')} | {resume.get('title') or resume.get('name')}")
    raise SystemExit("Set HH_RESUME_ID in .env and run again.")


def apply_to_new_vacancies(client: HHClient, log: ApplicationLog) -> ApplyStats:
    settings = client.settings
    rules = load_rules(settings.cover_letter_rules_file)
    resume_id = pick_resume_id(client, settings.resume_id)
    stats = ApplyStats()

    for page in range(settings.search_pages):
        data = client.vacancies(page)
        vacancies = data.get("items", [])
        if not vacancies:
            break

        for item in vacancies:
            stats.seen += 1
            vacancy_id = str(item["id"])
            if log.was_processed(vacancy_id, resume_id):
                continue

            stats.new += 1
            vacancy = client.vacancy(vacancy_id)
            decision = evaluate_vacancy(vacancy, rules)
            reason = skip_reason(vacancy, decision, settings)
            if reason:
                log.record(vacancy, resume_id, "skipped", reason=reason)
                stats.skipped += 1
                print(f"SKIP {vacancy_id}: {reason}")
                continue

            cover_letter = settings.cover_letter or decision.cover_letter or ""
            if settings.dry_run:
                log.record(vacancy, resume_id, "dry_run", reason="dry run, no request sent")
                stats.dry_run += 1
                print(f"DRY {vacancy_id}: {vacancy.get('name')}")
            else:
                send_application(client, log, vacancy, resume_id, cover_letter, stats)
                if stats.applied >= settings.max_applications_per_run:
                    return stats

            if stats.dry_run + stats.applied >= settings.max_applications_per_run:
                return stats
            time.sleep(settings.request_delay_seconds)

    return stats


def skip_reason(vacancy: dict[str, Any], decision: Any, settings: Any) -> str:
    if getattr(decision, "status", "") == "SKIP":
        return getattr(decision, "reason", "vacancy not approved for cover letter")
    if settings.skip_if_no_response_url and not vacancy.get("response_url"):
        return "vacancy has no API response_url; use apply_alternate_url manually"
    if vacancy.get("has_test"):
        return "vacancy requires HH test; API response is unavailable"
    return ""


def send_application(
    client: HHClient,
    log: ApplicationLog,
    vacancy: dict[str, Any],
    resume_id: str,
    cover_letter: str,
    stats: ApplyStats,
) -> None:
    vacancy_id = str(vacancy["id"])
    try:
        client.apply(vacancy, resume_id, cover_letter)
    except HHApiError as exc:
        value = exc.error_value()
        status = "already_applied" if value == "already_applied" else "error"
        log.record(
            vacancy,
            resume_id,
            status,
            reason=value or "HH API error",
            response_status=exc.status,
            response_body=exc.body,
        )
        if status == "already_applied":
            stats.skipped += 1
            print(f"SKIP {vacancy_id}: already applied")
        else:
            stats.errors += 1
            print(f"ERROR {vacancy_id}: {exc.status} {value or exc.body[:160]}")
        return

    log.record(vacancy, resume_id, "applied", reason="created via HH API")
    stats.applied += 1
    print(f"APPLIED {vacancy_id}: {vacancy.get('name')}")

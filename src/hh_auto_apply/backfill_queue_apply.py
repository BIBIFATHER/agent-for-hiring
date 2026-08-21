from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from .browser_apply import (
    add_to_favorites,
    click_response,
    close_response_modal,
    ensure_playwright,
    extract_vacancy_page_text,
    goto_with_retry,
    handle_captcha_pause,
    launch_browser_context,
    response_flow_status,
    submit_cover_letter_if_present,
    wait_for_hh_confirmation,
    wait_like_human,
)
from .keyword_detector import detect_cover_letter_keyword_instruction, ensure_cover_letter_contains_keyword
from .cover_letter import load_rules
from .llm import choose_cover_letter
from .storage import ApplicationLog


TERMINAL_STATUSES = {
    "applied",
    "already_applied",
    "manual_required",
    "skipped",
    "error_terminal",
    "external_ats_skip",
    "frequent_response_warning",
    "unconfirmed_click",
}


@dataclass
class QueueApplyStats:
    queue_size_before: int = 0
    processed: int = 0
    pages_opened: int = 0
    applied_new: int = 0
    already_applied: int = 0
    manual_required: int = 0
    skipped_terminal: int = 0
    errors: int = 0
    keyword_detector_llm_calls: int = 0
    keyword_detector_input_chars: int = 0
    keyword_detector_input_tokens: int = 0
    opened_timings: list[float] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)


def browser_apply_backfill_queue(settings: Any, log: ApplicationLog) -> dict[str, Any]:
    queue_path = Path(os.environ.get("HH_BACKFILL_QUEUE_FILE", "outputs/backfill_queue_60d.json"))
    progress_path = Path(os.environ.get("HH_BACKFILL_PROGRESS_FILE", "outputs/backfill_queue_progress.json"))
    batch_size = int(os.environ.get("HH_BACKFILL_BATCH_SIZE", settings.max_applications_per_run or 20))
    if not queue_path.is_absolute():
        queue_path = Path.cwd() / queue_path
    if not progress_path.is_absolute():
        progress_path = Path.cwd() / progress_path

    queue = read_json_list(queue_path)
    progress = read_progress(progress_path)
    stats = QueueApplyStats(queue_size_before=len([item for item in queue if not progress_terminal(progress, item)]))
    rules = load_rules(settings.cover_letter_rules_file)
    sync_playwright = ensure_playwright()

    with sync_playwright() as pw:
        context, temp_dir = launch_browser_context(pw, settings, headless=settings.browser_headless)
        try:
            page = context.new_page()
            for item in queue:
                if stats.processed >= batch_size:
                    break
                vacancy_id = str(item["vacancy_id"])
                if progress_terminal(progress, item):
                    continue
                resume_id = str(item.get("resume_id") or settings.resume_id or "browser")
                vacancy = vacancy_from_queue_item(item)
                terminal_status = terminal_status_for(log, vacancy_id, resume_id)
                if terminal_status:
                    stats.skipped_terminal += 1
                    stats.processed += 1
                    result = result_entry(item, terminal_status, f"terminal state before open: {terminal_status}")
                    stats.results.append(result)
                    mark_progress(progress_path, progress, vacancy_id, result)
                    print(f"TERMINAL {vacancy_id}: {terminal_status}")
                    continue
                apply_one_queue_item(page, settings, log, vacancy, item, resume_id, rules, stats, progress_path, progress)
                time.sleep(settings.request_delay_seconds)
        finally:
            context.close()
            if temp_dir is not None:
                temp_dir.cleanup()

    remaining = len([item for item in queue if not progress_terminal(progress, item)])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "queue_file": str(queue_path),
        "progress_file": str(progress_path),
        "queue_size_before": stats.queue_size_before,
        "processed": stats.processed,
        "pages_opened": stats.pages_opened,
        "applied_new": stats.applied_new,
        "already_applied": stats.already_applied,
        "manual_required": stats.manual_required,
        "skipped_terminal": stats.skipped_terminal,
        "errors": stats.errors,
        "keyword_detector_llm_calls": stats.keyword_detector_llm_calls,
        "keyword_detector_input_chars": stats.keyword_detector_input_chars,
        "keyword_detector_input_tokens": stats.keyword_detector_input_tokens,
        "average_time_per_opened_vacancy": round(statistics.mean(stats.opened_timings), 2) if stats.opened_timings else 0,
        "median_time_per_opened_vacancy": round(statistics.median(stats.opened_timings), 2) if stats.opened_timings else 0,
        "queue_size_remaining": remaining,
        "results": stats.results,
    }
    report_path = Path.cwd() / "outputs" / "backfill_queue_apply_last_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Backfill queue apply summary: "
        f"queue_size_before={report['queue_size_before']}, "
        f"processed={report['processed']}, "
        f"pages_opened={report['pages_opened']}, "
        f"applied_new={report['applied_new']}, "
        f"already_applied={report['already_applied']}, "
        f"manual_required={report['manual_required']}, "
        f"skipped_terminal={report['skipped_terminal']}, "
        f"errors={report['errors']}, "
        f"keyword_detector_llm_calls={report['keyword_detector_llm_calls']}, "
        f"keyword_detector_input_chars={report['keyword_detector_input_chars']}, "
        f"keyword_detector_input_tokens={report['keyword_detector_input_tokens']}, "
        f"avg_opened_s={report['average_time_per_opened_vacancy']}, "
        f"median_opened_s={report['median_time_per_opened_vacancy']}, "
        f"queue_size_remaining={report['queue_size_remaining']}"
    )
    return report


def apply_one_queue_item(
    page: Any,
    settings: Any,
    log: ApplicationLog,
    vacancy: dict[str, Any],
    item: dict[str, Any],
    resume_id: str,
    rules: dict[str, Any],
    stats: QueueApplyStats,
    progress_path: Path,
    progress: dict[str, Any],
) -> None:
    vacancy_id = str(vacancy["id"])
    title = str(item.get("title") or vacancy_id)
    reason_prefix = f"{item.get('query', 'backfill_queue')}: "
    started = time.monotonic()
    stats.processed += 1

    if not goto_with_retry(page, str(item.get("url") or f"https://hh.ru/vacancy/{vacancy_id}")):
        log.record(vacancy, resume_id, "error", reason=reason_prefix + "navigation failed")
        stats.errors += 1
        result = result_entry(item, "error", "navigation failed")
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"ERROR {vacancy_id}: navigation failed")
        return

    stats.pages_opened += 1
    wait_like_human(page)
    vacancy["page_text"] = extract_vacancy_page_text(page)
    if handle_captcha_pause(page):
        log.record(vacancy, resume_id, "manual_required", reason=reason_prefix + "captcha pause")
        stats.manual_required += 1
        finish_opened(stats, started)
        result = result_entry(item, "manual_required", "captcha pause")
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        return

    status_before = response_flow_status(page)
    if status_before == "already_applied":
        log.record(vacancy, resume_id, "already_applied", reason=reason_prefix + "already applied on vacancy page")
        stats.already_applied += 1
        finish_opened(stats, started)
        result = result_entry(item, "already_applied", "already applied on vacancy page")
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"ALREADY {vacancy_id}: already applied")
        return
    if status_before == "frequent_response_warning":
        log.record(vacancy, resume_id, "frequent_response_warning", reason=reason_prefix + "frequent responses warning")
        stats.manual_required += 1
        finish_opened(stats, started)
        result = result_entry(item, "manual_required", "frequent responses warning")
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"PAUSE {vacancy_id}: frequent responses warning")
        return
    if status_before == "manual_required":
        favorite_added = add_to_favorites(page, vacancy_id)
        reason = reason_prefix + "manual task/questions required"
        if favorite_added:
            reason += "; added to favorites"
        log.record(vacancy, resume_id, "manual_required", reason=reason)
        stats.manual_required += 1
        finish_opened(stats, started)
        result = result_entry(item, "manual_required", reason)
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
        return

    keyword_instruction = detect_cover_letter_keyword_instruction(vacancy.get("page_text", ""), settings)
    stats.keyword_detector_llm_calls += keyword_instruction.llm_calls
    stats.keyword_detector_input_chars += keyword_instruction.input_chars
    stats.keyword_detector_input_tokens += keyword_instruction.input_tokens
    if keyword_instruction.has_instruction and not keyword_instruction.keyword:
        reason = reason_prefix + f"cover letter keyword/instruction required; {keyword_instruction.reason}"
        log.record(vacancy, resume_id, "manual_required", reason=reason)
        stats.manual_required += 1
        finish_opened(stats, started)
        result = result_entry(item, "manual_required", reason)
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"MANUAL {vacancy_id}: cover letter keyword/instruction required")
        return

    letter_decision = choose_cover_letter(settings, log, vacancy, rules)
    if letter_decision.status == "SKIP":
        log.record(vacancy, resume_id, "skipped", reason=reason_prefix + f"llm_skip: {letter_decision.reason}")
        stats.skipped_terminal += 1
        finish_opened(stats, started)
        result = result_entry(item, "skipped", f"llm_skip: {letter_decision.reason}")
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"SKIP {vacancy_id}: llm_skip {letter_decision.reason}")
        return

    cover_letter = settings.cover_letter or letter_decision.cover_letter or ""
    if keyword_instruction.keyword:
        cover_letter = ensure_cover_letter_contains_keyword(cover_letter, keyword_instruction)
        if keyword_instruction.keyword not in cover_letter:
            reason = reason_prefix + "cover letter keyword/instruction required; keyword missing after letter preparation"
            log.record(vacancy, resume_id, "manual_required", reason=reason)
            stats.manual_required += 1
            finish_opened(stats, started)
            result = result_entry(item, "manual_required", reason)
            stats.results.append(result)
            mark_progress(progress_path, progress, vacancy_id, result)
            print(f"MANUAL {vacancy_id}: cover letter keyword missing")
            return
    outcome = click_response(page)
    if outcome == "opened":
        handle_captcha_pause(page)
        status_after_open = response_flow_status(page)
        if status_after_open == "already_applied":
            close_response_modal(page)
            log.record(vacancy, resume_id, "already_applied", reason=reason_prefix + "already applied warning")
            stats.already_applied += 1
            finish_opened(stats, started)
            result = result_entry(item, "already_applied", "already applied warning")
            stats.results.append(result)
            mark_progress(progress_path, progress, vacancy_id, result)
            print(f"ALREADY {vacancy_id}: already applied")
            return
        if status_after_open in {"frequent_response_warning", "manual_required"}:
            close_response_modal(page)
            favorite_added = add_to_favorites(page, vacancy_id)
            reason = reason_prefix + ("frequent responses warning" if status_after_open == "frequent_response_warning" else "manual task/questions required")
            if favorite_added:
                reason += "; added to favorites"
            log.record(vacancy, resume_id, "manual_required", reason=reason)
            stats.manual_required += 1
            finish_opened(stats, started)
            result = result_entry(item, "manual_required", reason)
            stats.results.append(result)
            mark_progress(progress_path, progress, vacancy_id, result)
            print(f"MANUAL {vacancy_id}: {status_after_open}, favorite={'yes' if favorite_added else 'no'}")
            return

        submit_result = submit_cover_letter_if_present(page, cover_letter)
        if submit_result == "manual_required":
            close_response_modal(page)
            favorite_added = add_to_favorites(page, vacancy_id)
            reason = reason_prefix + "manual task/questions required"
            if favorite_added:
                reason += "; added to favorites"
            log.record(vacancy, resume_id, "manual_required", reason=reason)
            stats.manual_required += 1
            finish_opened(stats, started)
            result = result_entry(item, "manual_required", reason)
            stats.results.append(result)
            mark_progress(progress_path, progress, vacancy_id, result)
            print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
            return
        if submit_result == "submitted":
            handle_captcha_pause(page)
            status_after_submit = response_flow_status(page)
            if status_after_submit == "already_applied":
                close_response_modal(page)
                log.record(vacancy, resume_id, "already_applied", reason=reason_prefix + "already applied warning")
                stats.already_applied += 1
                finish_opened(stats, started)
                result = result_entry(item, "already_applied", "already applied warning")
                stats.results.append(result)
                mark_progress(progress_path, progress, vacancy_id, result)
                print(f"ALREADY {vacancy_id}: already applied")
                return
            if status_after_submit == "applied":
                close_response_modal(page)
                log.record(
                    vacancy,
                    resume_id,
                    "applied",
                    reason=reason_prefix + f"confirmed in browser; cover_source={letter_decision.source}; cover_reason={letter_decision.reason}",
                )
                stats.applied_new += 1
                finish_opened(stats, started)
                result = result_entry(item, "applied", "confirmed in browser")
                stats.results.append(result)
                mark_progress(progress_path, progress, vacancy_id, result)
                print(f"APPLIED {vacancy_id}: {title}")
                return
        elif submit_result == "needs_manual_submit":
            confirmation = wait_for_hh_confirmation(page)
            if confirmation in {"applied", "already_applied"}:
                close_response_modal(page)
                status = "already_applied" if confirmation == "already_applied" else "applied"
                log.record(vacancy, resume_id, status, reason=reason_prefix + "confirmed after response form submit")
                if status == "already_applied":
                    stats.already_applied += 1
                    print(f"ALREADY {vacancy_id}: already applied")
                else:
                    stats.applied_new += 1
                    print(f"APPLIED {vacancy_id}: {title}")
                finish_opened(stats, started)
                result = result_entry(item, status, "confirmed after response form submit")
                stats.results.append(result)
                mark_progress(progress_path, progress, vacancy_id, result)
                return
            close_response_modal(page)
            favorite_added = add_to_favorites(page, vacancy_id)
            reason = reason_prefix + "cover letter form opened but submit button was not found"
            if favorite_added:
                reason += "; added to favorites"
            log.record(vacancy, resume_id, "manual_required", reason=reason)
            stats.manual_required += 1
            finish_opened(stats, started)
            result = result_entry(item, "manual_required", reason)
            stats.results.append(result)
            mark_progress(progress_path, progress, vacancy_id, result)
            print(f"MANUAL {vacancy_id}: submit button not found, favorite={'yes' if favorite_added else 'no'}")
            return

        log.record(
            vacancy,
            resume_id,
            "unconfirmed_click",
            reason=reason_prefix + f"clicked response in browser but HH confirmation was not detected; cover_source={letter_decision.source}; cover_reason={letter_decision.reason}",
        )
        stats.errors += 1
        finish_opened(stats, started)
        result = result_entry(item, "error", "submitted/clicked but HH confirmation was not detected")
        stats.results.append(result)
        mark_progress(progress_path, progress, vacancy_id, result)
        print(f"UNCONFIRMED {vacancy_id}: {title}")
        return

    if outcome == "external_ats_skip":
        log.record(vacancy, resume_id, "external_ats_skip", reason=reason_prefix + "external ATS redirect")
        stats.skipped_terminal += 1
        status, reason = "external_ats_skip", "external ATS redirect"
    elif outcome == "already_applied":
        log.record(vacancy, resume_id, "already_applied", reason=reason_prefix + "already applied warning")
        stats.already_applied += 1
        status, reason = "already_applied", "already applied warning"
    elif outcome == "frequent_response_warning":
        log.record(vacancy, resume_id, "manual_required", reason=reason_prefix + "frequent responses warning")
        stats.manual_required += 1
        status, reason = "manual_required", "frequent responses warning"
    elif outcome == "manual_required":
        favorite_added = add_to_favorites(page, vacancy_id)
        reason = reason_prefix + "manual task/questions required"
        if favorite_added:
            reason += "; added to favorites"
        log.record(vacancy, resume_id, "manual_required", reason=reason)
        stats.manual_required += 1
        status = "manual_required"
    else:
        log.record(vacancy, resume_id, "error", reason=reason_prefix + f"unexpected click outcome: {outcome}")
        stats.errors += 1
        status, reason = "error", f"unexpected click outcome: {outcome}"
    finish_opened(stats, started)
    result = result_entry(item, status, reason)
    stats.results.append(result)
    mark_progress(progress_path, progress, vacancy_id, result)
    print(f"{status.upper()} {vacancy_id}: {reason}")


def terminal_status_for(log: ApplicationLog, vacancy_id: str, resume_id: str) -> str | None:
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
    if row is None:
        return None
    status = str(row["status"])
    return status if status in TERMINAL_STATUSES else None


def vacancy_from_queue_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item["vacancy_id"]),
        "name": item.get("title", ""),
        "employer": {"name": item.get("employer", "")},
        "alternate_url": item.get("url", ""),
        "snippet": item.get("snippet", ""),
        "salary": item.get("salary", ""),
        "published_at": item.get("published_at", ""),
    }


def read_json_list(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"Queue file must contain a JSON list: {path}")
    return [item for item in raw if isinstance(item, dict)]


def read_progress(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def progress_terminal(progress: dict[str, Any], item: dict[str, Any]) -> bool:
    vacancy_id = str(item["vacancy_id"])
    existing = progress.get(vacancy_id)
    return isinstance(existing, dict) and str(existing.get("status", "")) in TERMINAL_STATUSES.union({"error"})


def mark_progress(path: Path, progress: dict[str, Any], vacancy_id: str, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    progress[vacancy_id] = result
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def result_entry(item: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "vacancy_id": str(item["vacancy_id"]),
        "title": item.get("title", ""),
        "employer": item.get("employer", ""),
        "query": item.get("query", ""),
        "url": item.get("url", ""),
        "status": status,
        "reason": reason,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def finish_opened(stats: QueueApplyStats, started: float) -> None:
    stats.opened_timings.append(time.monotonic() - started)

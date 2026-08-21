from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlencode

from .cover_letter import evaluate_vacancy, load_rules
from .llm import choose_cover_letter
from .storage import ApplicationLog


HH_SEARCH_BASE = "https://hh.ru/search/vacancy"
HH_SENT_SYNC_RESUME_ID = "hh-sent-sync"
PROFILE_COPY_IGNORED_NAMES = {
    "SingletonCookie",
    "SingletonLock",
    "SingletonSocket",
    "lockfile",
    "LOCK",
    "RunningChromeVersion",
}
PROFILE_COPY_IGNORED_PREFIXES = (
    "Singleton",
)
PROFILE_COPY_IGNORED_DIRS = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "GrShaderCache",
    "ShaderCache",
    "DawnCache",
    "Media Cache",
    "VideoDecodeStats",
}


@dataclass
class BrowserStats:
    seen: int = 0
    new: int = 0
    clicked: int = 0
    dry_run: int = 0
    skipped: int = 0
    errors: int = 0
    manual_required: int = 0
    external_ats_skip: int = 0
    already_applied: int = 0
    frequent_response_warning: int = 0
    unconfirmed_click: int = 0


def ensure_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is not installed. Run: python -m pip install -e . && python -m playwright install chromium"
        ) from exc
    return sync_playwright


def browser_executable_path() -> str | None:
    path = os.environ.get("HH_BROWSER_EXECUTABLE_PATH", "").strip()
    return path or None


def load_search_profiles(settings: Any) -> list[dict[str, Any]]:
    if settings.search_profiles_file.exists():
        raw_profiles = json.loads(settings.search_profiles_file.read_text(encoding="utf-8"))
        profiles = []
        for profile in raw_profiles:
            search = dict(settings.search_params)
            search.update(profile.get("search") or {})
            profiles.append(
                {
                    "resume_id": profile.get("resume_id") or profile.get("id") or "browser",
                    "title": profile.get("title") or profile.get("id") or "Browser profile",
                    "search": search,
                    "browser_search_url": profile.get("browser_search_url", ""),
                }
            )
        return profiles
    return [
        {
            "resume_id": settings.resume_id or "browser",
            "title": "Default browser search",
            "search": settings.search_params,
            "browser_search_url": settings.browser_search_url,
        }
    ]


def build_search_url(settings: Any, page: int = 0, profile: dict[str, Any] | None = None) -> str:
    profile = profile or {}
    browser_search_url = profile.get("browser_search_url") or settings.browser_search_url
    if browser_search_url:
        separator = "&" if "?" in browser_search_url else "?"
        return f"{browser_search_url}{separator}page={page}"
    params = browser_search_params(profile.get("search") or settings.search_params)
    params["page"] = str(page)
    return f"{HH_SEARCH_BASE}?{urlencode(params, doseq=True)}"


def browser_search_params(search_params: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if text := search_params.get("text"):
        params["text"] = text
    if area := search_params.get("area"):
        params["area"] = area
    if experience := search_params.get("experience"):
        params["experience"] = experience
    if per_page := search_params.get("per_page"):
        params["items_on_page"] = per_page

    # HH API and HH web search use slightly different names for some filters.
    if period := search_params.get("period"):
        params["search_period"] = period
    if work_format := search_params.get("work_format"):
        params["work_format"] = work_format
    elif search_params.get("schedule") == "remote":
        params["work_format"] = "REMOTE"
    elif schedule := search_params.get("schedule"):
        params["schedule"] = schedule
    if employment_form := search_params.get("employment_form"):
        params["employment_form"] = employment_form
    elif search_params.get("employment") == "full":
        params["employment_form"] = "FULL"
    elif employment := search_params.get("employment"):
        params["employment"] = employment
    return params


def browser_login(settings: Any) -> None:
    sync_playwright = ensure_playwright()
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(settings.browser_profile_dir),
            executable_path=browser_executable_path(),
            headless=False,
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.goto("https://hh.ru/account/login", wait_until="domcontentloaded")
        print("A browser is open. Log in to HH there, then press Enter here.")
        input()
        context.close()


def copy_browser_profile(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source):
        root_path = Path(root)
        rel_root = root_path.relative_to(source)
        dirs[:] = [name for name in dirs if name not in PROFILE_COPY_IGNORED_DIRS and not name.startswith(".")]
        for directory in dirs:
            (destination / rel_root / directory).mkdir(parents=True, exist_ok=True)
        for filename in files:
            if filename in PROFILE_COPY_IGNORED_NAMES or any(filename.startswith(prefix) for prefix in PROFILE_COPY_IGNORED_PREFIXES):
                continue
            source_file = root_path / filename
            dest_file = destination / rel_root / filename
            try:
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, dest_file)
            except (FileNotFoundError, OSError):
                continue


def launch_browser_context(pw: Any, settings: Any, *, headless: bool) -> tuple[Any, Any | None]:
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        context = pw.chromium.launch_persistent_context(
            str(settings.browser_profile_dir),
            executable_path=browser_executable_path(),
            headless=headless,
            viewport={"width": 1440, "height": 1000},
        )
        return context, None
    except Exception as exc:
        message = str(exc)
        if "ProcessSingleton" not in message and "SingletonLock" not in message:
            raise
        temp_dir = tempfile.TemporaryDirectory()
        profile_copy = Path(temp_dir.name) / "browser-profile"
        copy_browser_profile(settings.browser_profile_dir, profile_copy)
        context = pw.chromium.launch_persistent_context(
            str(profile_copy),
            executable_path=browser_executable_path(),
            headless=headless,
            viewport={"width": 1440, "height": 1000},
        )
        return context, temp_dir


def browser_sync_sent(settings: Any, log: ApplicationLog) -> int:
    sync_playwright = ensure_playwright()
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        profile_copy = Path(tmp_dir) / "browser-profile"
        copy_browser_profile(settings.browser_profile_dir, profile_copy)
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                str(profile_copy),
                executable_path=browser_executable_path(),
                headless=True,
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            try:
                sent_vacancies = scrape_hh_sent_vacancies(page)
                for vacancy in sent_vacancies:
                    log.record(
                        vacancy,
                        HH_SENT_SYNC_RESUME_ID,
                        "applied",
                        reason="synced from HH negotiations page",
                    )
                return len(sent_vacancies)
            finally:
                context.close()


def scrape_hh_sent_vacancies(page: Any) -> list[dict[str, Any]]:
    sent: dict[str, dict[str, Any]] = {}
    for page_index in range(5):
        url = f"https://hh.ru/applicant/negotiations?page={page_index}" if page_index else "https://hh.ru/applicant/negotiations"
        if not goto_with_retry(page, url):
            continue
        page.wait_for_timeout(2500)
        try:
            raw_items = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('[data-qa*="negotiation"]')).map((item) => {
                  const link = item.querySelector('a[href*="/vacancy/"]');
                  const employerLink = item.querySelector('a[href*="/employer/"]');
                  return link ? {
                    href: link.getAttribute('href') || '',
                    title: (link.innerText || link.textContent || '').trim(),
                    employer: (employerLink && (employerLink.innerText || employerLink.textContent || '').trim()) || '',
                    text: (item.innerText || '').trim(),
                  } : null;
                }).filter(Boolean)
                """
            )
        except Exception:
            continue
        for item in raw_items:
            vacancy = negotiation_item_to_vacancy(item)
            if vacancy is None:
                continue
            sent[str(vacancy["id"])] = vacancy
    return list(sent.values())


def negotiation_item_to_vacancy(item: dict[str, Any]) -> dict[str, Any] | None:
    text = str(item.get("text", "")).lower()
    href = str(item.get("href", ""))
    if "сегодня" not in text:
        return None
    match = re.search(r"/vacancy/(\d+)", href)
    if not match:
        return None
    vacancy_id = match.group(1)
    title = str(item.get("title", "")).strip()
    employer_name = str(item.get("employer", "")).strip()
    return {
        "id": vacancy_id,
        "name": title,
        "employer": {"name": employer_name},
        "alternate_url": f"https://hh.ru{href}" if href.startswith("/") else href,
    }


def browser_apply(settings: Any, log: ApplicationLog) -> BrowserStats:
    profiles = load_search_profiles(settings)
    rules = load_rules(settings.cover_letter_rules_file)
    sync_playwright = ensure_playwright()
    stats = BrowserStats()
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context, temp_dir = launch_browser_context(pw, settings, headless=settings.browser_headless)
        try:
            page = context.new_page()
            total_applications = 0
            for profile in profiles:
                resume_key = profile["resume_id"]
                if total_applications >= settings.max_applications_per_run:
                    break
                print(f"Profile: {profile['title']} ({resume_key})")
                for page_index in range(settings.search_pages):
                    if total_applications >= settings.max_applications_per_run:
                        break
                    search_url = build_search_url(settings, page_index, profile)
                    if not goto_with_retry(page, search_url):
                        print(f"ERROR search page {page_index}: navigation failed")
                        stats.errors += 1
                        break
                    page.wait_for_timeout(2000)
                    cards = extract_cards(page)
                    if not cards:
                        print(f"No vacancies found on page {page_index}.")
                        break

                    for card in cards:
                        stats.seen += 1
                        vacancy_id = card["id"]
                        if log.was_processed(vacancy_id, resume_key) or log.was_vacancy_processed(vacancy_id):
                            continue
                        stats.new += 1

                        reason_prefix = f"{profile['title']}: "
                        vacancy = {
                            "id": vacancy_id,
                            "name": card["name"],
                            "employer": {"name": card["employer"]},
                            "alternate_url": card["url"],
                        }
                        decision = evaluate_vacancy(vacancy, rules)
                        if decision.status == "SKIP":
                            log.record(vacancy, resume_key, "skipped", reason=reason_prefix + decision.reason)
                            stats.skipped += 1
                            print(f"SKIP {vacancy_id}: {decision.reason}")
                            continue
                        if not card["can_apply"]:
                            log.record(vacancy, resume_key, "skipped", reason=reason_prefix + "no visible response button in browser")
                            stats.skipped += 1
                            print(f"SKIP {vacancy_id}: no response button")
                            continue

                        if settings.dry_run:
                            log.record(vacancy, resume_key, "browser_dry_run", reason=reason_prefix + "browser dry run, no click sent")
                            stats.dry_run += 1
                            total_applications += 1
                            print(f"DRY {vacancy_id}: {card['name']}")
                        else:
                            letter_decision = choose_cover_letter(settings, log, vacancy, rules)
                            if letter_decision.status == "SKIP":
                                log.record(vacancy, resume_key, "skipped", reason=reason_prefix + f"llm_skip: {letter_decision.reason}")
                                stats.skipped += 1
                                print(f"SKIP {vacancy_id}: llm_skip {letter_decision.reason}")
                                continue

                        cover_letter = settings.cover_letter or letter_decision.cover_letter or decision.cover_letter or ""
                        response_url = card.get("response_url") or ""
                        if response_url:
                            if not goto_with_retry(page, card["url"]):
                                log.record(vacancy, resume_key, "error", reason=reason_prefix + "vacancy page navigation failed")
                                stats.errors += 1
                                print(f"ERROR {vacancy_id}: vacancy page navigation failed")
                                continue
                            wait_like_human(page)
                            vacancy["page_text"] = extract_vacancy_page_text(page)
                            if handle_captcha_pause(page):
                                log.record(vacancy, resume_key, "manual_required", reason=reason_prefix + "captcha pause")
                                stats.manual_required += 1
                                continue
                            vacancy_page_status = response_flow_status(page)
                            if vacancy_page_status == "already_applied":
                                log.record(vacancy, resume_key, "already_applied", reason=reason_prefix + "already applied on vacancy page")
                                stats.already_applied += 1
                                print(f"ALREADY {vacancy_id}: already applied")
                                total_applications += 1
                                continue
                            if vacancy_page_status == "frequent_response_warning":
                                log.record(vacancy, resume_key, "frequent_response_warning", reason=reason_prefix + "frequent responses warning on vacancy page")
                                stats.frequent_response_warning += 1
                                print(f"PAUSE {vacancy_id}: frequent responses warning")
                                total_applications += 1
                                continue
                            if vacancy_page_status == "manual_required":
                                favorite_added = add_to_favorites(page, vacancy_id)
                                reason = reason_prefix + "manual task/questions required on vacancy page"
                                if favorite_added:
                                    reason += "; added to favorites"
                                log.record(vacancy, resume_key, "manual_required", reason=reason)
                                stats.manual_required += 1
                                print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
                                continue
                            if not goto_with_retry(page, response_url):
                                log.record(vacancy, resume_key, "error", reason=reason_prefix + "response page navigation failed")
                                stats.errors += 1
                                print(f"ERROR {vacancy_id}: response page navigation failed")
                                continue
                            wait_like_human(page)
                            if handle_captcha_pause(page):
                                log.record(vacancy, resume_key, "manual_required", reason=reason_prefix + "captcha pause")
                                stats.manual_required += 1
                                continue
                            status_before_submit = response_flow_status(page)
                            if status_before_submit == "already_applied":
                                log.record(vacancy, resume_key, "already_applied", reason=reason_prefix + "already applied warning")
                                stats.already_applied += 1
                                print(f"ALREADY {vacancy_id}: already applied")
                                total_applications += 1
                                continue
                            if status_before_submit == "frequent_response_warning":
                                log.record(vacancy, resume_key, "frequent_response_warning", reason=reason_prefix + "frequent responses warning")
                                stats.frequent_response_warning += 1
                                print(f"PAUSE {vacancy_id}: frequent responses warning")
                                total_applications += 1
                                continue
                            if status_before_submit == "manual_required":
                                favorite_added = add_to_favorites(page, vacancy_id)
                                reason = reason_prefix + "manual task/questions required"
                                if favorite_added:
                                    reason += "; added to favorites"
                                log.record(vacancy, resume_key, "manual_required", reason=reason)
                                stats.manual_required += 1
                                print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
                                continue

                            submit_result = submit_cover_letter_if_present(page, cover_letter)
                            if submit_result == "submitted":
                                handle_captcha_pause(page)
                                status_after_submit = response_flow_status(page)
                                if status_after_submit == "already_applied":
                                    log.record(vacancy, resume_key, "already_applied", reason=reason_prefix + "already applied warning")
                                    stats.already_applied += 1
                                    print(f"ALREADY {vacancy_id}: already applied")
                                    total_applications += 1
                                    continue
                                if status_after_submit == "frequent_response_warning":
                                    log.record(vacancy, resume_key, "frequent_response_warning", reason=reason_prefix + "frequent responses warning")
                                    stats.frequent_response_warning += 1
                                    print(f"PAUSE {vacancy_id}: frequent responses warning")
                                    total_applications += 1
                                    continue
                                if status_after_submit == "manual_required":
                                    favorite_added = add_to_favorites(page, vacancy_id)
                                    reason = reason_prefix + "manual task/questions required"
                                    if favorite_added:
                                        reason += "; added to favorites"
                                    log.record(vacancy, resume_key, "manual_required", reason=reason)
                                    stats.manual_required += 1
                                    print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
                                    continue
                                if status_after_submit == "applied":
                                    log.record(
                                        vacancy,
                                        resume_key,
                                        "applied",
                                        reason=reason_prefix + f"confirmed in browser; cover_source={letter_decision.source}; cover_reason={letter_decision.reason}",
                                    )
                                    stats.clicked += 1
                                    total_applications += 1
                                    print(f"APPLIED {vacancy_id}: {card['name']}")
                                    continue
                            elif submit_result == "needs_manual_submit":
                                confirmation = wait_for_hh_confirmation(page)
                                if confirmation in {"applied", "already_applied"}:
                                    log.record(
                                        vacancy,
                                        resume_key,
                                        "already_applied" if confirmation == "already_applied" else "applied",
                                        reason=reason_prefix + "confirmed after response form submit",
                                    )
                                    if confirmation == "already_applied":
                                        stats.already_applied += 1
                                        print(f"ALREADY {vacancy_id}: already applied")
                                    else:
                                        stats.clicked += 1
                                        print(f"APPLIED {vacancy_id}: {card['name']}")
                                    total_applications += 1
                                    continue
                                favorite_added = add_to_favorites(page, vacancy_id)
                                reason = reason_prefix + "cover letter form opened but submit button was not found"
                                if favorite_added:
                                    reason += "; added to favorites"
                                log.record(vacancy, resume_key, "manual_required", reason=reason)
                                stats.manual_required += 1
                                print(f"MANUAL {vacancy_id}: submit button not found, favorite={'yes' if favorite_added else 'no'}")
                                continue
                            log.record(
                                vacancy,
                                resume_key,
                                "unconfirmed_click",
                                reason=reason_prefix + f"submitted response page but HH confirmation was not detected; cover_source={letter_decision.source}; cover_reason={letter_decision.reason}",
                            )
                            stats.unconfirmed_click += 1
                            print(f"UNCONFIRMED {vacancy_id}: {card['name']}")
                        else:
                            if not goto_with_retry(page, card["url"]):
                                log.record(vacancy, resume_key, "error", reason=reason_prefix + "navigation failed")
                                stats.errors += 1
                                print(f"ERROR {vacancy_id}: navigation failed")
                                continue
                            wait_like_human(page)
                            vacancy["page_text"] = extract_vacancy_page_text(page)
                            if handle_captcha_pause(page):
                                log.record(vacancy, resume_key, "manual_required", reason=reason_prefix + "captcha pause")
                                stats.manual_required += 1
                                continue

                            outcome = click_response(page)
                            if outcome == "opened":
                                handle_captcha_pause(page)
                                status_after_open = response_flow_status(page)
                                if status_after_open == "already_applied":
                                    close_response_modal(page)
                                    log.record(vacancy, resume_key, "already_applied", reason=reason_prefix + "already applied warning")
                                    stats.already_applied += 1
                                    print(f"ALREADY {vacancy_id}: already applied")
                                    total_applications += 1
                                    continue
                                if status_after_open == "frequent_response_warning":
                                    close_response_modal(page)
                                    log.record(vacancy, resume_key, "frequent_response_warning", reason=reason_prefix + "frequent responses warning")
                                    stats.frequent_response_warning += 1
                                    print(f"PAUSE {vacancy_id}: frequent responses warning")
                                    total_applications += 1
                                    continue
                                if status_after_open == "manual_required":
                                    close_response_modal(page)
                                    favorite_added = add_to_favorites(page, vacancy_id)
                                    reason = reason_prefix + "manual task/questions required"
                                    if favorite_added:
                                        reason += "; added to favorites"
                                    log.record(vacancy, resume_key, "manual_required", reason=reason)
                                    stats.manual_required += 1
                                    print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
                                    continue

                                submit_result = submit_cover_letter_if_present(page, cover_letter)
                                if submit_result == "submitted":
                                    handle_captcha_pause(page)
                                    status_after_submit = response_flow_status(page)
                                    if status_after_submit == "already_applied":
                                        close_response_modal(page)
                                        log.record(vacancy, resume_key, "already_applied", reason=reason_prefix + "already applied warning")
                                        stats.already_applied += 1
                                        print(f"ALREADY {vacancy_id}: already applied")
                                        continue
                                    if status_after_submit == "applied":
                                        close_response_modal(page)
                                        log.record(
                                            vacancy,
                                            resume_key,
                                            "applied",
                                            reason=reason_prefix + f"confirmed in browser; cover_source={letter_decision.source}; cover_reason={letter_decision.reason}",
                                        )
                                        stats.clicked += 1
                                        total_applications += 1
                                        print(f"APPLIED {vacancy_id}: {card['name']}")
                                        continue
                                elif submit_result == "needs_manual_submit":
                                    confirmation = wait_for_hh_confirmation(page)
                                    if confirmation in {"applied", "already_applied"}:
                                        close_response_modal(page)
                                        log.record(
                                            vacancy,
                                            resume_key,
                                            "already_applied" if confirmation == "already_applied" else "applied",
                                            reason=reason_prefix + "confirmed after response form submit",
                                        )
                                        if confirmation == "already_applied":
                                            stats.already_applied += 1
                                            print(f"ALREADY {vacancy_id}: already applied")
                                        else:
                                            stats.clicked += 1
                                            print(f"APPLIED {vacancy_id}: {card['name']}")
                                        total_applications += 1
                                        continue
                                    close_response_modal(page)
                                    favorite_added = add_to_favorites(page, vacancy_id)
                                    reason = reason_prefix + "cover letter form opened but submit button was not found"
                                    if favorite_added:
                                        reason += "; added to favorites"
                                    log.record(vacancy, resume_key, "manual_required", reason=reason)
                                    stats.manual_required += 1
                                    print(f"MANUAL {vacancy_id}: submit button not found, favorite={'yes' if favorite_added else 'no'}")
                                    continue
                                log.record(
                                    vacancy,
                                    resume_key,
                                    "unconfirmed_click",
                                    reason=reason_prefix + f"clicked response in browser but HH confirmation was not detected; cover_source={letter_decision.source}; cover_reason={letter_decision.reason}",
                                )
                                stats.unconfirmed_click += 1
                                print(f"UNCONFIRMED {vacancy_id}: {card['name']}")
                            elif outcome == "external_ats_skip":
                                log.record(vacancy, resume_key, "external_ats_skip", reason=reason_prefix + "external ATS redirect")
                                stats.external_ats_skip += 1
                                print(f"EXTERNAL {vacancy_id}: ATS redirect skipped")
                            elif outcome == "already_applied":
                                log.record(vacancy, resume_key, "already_applied", reason=reason_prefix + "already applied warning")
                                stats.already_applied += 1
                                print(f"ALREADY {vacancy_id}: already applied")
                            elif outcome == "frequent_response_warning":
                                log.record(vacancy, resume_key, "frequent_response_warning", reason=reason_prefix + "frequent responses warning")
                                stats.frequent_response_warning += 1
                                print(f"PAUSE {vacancy_id}: frequent responses warning")
                            elif outcome == "manual_required":
                                favorite_added = add_to_favorites(page, vacancy_id)
                                reason = reason_prefix + "manual task/questions required"
                                if favorite_added:
                                    reason += "; added to favorites"
                                log.record(vacancy, resume_key, "manual_required", reason=reason)
                                stats.manual_required += 1
                                print(f"MANUAL {vacancy_id}: task/questions, favorite={'yes' if favorite_added else 'no'}")
                            elif outcome == "no_response_button":
                                log.record(vacancy, resume_key, "error", reason=reason_prefix + "no response button on vacancy page")
                                stats.errors += 1
                                print(f"ERROR {vacancy_id}: no response button on vacancy page")
                            elif outcome.startswith("click_failed_"):
                                detail = outcome.removeprefix("click_failed_").replace("_", " ")
                                log.record(vacancy, resume_key, "error", reason=reason_prefix + f"failed to click response button in browser: {detail}")
                                stats.errors += 1
                                print(f"ERROR {vacancy_id}: click failed ({detail})")
                            else:
                                log.record(vacancy, resume_key, "error", reason=reason_prefix + f"unexpected click outcome: {outcome}")
                                stats.errors += 1
                                print(f"ERROR {vacancy_id}: {outcome}")

                        total_applications += 1

                        if total_applications >= settings.max_applications_per_run:
                            break
                        time.sleep(settings.request_delay_seconds)
                    if total_applications >= settings.max_applications_per_run:
                        break
        finally:
            context.close()
            if temp_dir is not None:
                temp_dir.cleanup()
    return stats


def extract_cards(page: Any) -> list[dict[str, Any]]:
    return page.evaluate(
        """
        () => {
          const items = Array.from(document.querySelectorAll('[data-qa="vacancy-serp__vacancy"], [data-qa="serp-item"]'));
          return items.map((item) => {
            const link = item.querySelector('a[data-qa="serp-item__title"], a[href*="/vacancy/"]');
            const url = link ? link.href : '';
            const match = url.match(/vacancy\\/(\\d+)/) || url.match(/vacancyId=(\\d+)/);
            const employer = item.querySelector('[data-qa="vacancy-serp__vacancy-employer-text"], [data-qa="vacancy-serp__vacancy-employer"]');
            const response = match
              ? item.querySelector(`a[data-qa="vacancy-serp__vacancy_response"][href*="vacancyId=${match[1]}"], a[href*="vacancy_response"][href*="vacancyId=${match[1]}"], button[data-qa*="vacancy_response"][data-qa*="vacancyId=${match[1]}"]`) ||
                document.querySelector(`a[data-qa="vacancy-serp__vacancy_response"][href*="vacancyId=${match[1]}"], a[href*="vacancy_response"][href*="vacancyId=${match[1]}"], button[data-qa*="vacancy_response"][data-qa*="vacancyId=${match[1]}"]`)
              : null;
            const responseText = response ? (response.innerText || response.textContent || '') : '';
            return {
              id: match ? match[1] : '',
              name: link ? (link.innerText || link.textContent || '').trim() : '',
              employer: employer ? (employer.innerText || employer.textContent || '').trim() : '',
              url,
              response_url: response ? response.href : '',
              can_apply: /Откликнуться|Отклик|Respond|Apply/i.test(responseText)
            };
          }).filter((item) => item.id && item.name);
        }
        """
    )


def goto_with_retry(page: Any, url: str, attempts: int = 2) -> bool:
    for attempt in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return True
        except Exception:
            if attempt + 1 >= attempts:
                return False
            try:
                page.wait_for_timeout(1500)
            except Exception:
                return False
    return False


def extract_vacancy_page_text(page: Any) -> str:
    selectors = [
        '[data-qa="vacancy-title"]',
        '[data-qa="vacancy-company-name"]',
        '[data-qa="vacancy-description"]',
        '[data-qa="vacancy-experience"]',
        '[data-qa="vacancy-view-employment-mode"]',
        '[data-qa="vacancy-view-salary"]',
    ]
    pieces: list[str] = []
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=500):
                text = locator.inner_text(timeout=1000).strip()
                if text:
                    pieces.append(text)
        except Exception:
            continue
    if pieces:
        return "\n\n".join(pieces)
    try:
        return page.locator("body").inner_text(timeout=2000)[:12000]
    except Exception:
        return ""


def click_response(page: Any) -> str:
    try:
        button = find_response_button(page)
        if button is None:
            return "no_response_button"
        known_pages = list(page.context.pages)
        button.click(timeout=5000)
        page.wait_for_timeout(1500)
        external_page = find_external_ats_page(page, known_pages)
        if external_page is not None:
            try:
                external_page.close()
            except Exception:
                pass
            return "external_ats_skip"
        if is_external_ats_url(page.url):
            return "external_ats_skip"
        status = response_flow_status(page)
        if status in {"manual_required", "already_applied", "frequent_response_warning"}:
            if status != "manual_required":
                close_response_modal(page)
            return status
        return "opened"
    except Exception:
        return diagnose_click_failure(page)


def diagnose_click_failure(page: Any) -> str:
    if captcha_present(page):
        return "click_failed_captcha"
    if has_already_applied_warning(page):
        return "click_failed_already_applied"
    if has_frequent_response_warning(page):
        return "click_failed_frequent_response"
    if has_manual_task_or_questions(page):
        return "click_failed_manual_required"
    return "click_failed_unknown"


def response_flow_status(page: Any) -> str:
    if has_already_applied_warning(page):
        return "already_applied"
    if has_response_sent_confirmation(page):
        return "applied"
    if has_frequent_response_warning(page):
        return "frequent_response_warning"
    if has_manual_task_or_questions(page):
        return "manual_required"
    return ""


def has_manual_task_or_questions(page: Any) -> bool:
    patterns = [
        "тестовое задание",
        "задание",
        "ответьте на вопросы",
        "вопросы работодателя",
        "обязательные вопросы",
        "пройти тест",
        "тест",
    ]
    try:
        text = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return False
    return any(pattern in text for pattern in patterns)


def has_already_applied_warning(page: Any) -> bool:
    patterns = [
        "вы откликнулись",
        "отклик другим резюме",
        "резюме доставлено",
        "вы уже откликались",
        "уже откликались",
        "уже был отклик",
        "отклик уже отправлен",
        "already applied",
    ]
    return page_has_any_text(page, patterns)


def has_response_sent_confirmation(page: Any) -> bool:
    patterns = [
        "отклик отправлен",
        "откликнулся",
        "вы откликнулись",
        "резюме отправлено",
        "response sent",
        "application sent",
    ]
    return page_has_any_text(page, patterns)


def has_frequent_response_warning(page: Any) -> bool:
    patterns = [
        "часто откликаетесь",
        "частые отклики",
        "слишком много откликов",
        "много откликов",
    ]
    return page_has_any_text(page, patterns)


def page_has_any_text(page: Any, patterns: list[str]) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return False
    return any(pattern in text for pattern in patterns)


def add_to_favorites(page: Any, vacancy_id: str) -> bool:
    selectors = [
        '[data-qa="vacancy-favorite"]',
        '[data-qa="vacancy__favorite"]',
        '[data-qa*="vacancy"][data-qa*="favorite"]',
        'button[aria-label*="избран" i]',
        'a[aria-label*="избран" i]',
        'button:has-text("В избранное")',
        'button:has-text("Добавить в избранное")',
        f'[data-qa="vacancy-serp__vacancy"]:has(a[href*="/vacancy/{vacancy_id}"]) [data-qa*="favorite"]',
        f'[data-qa="serp-item"]:has(a[href*="/vacancy/{vacancy_id}"]) [data-qa*="favorite"]',
        f'a[href*="/vacancy/{vacancy_id}"] >> xpath=ancestor::*[@data-qa="vacancy-serp__vacancy" or @data-qa="serp-item"]//*[contains(@data-qa, "favorite")]',
    ]
    for selector in selectors:
        try:
            favorite = page.locator(selector).first
            if favorite.is_visible(timeout=1000):
                favorite.click(timeout=2000)
                page.wait_for_timeout(700)
                return True
        except Exception:
            continue
    return False


def close_response_modal(page: Any) -> None:
    for selector in ['button[aria-label="Закрыть"]', '[data-qa="modal-close"]', 'button:has-text("Закрыть")']:
        try:
            locator = page.locator(selector).last
            if locator.is_visible(timeout=500):
                locator.click(timeout=1000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        return


def submit_cover_letter_if_present(page: Any, cover_letter: str) -> str:
    status = response_flow_status(page)
    if status in {"applied", "already_applied"}:
        return "submitted"
    try:
        if cover_letter:
            opener = page.get_by_text("Написать сопроводительное", exact=False).first
            if opener.is_visible(timeout=500):
                opener.click(timeout=1000)
                page.wait_for_timeout(700)
    except Exception:
        pass
    if cover_letter:
        textarea_selectors = [
            'textarea[placeholder*="сопроводительное" i]',
            'textarea[placeholder*="сопровод" i]',
            'textarea[aria-label*="сопроводительное" i]',
            'textarea[aria-label*="сопровод" i]',
            'textarea[placeholder*="письмо" i]',
            'textarea[aria-label*="письмо" i]',
        ]
        for selector in textarea_selectors:
            try:
                textarea = page.locator(selector).first
                if textarea.count() == 0:
                    continue
                if textarea.is_visible(timeout=700):
                    type_cover_letter(textarea, cover_letter[:4000], page)
                    break
            except Exception:
                continue

    if fill_response_questions_if_present(page):
        page.wait_for_timeout(1200)

    for selector in [
        'button[data-qa="vacancy-response-submit-popup"]',
        'button[data-qa*="vacancy-response-submit"]',
        'button:has-text("Откликнуться без теста")',
        'button:has-text("Откликнуться")',
        'button:has-text("Отправить отклик")',
        'button:has-text("Отправить")',
        'button:has-text("Продолжить")',
        'button:has-text("Далее")',
    ]:
        try:
            button = page.locator(selector).first
            if click_locator_with_retry(page, button):
                confirmation = wait_for_hh_confirmation(page)
                if confirmation in {"applied", "already_applied"}:
                    return "submitted"
        except Exception:
            continue
    if response_flow_status(page) == "applied":
        return "submitted"
    return "needs_manual_submit"


def fill_response_questions_if_present(page: Any) -> bool:
    try:
        body = page.locator("body").inner_text(timeout=1500).lower()
    except Exception:
        return False
    question_markers = [
        "ответьте на",
        "вопроса ниже",
        "вопросы работодателя",
        "пожалуйста, ответьте",
        "задать вопрос",
        "писать тут",
    ]
    if not any(marker in body for marker in question_markers):
        return False
    answers = response_question_answers()
    try:
        textareas = page.locator("textarea")
        count = textareas.count()
    except Exception:
        return False
    visible = []
    for index in range(count):
        try:
            textarea = textareas.nth(index)
            if textarea.is_visible(timeout=500):
                visible.append(textarea)
        except Exception:
            continue
    if not visible:
        return False
    for index, textarea in enumerate(visible):
        answer = answers[min(index, len(answers) - 1)]
        try:
            textarea.click(timeout=1000)
            textarea.fill("")
            textarea.type(answer, delay=random.randint(8, 16))
        except Exception:
            try:
                textarea.fill(answer)
            except Exception:
                continue
        try:
            textarea.dispatch_event("input")
            textarea.dispatch_event("change")
            textarea.blur()
        except Exception:
            pass
    page.wait_for_timeout(400)
    return True


def wait_for_hh_confirmation(page: Any) -> str:
    try:
        page.wait_for_function(
            """
            () => {
              const text = (document.body && document.body.innerText || '').toLowerCase();
              return text.includes('вы откликнулись')
                || text.includes('отклик отправлен')
                || text.includes('already applied')
                || text.includes('отклик другим резюме');
            }
            """,
            timeout=15000,
        )
    except Exception:
        pass
    for _ in range(12):
        status = response_flow_status(page)
        if status in {"applied", "already_applied"}:
            return status
        try:
            page.wait_for_load_state("domcontentloaded", timeout=1000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return response_flow_status(page)


def click_locator_with_retry(page: Any, locator: Any) -> bool:
    try:
        if locator.count() == 0:
            return False
    except Exception:
        pass
    for _ in range(5):
        try:
            if locator.is_visible(timeout=500):
                try:
                    locator.scroll_into_view_if_needed(timeout=1000)
                except Exception:
                    pass
                locator.click(timeout=1500)
                return True
        except Exception:
            pass
        try:
            page.wait_for_timeout(300)
        except Exception:
            break
    try:
        locator.click(timeout=1500)
        return True
    except Exception:
        return False


def response_question_answers() -> list[str]:
    return [
        "Да, выстраивал отдел продаж с нуля: воронка, CRM, KPI, регламенты, найм и контроль качества.",
        "Да, работал в консультационных и демо-продажах, вел диагностики и закрывал сделки.",
        "Да, контролировал ФОТ, бонусы, издержки и экономику направления, работал с P&L.",
        "Смотрю на источники, качество лидов, скрипты, подтверждение записи, напоминания и ежедневный разбор причин.",
    ]


def type_cover_letter(textarea: Any, cover_letter: str, page: Any) -> None:
    try:
        textarea.click(timeout=1000)
        textarea.fill("")
        textarea.type(cover_letter, delay=random.randint(10, 20))
    except Exception:
        textarea.fill(cover_letter)
    try:
        textarea.dispatch_event("input")
        textarea.dispatch_event("change")
        textarea.blur()
    except Exception:
        pass
    page.wait_for_timeout(500)


def find_response_button(page: Any) -> Any | None:
    selectors = [
        'a[data-qa="vacancy-response-link"], button[data-qa="vacancy-response-link"]',
        'a[data-qa*="vacancy_response"], button[data-qa*="vacancy_response"]',
        'a[href*="vacancy_response"], button:has-text("Откликнуться")',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=700):
                return locator
        except Exception:
            continue
    try:
        locator = page.get_by_role("button", name=re.compile("Откликнуться|Respond|Apply", re.I)).first
        if locator.is_visible(timeout=700):
            return locator
    except Exception:
        pass
    try:
        locator = page.get_by_role("link", name=re.compile("Откликнуться|Respond|Apply", re.I)).first
        if locator.is_visible(timeout=700):
            return locator
    except Exception:
        pass
    return None


def find_external_ats_page(page: Any, known_pages: list[Any]) -> Any | None:
    known_ids = {id(item) for item in known_pages}
    for candidate in page.context.pages:
        if id(candidate) in known_ids:
            continue
        try:
            candidate.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        if is_external_ats_url(candidate.url):
            return candidate
    return None


def is_external_ats_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = parsed.netloc.lower()
    if not host:
        return False
    return not (host == "hh.ru" or host.endswith(".hh.ru"))


def wait_like_human(page: Any) -> None:
    try:
        page.evaluate(
            """
            () => {
              const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
              window.scrollTo({ top: Math.min(height * 0.35, 900), behavior: 'instant' });
            }
            """
        )
    except Exception:
        pass
    page.wait_for_timeout(int(random.uniform(3000, 7000)))


def handle_captcha_pause(page: Any) -> bool:
    while captcha_present(page):
        print("\a", end="", flush=True)
        print("Captcha detected. Solve it in the browser, then press Enter here.")
        input()
        page.wait_for_timeout(1000)
    return False


def captcha_present(page: Any) -> bool:
    selectors = [
        'iframe[src*="captcha"]',
        'iframe[src*="cf-chl"]',
        '[class*="captcha"]',
        '[id*="captcha"]',
        'input[name*="captcha"]',
    ]
    for selector in selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    try:
        text = page.locator("body").inner_text(timeout=1000).lower()
    except Exception:
        return False
    markers = [
        "captcha",
        "капча",
        "qrator",
        "cloudflare",
        "подтвердите, что вы не робот",
        "verify you are human",
    ]
    return any(marker in text for marker in markers)


def close_optional_modal(page: Any) -> None:
    for text in ["Откликнуться", "Отправить", "Продолжить", "Готово"]:
        try:
            locator = page.get_by_text(text, exact=True).last
            if locator.is_visible(timeout=500):
                locator.click(timeout=1000)
                page.wait_for_timeout(1000)
                return
        except Exception:
            continue

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse
import secrets
import webbrowser

from .apply import apply_to_new_vacancies
from .backfill_queue_apply import browser_apply_backfill_queue
from .backfill_search import browser_backfill_search
from .browser_apply import browser_apply, browser_login, browser_sync_sent
from .config import Settings
from .hh_api import HHClient
from .oauth_server import OAuthCallbackServer
from .storage import ApplicationLog, TokenStore


def build_client() -> HHClient:
    settings = Settings.from_env(Path.cwd())
    return HHClient(settings=settings, token_store=TokenStore(settings.token_file))


def configured(value: str) -> bool:
    return bool(value and "..." not in value and "example.com" not in value)


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def command_doctor(_: argparse.Namespace) -> None:
    client = build_client()
    settings = client.settings
    token = client.token_store.load()

    checks = [
        ("HH_CLIENT_ID", configured(settings.client_id)),
        ("HH_CLIENT_SECRET", configured(settings.client_secret)),
        ("HH_REDIRECT_URI", configured(settings.redirect_uri)),
        ("HH_USER_AGENT", configured(settings.user_agent)),
        ("OAuth token", bool(token.get("access_token"))),
        ("HH_RESUME_ID", configured(settings.resume_id)),
        ("Dry run enabled", settings.dry_run),
        ("Browser fallback package", playwright_available()),
    ]

    for name, ok in checks:
        print(f"{'OK' if ok else 'MISSING'} {name}")

    if not configured(settings.client_id) or not configured(settings.client_secret):
        print("\nNext step:")
        if playwright_available():
            print("API app is not ready. Use browser fallback now:")
            print("1. hh-auto-apply browser-login")
            print("2. hh-auto-apply browser-apply")
        else:
            print("1. python -m pip install -e .")
            print("2. python -m playwright install chromium")
            print("3. hh-auto-apply browser-login")
        return

    if not token.get("access_token"):
        print("\nNext step: hh-auto-apply auth")
        return

    if not configured(settings.resume_id):
        print("\nNext step: hh-auto-apply resumes, then paste the needed id into HH_RESUME_ID")
        return

    print("\nReady. Start safely with: hh-auto-apply apply")


def command_auth(_: argparse.Namespace) -> None:
    client = build_client()
    client.settings.require_oauth_config()
    parsed = urlparse(client.settings.redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise SystemExit("HH_REDIRECT_URI must be local HTTP with a port, e.g. http://127.0.0.1:8765/callback")

    state = secrets.token_urlsafe(24)
    url = client.authorization_url(state)
    print("Open this URL and allow access:")
    print(url)
    webbrowser.open(url)
    server = OAuthCallbackServer(parsed.hostname, parsed.port, state)
    code = server.wait_for_code()
    token = client.exchange_code(code)
    expires = token.get("expires_in", "unknown")
    print(f"Token saved to {client.settings.token_file} (expires_in={expires}).")


def command_resumes(_: argparse.Namespace) -> None:
    client = build_client()
    for resume in client.resumes_mine():
        title = resume.get("title") or resume.get("name") or ""
        print(f"{resume.get('id')} | {title}")


def command_apply(_: argparse.Namespace) -> None:
    client = build_client()
    log = ApplicationLog(client.settings.db_file)
    try:
        stats = apply_to_new_vacancies(client, log)
    finally:
        log.close()
    print(
        "Summary: "
        f"seen={stats.seen}, new={stats.new}, dry_run={stats.dry_run}, "
        f"applied={stats.applied}, skipped={stats.skipped}, errors={stats.errors}"
    )


def command_log(args: argparse.Namespace) -> None:
    settings = Settings.from_env(Path.cwd())
    log = ApplicationLog(settings.db_file)
    try:
        for row in log.recent(args.limit):
            print(
                f"{row['updated_at']} | {row['status']} | {row['vacancy_id']} | "
                f"{row['vacancy_name']} | {row['employer_name']} | {row['reason']}"
            )
    finally:
        log.close()


def command_browser_login(_: argparse.Namespace) -> None:
    settings = Settings.from_env(Path.cwd())
    browser_login(settings)


def command_browser_sync(_: argparse.Namespace) -> None:
    settings = Settings.from_env(Path.cwd())
    log = ApplicationLog(settings.db_file)
    try:
        synced = browser_sync_sent(settings, log)
    finally:
        log.close()
    print(f"Synced sent responses: {synced}")


def command_browser_apply(_: argparse.Namespace) -> None:
    settings = Settings.from_env(Path.cwd())
    log = ApplicationLog(settings.db_file)
    try:
        stats = browser_apply(settings, log)
    finally:
        log.close()
    print(
        "Browser summary: "
        f"seen={stats.seen}, new={stats.new}, dry_run={stats.dry_run}, "
        f"clicked={stats.clicked}, manual_required={stats.manual_required}, "
        f"filtered_before_open={stats.filtered_before_open}, "
        f"pages_opened={stats.pages_opened}, likely_apply={stats.likely_apply}, "
        f"external_ats_skip={stats.external_ats_skip}, "
        f"already_applied={stats.already_applied}, "
        f"unconfirmed_click={stats.unconfirmed_click}, "
        f"frequent_response_warning={stats.frequent_response_warning}, "
        f"keyword_detector_llm_calls={stats.keyword_detector_llm_calls}, "
        f"keyword_detector_input_chars={stats.keyword_detector_input_chars}, "
        f"keyword_detector_input_tokens={stats.keyword_detector_input_tokens}, "
        f"skipped={stats.skipped}, errors={stats.errors}"
    )


def command_browser_backfill_search(_: argparse.Namespace) -> None:
    settings = Settings.from_env(Path.cwd())
    log = ApplicationLog(settings.db_file)
    try:
        browser_backfill_search(settings, log)
    finally:
        log.close()


def command_browser_backfill_apply(_: argparse.Namespace) -> None:
    settings = Settings.from_env(Path.cwd())
    log = ApplicationLog(settings.db_file)
    try:
        browser_apply_backfill_queue(settings, log)
    finally:
        log.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="hh-auto-apply")
    subparsers = parser.add_subparsers(required=True)

    doctor = subparsers.add_parser("doctor", help="Check local setup and show the next required step")
    doctor.set_defaults(func=command_doctor)

    auth = subparsers.add_parser("auth", help="Run HH OAuth flow and save tokens locally")
    auth.set_defaults(func=command_auth)

    resumes = subparsers.add_parser("resumes", help="List own HH resumes")
    resumes.set_defaults(func=command_resumes)

    apply_cmd = subparsers.add_parser("apply", help="Apply to new filtered vacancies")
    apply_cmd.set_defaults(func=command_apply)

    browser_login_cmd = subparsers.add_parser("browser-login", help="Log in to HH in a persistent browser profile")
    browser_login_cmd.set_defaults(func=command_browser_login)

    browser_sync_cmd = subparsers.add_parser("browser-sync", help="Sync HH sent responses into the local log")
    browser_sync_cmd.set_defaults(func=command_browser_sync)

    browser_apply_cmd = subparsers.add_parser("browser-apply", help="Browser fallback while HH API app is moderated")
    browser_apply_cmd.set_defaults(func=command_browser_apply)

    browser_backfill_cmd = subparsers.add_parser("browser-backfill-search", help="Search-only browser backfill queue builder")
    browser_backfill_cmd.set_defaults(func=command_browser_backfill_search)

    browser_backfill_apply_cmd = subparsers.add_parser("browser-backfill-apply", help="Apply a bounded batch from the backfill queue")
    browser_backfill_apply_cmd.set_defaults(func=command_browser_backfill_apply)

    log = subparsers.add_parser("log", help="Show recent application log")
    log.add_argument("--limit", type=int, default=20)
    log.set_defaults(func=command_log)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

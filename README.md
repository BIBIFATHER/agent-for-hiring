# Agent for Hiring

Minimal local agent for Anton: fetch new HH vacancies by configured filters, deduplicate by `vacancy_id + resume_id`, choose the matching resume, apply through official HH API when available or Playwright browser fallback while the HH app is moderated, and log every result.

No n8n or Supabase is used in v1. LLM generation is intentionally optional: local rules filter obvious trash first and the static Short Pitch fallback keeps mass applications moving when the LLM daily limit is exhausted.

## Checked HH API surface, August 2026

Official sources checked:

- HeadHunter API portal: `https://dev.hh.ru`
- Official docs repository: `https://github.com/hhru/api`
- Official OpenAPI Redoc: `https://api.hh.ru/openapi/redoc`

Relevant API support:

- Vacancy search exists: `GET https://api.hh.ru/vacancies`.
- Vacancy details include fields useful for safe automation: `response_url`, `apply_alternate_url`, `response_letter_required`, `has_test`.
- OAuth is required for applicant actions. Access token is sent as `Authorization: Bearer ACCESS_TOKEN`.
- Own resumes are available through `GET /resumes/mine`.
- Applicant response exists: `POST /negotiations` with `vacancy_id`, `resume_id`, optional `message`.
- HH documents negotiation errors including `already_applied`, `test_required`, `limit_exceeded`, empty/too-long message, archived vacancy, hidden/deleted resume, and other 400/403/409/429 cases.

Known constraints handled in this MVP:

- Vacancies with `has_test=true` are skipped because official docs say API response to such vacancies is unavailable.
- Approved vacancies get a local Short Pitch from `cover_letter_rules.json` unless `HH_COVER_LETTER` overrides it.
- Vacancies without `response_url` are skipped by default. Their `apply_alternate_url` is logged for manual handling.
- `429 Too Many Requests` is retried once if `Retry-After` is provided.
- Per-run application count and delay between requests are configurable.

## Setup

```bash
cd /Users/anton/Documents/Codex/agent-for-hiring
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Create an HH app at `https://dev.hh.ru`, set redirect URI to:

```text
http://127.0.0.1:8765/callback
```

Then fill `.env`:

- `HH_CLIENT_ID`
- `HH_CLIENT_SECRET`
- `HH_USER_AGENT` with a real contact
- search filters
- later, `HH_RESUME_ID`

## Authorize

```bash
hh-auto-apply auth
```

The CLI opens the HH OAuth page and stores tokens in `data/tokens.json` with file mode `0600`.

## Pick Resume

```bash
hh-auto-apply resumes
```

Copy the needed resume id into `.env` as `HH_RESUME_ID`.

## Dry Run

Keep:

```text
HH_DRY_RUN=true
```

Run:

```bash
hh-auto-apply apply
hh-auto-apply log
```

Dry-run records candidates as `dry_run`, `skipped`, or `error` without sending applications.

## Real Run

After reviewing the dry-run log:

```text
HH_DRY_RUN=false
HH_MAX_APPLICATIONS_PER_RUN=15
HH_REQUEST_DELAY_SECONDS=1.5
```

Then:

```bash
hh-auto-apply apply
```

## Search Filters

The MVP uses HH search params from env. Common values:

- `HH_SEARCH_TEXT`: HH search query, e.g. `коммерческий директор OR head of sales OR CCO OR директор по продажам`
- `HH_SEARCH_AREA`: leave empty for remote/hybrid mass mode; use `1` for Moscow only if city restriction is needed
- `HH_SEARCH_SCHEDULE`: e.g. `remote`, `fullDay`, `flexible`
- `HH_SEARCH_EMPLOYMENT`: e.g. `full`, `part`
- `HH_SEARCH_EXPERIENCE`: e.g. `between3And6`, `moreThan6`
- `HH_SEARCH_PERIOD`: recent days
- `HH_SEARCH_PER_PAGE`, `HH_SEARCH_PAGES`: pagination limits
- `HH_SEARCH_PROFILES_FILE`: optional JSON file with per-resume filters. If present, browser mode scans each profile separately and deduplicates by `vacancy_id + resume_id`.

## Data

Local files:

- `data/tokens.json`: OAuth tokens, ignored by git
- `data/hh_auto_apply.sqlite3`: deduplication and result log, ignored by git

## Browser Automation

The official API path is preferred. If the HH developer app is still under moderation, use the browser fallback.

Install browser support:

```bash
python -m pip install -e .
python -m playwright install chromium
```

Log in once:

```bash
hh-auto-apply browser-login
```

Run dry-run scan:

```bash
hh-auto-apply browser-apply
hh-auto-apply log
```

Only after checking the log, switch real clicking on:

```text
HH_DRY_RUN=false
```

Then:

```bash
hh-auto-apply browser-apply
```

Browser fallback uses the same search filters, local SQLite deduplication, per-run limit, and delay settings.
For browser mode, API-style env filters are converted to HH web filters, for example `HH_SEARCH_PERIOD=1` becomes `search_period=1`.
When `search_profiles.json` is used, `HH_MAX_APPLICATIONS_PER_RUN` is applied to the whole run across all profiles/resumes.
The same `vacancy_id` is not submitted again through another resume once it has `browser_clicked`, `applied`, or `already_applied` in the local log.
Use `work_format: ["REMOTE", "HYBRID"]` in a profile to exclude office-only roles.
For mass remote/hybrid mode, omit `area` so HH does not restrict remote vacancies by city.
Use `employment_form: ["FULL", "PROJECT"]` to include both permanent and project work.
If a response flow shows a task, test, or mandatory employer questions, browser mode records `manual_required`, adds the vacancy to favorites when possible, and does not submit guessed answers.
Before clicking the response button in real mode, browser automation opens the vacancy page, scrolls it, waits a random 3-7 seconds, and pauses for manual captcha solving if HH shows captcha/Cloudflare/Qrator checks.
Cover letters are typed with small per-character delays and explicit `input/change/blur` events so HH's frontend enables the submit button reliably.

Special browser statuses:

- `external_ats_skip`: HH redirected the response flow to an external ATS domain; the external tab is closed when possible.
- `already_applied`: HH showed that the response already exists.
- `frequent_response_warning`: HH showed a frequent-response warning; the modal is closed and the run continues.
- `manual_required`: task, test, or mandatory employer questions; the vacancy is added to favorites when possible.

Every result is stored in SQLite and mirrored into `data/history.json` for easy pilot review.

## Cover Letter Rules

`cover_letter_rules.json` has the local prefilter that runs before any future LLM call:

- `title_must_contain`: commercial/sales leadership keywords required in the vacancy title.
- `stop_words`: hard skip phrases checked across title, employer, snippet, and description.
- `max_age_days`: skip old vacancies when publication date is available.
- `daily_llm_limit`: future LLM budget, currently set to 30.
- `segment_templates`: local Plus-safe cover letters selected by vacancy-title keywords.
- `default_template`: fallback local template when no segment matches.
- `short_pitch`: legacy fallback text for approved vacancies when segment templates are absent.

Local template selection:

```text
vacancy title lower-case -> first matching segment_templates.keywords -> segment text -> default_template
```

## AI Personalization

The browser flow can call OpenAI right before inserting the cover letter:

```text
local prefilter PASS -> daily_llm_limit check -> OpenAI JSON decision -> insert custom letter or fallback Short Pitch
```

Enable it only after the browser flow is stable:

```text
HH_LLM_ENABLED=true
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.4-mini
HH_LLM_TIMEOUT_SECONDS=6.0
```

Fallback behavior:

- Missing key, disabled LLM, exhausted daily limit, timeout, API error, or invalid JSON: use the local segment template selected from `segment_templates`.
- LLM returns `SKIP`: close the response modal and log `skipped`.
- LLM returns `APPROVED`: insert the custom cover letter.
- The daily limit is counted by actual API calls, not only approved letters, so repeated model-side skips cannot burn unlimited tokens.

Mass mode defaults:

- `HH_SEARCH_PAGES=10`: scan up to 500 vacancies per profile before dedupe.
- `HH_MAX_APPLICATIONS_PER_RUN=15`: up to 15 actions per resume/profile per run in the regular headless pass.
- `HH_REQUEST_DELAY_SECONDS=1.5`: keep browser actions paced.

Current rollout mode:

- `search_profiles.json`: first full pass, last 7 days.
- `search_profiles_hh_recommended.json`: first full pass using HH's own recommended-vacancy links under each resume, also 7 days.
- `search_profiles_recent.json`: regular mode after the first pass, last 3 days.

Switch to regular mode after the full pass by setting:

```text
HH_SEARCH_PROFILES_FILE=search_profiles_recent.json
```

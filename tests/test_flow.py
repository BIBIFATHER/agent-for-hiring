from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from hh_auto_apply.apply import skip_reason
from hh_auto_apply.cover_letter import DEFAULT_RULES, build_cover_letter, decision_json, evaluate_vacancy, select_segment_template
from hh_auto_apply.browser_apply import browser_search_params, diagnose_click_failure, is_external_ats_url, negotiation_item_to_vacancy, response_flow_status, submit_cover_letter_if_present
from hh_auto_apply.llm import choose_cover_letter
from hh_auto_apply.config import Settings
from hh_auto_apply.storage import ApplicationLog


class FlowTests(unittest.TestCase):
    def settings(self) -> Settings:
        return Settings(
            client_id="client",
            client_secret="secret",
            redirect_uri="http://127.0.0.1:8765/callback",
            user_agent="test",
            resume_id="resume",
            cover_letter="",
            dry_run=True,
            max_applications_per_run=20,
            request_delay_seconds=0,
            skip_if_letter_required=True,
            skip_if_no_response_url=True,
            browser_profile_dir=Path("browser-profile"),
            browser_headless=False,
            browser_search_url="",
            search_profiles_file=Path("search_profiles.json"),
            cover_letter_rules_file=Path("cover_letter_rules.json"),
            llm_enabled=False,
            openai_api_key="",
            openai_model="gpt-5.4-mini",
            llm_timeout_seconds=6.0,
            token_file=Path("tokens.json"),
            db_file=Path("log.sqlite3"),
            search_params={"text": "python"},
        )

    def test_dry_run_does_not_block_future_real_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ApplicationLog(Path(tmp) / "log.sqlite3")
            vacancy = {"id": "1", "name": "Role", "employer": {"name": "Company"}}
            log.record(vacancy, "resume", "dry_run")
            self.assertFalse(log.was_processed("1", "resume"))
            log.record(vacancy, "resume", "applied")
            self.assertTrue(log.was_processed("1", "resume"))
            self.assertTrue(log.was_vacancy_processed("1"))
            self.assertTrue((Path(tmp) / "history.json").exists())
            log.close()

    def test_unconfirmed_browser_click_does_not_count_as_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ApplicationLog(Path(tmp) / "log.sqlite3")
            vacancy = {"id": "1", "name": "Role", "employer": {"name": "Company"}}
            log.record(vacancy, "resume-a", "unconfirmed_click")
            self.assertFalse(log.was_processed("1", "resume-a"))
            self.assertFalse(log.was_vacancy_processed("1"))
            log.close()

    def test_log_stores_copy_ready_vacancy_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ApplicationLog(Path(tmp) / "log.sqlite3")
            vacancy = {
                "id": "136239297",
                "name": "Head of Sales",
                "employer": {"name": "Company"},
                "alternate_url": "https://hh.ru/vacancy/136239297?hhtmFrom=vacancy_search_list",
            }
            log.record(vacancy, "resume", "dry_run")
            history = json.loads((Path(tmp) / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(
                history[0]["url"],
                "[https://hh.ru/vacancy/](https://hh.ru/vacancy/)136239297",
            )
            log.close()

    def test_response_confirmation_counts_as_applied(self) -> None:
        class Body:
            def inner_text(self, timeout: int) -> str:
                return "Отклик отправлен. Работодатель скоро получит ваше резюме."

        class Page:
            def locator(self, selector: str) -> Body:
                if selector != "body":
                    raise AssertionError(selector)
                return Body()

        self.assertEqual(response_flow_status(Page()), "applied")

    def test_click_failure_is_classified_by_page_text(self) -> None:
        class Locator:
            def __init__(self, text: str) -> None:
                self.text = text

            def count(self) -> int:
                return 1 if self.text else 0

            def inner_text(self, timeout: int) -> str:
                return self.text

        class Page:
            def locator(self, selector: str) -> Locator:
                if selector == "body":
                    return Locator("Вы уже откликались на эту вакансию")
                return Locator("")

        self.assertEqual(diagnose_click_failure(Page()), "click_failed_already_applied")

    def test_already_applied_warning_detects_hh_response_confirmation(self) -> None:
        class Body:
            def inner_text(self, timeout: int) -> str:
                return "Вы откликнулись. Отклик другим резюме."

        class Page:
            def locator(self, selector: str) -> Body:
                if selector != "body":
                    raise AssertionError(selector)
                return Body()

        self.assertEqual(response_flow_status(Page()), "already_applied")

    def test_negotiation_item_to_vacancy_uses_today_cards_only(self) -> None:
        today_item = {
            "href": "/vacancy/135520329?from=negotiation",
            "title": "Руководитель отдела продаж",
            "employer": "Company",
            "text": "Руководитель отдела продаж | Company | сегодня",
        }
        old_item = {
            "href": "/vacancy/135520330?from=negotiation",
            "title": "Руководитель отдела продаж",
            "employer": "Company",
            "text": "Руководитель отдела продаж | Company | 30 июня 2021",
        }
        self.assertEqual(negotiation_item_to_vacancy(today_item)["id"], "135520329")
        self.assertIsNone(negotiation_item_to_vacancy(old_item))

    def test_cover_letter_variants_depend_on_vacancy_id(self) -> None:
        base = {
            "name": "Коммерческий директор",
            "employer": {"name": "Company"},
        }
        first = build_cover_letter({**DEFAULT_RULES}, {**base, "id": "1"})
        second = build_cover_letter({**DEFAULT_RULES}, {**base, "id": "2"})
        self.assertNotEqual(first, second)

    def test_cover_letter_form_without_submit_button_needs_manual_submit(self) -> None:
        class TextLocator:
            @property
            def first(self):
                return self

            def count(self) -> int:
                return 1

            def is_visible(self, timeout: int) -> bool:
                return True

            def click(self, timeout: int) -> None:
                return None

            def fill(self, value: str) -> None:
                return None

            def type(self, value: str, delay: int) -> None:
                return None

            def dispatch_event(self, name: str) -> None:
                return None

            def blur(self) -> None:
                return None

        class ButtonLocator:
            def is_visible(self, timeout: int) -> bool:
                return False

        class Page:
            def get_by_text(self, text: str, exact: bool = False) -> TextLocator:
                return TextLocator()

            def locator(self, selector: str) -> TextLocator:
                return TextLocator()

            def get_by_role(self, role: str, name) -> ButtonLocator:
                return ButtonLocator()

            def wait_for_timeout(self, ms: int) -> None:
                return None

        self.assertEqual(submit_cover_letter_if_present(Page(), "Cover letter"), "needs_manual_submit")

    def test_cover_letter_form_with_delayed_submit_button_still_submits(self) -> None:
        class BodyLocator:
            def __init__(self, page: "Page") -> None:
                self.page = page

            def inner_text(self, timeout: int) -> str:
                if self.page.clicked and self.page.elapsed_ms >= 1500:
                    return "Вы откликнулись. Отклик отправлен."
                return "Ответьте на вопросы работодателя"

        class ButtonLocator:
            def __init__(self, page: "Page") -> None:
                self.page = page

            @property
            def first(self):
                return self

            def count(self) -> int:
                return 1

            def is_visible(self, timeout: int) -> bool:
                return self.page.elapsed_ms >= 900

            def scroll_into_view_if_needed(self, timeout: int) -> None:
                return None

            def click(self, timeout: int) -> None:
                self.page.clicked = True

        class Page:
            def __init__(self) -> None:
                self.elapsed_ms = 0
                self.clicked = False

            def get_by_text(self, text: str, exact: bool = False) -> TextLocator:
                return TextLocator()

            def locator(self, selector: str):
                if selector == "body":
                    return BodyLocator(self)
                return ButtonLocator(self)

            def get_by_role(self, role: str, name) -> ButtonLocator:
                return ButtonLocator(self)

            def wait_for_timeout(self, ms: int) -> None:
                self.elapsed_ms += ms

            def wait_for_load_state(self, state: str, timeout: int) -> None:
                return None

            @property
            def url(self) -> str:
                return "https://hh.ru/applicant/vacancy_response?vacancyId=1"

        self.assertEqual(submit_cover_letter_if_present(Page(), ""), "submitted")

    def test_already_applied_response_page_short_circuits_without_cover_letter(self) -> None:
        class BodyLocator:
            def inner_text(self, timeout: int) -> str:
                return "Вы откликнулись. Резюме доставлено."

        class Page:
            def locator(self, selector: str) -> BodyLocator:
                if selector != "body":
                    raise AssertionError(selector)
                return BodyLocator()

        self.assertEqual(submit_cover_letter_if_present(Page(), ""), "submitted")

    def test_cover_letter_generation_approves_target_role(self) -> None:
        vacancy = {
            "id": "1",
            "name": "Коммерческий директор",
            "employer": {"name": "Company"},
            "response_letter_required": True,
        }
        decision = evaluate_vacancy(vacancy, None)
        reason = skip_reason(
            {"id": "1", "response_url": "https://api.hh.ru/negotiations", "response_letter_required": True, "name": "Коммерческий директор", "employer": {"name": "Company"}},
            decision,
            self.settings(),
        )
        self.assertEqual(reason, "")
        self.assertEqual(decision.status, "APPROVED")
        self.assertIsNotNone(decision.cover_letter)
        self.assertGreaterEqual(len(decision.cover_letter.split()), 50)
        parsed = decision_json(vacancy)
        self.assertIn('"status": "APPROVED"', parsed)

    def test_segment_template_selection_uses_first_matching_title_keyword(self) -> None:
        letter = select_segment_template(DEFAULT_RULES, "Руководитель отдела продаж B2B IT")
        self.assertIn("управлении B2B-продажами", letter)

    def test_segment_template_default_when_no_segment_matches(self) -> None:
        letter = select_segment_template(DEFAULT_RULES, "Директор направления")
        self.assertIn("Коммерческий руководитель", letter)

    def test_skip_non_target_role(self) -> None:
        decision = evaluate_vacancy({"name": "Менеджер проекта", "employer": {"name": "Company"}}, None)
        self.assertEqual(decision.status, "SKIP")

    def test_title_must_match_before_cover_letter(self) -> None:
        vacancy = {
            "name": "Менеджер B2B продаж",
            "description": "Нужен коммерческий директор с опытом продаж",
            "employer": {"name": "Company"},
        }
        decision = evaluate_vacancy(vacancy, DEFAULT_RULES)
        self.assertEqual(decision.status, "SKIP")
        self.assertIn("title keyword", decision.reason)

    def test_stop_word_blocks_before_cover_letter(self) -> None:
        vacancy = {
            "name": "Директор по продажам",
            "description": "Доход только процент, холодные звонки",
            "employer": {"name": "Company"},
        }
        decision = evaluate_vacancy(vacancy, DEFAULT_RULES)
        self.assertEqual(decision.status, "SKIP")
        self.assertIn("stop word", decision.reason)

    def test_title_stop_word_blocks_linear_sales_roles(self) -> None:
        decision = evaluate_vacancy({"name": "Старший менеджер по продажам KAM", "employer": {"name": "Company"}}, DEFAULT_RULES)
        self.assertEqual(decision.status, "SKIP")
        self.assertIn("title stop word", decision.reason)

    def test_manager_word_in_description_does_not_block_director_title(self) -> None:
        vacancy = {
            "name": "Директор по продажам",
            "description": "В подчинении менеджеры по продажам и специалисты поддержки.",
            "employer": {"name": "Company"},
        }
        decision = evaluate_vacancy(vacancy, DEFAULT_RULES)
        self.assertEqual(decision.status, "APPROVED")

    def test_non_commercial_director_titles_are_skipped(self) -> None:
        for title in [
            "Операционный директор (COO)",
            "Директор по маркетингу",
            "CPO / Руководитель продукта",
            "Директор по инвестициям",
            "Финансовый директор CFO",
            "Технический директор CTO",
        ]:
            with self.subTest(title=title):
                decision = evaluate_vacancy({"name": title, "employer": {"name": "Company"}}, DEFAULT_RULES)
                self.assertEqual(decision.status, "SKIP")

    def test_commercial_director_titles_are_approved(self) -> None:
        for title in [
            "Директор по продажам B2B",
            "Директор по развитию бизнеса",
            "Директор по коммерции",
            "Коммерческий директор",
            "Руководитель отдела продаж",
            "Head of Sales",
            "Директор e-commerce",
        ]:
            with self.subTest(title=title):
                decision = evaluate_vacancy({"name": title, "employer": {"name": "Company"}}, DEFAULT_RULES)
                self.assertEqual(decision.status, "APPROVED")

    def test_skip_test_required(self) -> None:
        reason = skip_reason(
            {"id": "1", "response_url": "https://api.hh.ru/negotiations", "has_test": True},
            evaluate_vacancy({"name": "Коммерческий директор", "employer": {"name": "Company"}}, None),
            self.settings(),
        )
        self.assertIn("requires HH test", reason)

    def test_browser_work_format_supports_remote_and_hybrid(self) -> None:
        params = browser_search_params({
            "work_format": ["REMOTE", "HYBRID"],
            "employment_form": ["FULL", "PROJECT"],
            "period": "7",
        })
        self.assertEqual(params["work_format"], ["REMOTE", "HYBRID"])
        self.assertEqual(params["employment_form"], ["FULL", "PROJECT"])
        self.assertEqual(params["search_period"], "7")

    def test_external_ats_detection(self) -> None:
        self.assertFalse(is_external_ats_url("https://hh.ru/vacancy/123"))
        self.assertFalse(is_external_ats_url("https://spb.hh.ru/vacancy/123"))
        self.assertTrue(is_external_ats_url("https://boards.greenhouse.io/company/jobs/123"))
        self.assertTrue(is_external_ats_url("https://company.workdayjobs.com/job/123"))

    def test_llm_disabled_falls_back_to_short_pitch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ApplicationLog(Path(tmp) / "log.sqlite3")
            decision = choose_cover_letter(
                self.settings(),
                log,
                {"name": "Коммерческий директор", "employer": {"name": "Company"}},
                DEFAULT_RULES,
            )
            self.assertEqual(decision.status, "APPROVED")
            self.assertEqual(decision.source, "local_template")
            self.assertIn("P&L", decision.cover_letter or "")
            self.assertEqual(log.llm_calls_today(), 0)
            log.close()

    def test_llm_limit_falls_back_without_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = ApplicationLog(Path(tmp) / "log.sqlite3")
            settings = self.settings()
            settings = Settings(
                **{
                    **settings.__dict__,
                    "llm_enabled": True,
                    "openai_api_key": "test",
                }
            )
            rules = {**DEFAULT_RULES, "daily_llm_limit": 1}
            log.record_llm_call("APPROVED")
            decision = choose_cover_letter(settings, log, {"name": "Коммерческий директор"}, rules)
            self.assertEqual(decision.status, "APPROVED")
            self.assertEqual(decision.source, "local_template")
            self.assertEqual(decision.reason, "daily_llm_limit_exhausted")
            self.assertEqual(log.llm_calls_today(), 1)
            log.close()


if __name__ == "__main__":
    unittest.main()

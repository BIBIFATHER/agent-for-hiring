from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib import error, request

from .cover_letter import build_cover_letter, has_banned_phrase


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

SYSTEM_PROMPT = """Ты — коммерческий руководитель уровня Head of Sales / CCO.
Твоя задача — по вакансии вернуть либо SKIP, либо APPROVED с коротким, естественным сопроводительным письмом.

Правила стиля:
- Пиши как живой человек, а не как шаблон.
- Не повторяй одинаковое вступление в каждом ответе.
- Используй только факты из входа и профиля кандидата.
- Длина письма: 70-110 слов, максимум 3 коротких абзаца.
- Без канцелярита, без рекламных штампов, без фраз вроде "с большим интересом", "идеально подхожу", "кроме того", "в заключение", "приветствую".

ФОРМАТ ОТВЕТА (ТОЛЬКО VALID JSON):
{
  "status": "APPROVED" | "SKIP",
  "reason": "краткое объяснение причины SKIP или совпадения",
  "cover_letter": "текст письма (null если SKIP)"
}"""


@dataclass(frozen=True)
class LLMDecision:
    status: str
    cover_letter: str | None
    reason: str
    source: str


def choose_cover_letter(settings: Any, log: Any, vacancy: dict[str, Any], rules: dict[str, Any]) -> LLMDecision:
    local_template = build_cover_letter(rules, vacancy)
    limit = int(rules.get("daily_llm_limit", 0) or 0)
    if not settings.llm_enabled:
        return LLMDecision("APPROVED", local_template, "llm_disabled", "local_template")
    if not settings.openai_api_key:
        return LLMDecision("APPROVED", local_template, "openai_api_key_missing", "local_template")
    if limit > 0 and log.llm_calls_today() >= limit:
        return LLMDecision("APPROVED", local_template, "daily_llm_limit_exhausted", "local_template")

    try:
        decision = call_openai(settings, vacancy, rules)
        log.record_llm_call(decision.status)
    except TimeoutError:
        log.record_llm_call("error")
        return LLMDecision("APPROVED", local_template, "llm_timeout", "local_template")
    except Exception as exc:
        log.record_llm_call("error")
        return LLMDecision("APPROVED", local_template, f"llm_error: {type(exc).__name__}", "local_template")

    if decision.status == "SKIP":
        return decision
    if not decision.cover_letter:
        return LLMDecision("APPROVED", local_template, "llm_empty_cover_letter", "local_template")
    if has_banned_phrase(decision.cover_letter, rules.get("banned_phrases", [])):
        return LLMDecision("APPROVED", local_template, "llm_banned_phrase_fallback", "local_template")
    return decision


def call_openai(settings: Any, vacancy: dict[str, Any], rules: dict[str, Any]) -> LLMDecision:
    payload = {
        "model": settings.openai_model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": build_user_prompt(vacancy, rules)}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "hh_cover_letter_decision",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {"type": "string", "enum": ["APPROVED", "SKIP"]},
                        "reason": {"type": "string"},
                        "cover_letter": {"type": ["string", "null"]},
                    },
                    "required": ["status", "reason", "cover_letter"],
                },
                "strict": True,
            }
        },
        "max_output_tokens": 500,
    }
    req = request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=settings.llm_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except TimeoutError:
        raise
    except error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError from exc
        raise
    data = json.loads(raw)
    parsed = json.loads(extract_output_text(data))
    status = str(parsed.get("status", "")).upper()
    if status not in {"APPROVED", "SKIP"}:
        raise ValueError("invalid LLM status")
    cover_letter = parsed.get("cover_letter")
    if cover_letter is not None:
        cover_letter = str(cover_letter).strip()
    return LLMDecision(status, cover_letter, str(parsed.get("reason", "")).strip(), "llm")


def build_user_prompt(vacancy: dict[str, Any], rules: dict[str, Any]) -> str:
    employer = vacancy.get("employer") or {}
    employer_name = employer.get("name", "") if isinstance(employer, dict) else str(employer)
    candidate_context = rules.get("candidate_context") or {}
    ai_practice = candidate_context.get("ai_practice") or []
    results_2025 = candidate_context.get("results_2025") or {}
    candidate_lines = [
        f"- Опыт: {candidate_context.get('experience_years', '15+')}",
        f"- Сфера: {candidate_context.get('domain', 'B2B-коммерция')}",
        f"- Масштаб: {candidate_context.get('pnl', 'до 1 млрд руб.')}, команда {candidate_context.get('team_size', '12 человек')}",
        f"- Результаты 2025: выручка {results_2025.get('sales_growth', '+36%')}, база {results_2025.get('client_base_growth', '+25%')}",
        "- Практика AI: " + "; ".join(str(item) for item in ai_practice) if ai_practice else "- Практика AI: не указывать",
    ]
    parts = [
        f"Название вакансии: {vacancy.get('name', '')}",
        f"Компания: {employer_name}",
        f"URL: {vacancy.get('alternate_url', '')}",
        "Факты о кандидате:",
        *candidate_lines,
        "Стиль: коротко, без штампов, с разным порядком фраз. Не начинай каждый ответ одинаково.",
        "Текст вакансии:",
        str(vacancy.get("page_text") or vacancy.get("description") or vacancy.get("snippet") or "")[:8000],
    ]
    return "\n".join(parts)


def extract_output_text(response_data: dict[str, Any]) -> str:
    if text := response_data.get("output_text"):
        return str(text)
    chunks: list[str] = []
    for item in response_data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(str(content["text"]))
    text = "".join(chunks).strip()
    if not text:
        raise ValueError("empty LLM response text")
    return text

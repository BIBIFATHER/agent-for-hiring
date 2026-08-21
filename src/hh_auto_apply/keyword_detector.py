from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib import error, request


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

TRIGGER_PATTERNS = [
    r"кодовое\s+слов[оа]",
    r"укаж(?:ите|и)\s+(?:слово|фразу)",
    r"напиш(?:ите|и)\s+в\s+сопроводительн",
    r"начн(?:ите|и)\s+(?:письмо|отклик)",
    r"в\s+конце\s+(?:письма|отклика)\s+укаж",
    r"чтобы\s+(?:понять|убедиться)[^.\n]{0,120}дочитал",
    r"без\s+этой\s+фразы\s+отклик\s+не\s+рассматриваем",
    r"в\s+сопроводительном\s+(?:письме\s+)?(?:укаж|напиш)",
]


@dataclass(frozen=True)
class KeywordInstruction:
    has_instruction: bool
    keyword: str
    fragment: str
    reason: str
    llm_calls: int = 0
    input_chars: int = 0
    input_tokens: int = 0


def detect_cover_letter_keyword_instruction(text: str, settings: Any | None = None) -> KeywordInstruction:
    source = str(text or "")
    match = find_trigger(source)
    if not match:
        return KeywordInstruction(False, "", "", "no_trigger")

    fragment = source[max(0, match.start() - 700): min(len(source), match.end() + 700)]
    keyword = extract_keyword_deterministic(fragment)
    if keyword and keyword in fragment:
        return KeywordInstruction(True, keyword, fragment, "deterministic", 0, 0, 0)

    if settings is not None and getattr(settings, "llm_enabled", False) and getattr(settings, "openai_api_key", ""):
        llm_keyword = extract_keyword_with_llm(settings, fragment)
        if llm_keyword and llm_keyword in fragment:
            return KeywordInstruction(True, llm_keyword, fragment, "llm", 1, len(fragment), estimate_tokens(fragment))
        return KeywordInstruction(True, "", fragment, "llm_uncertain", 1, len(fragment), estimate_tokens(fragment))

    return KeywordInstruction(True, "", fragment, "extraction_uncertain", 0, 0, 0)


def find_trigger(text: str) -> re.Match[str] | None:
    for pattern in TRIGGER_PATTERNS:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match
    return None


def extract_keyword_deterministic(fragment: str) -> str:
    quoted = re.search(r"[«\"']([^»\"'\n]{2,80})[»\"']", fragment)
    if quoted:
        return clean_keyword(quoted.group(1))

    patterns = [
        r"(?:кодовое\s+слов[оа]|слово|фраз[уаы])\s*[:\-]\s*([^\n.]{2,80})",
        r"(?:укаж(?:ите|и)|напиш(?:ите|и))\s+(?:слово|фразу)\s+([^\n.]{2,80})",
        r"(?:начн(?:ите|и)\s+(?:письмо|отклик)\s+(?:со\s+слова|с\s+фразы))\s+([^\n.]{2,80})",
        r"(?:в\s+конце\s+(?:письма|отклика)\s+укаж(?:ите|и))\s+([^\n.]{2,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, fragment, flags=re.IGNORECASE)
        if match:
            keyword = clean_keyword(match.group(1))
            if keyword:
                return keyword
    return ""


def clean_keyword(value: str) -> str:
    keyword = str(value or "").strip()
    keyword = re.sub(r"\s+", " ", keyword)
    keyword = keyword.strip(" .,:;!?()[]{}«»\"'")
    if not keyword:
        return ""
    if len(keyword) > 80:
        return ""
    return keyword


def ensure_cover_letter_contains_keyword(cover_letter: str, instruction: KeywordInstruction) -> str:
    keyword = instruction.keyword.strip()
    if not keyword or keyword in cover_letter:
        return cover_letter
    fragment_lower = instruction.fragment.lower()
    if "начните" in fragment_lower or "начни" in fragment_lower:
        return f"{keyword}\n\n{cover_letter}".strip()
    return f"{cover_letter.rstrip()}\n\n{keyword}".strip()


def extract_keyword_with_llm(settings: Any, fragment: str) -> str:
    payload = {
        "model": settings.openai_model,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Extract only the exact code word or phrase that the applicant must include "
                            "in a cover letter. Return JSON. If unsure, keyword must be null."
                        ),
                    }
                ],
            },
            {"role": "user", "content": [{"type": "input_text", "text": fragment[:1600]}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "cover_letter_keyword",
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"keyword": {"type": ["string", "null"]}},
                    "required": ["keyword"],
                },
                "strict": True,
            }
        },
        "max_output_tokens": 80,
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
            data = json.loads(response.read().decode("utf-8"))
    except (TimeoutError, error.URLError, ValueError, json.JSONDecodeError):
        return ""
    try:
        parsed = json.loads(extract_output_text(data))
    except (ValueError, json.JSONDecodeError):
        return ""
    return clean_keyword(str(parsed.get("keyword") or ""))


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


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0

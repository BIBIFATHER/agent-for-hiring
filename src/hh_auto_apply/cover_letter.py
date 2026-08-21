from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_RULES = {
    "title_must_contain": [
        "коммерческ",
        "директор по продажам",
        "директор по развитию",
        "директор по коммерции",
        "руководитель отдела продаж",
        "руководитель по продажам",
        "руководитель продаж",
        "роп",
        "руководитель коммерческ",
        "head of sales",
        "cco",
        "комдир",
        "исполнительн",
        "директор филиала",
        "директор направления",
        "директор бизнес-направления",
        "business development",
        "revenue",
        "growth",
        "директор e-commerce",
        "директор по маркетплейсам",
        "директор b2b",
    ],
    "target_roles": [
        "коммерческий директор",
        "head of sales",
        "cco",
        "директор по продажам",
        "руководитель коммерческого направления",
        "руководитель отдела продаж",
        "руководитель продаж",
        "директор по развитию",
        "операционный директор",
        "исполнительный директор",
        "директор филиала",
        "директор бизнес-направления",
    ],
    "stop_words": [
        "без оклада",
        "только процент",
        "риелтор",
        "агент",
        "стажер",
        "junior",
        "страховой",
        "оператор",
        "холодные звонки",
        "младший менеджер",
        "страховой агент",
    ],
    "title_stop_words": [
        "cmo",
        "cpo",
        "инвестици",
        "инвестициям",
        "cfo",
        "персонал",
        "chro",
        "техническ",
        "cto",
        "kam",
        "key account",
        "кей аккаунт",
        "менеджер по продажам",
        "старший менеджер",
        "специалист",
        "франшиз",
        "ассистент",
        "помощник",
        "риелтор",
        "агент",
        "стажер",
        "junior",
        "страховой",
        "оператор",
    ],
    "soft_title_stop_words": [
        "операционн",
        "coo",
        "маркетинг",
        "marketing",
        "продукт",
        "product",
        "финанс",
        "finance",
        "hr",
    ],
    "semantic_management_signals": [
        "управление командой",
        "руководство командой",
        "управление отделом",
        "руководство отделом",
        "управление сотрудниками",
        "управление руководителями",
        "руководитель направления",
        "директор направления",
        "руководитель подразделения",
        "директор подразделения",
        "руководитель бизнес-направления",
        "директор бизнес-направления",
        "директор филиала",
        "операционный директор",
        "исполнительный директор",
        "coo",
        "team management",
        "head",
        "director",
        "business unit head",
        "division head",
    ],
    "semantic_commercial_signal_groups": {
        "revenue": [
            "выручка",
            "рост выручки",
            "ответственность за выручку",
            "план по выручке",
            "выполнение плана продаж",
            "рост продаж",
            "коммерческий результат",
            "revenue",
            "revenue growth",
            "sales target",
            "commercial result",
        ],
        "pnl_profitability": [
            "p&l",
            "прибыль",
            "ответственность за прибыль",
            "финансовый результат",
            "прибыльность",
            "маржинальность",
            "управление маржинальностью",
            "profitability",
            "margin",
            "financial result",
        ],
        "pricing": [
            "pricing",
            "ценообразование",
            "ценовая политика",
            "скидочная политика",
            "управление ценами",
        ],
        "forecasting_budgeting": [
            "forecasting",
            "прогнозирование продаж",
            "прогноз выручки",
            "бюджетирование",
            "формирование бюджета",
            "управление бюджетом",
            "план-факт",
        ],
        "sales_commercial_strategy": [
            "стратегия продаж",
            "коммерческая стратегия",
            "построение системы продаж",
            "развитие продаж",
            "управление коммерческой функцией",
            "sales strategy",
            "commercial strategy",
        ],
        "business_development": [
            "развитие бизнеса",
            "business development",
            "развитие новых направлений",
            "поиск точек роста",
            "запуск новых направлений",
        ],
        "channels": [
            "развитие каналов продаж",
            "дистрибуция",
            "дилерская сеть",
            "партнерская сеть",
            "партнерские каналы",
            "channel development",
            "distribution",
        ],
        "scaling_launch": [
            "масштабирование",
            "масштабирование продаж",
            "масштабирование бизнеса",
            "запуск направления",
            "построение с нуля",
            "вывод на новые рынки",
            "launch",
            "scaling",
        ],
        "kpi_performance": [
            "kpi",
            "система kpi",
            "система мотивации",
            "бонусная система",
            "показатели эффективности",
        ],
    },
    "semantic_hard_negative_signals": [
        "менеджер по продажам",
        "sales manager",
        "агент",
        "assistant",
        "ассистент",
        "специалист",
        "стажер",
        "intern",
        "junior",
        "личные холодные продажи",
        "самостоятельный поиск клиентов",
        "банк",
        "банковский сектор",
        "страхование",
        "инвестиции",
        "инвестиционная компания",
        "финансовый сектор",
    ],
    "max_age_days": 7,
    "daily_llm_limit": 30,
    "banned_phrases": [
        "с большим интересом",
        "идеально подхожу",
        "динамично развивающаяся",
        "кроме того",
        "в заключение",
        "синергия",
        "приветствую",
    ],
    "candidate_context": {
        "experience_years": "15+",
        "domain": "B2B-коммерция и дистрибуция",
        "pnl": "до 1 млрд руб.",
        "team_size": "12 человек",
        "results_2025": {
            "sales_growth": "+36%",
            "client_base_growth": "+25%",
        },
        "ai_practice": [
            "подготовка баз: 3 недели -> 1 день",
            "создание КП: 3 часа -> 15 минут",
            "аудит звонков: 100% AI",
        ],
    },
    "opening_variants": [
        "Рассматриваю эту роль как задачу на рост выручки и управляемую коммерческую систему.",
        "В этой вакансии вижу работу, где важны цифры, дисциплина и внятная воронка продаж.",
        "Могу быстро включиться в эту позицию и взять на себя коммерческий контур.",
        "Для такой роли у меня прямой профиль: B2B, команда, P&L и измеримый результат.",
        "Смотрю на эту вакансию через рост, маржу и качество исполнения.",
        "Если нужен коммерческий руководитель с практикой в B2B, профиль у меня совпадает.",
    ],
    "closing_variants": [
        "Готов предметно обсудить, как закрывать план и выстраивать систему.",
        "Если задача про рост и управляемый масштаб, готов подключиться.",
        "Могу быстро выйти на разговор и показать, как бы я подошел к роли.",
        "Готов обсудить детали и начать с конкретного плана на первые 90 дней.",
        "Если это про выручку, воронку и ответственность за результат, давайте обсудим.",
        "Готов подключиться и разложить задачи по цифрам, процессам и людям.",
    ],
    "segment_templates": [
        {
            "id": "cco_executive",
            "keywords": ["коммерческий директор", "cco", "директор по коммерции", "генеральный"],
            "text": "Коммерческий руководитель с 15-летним опытом в B2B и управлении дистрибуцией. Управлял P&L до 1 млрд руб. и командой из 12 человек. В 2025 году увеличил продажи на +36%, а клиентскую базу на +25%. Системно выстраиваю P&L, CRM-воронку, pricing и контроллинг маржинальности.",
        },
        {
            "id": "head_of_sales",
            "keywords": ["руководитель отдела продаж", "head of sales", "директор по продажам", "rop", "роп"],
            "text": "Более 15 лет в управлении B2B-продажами и командами. В 2025 году оцифровал воронку и выстроил систему мотивации, что дало +36% к выручке и +25% к приросту базы. Опыт управления командой — 12 человек, включая федеральные B2B-сети.",
        },
        {
            "id": "biz_dev_networks",
            "keywords": ["развити", "fmcg", "сетевые", "региональн", "канал", "экспансия"],
            "text": "Эксперт в развитии B2B-каналов продаж и дистрибуции. В портфолио — заведение и сопровождение контрактов с федеральными сетями и маркетплейсами (Ozon, Читай-город, Castorama и др.). В 2025 году расширил клиентскую базу на +25% и вырос по выручке на +36%.",
        },
        {
            "id": "b2b_it_innovations",
            "keywords": ["it", "айти", "автоматизац", "проектн", "софт", "crm", "llm"],
            "text": "B2B-руководитель с фокусом на технологичные процессы и AI-автоматизацию. Внедрил LLM в коммерческий департамент: сократил подготовку B2B-баз с 3 недель до 1 дня, создание КП — с 3 часов до 15 минут, запустил 100% AI-аудит переговоров. Результат 2025 года: +36% к выручке.",
        },
    ],
    "default_template": "Коммерческий руководитель с 15-летним опытом в B2B и управлении командами (до 12 человек). В 2025 году увеличил продажи на +36% (P&L до 1 млрд руб.) и расширил клиентскую базу на +25%. Специализируюсь на системных B2B-продажах, настройке CRM и повышении маржинальности.",
    "short_pitch": (
        "Рассматриваю коммерческую роль уровня Head of Sales / CCO. "
        "15+ лет в B2B-коммерции и дистрибуции: P&L до 1 млрд руб., команда 12 человек. "
        "В 2025 году дал +36% к продажам и +25% к клиентской базе.\n\n"
        "Мой фокус — рост выручки, управляемая воронка, дисциплина продаж и сильная коммерческая команда. "
        "Практически использую AI в B2B: сократил подготовку баз с 3 недель до 1 дня, "
        "создание КП с 3 часов до 15 минут, запустил 100% AI-аудит звонков."
    ),
    "template_paragraphs": [
        "Рассматриваю роль коммерческого директора / Head of Sales. 15+ лет в B2B-коммерции и дистрибуции; P&L до 1 млрд руб., команда 12 человек. В 2025 году дал +36% к продажам и +25% к клиентской базе.",
        "Сейчас фокус на росте выручки, управлении воронкой и коммерческой дисциплине. Практически использую AI: базы готовлю за 1 день вместо 3 недель, КП за 15 минут вместо 3 часов, аудит звонков — 100%.",
        "Если роль про рост, P&L и управляемый масштаб, готов быстро включиться и дать результат.",
    ],
}


@dataclass(frozen=True)
class CoverLetterDecision:
    status: str
    cover_letter: str | None
    reason: str = ""

    def to_json(self) -> dict[str, str | None]:
        return {"status": self.status, "cover_letter": self.cover_letter}


def load_rules(rules_path: Path | None) -> dict[str, Any]:
    if rules_path and rules_path.exists():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        rules["_rules_path"] = str(rules_path)
        return rules
    return DEFAULT_RULES


def load_json_file(path: Path | None, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default or {}


def candidate_profile_path(rules_path: Path | None = None) -> Path:
    base = rules_path.parent if rules_path else Path.cwd()
    return base / "candidate_profile.json"


def achievement_bank_path(rules_path: Path | None = None) -> Path:
    base = rules_path.parent if rules_path else Path.cwd()
    return base / "achievement_bank.json"


def confirmed_achievement_texts(rules_path: Path | None = None) -> list[str]:
    bank = load_json_file(achievement_bank_path(rules_path))
    confirmed = bank.get("confirmed", [])
    return [str(item.get("text", "")).strip() for item in confirmed if isinstance(item, dict) and item.get("text")]


def signal_hits(text: str, signals: list[str]) -> list[str]:
    normalized = text.lower()
    return [str(signal).lower() for signal in signals if str(signal).lower() in normalized]


def commercial_signal_group_hits(text: str, groups: dict[str, list[str]]) -> dict[str, list[str]]:
    normalized = text.lower()
    hits: dict[str, list[str]] = {}
    for group, signals in groups.items():
        group_hits = [str(signal).lower() for signal in signals if str(signal).lower() in normalized]
        if group_hits:
            hits[str(group)] = group_hits
    return hits


def semantic_gate_decision(text: str, rules: dict[str, Any]) -> dict[str, Any]:
    management_signals = signal_hits(
        text,
        rules.get("semantic_management_signals", DEFAULT_RULES["semantic_management_signals"]),
    )
    commercial_groups = commercial_signal_group_hits(
        text,
        rules.get("semantic_commercial_signal_groups", DEFAULT_RULES["semantic_commercial_signal_groups"]),
    )
    hard_negative_signals = signal_hits(
        text,
        rules.get("semantic_hard_negative_signals", DEFAULT_RULES["semantic_hard_negative_signals"]),
    )
    decision = (
        "evaluate"
        if management_signals and len(commercial_groups) >= 2 and not hard_negative_signals
        else "skip"
    )
    return {
        "decision": decision,
        "management_signals": management_signals,
        "commercial_signal_groups": list(commercial_groups),
        "commercial_signal_hits": commercial_groups,
        "hard_negative_signals": hard_negative_signals,
    }


MASS_TARGET_TITLE_PATTERNS = [
    "commercial director",
    "chief commercial officer",
    "директор по продажам",
    "sales director",
    "head of sales",
    "руководитель отдела продаж",
    "руководитель продаж",
    "директор по развитию",
    "business development director",
    "head of business development",
    "коммерческий директор",
    "cco",
    "роп",
]

MASS_ADJACENT_TITLE_PATTERNS = [
    "директор направления",
    "руководитель направления",
    "директор бизнес-направления",
    "руководитель бизнес-направления",
    "операционный директор",
    "исполнительный директор",
    "директор филиала",
    "региональный директор",
    "business unit director",
    "business unit head",
    "head of business unit",
    "head of growth",
    "revenue director",
    "chief revenue officer",
    "head of revenue",
    "coo",
    "cro",
]

MASS_HARD_TITLE_PATTERNS = [
    "менеджер по продажам",
    "sales manager",
    "account manager",
    "key account",
    "kam",
    "специалист",
    "ассистент",
    "помощник",
    "стажер",
    "junior",
    "агент",
    "риелтор",
    "маркетолог",
    "hr",
    "юрист",
    "бухгалтер",
    "аналитик",
    "cfo",
    "cto",
    "cpo",
]

MASS_HARD_TEXT_PATTERNS = [
    "банк",
    "банковский сектор",
    "страхование",
    "страховой",
    "инвестиционная компания",
    "инвестиции",
    "финансовый сектор",
    "личные холодные продажи",
    "самостоятельный поиск клиентов",
    "только процент",
    "агентская схема",
]

MASS_BASIC_SCOPE_PATTERNS = [
    "команда",
    "управление отделом",
    "руководство отделом",
    "руководство командой",
    "подчинении",
    "продажи",
    "выручка",
    "p&l",
    "коммерческий результат",
    "развитие бизнеса",
    "каналы продаж",
    "масштабирование",
    "запуск направления",
    "стратегия",
    "team",
    "sales",
    "revenue",
    "commercial",
    "business development",
    "scaling",
    "launch",
]


def mass_basic_relevance_decision(vacancy: dict[str, Any], rules: dict[str, Any] | None = None) -> CoverLetterDecision:
    rules = rules or DEFAULT_RULES
    title = str(vacancy.get("name", "")).lower()
    text = " ".join(
        str(vacancy.get(key, ""))
        for key in ("name", "description", "snippet", "page_text", "employer")
    ).lower()
    if isinstance(vacancy.get("employer"), dict):
        text += " " + str(vacancy["employer"].get("name", "")).lower()

    for pattern in MASS_HARD_TITLE_PATTERNS:
        if mass_hard_title_matches(title, pattern):
            return CoverLetterDecision("SKIP", None, f"mass hard title exclusion: {pattern}")
    for pattern in MASS_HARD_TEXT_PATTERNS:
        if pattern in text:
            return CoverLetterDecision("SKIP", None, f"mass hard exclusion: {pattern}")

    if any(pattern in title for pattern in MASS_TARGET_TITLE_PATTERNS):
        cover_letter = build_cover_letter(rules, vacancy)
        return CoverLetterDecision("APPROVED", cover_letter, "selection_mode=mass_v1; scenario=target_role")

    adjacent_title = any(pattern in title for pattern in MASS_ADJACENT_TITLE_PATTERNS)
    basic_scope = [pattern for pattern in MASS_BASIC_SCOPE_PATTERNS if pattern in text]
    if adjacent_title and basic_scope:
        cover_letter = build_cover_letter(rules, vacancy)
        return CoverLetterDecision(
            "APPROVED",
            cover_letter,
            "selection_mode=mass_v1; scenario=adjacent_role; basic_scope=" + ",".join(basic_scope[:8]),
        )

    return CoverLetterDecision("SKIP", None, "mass basic relevance not matched")


def mass_card_relevance_decision(vacancy: dict[str, Any], rules: dict[str, Any] | None = None) -> CoverLetterDecision:
    rules = rules or DEFAULT_RULES
    title = str(vacancy.get("name", "")).lower()
    snippet = str(vacancy.get("snippet", "") or vacancy.get("responsibility", "") or "").lower()
    employer = vacancy.get("employer") or {}
    company = str(employer.get("name", "") if isinstance(employer, dict) else employer).lower()
    text = " ".join([title, snippet, company])

    for pattern in MASS_HARD_TITLE_PATTERNS:
        if mass_hard_title_matches(title, pattern):
            return CoverLetterDecision("SKIP", None, f"mass_v1.1 hard title exclusion: {pattern}")
    for pattern in MASS_HARD_TEXT_PATTERNS:
        if pattern in text:
            return CoverLetterDecision("SKIP", None, f"mass_v1.1 hard exclusion: {pattern}")

    if any(pattern in title for pattern in MASS_TARGET_TITLE_PATTERNS):
        return CoverLetterDecision(
            "APPROVED",
            build_cover_letter(rules, vacancy),
            "selection_mode=mass_v1.1; decision=likely_apply; source=card; scenario=target_role",
        )

    adjacent_title = any(pattern in title for pattern in MASS_ADJACENT_TITLE_PATTERNS)
    basic_scope = [pattern for pattern in MASS_BASIC_SCOPE_PATTERNS if pattern in text]
    if adjacent_title and basic_scope:
        return CoverLetterDecision(
            "APPROVED",
            build_cover_letter(rules, vacancy),
            "selection_mode=mass_v1.1; decision=likely_apply; source=card; scenario=adjacent_role; basic_scope="
            + ",".join(basic_scope[:8]),
        )

    return CoverLetterDecision("SKIP", None, "mass_v1.1 card relevance not matched")


def mass_hard_title_matches(title: str, pattern: str) -> bool:
    if pattern in {"hr", "cfo", "cto", "cpo"}:
        return re.search(rf"(?<![a-zа-яё]){re.escape(pattern)}(?![a-zа-яё])", title) is not None
    return pattern in title


def evaluate_vacancy(vacancy: dict[str, Any], rules: dict[str, Any] | None = None) -> CoverLetterDecision:
    rules = rules or DEFAULT_RULES
    title = str(vacancy.get("name", "")).lower()
    text = " ".join(
        str(vacancy.get(key, ""))
        for key in ("name", "description", "snippet", "employer")
    ).lower()
    if isinstance(vacancy.get("employer"), dict):
        text += " " + str(vacancy["employer"].get("name", "")).lower()

    semantic_rescue_reason = ""
    soft_title_stop_words = [str(item).lower() for item in rules.get("soft_title_stop_words", DEFAULT_RULES["soft_title_stop_words"])]
    for stop_word in rules.get("title_stop_words", []):
        if stop_word.lower() in title:
            if stop_word.lower() in soft_title_stop_words:
                semantic = semantic_gate_decision(text, rules)
                if semantic["decision"] == "evaluate":
                    semantic_rescue_reason = (
                        f"semantic_gate=evaluate; legacy_reason=title_stop_word_matched: {stop_word}; "
                        f"management_signals={','.join(semantic['management_signals'])}; "
                        f"commercial_signal_groups={','.join(semantic['commercial_signal_groups'])}"
                    )
                    break
                return CoverLetterDecision(
                    "SKIP",
                    None,
                    f"title stop word matched: {stop_word}; semantic_gate=skip; "
                    f"management_signals={','.join(semantic['management_signals'])}; "
                    f"commercial_signal_groups={','.join(semantic['commercial_signal_groups'])}; "
                    f"hard_negative_signals={','.join(semantic['hard_negative_signals'])}",
                )
            return CoverLetterDecision("SKIP", None, f"title stop word matched: {stop_word}")

    for stop_word in soft_title_stop_words:
        if stop_word in title:
            semantic = semantic_gate_decision(text, rules)
            if semantic["decision"] == "evaluate":
                semantic_rescue_reason = (
                    f"semantic_gate=evaluate; legacy_reason=title_stop_word_matched: {stop_word}; "
                    f"management_signals={','.join(semantic['management_signals'])}; "
                    f"commercial_signal_groups={','.join(semantic['commercial_signal_groups'])}"
                )
                break
            return CoverLetterDecision(
                "SKIP",
                None,
                f"title stop word matched: {stop_word}; semantic_gate=skip; "
                f"management_signals={','.join(semantic['management_signals'])}; "
                f"commercial_signal_groups={','.join(semantic['commercial_signal_groups'])}; "
                f"hard_negative_signals={','.join(semantic['hard_negative_signals'])}",
            )

    for stop_word in rules.get("stop_words", []):
        if stop_word.lower() in text:
            return CoverLetterDecision("SKIP", None, f"stop word matched: {stop_word}")

    if is_too_old(vacancy, int(rules.get("max_age_days", 0) or 0)):
        return CoverLetterDecision("SKIP", None, f"older than {rules.get('max_age_days')} days")

    title_rules = rules.get("title_must_contain") or rules.get("target_roles", [])
    if not semantic_rescue_reason and not any(str(role).lower() in title for role in title_rules):
        return CoverLetterDecision("SKIP", None, "title keyword not matched")

    cover_letter = build_cover_letter(rules, vacancy)
    if has_banned_phrase(cover_letter, rules.get("banned_phrases", [])):
        return CoverLetterDecision("SKIP", None, "template contains banned phrase")
    return CoverLetterDecision("APPROVED", cover_letter, semantic_rescue_reason)


def build_cover_letter(rules: dict[str, Any], vacancy: dict[str, Any]) -> str:
    title = str(vacancy.get("name", "")).strip()
    segment = select_segment_entry(rules, title)
    opening = pick_stable_variant(vacancy, rules.get("opening_variants") or DEFAULT_RULES["opening_variants"], "opening")
    closing = pick_stable_variant(vacancy, rules.get("closing_variants") or DEFAULT_RULES["closing_variants"], "closing")
    rules_path = Path(str(rules.get("_rules_path"))) if rules.get("_rules_path") else None
    profile = load_json_file(candidate_profile_path(rules_path))
    achievement_texts = confirmed_achievement_texts(rules_path)

    if segment:
        body = choose_segment_body(segment, vacancy)
    else:
        body = build_profile_body(profile, achievement_texts, rules)

    parts = [part.strip() for part in [opening, body, closing] if str(part).strip()]
    if not parts:
        return str(rules.get("short_pitch", "")).strip()
    return "\n\n".join(parts)


def select_segment_entry(rules: dict[str, Any], vacancy_title: str) -> dict[str, Any] | None:
    title = vacancy_title.lower()
    for segment in rules.get("segment_templates", []):
        keywords = segment.get("keywords", []) if isinstance(segment, dict) else []
        if any(str(keyword).lower() in title for keyword in keywords):
            return segment if isinstance(segment, dict) else None
    return None


def select_segment_template(rules: dict[str, Any], vacancy_title: str) -> str:
    segment = select_segment_entry(rules, vacancy_title)
    if not segment:
        return str(rules.get("default_template", "")).strip()
    return choose_segment_body(segment, {"name": vacancy_title})


def build_profile_body(profile: dict[str, Any], achievements: list[str], rules: dict[str, Any]) -> str:
    team = profile.get("team_size") or {}
    markets = profile.get("markets") or []
    skills = profile.get("skills") or []
    role_bits = profile.get("target_roles") or []
    picks = [text for text in achievements[:4] if text]
    lines = [
        "Добрый день. Коммерческий руководитель с подтвержденным опытом в B2B и управлении продажами.",
        f"Команда: {team.get('direct', 0)} direct / {team.get('total', 0)} total. Рынки: {', '.join(str(x) for x in markets)}.",
        f"Фокус: {', '.join(str(x) for x in skills)}.",
    ]
    if picks:
        lines.append("Подтвержденные достижения: " + "; ".join(picks) + ".")
    if role_bits:
        lines.append("Целевые роли: " + ", ".join(str(x) for x in role_bits[:6]) + ".")
    fallback = str(rules.get("default_template", "")).strip()
    return "\n".join(line for line in lines if line) or fallback or str(rules.get("short_pitch", "")).strip()


def choose_segment_body(segment: dict[str, Any], vacancy: dict[str, Any]) -> str:
    variants = segment.get("variants")
    if isinstance(variants, list) and variants:
        choice = pick_stable_variant(vacancy, [str(item) for item in variants], f"segment:{segment.get('id', '')}")
        if choice:
            return choice
    return str(segment.get("text", "")).strip()


def pick_stable_variant(vacancy: dict[str, Any], variants: list[str], salt: str) -> str:
    items = [item.strip() for item in variants if item and item.strip()]
    if not items:
        return ""
    key = str(vacancy.get("id") or vacancy.get("name") or vacancy.get("alternate_url") or vacancy.get("url") or "")
    salt_score = sum(ord(char) for char in salt)
    if key.isdigit():
        return items[(int(key) + salt_score) % len(items)]
    digest = hashlib.sha256(f"{key}|{salt}".encode("utf-8")).digest()
    return items[digest[0] % len(items)]


def has_banned_phrase(text: str, banned_phrases: list[str]) -> bool:
    normalized = text.lower()
    return any(phrase.lower() in normalized for phrase in banned_phrases)


def is_too_old(vacancy: dict[str, Any], max_age_days: int) -> bool:
    if max_age_days <= 0:
        return False
    raw_date = vacancy.get("published_at") or vacancy.get("created_at")
    if not raw_date:
        return False
    try:
        parsed = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return age.days > max_age_days


def decision_json(vacancy: dict[str, Any], rules_path: Path | None = None) -> str:
    decision = evaluate_vacancy(vacancy, load_rules(rules_path))
    return json.dumps(decision.to_json(), ensure_ascii=False)

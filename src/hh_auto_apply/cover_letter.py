from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
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
        "операционн",
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
        "маркетинг",
        "маркетингу",
        "cmo",
        "продукт",
        "продукту",
        "cpo",
        "инвестици",
        "инвестициям",
        "операционн",
        "coo",
        "финанс",
        "финансовый",
        "cfo",
        "персонал",
        "hr",
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


def evaluate_vacancy(vacancy: dict[str, Any], rules: dict[str, Any] | None = None) -> CoverLetterDecision:
    rules = rules or DEFAULT_RULES
    title = str(vacancy.get("name", "")).lower()
    text = " ".join(
        str(vacancy.get(key, ""))
        for key in ("name", "description", "snippet", "employer")
    ).lower()
    if isinstance(vacancy.get("employer"), dict):
        text += " " + str(vacancy["employer"].get("name", "")).lower()

    for stop_word in rules.get("title_stop_words", []):
        if stop_word.lower() in title:
            return CoverLetterDecision("SKIP", None, f"title stop word matched: {stop_word}")

    for stop_word in rules.get("stop_words", []):
        if stop_word.lower() in text:
            return CoverLetterDecision("SKIP", None, f"stop word matched: {stop_word}")

    if is_too_old(vacancy, int(rules.get("max_age_days", 0) or 0)):
        return CoverLetterDecision("SKIP", None, f"older than {rules.get('max_age_days')} days")

    title_rules = rules.get("title_must_contain") or rules.get("target_roles", [])
    if not any(str(role).lower() in title for role in title_rules):
        return CoverLetterDecision("SKIP", None, "title keyword not matched")

    cover_letter = build_cover_letter(rules, vacancy)
    if has_banned_phrase(cover_letter, rules.get("banned_phrases", [])):
        return CoverLetterDecision("SKIP", None, "template contains banned phrase")
    return CoverLetterDecision("APPROVED", cover_letter)


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

from __future__ import annotations

from domain_scanner.db.models import DomainSource, Verdict

VERDICT_EMOJI: dict[Verdict, str] = {
    Verdict.UNKNOWN: "❔",
    Verdict.CLEAN: "✅",
    Verdict.SUSPICIOUS: "⚠️",
    Verdict.FLAGGED: "🚨",
    Verdict.ERROR: "🛑",
}

VERDICT_RU: dict[Verdict, str] = {
    Verdict.UNKNOWN: "не проверен",
    Verdict.CLEAN: "чисто",
    Verdict.SUSPICIOUS: "подозрительно",
    Verdict.FLAGGED: "зашкварен",
    Verdict.ERROR: "ошибка проверки",
}

# Card headline for a domain in this state.
VERDICT_TITLE: dict[Verdict, str] = {
    Verdict.UNKNOWN: "Нет данных",
    Verdict.CLEAN: "Домен чистый",
    Verdict.SUSPICIOUS: "Домен под подозрением",
    Verdict.FLAGGED: "Домен зашкварен",
    Verdict.ERROR: "Не удалось проверить",
}

# Worst first — the order problems are listed in.
VERDICT_ORDER: tuple[Verdict, ...] = (
    Verdict.FLAGGED,
    Verdict.SUSPICIOUS,
    Verdict.ERROR,
    Verdict.CLEAN,
    Verdict.UNKNOWN,
)

CHECKER_LABELS: dict[str, str] = {
    "dns_rbl": "DNS-блоклисты",
    "google_safe_browsing": "Google Safe Browsing",
    "facebook": "Facebook",
}

SOURCE_LABELS: dict[DomainSource, str] = {
    DomainSource.PWA: "PWA.partners",
    DomainSource.UCLIENT: "UClient",
    DomainSource.MANUAL: "вручную",
}


def checker_label(name: str) -> str:
    return CHECKER_LABELS.get(name, name)


def source_label(source: DomainSource | None) -> str:
    return SOURCE_LABELS.get(source, "—") if source else "—"

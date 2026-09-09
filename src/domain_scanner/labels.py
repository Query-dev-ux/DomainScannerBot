from __future__ import annotations

from domain_scanner.db.models import Verdict

VERDICT_EMOJI: dict[Verdict, str] = {
    Verdict.UNKNOWN: "❔",
    Verdict.CLEAN: "✅",
    Verdict.SUSPICIOUS: "⚠️",
    Verdict.FLAGGED: "🚨",
    Verdict.ERROR: "🛑",
}

VERDICT_RU: dict[Verdict, str] = {
    Verdict.UNKNOWN: "неизвестно",
    Verdict.CLEAN: "чисто",
    Verdict.SUSPICIOUS: "подозрительно",
    Verdict.FLAGGED: "зашкварен",
    Verdict.ERROR: "ошибка проверки",
}

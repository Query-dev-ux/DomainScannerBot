"""What the source platform itself says about the domain.

The platforms know about bans long before they show up anywhere else: a domain
banned in PWApartners keeps resolving for days, so DNS and Safe Browsing stay
quiet while the ban is already there. The status arrives with every sync and is
stored on the domain, so this check costs no network call.
"""

from __future__ import annotations

from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import DomainSource, Verdict

NAME = "source_status"

# PWApartners models.Domain.status: 0 - выключен, 1 - активен, 2 - ждёт покупки,
# 5 - ожидает подключения ns1/ns2, 6 - в процессе покупки, 7 - куплен, ждём подвязку,
# 8 - просрочен, 9 - забанен, 10 - неудачная покупка, 11 - не хватает баланса.
# Only the states that mean "this domain is no good" are reported.
_BAD_STATUSES: dict[DomainSource, dict[str, tuple[Verdict, str]]] = {
    DomainSource.PWA: {
        "9": (Verdict.FLAGGED, "забанен в PWApartners"),
        "8": (Verdict.SUSPICIOUS, "просрочен в PWApartners"),
    },
    # SkakApp's PWA statuses (NEW / ACTIVE / DISABLE / DISABLE_BALANCE / ARCHIVE)
    # say nothing about the domain's reputation, so nothing is reported for them.
    DomainSource.SKAKAPP: {},
}


def check_source_status(source: DomainSource | None, status: str | None) -> CheckOutcome | None:
    """None when the source says nothing bad (or says nothing at all)."""
    if source is None or status is None:
        return None
    verdict_and_text = _BAD_STATUSES.get(source, {}).get(status.strip())
    if verdict_and_text is None:
        return None
    verdict, summary = verdict_and_text
    return CheckOutcome(
        checker=NAME, verdict=verdict, summary=summary, raw={"status": status}
    )

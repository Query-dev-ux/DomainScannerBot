from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from domain_scanner.bot import render
from domain_scanner.bot.keyboards import domain_keyboard
from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Domain, DomainSource, Verdict
from domain_scanner.repositories.domains import DomainStats, DomainWithChecks
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult

WHEN = datetime(2026, 9, 18, 9, 40, tzinfo=UTC)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️■-◿]")


def _report(**kw) -> ScanReport:
    base = dict(
        domain="wintonic.living",
        verdict=Verdict.FLAGGED,
        previous_verdict=Verdict.CLEAN,
        changed=True,
        outcomes=[
            CheckOutcome("dns_rbl", Verdict.FLAGGED, summary="в блоклистах: SURBL"),
            CheckOutcome("google_safe_browsing", Verdict.CLEAN, summary="нет совпадений"),
        ],
        domain_id=7,
        source=DomainSource.SKAKAPP,
        finished_at=WHEN,
    )
    base.update(kw)
    return ScanReport(**base)


def _domain(name: str, verdict: Verdict = Verdict.CLEAN, checks=None, **kw) -> DomainWithChecks:
    base = dict(source=DomainSource.PWA, is_active=True, monitoring_enabled=True)
    base.update(kw)
    return DomainWithChecks(Domain(name=name, current_verdict=verdict, **base), checks or {})


def _every_message() -> list[str]:
    stats = DomainStats(
        by_verdict={Verdict.CLEAN: 9, Verdict.FLAGGED: 1},
        by_source={DomainSource.PWA: 6, DomainSource.SKAKAPP: 4},
        muted=2,
    )
    sync = [
        SyncResult(DomainSource.PWA, "PWApartners", fetched=5, removed=1, foreign=1),
        SyncResult(DomainSource.SKAKAPP, "SkakApp", error="HTTP 401"),
    ]
    return [
        render.render_help(),
        render.render_startup(["PWApartners", "SkakApp"], ["dns_rbl"]),
        render.render_report(_report(), alert=True),
        render.render_report(_report(changed=False)),
        render.render_checking("a.com"),
        render.render_status(stats),
        render.render_status(DomainStats()),
        render.render_list(
            [_domain("a.com", Verdict.FLAGGED, {"source_status": Verdict.FLAGGED})],
            "Проблемные домены",
            empty_hint="—",
        ),
        render.render_sync(sync),
        render.render_sync([]),
        render.render_sync_failure(sync),
        render.render_job_crash("Синхронизация"),
        render.render_scan_started(5),
        render.render_scan_done([_report()]),
    ]


@pytest.mark.parametrize("text", _every_message())
def test_no_emoji_anywhere(text: str):
    assert not EMOJI.search(text), text


def test_no_emoji_on_buttons():
    for enabled in (True, False):
        for row in domain_keyboard(1, monitoring_enabled=enabled).inline_keyboard:
            for button in row:
                assert not EMOJI.search(button.text), button.text


@pytest.mark.parametrize("text", _every_message())
def test_no_on_off_vocabulary(text: str):
    lowered = text.lower()
    for word in ("выключ", "включ", "неактивн", "активн"):
        assert word not in lowered, (word, text)


def test_alert_card_layout():
    text = render.render_report(_report(), alert=True)
    lines = text.split("\n")
    assert lines[0] == "<b>Домен зашкварен</b>"
    assert lines[1] == "<code>wintonic.living</code>"
    assert lines[2] == "<i>SkakApp · было: чисто</i>"
    assert "<b>DNS-блоклисты</b> — в блоклистах: SURBL" in text  # problem is bold
    assert lines[-1] == "Google Safe Browsing — нет совпадений"  # clean stays plain
    assert "MSK" not in text and "18.09" not in text  # Telegram shows the time itself


def test_no_previous_state_when_domain_was_never_checked():
    text = render.render_report(_report(previous_verdict=Verdict.UNKNOWN), alert=True)
    assert "было" not in text
    assert text.split("\n")[2] == "<i>SkakApp</i>"


def test_unchanged_check_card_omits_previous_state():
    text = render.render_report(_report(changed=False, previous_verdict=Verdict.FLAGGED))
    assert "было:" not in text


def test_untrusted_text_is_escaped():
    evil = CheckOutcome("facebook", Verdict.ERROR, error="<script>alert(1)</script>")
    text = render.render_report(_report(domain="a<b>.com", outcomes=[evil]))
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "a&lt;b&gt;.com" in text


def test_long_details_are_clipped():
    long = CheckOutcome("facebook", Verdict.ERROR, error="x" * 1000)
    text = render.render_report(_report(outcomes=[long]))
    assert "x" * 1000 not in text and "…" in text


def test_status_is_just_the_count_and_verdicts():
    stats = DomainStats(
        by_verdict={Verdict.CLEAN: 9, Verdict.FLAGGED: 1},
        by_source={DomainSource.PWA: 6, DomainSource.SKAKAPP: 4},
        muted=2,
    )
    text = render.render_status(stats)
    assert "10 доменов" in text
    assert "Зашкварен — 1" in text and "Чисто — 9" in text
    assert "Подозрительно" not in text  # zero rows are hidden
    for extra in ("%", "PWApartners", "SkakApp", "отслежива"):
        assert extra not in text


def test_status_empty_db_points_to_sync():
    assert "/sync_now" in render.render_status(DomainStats())


@pytest.mark.parametrize(
    ("n", "word"), [(1, "домен"), (2, "домена"), (5, "доменов"), (11, "доменов"), (21, "домен")]
)
def test_domain_count_plural(n: int, word: str):
    assert render._domains(n) == f"{n} {word}"


def test_list_groups_by_source_in_order():
    items = [
        _domain("b.com", Verdict.SUSPICIOUS, source=DomainSource.SKAKAPP),
        _domain("a.com", Verdict.SUSPICIOUS, source=DomainSource.PWA),
        _domain("m.com", Verdict.SUSPICIOUS, source=DomainSource.MANUAL),
    ]
    text = render.render_list(items, "Проблемные домены", empty_hint="—")
    assert text.split("\n")[0] == "<b>Проблемные домены</b> · 3"
    pwa, skak, manual = (
        text.index(f"<b>{name}</b>") for name in ("PWApartners", "SkakApp", "вручную")
    )
    assert pwa < skak < manual
    assert text.index("a.com") < text.index("b.com")


def test_tags_name_the_actual_problem():
    banned_in_source = _domain(
        "s.com", Verdict.FLAGGED, {"source_status": Verdict.FLAGGED, "dns_rbl": Verdict.CLEAN}
    )
    banned_in_fb = _domain("f.com", Verdict.FLAGGED, {"facebook": Verdict.FLAGGED})
    both = _domain(
        "sf.com", Verdict.FLAGGED,
        {"source_status": Verdict.FLAGGED, "facebook": Verdict.FLAGGED},
    )
    suspicious = _domain("p.com", Verdict.SUSPICIOUS, {"dns_rbl": Verdict.SUSPICIOUS})

    assert render.domain_tags(banned_in_source) == ["Заблокирован в PWA сервисе"]
    assert render.domain_tags(banned_in_fb) == ["Заблокирован в FB"]
    assert render.domain_tags(both) == ["Заблокирован в PWA сервисе", "Заблокирован в FB"]
    assert render.domain_tags(suspicious) == ["Под подозрением"]

    text = render.render_list([banned_in_source, both], "Проблемные домены", empty_hint="—")
    assert "<code>s.com</code> — <i>Заблокирован в PWA сервисе</i>" in text
    assert "<code>sf.com</code> — <i>Заблокирован в PWA сервисе · Заблокирован в FB</i>" in text


def test_flagged_by_blocklists_still_gets_a_tag():
    # Nothing in the list may appear without a reason next to it.
    item = _domain("x.com", Verdict.FLAGGED, {"dns_rbl": Verdict.FLAGGED})
    assert render.domain_tags(item) == ["Зашкварен"]


def test_clean_domain_has_no_tags():
    assert render.domain_tags(_domain("ok.com", Verdict.CLEAN)) == []


def test_worst_first_inside_a_source_group():
    items = [
        _domain("a-suspicious.com", Verdict.SUSPICIOUS),
        _domain("z-flagged.com", Verdict.FLAGGED, {"facebook": Verdict.FLAGGED}),
    ]
    text = render.render_list(items, "Проблемные домены", empty_hint="—")
    assert text.index("z-flagged.com") < text.index("a-suspicious.com")


def test_list_is_capped():
    items = [_domain(f"d{i}.com", Verdict.SUSPICIOUS) for i in range(render.LIST_LIMIT + 5)]
    assert "И ещё 5" in render.render_list(items, "Проблемные домены", empty_hint="—")


def test_long_list_fits_telegram_limit():
    items = [
        _domain(f"{'x' * 50}{i}.com", Verdict.FLAGGED, {"facebook": Verdict.FLAGGED})
        for i in range(500)
    ]
    assert len(render.render_list(items, "Проблемные домены", empty_hint="—")) < 4096


def test_sync_card_reports_each_source():
    results = [
        SyncResult(DomainSource.PWA, "PWApartners", fetched=299, updated=299),
        SyncResult(DomainSource.SKAKAPP, "SkakApp", error="SourceError: HTTP 401"),
    ]
    text = render.render_sync(results)
    assert "<b>PWApartners</b> — 299 доменов" in text
    for extra in ("новых", "обновлено", "удалено", "другом источнике"):
        assert extra not in text
    assert "<b>SkakApp</b> — ошибка" in text
    failure = render.render_sync_failure(results)
    assert "SkakApp" in failure and "PWApartners" not in failure

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from domain_scanner.bot import render
from domain_scanner.bot.keyboards import domain_keyboard
from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Domain, DomainSource, Verdict
from domain_scanner.repositories.domains import DomainStats
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult

MSK = ZoneInfo("Europe/Moscow")
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


def _domain(name: str, verdict: Verdict = Verdict.CLEAN, **kw) -> Domain:
    base = dict(source=DomainSource.PWA, is_active=True, monitoring_enabled=True)
    base.update(kw)
    return Domain(name=name, current_verdict=verdict, **base)


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
    jobs = [render.JobInfo("Проверка", 60, WHEN + timedelta(minutes=5))]
    return [
        render.render_help(),
        render.render_startup(["PWApartners", "SkakApp"], ["dns_rbl"]),
        render.render_report(_report(), alert=True),
        render.render_report(_report(changed=False)),
        render.render_checking("a.com"),
        render.render_status(stats),
        render.render_status(DomainStats()),
        render.render_list([_domain("a.com", Verdict.FLAGGED)], "Проблемные", empty_hint="—"),
        render.render_jobs(jobs, WHEN, MSK, scan_interval=180, batch_size=50, queue=3),
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


def test_list_groups_worst_first():
    domains = [
        _domain("ok.com"),
        _domain("bad.com", Verdict.FLAGGED, source=DomainSource.SKAKAPP, monitoring_enabled=False),
    ]
    text = render.render_list(domains, "Все домены", empty_hint="—")
    assert text.index("bad.com") < text.index("ok.com")
    assert "отслежива" not in text


def test_list_is_capped():
    domains = [_domain(f"d{i}.com") for i in range(render.LIST_LIMIT + 5)]
    assert "И ещё 5" in render.render_list(domains, "Все", empty_hint="—")


def test_long_list_fits_telegram_limit():
    domains = [
        _domain(f"{'x' * 50}{i}.com", Verdict.FLAGGED, monitoring_enabled=False)
        for i in range(500)
    ]
    assert len(render.render_list(domains, "Все", empty_hint="—")) < 4096


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


def test_jobs_card_shows_local_time_and_queue():
    jobs = [render.JobInfo("Синхронизация источников", 60, WHEN + timedelta(minutes=12))]
    text = render.render_jobs(jobs, WHEN, MSK, scan_interval=180, batch_size=50, queue=4)
    assert "Синхронизация источников — каждые 60 мин" in text
    assert "18.09, 12:52 MSK, через 12 мин" in text
    assert "В очереди: 4" in text


def test_bad_list_is_plain_one_per_line():
    (text,) = render.render_bad_list(["bad.com", "sus.io"])
    assert text == "<pre>bad.com\nsus.io</pre>"


def test_bad_list_empty():
    assert render.render_bad_list([]) == ["Плохих доменов нет."]


def test_long_bad_list_is_split_and_keeps_every_domain():
    names = [f"{'x' * 40}{i}.com" for i in range(400)]
    parts = render.render_bad_list(names)
    assert len(parts) > 1
    assert all(len(p) < 4096 for p in parts)
    got = [n for p in parts for n in p.removeprefix("<pre>").removesuffix("</pre>").split("\n")]
    assert got == names


def test_bad_list_escapes_names():
    (text,) = render.render_bad_list(["a<b>.com"])
    assert "a&lt;b&gt;.com" in text


def test_status_button_has_no_emoji():
    from domain_scanner.bot.keyboards import status_keyboard

    (button,) = status_keyboard().inline_keyboard[0]
    assert button.text == "Плохие домены"

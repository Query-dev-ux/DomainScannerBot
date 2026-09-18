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
        SyncResult(DomainSource.PWA, "PWA.partners", fetched=5, removed=1, foreign=1),
        SyncResult(DomainSource.SKAKAPP, "SkakApp", error="HTTP 401"),
    ]
    jobs = [render.JobInfo("Проверка", 60, WHEN + timedelta(minutes=5))]
    return [
        render.render_help(),
        render.render_startup(["PWA.partners", "SkakApp"], ["dns_rbl"], 60, 180),
        render.render_report(_report(), MSK, alert=True),
        render.render_report(_report(changed=False), MSK),
        render.render_checking("a.com"),
        render.render_status(stats),
        render.render_status(DomainStats()),
        render.render_list([_domain("a.com", Verdict.FLAGGED)], "Проблемные", empty_hint="—"),
        render.render_jobs(jobs, WHEN, MSK, scan_interval=180, batch_size=50, queue=3),
        render.render_sync(sync),
        render.render_sync([]),
        render.render_sync_failure(sync),
        render.render_job_crash("Синхронизация"),
        render.render_scan_started(5, 10),
        render.render_scan_done([_report()], remaining=4),
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
    text = render.render_report(_report(), MSK, alert=True)
    lines = text.split("\n")
    assert lines[0] == "<b>Домен зашкварен</b>"
    assert lines[1] == "<code>wintonic.living</code>"
    assert lines[2] == "<i>SkakApp · было: чисто</i>"
    assert "<b>DNS-блоклисты</b> — в блоклистах: SURBL" in text  # problem is bold
    assert "\nGoogle Safe Browsing — нет совпадений" in text  # clean stays plain
    assert lines[-1] == "<i>18.09, 12:40 MSK</i>"


def test_unchanged_check_card_omits_previous_state():
    text = render.render_report(_report(changed=False, previous_verdict=Verdict.FLAGGED), UTC)
    assert "было:" not in text


def test_untrusted_text_is_escaped():
    evil = CheckOutcome("facebook", Verdict.ERROR, error="<script>alert(1)</script>")
    text = render.render_report(_report(domain="a<b>.com", outcomes=[evil]), UTC)
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "a&lt;b&gt;.com" in text


def test_long_details_are_clipped():
    long = CheckOutcome("facebook", Verdict.ERROR, error="x" * 1000)
    text = render.render_report(_report(outcomes=[long]), UTC)
    assert "x" * 1000 not in text and "…" in text


def test_status_shows_only_nonzero_verdicts_share_and_sources():
    stats = DomainStats(
        by_verdict={Verdict.CLEAN: 9, Verdict.FLAGGED: 1},
        by_source={DomainSource.PWA: 6, DomainSource.SKAKAPP: 4},
        muted=2,
    )
    text = render.render_status(stats)
    assert "10 доменов" in text
    assert "Зашкварен — 1" in text and "Чисто — 9" in text
    assert "Подозрительно" not in text  # zero rows are hidden
    assert "Чистых — 90%" in text
    assert "PWA.partners 6 · SkakApp 4" in text
    assert "Не отслеживается — 2" in text


def test_status_empty_db_points_to_sync():
    assert "/sync_now" in render.render_status(DomainStats())


@pytest.mark.parametrize(
    ("n", "word"), [(1, "домен"), (2, "домена"), (5, "доменов"), (11, "доменов"), (21, "домен")]
)
def test_domain_count_plural(n: int, word: str):
    assert render._domains(n) == f"{n} {word}"


def test_list_groups_worst_first_and_marks_muted():
    domains = [
        _domain("ok.com"),
        _domain("bad.com", Verdict.FLAGGED, source=DomainSource.SKAKAPP, monitoring_enabled=False),
    ]
    text = render.render_list(domains, "Все домены", empty_hint="—")
    assert text.index("bad.com") < text.index("ok.com")
    assert "SkakApp · не отслеживается" in text


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
        SyncResult(DomainSource.PWA, "PWA.partners", fetched=299, updated=299),
        SyncResult(DomainSource.SKAKAPP, "SkakApp", error="SourceError: HTTP 401"),
    ]
    text = render.render_sync(results)
    assert "<b>PWA.partners</b> — 299 доменов" in text
    assert "удалено из источника 0" in text
    assert "<b>SkakApp</b> — ошибка" in text
    failure = render.render_sync_failure(results)
    assert "SkakApp" in failure and "PWA.partners" not in failure


def test_jobs_card_shows_local_time_and_queue():
    jobs = [render.JobInfo("Синхронизация источников", 60, WHEN + timedelta(minutes=12))]
    text = render.render_jobs(jobs, WHEN, MSK, scan_interval=180, batch_size=50, queue=4)
    assert "Синхронизация источников — каждые 60 мин" in text
    assert "18.09, 12:52 MSK, через 12 мин" in text
    assert "В очереди: 4" in text

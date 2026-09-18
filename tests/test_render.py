from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from domain_scanner.bot import render
from domain_scanner.checkers.base import CheckOutcome
from domain_scanner.db.models import Domain, DomainSource, Verdict
from domain_scanner.repositories.domains import DomainStats
from domain_scanner.services.scanner import ScanReport
from domain_scanner.services.sync import SyncResult

MSK = ZoneInfo("Europe/Moscow")
WHEN = datetime(2026, 9, 18, 9, 40, tzinfo=UTC)


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
        source=DomainSource.UCLIENT,
        finished_at=WHEN,
    )
    base.update(kw)
    return ScanReport(**base)


def test_alert_card_has_headline_source_previous_checks_and_local_time():
    text = render.render_report(_report(), MSK, alert=True)
    assert "🚨 <b>Домен зашкварен</b>" in text
    assert "<code>wintonic.living</code>" in text
    assert "UClient" in text
    assert "было: ✅ чисто" in text
    assert "<blockquote>" in text
    assert "DNS-блоклисты" in text and "Google Safe Browsing" in text  # human names
    assert "18.09, 12:40 MSK" in text


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


def test_status_shows_counts_bar_and_sources():
    stats = DomainStats(
        by_verdict={Verdict.CLEAN: 9, Verdict.FLAGGED: 1},
        by_source={DomainSource.PWA: 6, DomainSource.UCLIENT: 4},
        muted=2,
        inactive=3,
    )
    text = render.render_status(stats)
    assert "10 в мониторинге" in text
    assert "Зашкварен — <b>1</b>" in text
    assert "▰▰▰▰▰▰▰▰▰▱" in text and "90% чистых" in text
    assert "PWA.partners — 6 · UClient — 4" in text
    assert "без мониторинга — 2" in text and "неактивных — 3" in text


def test_status_empty_db_points_to_sync():
    assert "/sync_now" in render.render_status(DomainStats())


def test_list_groups_worst_first_and_marks_state():
    domains = [
        Domain(name="ok.com", current_verdict=Verdict.CLEAN, source=DomainSource.PWA,
               is_active=True, monitoring_enabled=True),
        Domain(name="bad.com", current_verdict=Verdict.FLAGGED, source=DomainSource.UCLIENT,
               is_active=True, monitoring_enabled=False),
    ]
    text = render.render_list(domains, "Все домены", empty_hint="—")
    assert text.index("bad.com") < text.index("ok.com")
    assert "🔕" in text


def test_list_is_capped():
    domains = [
        Domain(name=f"d{i}.com", current_verdict=Verdict.CLEAN, source=DomainSource.PWA,
               is_active=True, monitoring_enabled=True)
        for i in range(render.LIST_LIMIT + 5)
    ]
    text = render.render_list(domains, "Все", empty_hint="—")
    assert "…и ещё 5" in text


def test_sync_card_reports_each_source():
    results = [
        SyncResult(DomainSource.PWA, "PWA.partners", fetched=199, updated=199),
        SyncResult(DomainSource.UCLIENT, "UClient", error="SourceError: HTTP 401"),
    ]
    text = render.render_sync(results)
    assert "✅ <b>PWA.partners</b> — 199 доменов" in text
    assert "🛑 <b>UClient</b> — ошибка" in text
    failure = render.render_sync_failure(results)
    assert "UClient" in failure and "PWA.partners" not in failure


def test_jobs_card_shows_relative_and_local_time():
    now = WHEN
    jobs = [render.JobInfo("🔄", "Синхронизация", 60, now + timedelta(minutes=12))]
    text = render.render_jobs(jobs, now, MSK, scan_interval=180, batch_size=50, queue=4)
    assert "через ~12 мин" in text and "12:52 MSK" in text
    assert "В очереди сейчас: <b>4</b>" in text


def test_every_message_is_within_telegram_limit():
    domains = [
        Domain(name=f"{'x' * 50}{i}.com", current_verdict=Verdict.FLAGGED,
               source=DomainSource.UCLIENT, is_active=False, monitoring_enabled=False)
        for i in range(500)
    ]
    assert len(render.render_list(domains, "Все", empty_hint="—")) < 4096
    assert len(render.render_help()) < 4096


def test_bar_is_only_full_at_100_percent():
    assert render.bar(99, 100) == "▰" * 9 + "▱"
    assert render.bar(100, 100) == "▰" * 10
    assert render.bar(0, 0) == "▱" * 10

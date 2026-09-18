from __future__ import annotations

import base64

from domain_scanner.config import Settings
from domain_scanner.sources import SkakAppProvider, SourceDomain, build_providers, dedupe
from domain_scanner.sources.pwa_partners import parse_domains
from domain_scanner.sources.skakapp import parse_pwas


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, bot_token="1:A", alert_chat_id=-1, postgres_password="p")
    return Settings(**base, **kw)


def test_skakapp_collects_main_ext_and_split_domains():
    pwas = [
        {
            "id": "pwa-1",
            "name": "Slots",
            "status": "ACTIVE",
            "domain": "https://Main.example.com/",
            "extDomains": ["ext.example.com"],
            "splits": [{"id": "s1", "domain": "split.example.com"}],
        }
    ]
    by_name = {d.name: d for d in parse_pwas(pwas)}

    assert set(by_name) == {"main.example.com", "ext.example.com", "split.example.com"}
    assert all(d.external_parent_id == "pwa-1" for d in by_name.values())
    assert by_name["split.example.com"].external_id == "s1"
    assert by_name["ext.example.com"].raw["role"] == "ext"


def test_skakapp_keeps_every_domain_whatever_the_pwa_status():
    statuses = ["NEW", "ACTIVE", "DISABLE", "DISABLE_BALANCE", "ARCHIVE"]
    pwas = [
        {"id": str(i), "status": s, "domain": f"d{i}.example.com"}
        for i, s in enumerate(statuses)
    ]
    got = parse_pwas(pwas)
    assert len(got) == len(statuses)
    assert [d.status for d in got] == statuses  # kept for reference only


def test_skakapp_skips_missing_and_garbage_domains():
    pwas = [
        {"id": "a", "status": "ACTIVE", "domain": None, "extDomains": ["not a domain", 5]},
        {"id": "b", "status": "ACTIVE", "splits": [{"id": "x"}, "junk"]},
    ]
    assert parse_pwas(pwas) == []


def test_pwa_partners_parsing_keeps_every_status():
    items = [
        {"uuid": "u1", "domain": "Good.COM", "status": 1, "pwa_uuid": "p1"},
        {"uuid": "u2", "domain": "pending.com", "status": 6},
        {"uuid": "u3", "domain": ""},
    ]
    got = {d.name: d for d in parse_domains(items)}
    assert set(got) == {"good.com", "pending.com"}
    assert got["good.com"].external_id == "u1" and got["good.com"].status == "1"
    assert got["pending.com"].status == "6"


def test_dedupe_keeps_first_occurrence():
    items = [
        SourceDomain(name="x.com", external_parent_id="first"),
        SourceDomain(name="x.com", external_parent_id="second"),
        SourceDomain(name="y.com"),
    ]
    got = {d.name: d for d in dedupe(items)}
    assert set(got) == {"x.com", "y.com"}
    assert got["x.com"].external_parent_id == "first"


def test_skakapp_needs_login_and_password():
    both = build_providers(_settings(skakapp_login="me", skakapp_password="pw"))
    assert [p.title for p in both] == ["SkakApp"]
    assert build_providers(_settings(skakapp_login="me")) == []
    assert build_providers(_settings(skakapp_password="pw")) == []


def test_old_uclient_env_names_still_work(monkeypatch):
    # The server's .env predates the rename; it must keep working unchanged.
    monkeypatch.setenv("UCLIENT_LOGIN", "me")
    monkeypatch.setenv("UCLIENT_PASSWORD", "pw")
    s = _settings()
    assert (s.skakapp_login, s.skakapp_password) == ("me", "pw")
    assert [p.title for p in build_providers(s)] == ["SkakApp"]


def test_new_env_names_win_over_old_ones(monkeypatch):
    monkeypatch.setenv("UCLIENT_LOGIN", "old")
    monkeypatch.setenv("SKAKAPP_LOGIN", "new")
    assert _settings().skakapp_login == "new"


def test_skakapp_sends_login_and_password_as_basic_auth():
    header = SkakAppProvider("https://x/api", login="me", password="p:w")._headers["Authorization"]
    scheme, token = header.split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "me:p:w"


def test_defaults_check_hourly_and_show_moscow_time():
    s = _settings()
    assert s.scan_interval_minutes == 60
    assert s.display_timezone == "Europe/Moscow"

from __future__ import annotations

from domain_scanner.sources import SourceDomain, dedupe
from domain_scanner.sources.pwa_partners import parse_domains
from domain_scanner.sources.uclient import parse_pwas


def test_uclient_collects_main_ext_and_split_domains():
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
    assert all(d.is_active for d in by_name.values())
    assert all(d.external_parent_id == "pwa-1" for d in by_name.values())
    assert by_name["split.example.com"].external_id == "s1"
    assert by_name["main.example.com"].status_label == "активна"
    assert by_name["ext.example.com"].status_label == "активна · доп. домен"
    assert by_name["split.example.com"].status_label == "активна · сплит"


def test_uclient_only_active_status_is_active():
    pwas = [
        {"id": str(i), "status": s, "domain": f"d{i}.example.com"}
        for i, s in enumerate(["NEW", "ACTIVE", "DISABLE", "DISABLE_BALANCE", "ARCHIVE"])
    ]
    active = {d.name for d in parse_pwas(pwas) if d.is_active}
    assert active == {"d1.example.com"}


def test_uclient_skips_missing_and_garbage_domains():
    pwas = [
        {"id": "a", "status": "ACTIVE", "domain": None, "extDomains": ["not a domain", 5]},
        {"id": "b", "status": "ACTIVE", "splits": [{"id": "x"}, "junk"]},
    ]
    assert parse_pwas(pwas) == []


def test_pwa_partners_parsing():
    items = [
        {"uuid": "u1", "domain": "Good.COM", "status": 1, "pwa_uuid": "p1"},
        {"uuid": "u2", "domain": "pending.com", "status": 6},
        {"uuid": "u3", "domain": ""},
    ]
    got = {d.name: d for d in parse_domains(items)}
    assert set(got) == {"good.com", "pending.com"}
    assert got["good.com"].is_active and got["good.com"].external_id == "u1"
    assert got["good.com"].status == "1"
    assert not got["pending.com"].is_active
    assert got["pending.com"].status_label == "выпуск сертификата"


def test_dedupe_prefers_active_occurrence():
    items = [
        SourceDomain(name="x.com", is_active=False, external_parent_id="old"),
        SourceDomain(name="x.com", is_active=True, external_parent_id="live"),
        SourceDomain(name="y.com", is_active=True),
    ]
    got = {d.name: d for d in dedupe(items)}
    assert set(got) == {"x.com", "y.com"}
    assert got["x.com"].is_active and got["x.com"].external_parent_id == "live"


def _settings(**kw):
    from domain_scanner.config import Settings

    base = dict(_env_file=None, bot_token="1:A", alert_chat_id=-1, postgres_password="p")
    return Settings(**base, **kw)


def test_uclient_needs_login_and_password():
    from domain_scanner.sources import build_providers

    both = build_providers(_settings(uclient_login="me", uclient_password="pw"))
    assert [p.title for p in both] == ["UClient"]
    assert build_providers(_settings(uclient_login="me")) == []
    assert build_providers(_settings(uclient_password="pw")) == []


def test_uclient_sends_login_and_password_as_basic_auth():
    import base64

    from domain_scanner.sources import UClientProvider

    header = UClientProvider("https://x/api", login="me", password="p:w")._headers["Authorization"]
    scheme, token = header.split(" ", 1)
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "me:p:w"

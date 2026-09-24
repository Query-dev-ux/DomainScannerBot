from __future__ import annotations

import base64

from domain_scanner.checkers.source_status import check_source_status
from domain_scanner.config import Settings
from domain_scanner.db.models import DomainSource, Verdict
from domain_scanner.sources import SkakAppProvider, SourceDomain, build_providers, dedupe
from domain_scanner.sources.pwa_partners import parse_domains
from domain_scanner.sources.skakapp import domain_status, parse_pwas


def _settings(**kw) -> Settings:
    base = dict(_env_file=None, bot_token="1:A", alert_chat_id=-1, postgres_password="p")
    return Settings(**base, **kw)


# One PWA as the live API returns it (fields trimmed to the ones we read).
PWA_WITH_A_BANNED_DOMAIN = {
    "id": "4gQv",
    "name": "PL | Revolut Slots | STARE",
    "status": "active",
    "domain": "revogames.best",
    "extDomains": ["revogogame.online"],
    "splits": [],
    "domains": [
        {
            "cid": "bNA9",
            "domain": "revogames.best",
            "expiryDatetime": "2027-08-24 10:44:33",
            "is_disable": False,
            "is_baned_register": False,
            "cloudflare_id": -1,
            "is_main": True,
        },
        {
            "cid": "bNtl",
            "domain": "revogogame.online",
            "expiryDatetime": "2027-08-21 19:27:36",
            "is_disable": True,
            "is_baned_register": True,
            "cloudflare_id": None,
            "is_main": False,
        },
    ],
}


def test_skakapp_reads_the_ban_flag_from_the_domains_field():
    by_name = {d.name: d for d in parse_pwas([PWA_WITH_A_BANNED_DOMAIN])}

    assert set(by_name) == {"revogames.best", "revogogame.online"}
    assert by_name["revogames.best"].status == "ok"
    assert by_name["revogogame.online"].status == "banned"
    assert by_name["revogogame.online"].external_id == "bNtl"  # the domain's own id
    assert all(d.external_parent_id == "4gQv" for d in by_name.values())
    assert by_name["revogogame.online"].raw["expiry"] == "2027-08-21 19:27:36"


def test_banned_domain_is_flagged_end_to_end():
    parsed = parse_pwas([PWA_WITH_A_BANNED_DOMAIN])
    banned = next(d for d in parsed if d.name == "revogogame.online")
    outcome = check_source_status(DomainSource.SKAKAPP, banned.status)
    assert outcome is not None
    assert outcome.verdict is Verdict.FLAGGED
    assert outcome.summary == "заблокирован в SkakApp"


def test_domain_status_flags():
    assert domain_status({"is_baned_register": True, "is_disable": True}) == "banned"
    assert domain_status({"is_disable": True}) == "disabled"
    assert domain_status({"is_disable": False, "is_baned_register": False}) == "ok"
    # Merely disabled is not a reputation problem.
    assert check_source_status(DomainSource.SKAKAPP, "disabled") is None
    assert check_source_status(DomainSource.SKAKAPP, "ok") is None


def test_split_domains_are_added_on_top_of_the_domains_field():
    pwa = dict(PWA_WITH_A_BANNED_DOMAIN, splits=[{"id": "s1", "domain": "split.example.com"}])
    by_name = {d.name: d for d in parse_pwas([pwa])}
    assert "split.example.com" in by_name
    assert by_name["split.example.com"].external_id == "s1"


def test_falls_back_to_bare_hostnames_without_the_domains_field():
    pwa = {
        "id": "old",
        "domain": "https://Main.example.com/",
        "extDomains": ["ext.example.com", "not a domain", 5],
        "splits": [{"id": "s1", "domain": "split.example.com"}],
    }
    got = {d.name for d in parse_pwas([pwa])}
    assert got == {"main.example.com", "ext.example.com", "split.example.com"}


def test_skakapp_skips_garbage_entries():
    pwa = {"id": "a", "domains": [{"cid": "1"}, "junk", {"cid": "2", "domain": "not a domain"}]}
    assert parse_pwas([pwa]) == []


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


def test_default_is_to_check_hourly():
    assert _settings().scan_interval_minutes == 60


# ── кто владеет доменом ──────────────────────────────────────────────────────


def test_skakapp_takes_the_owner_from_the_pwa():
    pwa = dict(PWA_WITH_A_BANNED_DOMAIN, createdUsername="vlad_celestial")
    assert {d.owner for d in parse_pwas([pwa])} == {"vlad_celestial"}


def test_skakapp_without_a_username_leaves_the_owner_empty():
    pwa = dict(PWA_WITH_A_BANNED_DOMAIN, createdUsername="")
    assert {d.owner for d in parse_pwas([pwa])} == {None}


def test_pwapartners_maps_the_teamate_uuid_onto_a_name():
    from domain_scanner.sources.pwa_partners import parse_teamates

    owners = parse_teamates([
        {"uuid": "u1", "login": "vlad@mail", "team_username": "vlad_celestial"},
        {"uuid": "u2", "login": "petr"},          # no team_username — login is used
        {"uuid": "u3"},                            # no name at all — skipped
        {"login": "nouuid"},                       # no uuid — skipped
    ])
    assert owners == {"u1": "vlad_celestial", "u2": "petr"}

    items = [
        {"uuid": "d1", "domain": "a.com", "teamate_uuid": "u1"},
        {"uuid": "d2", "domain": "b.com", "teamate_uuid": "unknown"},
        {"uuid": "d3", "domain": "c.com"},
    ]
    by_name = {d.name: d for d in parse_domains(items, owners)}
    assert by_name["a.com"].owner == "vlad_celestial"
    assert by_name["b.com"].owner is None  # uuid we have no name for
    assert by_name["c.com"].owner is None


def test_owner_aliases_are_parsed_from_the_env_format():
    s = _settings(
        owner_aliases=(
            " skytrafficcpa@gmail.com=CG_Rustam , ahilesmatuna@gmail.com=CG_Raphael ,, junk"
        )
    )
    assert s.owner_alias_map == {
        "skytrafficcpa@gmail.com": "CG_Rustam",
        "ahilesmatuna@gmail.com": "CG_Raphael",
    }


def test_no_aliases_configured():
    assert _settings().owner_alias_map == {}

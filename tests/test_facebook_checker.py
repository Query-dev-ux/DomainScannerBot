from __future__ import annotations

from domain_scanner.checkers.facebook import classify
from domain_scanner.db.models import Verdict


def _err(message: str, code: int = 100, **extra: object) -> dict:
    return {"error": {"message": message, "code": code, "type": "OAuthException", **extra}}


def test_scraped_page_is_clean():
    payload = {
        "id": "https://example.com/",
        "og_object": {"title": "Example"},
        "engagement": {"share_count": 12},
    }
    outcome = classify(200, payload)
    assert outcome.verdict is Verdict.CLEAN
    assert "Example" in (outcome.summary or "")
    assert outcome.raw == payload


def test_bare_id_echo_is_not_clean():
    # Graph echoes the URL with nothing else when Facebook has no page for it.
    # Treating that as "clean" was the bug scrape=true fixes.
    outcome = classify(200, {"id": "https://example.com/"})
    assert outcome.verdict is Verdict.SUSPICIOUS


def test_community_standards_block_is_flagged():
    message = (
        "We can't review this website because the content doesn't meet "
        "our Community Standards"
    )
    assert classify(400, _err(message)).verdict is Verdict.FLAGGED


def test_url_not_allowed_is_flagged():
    outcome = classify(400, _err("The url you supplied is not allowed"))
    assert outcome.verdict is Verdict.FLAGGED


def test_block_marker_matched_in_user_message():
    payload = _err("Invalid parameter", error_user_msg="This link is blocked as unsafe")
    assert classify(400, payload).verdict is Verdict.FLAGGED


def test_unfetchable_page_is_suspicious():
    outcome = classify(400, _err("Error parsing input URL, no data was scraped", code=1609005))
    assert outcome.verdict is Verdict.SUSPICIOUS


def test_transient_error_is_error_not_alert():
    outcome = classify(400, _err("Service temporarily unavailable", code=2, is_transient=True))
    assert outcome.verdict is Verdict.ERROR


def test_rate_limit_code_is_error():
    assert classify(400, _err("Application request limit reached", code=4)).verdict is Verdict.ERROR


def test_bad_credentials_point_at_the_env_vars():
    outcome = classify(400, _err("Error validating application. Invalid application ID.", code=190))
    assert outcome.verdict is Verdict.ERROR
    assert "FB_APP_ID" in (outcome.error or "")


def test_unknown_error_stays_quiet_but_keeps_raw():
    payload = _err("Something entirely new", code=999)
    outcome = classify(400, payload)
    assert outcome.verdict is Verdict.ERROR
    assert outcome.raw == payload


def test_success_status_without_id_is_error():
    assert classify(200, {"unexpected": True}).verdict is Verdict.ERROR


# ── scrape=true responses (shapes verified against the live Graph API) ────────


def test_scraped_page_with_title_is_clean():
    payload = {"url": "https://a.com/", "type": "website", "title": "Chrono Shards"}
    outcome = classify(200, payload)
    assert outcome.verdict is Verdict.CLEAN
    assert "Chrono Shards" in (outcome.summary or "")


def test_page_facebook_could_not_read_is_suspicious():
    # What a domain that no longer resolves returns: no title, no description.
    payload = {"url": "https://gone.com/", "type": "website", "updated_time": "2026-09-24T10:03Z"}
    outcome = classify(200, payload)
    assert outcome.verdict is Verdict.SUSPICIOUS
    assert outcome.summary == "FB не смог прочитать страницу"
    assert outcome.raw == payload


def test_description_or_image_alone_counts_as_read():
    only_description = {"url": "https://a.com/", "description": "..."}
    only_image = {"url": "https://a.com/", "image": [{"url": "x"}]}
    assert classify(200, only_description).verdict is Verdict.CLEAN
    assert classify(200, only_image).verdict is Verdict.CLEAN


def test_empty_payload_is_an_error_not_a_verdict():
    assert classify(200, {}).verdict is Verdict.ERROR

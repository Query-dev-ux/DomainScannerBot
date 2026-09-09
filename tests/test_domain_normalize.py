from __future__ import annotations

import pytest

from domain_scanner.bot.handlers.domains import _normalize_domain


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM", "example.com"),
        ("https://www.example.com/path?x=1", "example.com"),
        ("http://sub.example.co.uk", "sub.example.co.uk"),
        ("  example.io  ", "example.io"),
    ],
)
def test_normalize_ok(raw: str, expected: str) -> None:
    assert _normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", ["not a domain", "localhost", "", "http://", "a..b"])
def test_normalize_rejects(raw: str) -> None:
    assert _normalize_domain(raw) is None

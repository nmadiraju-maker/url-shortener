"""Look-alike (homoglyph) domains are rejected (AC-LOOKALIKE-*)."""
import pytest
from fastapi.testclient import TestClient

from urlshort.errors import InvalidInput
from urlshort.validation import validate_url


@pytest.mark.parametrize("url", [
    "https://p\u0430ypal.com/login",            # Latin "paypal" with a Cyrillic "а"
    "https://xn--pypal-4ve.com/login",          # the same name in its punycode form
    "https://secure.\u0430pple.com/",           # look-alike in one label of several
    "https://xn--zz-.com/",                     # punycode that does not decode
])
def test_mixed_script_hostnames_are_rejected(client: TestClient, url: str) -> None:
    """AC-LOOKALIKE-1"""
    resp = client.post("/api/v1/links", json={"url": url})
    assert resp.status_code == 400 and "writing systems" in resp.json()["error"]["message"]


@pytest.mark.parametrize("url", [
    "https://\u043f\u0440\u0438\u043c\u0435\u0440.\u0440\u0444/",   # Cyrillic only
    "https://\u4f8b\u3048.jp/",                       # Japanese kanji + hiragana: one script group
    "https://xn--e1afmkfd.xn--p1ai/",                  # the Cyrillic name above, in punycode
    "https://caf\u00e9-7.example/menu",                # Latin with an accent and digits
])
def test_single_script_internationalised_names_are_accepted(url: str) -> None:
    """AC-LOOKALIKE-2"""
    assert validate_url(url, max_length=2048).startswith("https://")


def test_lookalike_error_is_an_invalid_input() -> None:
    with pytest.raises(InvalidInput):
        validate_url("https://g\u043e\u043egle.com/", max_length=2048)

import pytest

from urlshort.errors import InvalidInput
from urlshort.validation import KNOWN_SHORTENERS, validate_alias, validate_ttl, validate_url


def url(value: str, **kw: object) -> str:
    return validate_url(value, max_length=2048, **kw)  # type: ignore[arg-type]


# ---------------- URLs: accepted and normalised
@pytest.mark.parametrize("raw,expected", [
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("HTTPS://Example.COM/Path/Case", "https://example.com/Path/Case"),      # host lower-cased, path kept
    ("  http://example.com:8443/x  ", "http://example.com:8443/x"),          # trimmed, port kept
    ("http://8.8.8.8/dns", "http://8.8.8.8/dns"),                            # public IPs are fine
])
def test_valid_urls_are_normalised(raw: str, expected: str) -> None:
    assert url(raw) == expected


# ---------------- URLs: rejected, with the reason
@pytest.mark.parametrize("raw,reason", [
    ("", "required"),
    ("   ", "required"),
    ("https://example.com/" + "a" * 2050, "exceeds"),
    ("ftp://example.com/file", "only http and https"),
    ("javascript:alert(1)", "only http and https"),
    ("https:///no-host", "must include a host"),
    ("https://user:pass@bank.example/login", "credentials"),
    ("http://localhost:8080/", "internal"),
    ("http://app.localhost/", "internal"),
    ("http://metadata.internal/", "internal"),
    ("http://127.0.0.1/admin", "internal"),
    ("http://10.0.0.5/", "internal"),
    ("http://192.168.1.1/", "internal"),
    ("http://169.254.169.254/latest/meta-data", "internal"),                 # cloud metadata service
    ("http://[::1]/", "internal"),
    ("http://0.0.0.0/", "internal"),
    ("https://bit.ly/abc", "shorteners"),
    ("https://www.tinyurl.com/x", "shorteners"),                             # subdomain of a shortener
])
def test_unsafe_or_invalid_urls_rejected(raw: str, reason: str) -> None:
    with pytest.raises(InvalidInput, match=reason):
        url(raw)


def test_blocked_domain_and_its_subdomains() -> None:
    blocked = frozenset({"evil.example"})
    for raw in ("https://evil.example/x", "https://sub.evil.example/x"):
        with pytest.raises(InvalidInput, match="blocked"):
            url(raw, blocked_domains=blocked)
    assert url("https://notevil.example/x", blocked_domains=blocked) == "https://notevil.example/x"


def test_own_host_is_rejected_as_a_target() -> None:
    with pytest.raises(InvalidInput, match="shorteners"):
        url("https://SHO.RT/abc1234", self_host="sho.rt")


def test_shortener_list_is_replaceable() -> None:
    custom = frozenset({"short.example"})
    assert url("https://bit.ly/x", shorteners=custom) == "https://bit.ly/x"
    with pytest.raises(InvalidInput, match="shorteners"):
        url("https://go.short.example/x", shorteners=custom)
    assert "bit.ly" in KNOWN_SHORTENERS


# ---------------- aliases
@pytest.mark.parametrize("alias", ["abc", "spring-sale", "Promo_2026", "x" * 32])
def test_valid_aliases(alias: str) -> None:
    assert validate_alias(alias) == alias


@pytest.mark.parametrize("alias,reason", [
    ("ab", "3-32"), ("x" * 33, "3-32"), ("has space", "3-32"), ("bad/slash", "3-32"), ("emoji-😀", "3-32"),
    ("api", "reserved"), ("HEALTHZ", "reserved"), ("docs", "reserved"), ("metrics", "reserved"),
])
def test_invalid_or_reserved_aliases(alias: str, reason: str) -> None:
    with pytest.raises(InvalidInput, match=reason):
        validate_alias(alias)


# ---------------- TTL
def test_ttl_none_means_permanent() -> None:
    assert validate_ttl(None, max_ttl=60) is None


@pytest.mark.parametrize("ttl", [1, 30, 60])
def test_ttl_in_range(ttl: int) -> None:
    assert validate_ttl(ttl, max_ttl=60) == ttl


@pytest.mark.parametrize("ttl", [0, -5, 61])
def test_ttl_out_of_range(ttl: int) -> None:
    with pytest.raises(InvalidInput, match="between 1 and 60"):
        validate_ttl(ttl, max_ttl=60)

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_URL_QUERY_KEYS = {
    "auth",
    "key",
    "pass",
    "password",
    "passwd",
    "pwd",
    "session",
    "src",
    "token",
    "user",
    "username",
}
_URL_IN_TEXT_RE = re.compile(r"(?P<url>(?:https?|rtsps?)://[^\s'\"<>]+)")
_SENSITIVE_PAIR_RE = re.compile(
    r"(?i)(?P<prefix>(?:[?&;]|\b)"
    r"(?:auth|key|pass|password|passwd|pwd|session|src|token|user|username)=)"
    r"(?P<value>[^&;\s'\"<>]+)"
)


def sanitize_url_for_logs(url: str) -> str:
    """Redact credentials and tokens from camera URLs before logging."""
    try:
        parts = urlsplit(str(url))
        netloc = parts.netloc
        if "@" in netloc:
            netloc = f"REDACTED@{netloc.rsplit('@', 1)[1]}"

        query = urlencode(
            [
                (
                    key,
                    "REDACTED" if key.lower() in SENSITIVE_URL_QUERY_KEYS else value,
                )
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except Exception:
        return "<redacted-url>"


def sanitize_text_for_logs(value: str) -> str:
    """Redact credentials from URLs and URL-like key/value pairs in log text."""

    def replace_url(match):
        return sanitize_url_for_logs(match.group("url"))

    redacted = _URL_IN_TEXT_RE.sub(replace_url, str(value))
    return _SENSITIVE_PAIR_RE.sub(r"\g<prefix>REDACTED", redacted)

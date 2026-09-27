"""Fail-closed research text/URL checks; no network or persistence operations."""
import re
from urllib.parse import urlsplit, parse_qsl, unquote
from magi.storage import check_sensitive, StorageError


def safe_text(value):
    if not isinstance(value, str):
        raise ValueError('Expected text')
    # Known local secrets use the existing boundary. Never echo rejected content.
    try:
        check_sensitive(value)
    except StorageError:
        raise ValueError('Sensitive research content rejected') from None
    if re.search(r'(?i)(?:authorization\s*:|-----BEGIN .*PRIVATE KEY|\b(?:[A-Z_]*(?:API_KEY|CLIENT_ID|CLIENT_SECRET|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD))\s*[=:])', value):
        raise ValueError('Credential-like research content rejected')
    # URLs may appear in prose or nested metadata as well as the dedicated field.
    for match in re.finditer(r'(?i)https?://[^\s<>"\']+', value):
        try:
            parts = urlsplit(unquote(match.group()))
            if parts.username is not None or parts.password is not None or any(
                    re.search(r'(?i)token|secret|password|api[-_]?key|authorization|credential|signature', k)
                    for k,v in parse_qsl(parts.query+'&'+parts.fragment,keep_blank_values=True)):
                raise ValueError()
        except ValueError:
            raise ValueError('Credential-bearing content URL rejected') from None
    return value


def text(value, *, optional=False):
    if value is None and optional:
        return None
    safe_text(value)
    if not value.strip():
        raise ValueError('Nonempty text required')
    return value


def url(value):
    if value is None:
        return None
    text(value)
    safe_text(unquote(value))
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('https','http') or not parts.hostname or parts.username is not None
                or parts.password is not None or any(c.isspace() or ord(c)<32 for c in value)
                or '\\' in value or re.search(r'%(?![0-9A-Fa-f]{2})',value)
                or not parts.netloc or parts.port == 0):
            raise ValueError()
        if any(re.search(r'(?i)token|secret|password|api[-_]?key|authorization|credential|signature', k)
               for k,v in parse_qsl(parts.query+'&'+parts.fragment,keep_blank_values=True)):
            raise ValueError()
    except ValueError:
        raise ValueError('Malformed or credential-bearing URL') from None
    return value

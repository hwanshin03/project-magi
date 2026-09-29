"""Small syntactic compatibility helpers; no new language/date semantics."""
import re
from ..security import text


def normalize_language(value):
    # BCP-47 casing for the existing language[-region] model subset only.
    # Scripts/extensions/private-use tags require a separate model contract.
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z]{2})?', value):
        raise ValueError('Malformed language tag')
    parts = value.split('-')
    return parts[0].lower() + ('-' + parts[1].upper() if len(parts) == 2 else '')


def date_preview(value):
    """At most 80 ASCII characters of date-only syntax, otherwise a fixed marker.

    Check the entire value for known secrets before copying anything. Arbitrary
    prose, URLs, markup, controls, overlong values and unknown words are redacted.
    This is diagnostic sanitization, not an additional accepted date format.
    """
    redacted = '[REDACTED]'
    if not isinstance(value, str) or not value or len(value) > 80:
        return redacted
    try:
        text(value)
    except ValueError:
        return redacted
    if not re.fullmatch(r'[A-Za-z0-9 ,:+./-]+', value) or not re.search(r'\d', value):
        return redacted
    words = {'mon','tue','wed','thu','fri','sat','sun','jan','feb','mar','apr','may','jun',
             'jul','aug','sep','oct','nov','dec','gmt','utc','ut','est','edt','cst','cdt',
             'mst','mdt','pst','pdt','t','z'}
    if any(word.lower() not in words for word in re.findall(r'[A-Za-z]+', value)):
        return redacted
    return value

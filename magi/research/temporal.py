"""Public availability, not collection time, bounds analytical knowledge.

Dates retain calendar precision and use the caller's explicit boundary calendar.
No wall clock, provider calls, source mutation or inferred publication timestamps.
"""
from datetime import date, datetime
from .models import instant

TEMPORAL_VERSION = 'public-availability-v1'
DATE_PRECISIONS = ('DATE', 'date; midnight UTC convention', 'date; midnight KST convention')


def publication_time(obj):
    value = getattr(obj, 'published_at', None)
    meta = getattr(obj, 'metadata', {})
    if meta.get('publication_precision') in DATE_PRECISIONS:
        if isinstance(value, datetime): return value.date()
        if value is None and meta.get('published_at'): return date.fromisoformat(meta['published_at'])
    return value


def relation(value, boundary):
    instant(boundary)
    if value is None: return 'UNKNOWN'
    if isinstance(value, datetime):
        instant(value)
        return 'AFTER' if value > boundary else 'AT_OR_BEFORE'
    if type(value) is not date: raise ValueError('Invalid temporal value')
    return 'DATE_AFTER' if value > boundary.date() else 'DATE_BEFORE' if value < boundary.date() else 'SAME_DATE_UNORDERED'


def availability(obj, boundary):
    """Retrieval proves possession only when no publication time is supplied.

    In particular, retrieval never resolves an explicitly date-only same-day
    publication or overrides a future publication. Effective/period dates do not
    establish public availability.
    """
    value = publication_time(obj)
    field = 'published_at' if value is not None else 'retrieved_at'
    if value is None: value = getattr(obj, 'retrieved_at', None)
    return field, relation(value, boundary)


def available(obj, boundary):
    return availability(obj, boundary)[1] in ('AT_OR_BEFORE', 'DATE_BEFORE')


def derived_available(obj, boundary):
    """Use only after validating every source in the derivation's closure.

    Extraction/retrieval is provenance. Explicit measurement/publication bounds
    and creation of authored assertions still constrain derived records. Pure
    snapshot calculation time is not an assertion of new public information.
    """
    values = (publication_time(obj), getattr(obj, 'as_of', None), getattr(obj, 'created_at', None))
    return all(relation(v, boundary) in ('AT_OR_BEFORE', 'DATE_BEFORE') for v in values if v is not None)

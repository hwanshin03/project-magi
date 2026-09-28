"""Explicit caller clock. Age is metadata, never an investment weight."""
from datetime import timedelta
from ..models import instant
from .models import Freshness


def age(timestamp, as_of):
    instant(as_of);instant(timestamp,True)
    if timestamp is None: return None
    if timestamp>as_of: raise ValueError('Future timestamp cannot have news age')
    return as_of-timestamp


def freshness(timestamp, as_of):
    elapsed=age(timestamp,as_of)
    if elapsed is None: return Freshness.UNKNOWN
    for hours,kind in ((1,Freshness.BREAKING),(24,Freshness.RECENT),(168,Freshness.CURRENT),(720,Freshness.AGING)):
        if elapsed<=timedelta(hours=hours): return kind
    return Freshness.STALE

"""One source per explicit query. No automatic live transport, paging or follow-up."""
from collections import OrderedDict
from datetime import datetime, timezone
from ..models import instant
from .catalog import RegulatoryError, source_spec
from .parsing import parse, RegulatoryParseResult, SkippedRegulatoryItem
from .service import deduplicate_items


class GovernmentProvider:
    def __init__(self, *, transport=None, now=None, capacity=3, ttl=300):
        if type(capacity) is not int or not 1<=capacity<=3 or type(ttl) is not int or not 0<=ttl<=3600:
            raise RegulatoryError('INVALID_CACHE')
        self.transport=transport; self.now=now or (lambda:datetime.now(timezone.utc))
        self.capacity,self.ttl,self.cache=capacity,ttl,OrderedDict()
        self.last_diagnostics=()

    def parse(self, source_key, raw, *, retrieved_at):
        result=parse(source_key,raw,retrieved_at)
        self.last_diagnostics=result.skipped
        return result

    def clear_cache(self):
        self.cache.clear(); self.last_diagnostics=()

    def fetch_items(self, query):
        source_spec(query.source_key)
        if self.transport is None: raise RegulatoryError('TRANSPORT_NOT_CONFIGURED')
        self.last_diagnostics=()
        now=self.now(); instant(now)
        cached=self.cache.get(query.source_key)
        if cached and 0<=(now-cached[0]).total_seconds()<self.ttl:
            result=cached[1]; self.cache.move_to_end(query.source_key)
        else:
            raw=self.transport.get(query.source_key)
            retrieved_at=self.now(); instant(retrieved_at)
            result=self.parse(query.source_key,raw,retrieved_at=retrieved_at)
            self.cache[query.source_key]=(retrieved_at,result)
            self.cache.move_to_end(query.source_key)
            while len(self.cache)>self.capacity: self.cache.popitem(last=False)
        items=[]; skipped=list(result.skipped)
        for index,item in enumerate(deduplicate_items(result)):
            published=item.published_at
            cutoff=query.as_of if isinstance(published,datetime) else query.as_of.astimezone(timezone.utc).date()
            if published is not None and published>cutoff:
                skipped.append(SkippedRegulatoryItem(index,'REGULATORY_AFTER_QUERY_DATE')); continue
            items.append(item)
        # Stable operational bound only; no relevance/materiality ranking.
        for index in range(query.limit,len(items)):
            skipped.append(SkippedRegulatoryItem(index,'REGULATORY_QUERY_LIMIT'))
        self.last_diagnostics=tuple(skipped)
        return RegulatoryParseResult(tuple(items[:query.limit]),tuple(skipped))

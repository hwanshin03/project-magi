"""Injected source transport; bounded cache holds normalized records, never raw responses."""
from collections import OrderedDict
from datetime import datetime, timezone
from ..models import instant
from .catalog import source_spec, CompanySourceError
from .parsing import parse


class CompanyProvider:
    provider = None

    def __init__(self, source_ids, *, transport=None, now=None, capacity=8, ttl=300):
        source_ids = tuple(source_ids)
        self.specs = tuple(source_spec(s) for s in source_ids)
        if not self.specs or len(self.specs) > 10 or len(set(source_ids)) != len(self.specs):
            raise CompanySourceError('INVALID_SOURCES')
        if any(s.provider != self.provider for s in self.specs): raise CompanySourceError('IDENTITY_MISMATCH')
        if type(capacity) is not int or not 1 <= capacity <= 32 or type(ttl) is not int or not 0 <= ttl <= 3600:
            raise CompanySourceError('INVALID_CACHE')
        self.transport = transport
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.capacity, self.ttl, self.cache = capacity, ttl, OrderedDict()
        self.last_diagnostics = {}

    def parse(self, source_id, raw, *, retrieved_at):
        spec = source_spec(source_id)
        if spec.provider != self.provider: raise CompanySourceError('IDENTITY_MISMATCH')
        result = parse(spec, raw, retrieved_at)
        self.last_diagnostics[source_id] = result.skipped
        return result

    def clear_cache(self):
        self.cache.clear()
        self.last_diagnostics.clear()

    def fetch_items(self, query):
        if query.ticker != self.specs[0].ticker: raise CompanySourceError('IDENTITY_MISMATCH')
        if self.transport is None: raise CompanySourceError('TRANSPORT_NOT_CONFIGURED')
        self.last_diagnostics = {}
        records = []
        for spec in self.specs:
            now = self.now(); instant(now)
            cached = self.cache.get(spec.source_id)
            if cached and 0 <= (now - cached[0]).total_seconds() < self.ttl:
                self.cache.move_to_end(spec.source_id)
                items = cached[1]
                self.last_diagnostics[spec.source_id] = items.skipped
            else:
                raw = self.transport.get(spec.source_id)
                retrieved_at = self.now(); instant(retrieved_at)
                items = self.parse(spec.source_id, raw, retrieved_at=retrieved_at)
                self.cache[spec.source_id] = (retrieved_at, items)
                self.cache.move_to_end(spec.source_id)
                while len(self.cache) > self.capacity: self.cache.popitem(last=False)
            records.extend(i for i in items if (i.published_at is None or i.published_at <= query.as_of)
                           and (query.since is None or i.published_at is not None and i.published_at >= query.since))
        from .service import deduplicate_items
        # A deterministic ingestion bound, never a materiality ranking.
        return tuple(sorted(deduplicate_items(records), key=lambda i: (i.published_at or i.retrieved_at, i.item_id), reverse=True)[:query.limit])

    def fetch(self, query):
        from .service import articles_for
        return articles_for(self.fetch_items(query))

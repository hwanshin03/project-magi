"""Provider-neutral ingestion contract; consumers need no parsing knowledge."""
from typing import Protocol, Tuple
from ..news.base import NewsQuery
from .models import OfficialCompanyItem


class OfficialCompanyProvider(Protocol):
    def fetch_items(self, query: NewsQuery) -> Tuple[OfficialCompanyItem, ...]: ...

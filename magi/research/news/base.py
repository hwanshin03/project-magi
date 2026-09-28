"""Normalized future-provider contract. No HTTP implementation is included."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Tuple, Optional
from ..models import instant
from ..security import text
from .models import NewsArticle, normalize_ticker


@dataclass(frozen=True)
class NewsQuery:
    ticker: str
    subject: str
    as_of: datetime
    since: Optional[datetime] = None
    limit: int = 100

    def __post_init__(self):
        object.__setattr__(self,'ticker',normalize_ticker(self.ticker));text(self.subject)
        instant(self.as_of);instant(self.since,True)
        if self.since and self.since>self.as_of: raise ValueError('Invalid query interval')
        if type(self.limit) is not int or not 1<=self.limit<=1000: raise ValueError('Invalid query bound')


class NewsProvider(Protocol):
    def fetch(self, query: NewsQuery) -> Tuple[NewsArticle,...]:
        """Return normalized articles or a sanitized provider error. Never raw JSON."""
        ...

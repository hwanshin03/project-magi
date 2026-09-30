"""Agency queries never assume company relevance."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from ..models import instant
from .catalog import source_spec, RegulatoryError


@dataclass(frozen=True)
class RegulatoryQuery:
    source_key: str
    as_of: datetime
    limit: int = 20

    def __post_init__(self):
        source_spec(self.source_key); instant(self.as_of)
        if type(self.limit) is not int or not 1 <= self.limit <= 200:
            raise RegulatoryError('INVALID_LIMIT')


class RegulatoryProvider(Protocol):
    def fetch_items(self, query: RegulatoryQuery):
        """Return bounded records and explicit skipped-item diagnostics."""
        ...

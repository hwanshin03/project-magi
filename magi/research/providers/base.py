"""Provider-neutral results and normalized identities; no portfolio dependencies."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from functools import wraps
from typing import Optional, Protocol
import re
from ..models import FrozenMetadata, freeze, instant
from ..security import text


class ErrorCode(str, Enum):
    CONFIGURATION='CONFIGURATION'
    NOT_FOUND='NOT_FOUND'
    AMBIGUOUS='AMBIGUOUS'
    NO_DATA='NO_DATA'
    AUTHENTICATION='AUTHENTICATION'
    INVALID_REQUEST='INVALID_REQUEST'
    INVALID_RESPONSE='INVALID_RESPONSE'
    RATE_LIMITED='RATE_LIMITED'
    UNAVAILABLE='UNAVAILABLE'


class ResearchError(Exception):
    def __init__(self,code):
        self.code=ErrorCode(code)
        super().__init__(self.code.value)


@dataclass(frozen=True)
class ResearchResult:
    data: object = None
    error: Optional[ErrorCode] = None

    def require(self):
        if self.error: raise ResearchError(self.error)
        return self.data


def boundary(method):
    @wraps(method)
    def wrapped(*args,**kwargs):
        try: return ResearchResult(method(*args,**kwargs))
        except ResearchError as error: return ResearchResult(error=error.code)
        except (ValueError,KeyError,TypeError,IndexError,OverflowError,AttributeError):
            return ResearchResult(error=ErrorCode.INVALID_RESPONSE)
    return wrapped


@dataclass(frozen=True)
class Issuer:
    provider: str
    provider_issuer_id: str
    company_name: str
    market: str
    retrieved_at: datetime
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    modified_at: Optional[str] = None

    def __post_init__(self):
        if self.provider not in ('SEC','DART'): raise ValueError('Invalid issuer provider')
        pattern=r'\d{10}' if self.provider=='SEC' else r'\d{8}'
        if not re.fullmatch(pattern,self.provider_issuer_id): raise ValueError('Invalid issuer identifier')
        if self.market!=('US' if self.provider=='SEC' else 'KR'): raise ValueError('Invalid issuer market')
        text(self.company_name);instant(self.retrieved_at)
        text(self.exchange,optional=True);text(self.modified_at,optional=True)
        if self.ticker is not None:
            pattern=r'[A-Z][A-Z0-9.-]{0,15}' if self.provider=='SEC' else r'\d{6}'
            if not re.fullmatch(pattern,self.ticker): raise ValueError('Invalid issuer ticker')

    @property
    def issuer_id(self):
        return self.provider+':'+self.provider_issuer_id


@dataclass(frozen=True)
class CompanyProfile:
    issuer: Issuer
    fields: FrozenMetadata
    retrieved_at: datetime

    def __post_init__(self):
        instant(self.retrieved_at)
        object.__setattr__(self,'fields',freeze(self.fields))


@dataclass(frozen=True)
class FinancialFacts:
    issuer: Issuer
    sources: tuple
    evidence_items: tuple
    retrieved_at: datetime


class ResearchProvider(Protocol):
    def resolve_issuer(self,identifier): ...
    def get_company_profile(self,identifier): ...
    def list_filings(self,identifier,**options): ...
    def get_financial_facts(self,identifier,**options): ...
    def build_evidence_pack(self,identifier,**options): ...


def utcnow():
    return datetime.now(timezone.utc)

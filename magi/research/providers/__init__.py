"""Explicitly constructed official-source adapters; import performs no requests."""
from .base import ResearchProvider, ResearchResult, ResearchError, ErrorCode, Issuer, CompanyProfile, FinancialFacts
from .sec import SECProvider
from .dart import DARTProvider

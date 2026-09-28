"""Explicit entity annotations only; incidental mentions default to MENTION_ONLY."""
from .models import normalize_ticker, Relevance


def relevance(article,ticker):
    ticker=normalize_ticker(ticker)
    if ticker not in article.tickers: return None
    return Relevance(article.classification.relevance.get(ticker,Relevance.MENTION_ONLY.value))

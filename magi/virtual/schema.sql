-- Dedicated virtual schema v1; never used by magi.storage.Database.
CREATE TABLE virtual_portfolios (
    portfolio_id TEXT PRIMARY KEY,
    currency TEXT NOT NULL CHECK(currency = 'USD'),
    UNIQUE(portfolio_id, currency)
);
CREATE TABLE virtual_instruments (
    instrument_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market = 'US'),
    currency TEXT NOT NULL CHECK(currency = 'USD'),
    UNIQUE(symbol, market, currency),
    UNIQUE(instrument_id, currency)
);
CREATE TABLE virtual_events (
    portfolio_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('FUNDING','BUY','SELL')),
    currency TEXT NOT NULL CHECK(currency = 'USD'),
    amount TEXT NOT NULL,
    fill_id TEXT,
    instrument_id TEXT,
    quantity TEXT,
    price TEXT,
    fees TEXT,
    provenance_id TEXT,
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_json TEXT NOT NULL,
    PRIMARY KEY(portfolio_id, sequence),
    UNIQUE(portfolio_id, idempotency_key),
    UNIQUE(portfolio_id, fill_id),
    FOREIGN KEY(portfolio_id, currency) REFERENCES virtual_portfolios(portfolio_id, currency),
    FOREIGN KEY(instrument_id, currency) REFERENCES virtual_instruments(instrument_id, currency),
    CHECK(effective_at = recorded_at),
    CHECK((kind = 'FUNDING' AND fill_id IS NULL AND instrument_id IS NULL AND quantity IS NULL
        AND price IS NULL AND fees IS NULL AND provenance_id IS NULL)
        OR (kind IN ('BUY','SELL') AND fill_id IS NOT NULL AND instrument_id IS NOT NULL
        AND quantity IS NOT NULL AND price IS NOT NULL AND fees IS NOT NULL AND provenance_id IS NOT NULL))
);
CREATE UNIQUE INDEX virtual_one_funding ON virtual_events(portfolio_id) WHERE kind = 'FUNDING';
CREATE TRIGGER virtual_events_no_update BEFORE UPDATE ON virtual_events
BEGIN SELECT RAISE(ABORT, 'Immutable virtual history'); END;
CREATE TRIGGER virtual_events_no_delete BEFORE DELETE ON virtual_events
BEGIN SELECT RAISE(ABORT, 'Immutable virtual history'); END;
CREATE TRIGGER virtual_portfolios_no_update BEFORE UPDATE ON virtual_portfolios
BEGIN SELECT RAISE(ABORT, 'Immutable virtual identity'); END;
CREATE TRIGGER virtual_portfolios_no_delete BEFORE DELETE ON virtual_portfolios
BEGIN SELECT RAISE(ABORT, 'Immutable virtual identity'); END;
CREATE TRIGGER virtual_instruments_no_update BEFORE UPDATE ON virtual_instruments
BEGIN SELECT RAISE(ABORT, 'Immutable virtual instrument'); END;
CREATE TRIGGER virtual_instruments_no_delete BEFORE DELETE ON virtual_instruments
BEGIN SELECT RAISE(ABORT, 'Immutable virtual instrument'); END;
PRAGMA application_id = 1297503281;
PRAGMA user_version = 1;

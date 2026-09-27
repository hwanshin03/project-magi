-- Separate events preserve the v2 trade table and its BUY/SELL meaning verbatim.
CREATE TABLE portfolio_opening_balances (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    as_of TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    broker_provider TEXT NOT NULL CHECK (broker_provider != 'MANUAL'),
    broker_account_ref TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_name TEXT,
    market TEXT NOT NULL CHECK (length(market) > 0),
    currency TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action = 'OPENING_BALANCE'),
    quantity TEXT NOT NULL,
    opening_unit_cost TEXT NOT NULL,
    opening_book_cost TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source = 'BROKER_SNAPSHOT'),
    history_completeness TEXT NOT NULL CHECK (history_completeness = 'OPENING_BALANCE_HISTORY'),
    notes TEXT NOT NULL DEFAULT '',
    external_reference TEXT,
    UNIQUE (broker_provider, broker_account_ref, symbol, market, currency)
);
CREATE TRIGGER opening_no_update BEFORE UPDATE ON portfolio_opening_balances
BEGIN SELECT RAISE(ABORT, 'Opening balances are append-only'); END;
CREATE TRIGGER opening_no_delete BEFORE DELETE ON portfolio_opening_balances
BEGIN SELECT RAISE(ABORT, 'Opening balances are append-only'); END;
CREATE TRIGGER opening_no_prior_trades BEFORE INSERT ON portfolio_opening_balances
WHEN EXISTS (SELECT 1 FROM portfolio_transactions t
    WHERE t.broker_provider = NEW.broker_provider AND t.broker_account_ref = NEW.broker_account_ref
      AND t.symbol = NEW.symbol AND t.market = NEW.market AND t.currency = NEW.currency)
BEGIN SELECT RAISE(ABORT, 'Instrument already has tracked history'); END;
CREATE TRIGGER trade_not_before_opening BEFORE INSERT ON portfolio_transactions
WHEN EXISTS (SELECT 1 FROM portfolio_opening_balances o
    WHERE o.broker_provider = NEW.broker_provider AND o.broker_account_ref = NEW.broker_account_ref
      AND o.symbol = NEW.symbol AND o.market = NEW.market AND o.currency = NEW.currency
      AND NEW.timestamp < o.as_of)
BEGIN SELECT RAISE(ABORT, 'Trade precedes tracking start'); END;
PRAGMA user_version = 3;

-- Add identity without rewriting existing transaction values or dropping triggers.
ALTER TABLE portfolio_transactions ADD COLUMN broker_provider TEXT NOT NULL DEFAULT 'MANUAL';
ALTER TABLE portfolio_transactions ADD COLUMN broker_account_ref TEXT NOT NULL DEFAULT 'DEFAULT';
CREATE INDEX transaction_account_instrument ON portfolio_transactions
    (broker_provider, broker_account_ref, symbol, market, currency, timestamp, sequence);
CREATE INDEX transaction_external_reference ON portfolio_transactions
    (broker_provider, broker_account_ref, external_reference);
PRAGMA user_version = 2;

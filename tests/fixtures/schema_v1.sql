-- Schema v1. JSON stores structured lists and the voting snapshot, never credentials.
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    question TEXT NOT NULL,
    final_action TEXT NOT NULL CHECK (final_action IN
        ('BUY_APPROVED','SELL_APPROVED','HOLD','NO_CONSENSUS','INSUFFICIENT_PARTICIPATION')),
    voting_version TEXT NOT NULL CHECK (voting_version = '2of3-v1'),
    voting_json TEXT NOT NULL,
    explanation TEXT
);
CREATE INDEX IF NOT EXISTS analysis_recent ON analysis_runs(timestamp DESC, run_id);
CREATE INDEX IF NOT EXISTS analysis_action ON analysis_runs(final_action, timestamp DESC);

CREATE TABLE IF NOT EXISTS analysis_agents (
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id),
    agent TEXT NOT NULL CHECK (agent IN ('Melchior','Balthasar','Casper')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    position TEXT CHECK (position IN ('STRONG_BUY','BUY','HOLD','SELL','STRONG_SELL','ABSTAIN')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    reasoning TEXT NOT NULL,
    key_risks_json TEXT NOT NULL,
    evidence_gaps_json TEXT NOT NULL,
    changed_position INTEGER CHECK (changed_position IN (0,1)),
    availability TEXT NOT NULL CHECK (availability IN ('AVAILABLE','STALE','UNAVAILABLE')),
    error TEXT,
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    http_status INTEGER,
    PRIMARY KEY (run_id, agent),
    CHECK ((availability = 'UNAVAILABLE' AND position IS NULL AND confidence = 0)
        OR (availability IN ('AVAILABLE','STALE') AND position IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS agent_position ON analysis_agents(agent, position, run_id);

CREATE TABLE IF NOT EXISTS portfolio_transactions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    timestamp TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_name TEXT,
    market TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    quantity TEXT NOT NULL,
    price_per_share TEXT NOT NULL,
    fees TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    linked_analysis_run_id TEXT REFERENCES analysis_runs(run_id),
    external_reference TEXT
);
CREATE INDEX IF NOT EXISTS transaction_instrument ON portfolio_transactions
    (symbol, currency, market, timestamp, sequence);
CREATE INDEX IF NOT EXISTS transaction_analysis ON portfolio_transactions(linked_analysis_run_id);

CREATE TRIGGER IF NOT EXISTS analysis_no_update BEFORE UPDATE ON analysis_runs
BEGIN SELECT RAISE(ABORT, 'Analysis records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS analysis_no_delete BEFORE DELETE ON analysis_runs
BEGIN SELECT RAISE(ABORT, 'Analysis records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS agents_no_update BEFORE UPDATE ON analysis_agents
BEGIN SELECT RAISE(ABORT, 'Agent snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS agents_no_delete BEFORE DELETE ON analysis_agents
BEGIN SELECT RAISE(ABORT, 'Agent snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS transaction_no_update BEFORE UPDATE ON portfolio_transactions
BEGIN SELECT RAISE(ABORT, 'Transactions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS transaction_no_delete BEFORE DELETE ON portfolio_transactions
BEGIN SELECT RAISE(ABORT, 'Transactions are append-only'); END;
PRAGMA user_version = 1;

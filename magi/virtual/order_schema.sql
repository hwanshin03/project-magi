-- Additive virtual v2 foundation. No reservation, admission or settlement writer.
CREATE TABLE virtual_orders (
    order_id TEXT NOT NULL PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    currency TEXT NOT NULL CHECK(currency = 'USD'),
    submission_operation_id TEXT NOT NULL UNIQUE,
    initial_json TEXT NOT NULL CHECK(typeof(initial_json) = 'text' AND length(initial_json) <= 32768),
    UNIQUE(portfolio_id, order_id),
    FOREIGN KEY(portfolio_id, currency) REFERENCES virtual_portfolios(portfolio_id, currency),
    FOREIGN KEY(instrument_id, currency) REFERENCES virtual_instruments(instrument_id, currency),
    FOREIGN KEY(portfolio_id, order_id, submission_operation_id)
        REFERENCES virtual_order_commands(portfolio_id, order_id, operation_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE virtual_order_commands (
    operation_id TEXT NOT NULL PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL CHECK(operation_kind IN ('SUBMIT','TRANSITION')),
    idempotency_key TEXT NOT NULL,
    result_revision INTEGER NOT NULL CHECK(typeof(result_revision) = 'integer' AND result_revision > 0),
    UNIQUE(portfolio_id, actor_id, operation_kind, idempotency_key),
    UNIQUE(portfolio_id, order_id, operation_id),
    UNIQUE(portfolio_id, order_id, result_revision),
    FOREIGN KEY(portfolio_id, order_id) REFERENCES virtual_orders(portfolio_id, order_id),
    FOREIGN KEY(portfolio_id, order_id, result_revision, operation_id)
        REFERENCES virtual_order_events(portfolio_id, order_id, revision, command_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK((operation_kind = 'SUBMIT' AND result_revision = 1)
        OR (operation_kind = 'TRANSITION' AND result_revision > 1))
);
CREATE TABLE virtual_order_events (
    event_id TEXT NOT NULL UNIQUE,
    portfolio_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision > 0),
    command_id TEXT NOT NULL,
    prior_state TEXT CHECK(prior_state IN ('ACCEPTED','FROZEN','AWAITING_PRICE','CANCELLED','EXPIRED')),
    next_state TEXT NOT NULL CHECK(next_state IN ('ACCEPTED','FROZEN','AWAITING_PRICE','CANCELLED','EXPIRED')),
    effective_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL CHECK(recorded_at >= effective_at),
    event_json TEXT NOT NULL CHECK(typeof(event_json) = 'text' AND length(event_json) <= 32768),
    PRIMARY KEY(portfolio_id, order_id, revision),
    UNIQUE(portfolio_id, order_id, revision, command_id),
    FOREIGN KEY(portfolio_id, order_id) REFERENCES virtual_orders(portfolio_id, order_id),
    FOREIGN KEY(portfolio_id, order_id, command_id)
        REFERENCES virtual_order_commands(portfolio_id, order_id, operation_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK((revision = 1 AND prior_state IS NULL AND next_state = 'ACCEPTED')
        OR (revision > 1 AND prior_state IS NOT NULL AND next_state != 'ACCEPTED'))
);
CREATE TRIGGER virtual_order_event_sequence BEFORE INSERT ON virtual_order_events
BEGIN
    SELECT CASE WHEN NEW.revision != 1 + COALESCE((SELECT MAX(revision) FROM virtual_order_events
        WHERE portfolio_id = NEW.portfolio_id AND order_id = NEW.order_id), 0)
        THEN RAISE(ABORT, 'Invalid order revision') END;
    SELECT CASE WHEN NEW.revision > 1 AND NOT EXISTS (SELECT 1 FROM virtual_order_events
        WHERE portfolio_id = NEW.portfolio_id AND order_id = NEW.order_id AND revision = NEW.revision - 1
        AND next_state = NEW.prior_state AND effective_at <= NEW.effective_at AND recorded_at <= NEW.recorded_at)
        THEN RAISE(ABORT, 'Invalid order history') END;
END;
CREATE TRIGGER virtual_orders_no_update BEFORE UPDATE ON virtual_orders
BEGIN SELECT RAISE(ABORT, 'Immutable order intent'); END;
CREATE TRIGGER virtual_orders_no_delete BEFORE DELETE ON virtual_orders
BEGIN SELECT RAISE(ABORT, 'Immutable order intent'); END;
CREATE TRIGGER virtual_order_commands_no_update BEFORE UPDATE ON virtual_order_commands
BEGIN SELECT RAISE(ABORT, 'Immutable order command'); END;
CREATE TRIGGER virtual_order_commands_no_delete BEFORE DELETE ON virtual_order_commands
BEGIN SELECT RAISE(ABORT, 'Immutable order command'); END;
CREATE TRIGGER virtual_order_events_no_update BEFORE UPDATE ON virtual_order_events
BEGIN SELECT RAISE(ABORT, 'Immutable order history'); END;
CREATE TRIGGER virtual_order_events_no_delete BEFORE DELETE ON virtual_order_events
BEGIN SELECT RAISE(ABORT, 'Immutable order history'); END;
PRAGMA user_version = 2;

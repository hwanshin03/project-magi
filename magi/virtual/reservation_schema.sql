-- Additive v3. Immutable synthetic CREATE effects only; no release or settlement.
CREATE TABLE virtual_reservation_operations (
    operation_id TEXT NOT NULL PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(typeof(sequence) = 'integer' AND sequence > 0),
    order_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    creation_event_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('RESERVED','INSUFFICIENT_CASH','INSUFFICIENT_SHARES')),
    result_reservation_id TEXT,
    request_json TEXT NOT NULL CHECK(typeof(request_json) = 'text' AND length(request_json) <= 32768),
    UNIQUE(portfolio_id, actor_id, idempotency_key),
    UNIQUE(portfolio_id, sequence),
    UNIQUE(operation_id, result_reservation_id),
    FOREIGN KEY(portfolio_id, order_id, command_id)
        REFERENCES virtual_order_commands(portfolio_id, order_id, operation_id),
    FOREIGN KEY(creation_event_id) REFERENCES virtual_order_events(event_id),
    FOREIGN KEY(operation_id, result_reservation_id)
        REFERENCES virtual_order_reservations(operation_id, reservation_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK((outcome = 'RESERVED' AND result_reservation_id IS NOT NULL)
        OR (outcome != 'RESERVED' AND result_reservation_id IS NULL))
);
CREATE TABLE virtual_order_reservations (
    reservation_id TEXT NOT NULL PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    portfolio_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    creation_event_id TEXT NOT NULL UNIQUE,
    resource TEXT NOT NULL CHECK(resource IN ('CASH','SHARES')),
    amount TEXT NOT NULL CHECK(typeof(amount) = 'text'),
    currency TEXT,
    status TEXT NOT NULL CHECK(status = 'ACTIVE'),
    reservation_json TEXT NOT NULL CHECK(typeof(reservation_json) = 'text' AND length(reservation_json) <= 32768),
    UNIQUE(portfolio_id, order_id),
    UNIQUE(operation_id, reservation_id),
    FOREIGN KEY(portfolio_id, order_id, command_id)
        REFERENCES virtual_order_commands(portfolio_id, order_id, operation_id),
    FOREIGN KEY(instrument_id) REFERENCES virtual_instruments(instrument_id),
    FOREIGN KEY(creation_event_id) REFERENCES virtual_order_events(event_id),
    FOREIGN KEY(operation_id, reservation_id)
        REFERENCES virtual_reservation_operations(operation_id, result_reservation_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK((resource = 'CASH' AND currency IS NOT NULL AND currency = 'USD')
        OR (resource = 'SHARES' AND currency IS NULL))
);
CREATE TRIGGER virtual_reservation_operations_no_update BEFORE UPDATE ON virtual_reservation_operations
BEGIN SELECT RAISE(ABORT, 'Immutable reservation outcome'); END;
CREATE TRIGGER virtual_reservation_operations_no_delete BEFORE DELETE ON virtual_reservation_operations
BEGIN SELECT RAISE(ABORT, 'Immutable reservation outcome'); END;
CREATE TRIGGER virtual_order_reservations_no_update BEFORE UPDATE ON virtual_order_reservations
BEGIN SELECT RAISE(ABORT, 'Immutable reservation'); END;
CREATE TRIGGER virtual_order_reservations_no_delete BEFORE DELETE ON virtual_order_reservations
BEGIN SELECT RAISE(ABORT, 'Immutable reservation'); END;
PRAGMA user_version = 3;

-- Additive v4. Existing v1/v2/v3 objects and records retain their exact meaning.
CREATE TABLE virtual_transition_baselines (
    portfolio_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision > 0),
    PRIMARY KEY(portfolio_id, order_id),
    FOREIGN KEY(portfolio_id, order_id, revision)
        REFERENCES virtual_order_events(portfolio_id, order_id, revision)
);
CREATE TABLE virtual_transition_outcomes (
    command_id TEXT NOT NULL PRIMARY KEY,
    portfolio_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    reservation_id TEXT NOT NULL,
    released INTEGER NOT NULL CHECK(released IN (0,1)),
    release_id TEXT,
    request_json TEXT NOT NULL CHECK(typeof(request_json) = 'text' AND length(request_json) <= 32768),
    UNIQUE(portfolio_id, actor_id, idempotency_key),
    UNIQUE(command_id, release_id),
    FOREIGN KEY(portfolio_id, order_id, command_id)
        REFERENCES virtual_order_commands(portfolio_id, order_id, operation_id),
    FOREIGN KEY(event_id) REFERENCES virtual_order_events(event_id),
    FOREIGN KEY(reservation_id) REFERENCES virtual_order_reservations(reservation_id),
    FOREIGN KEY(command_id, release_id)
        REFERENCES virtual_reservation_releases(command_id, reservation_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK((released = 1 AND release_id IS NOT NULL AND release_id = reservation_id)
        OR (released = 0 AND release_id IS NULL))
);
CREATE TABLE virtual_reservation_releases (
    reservation_id TEXT NOT NULL PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL UNIQUE,
    portfolio_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    resource TEXT NOT NULL CHECK(resource IN ('CASH','SHARES')),
    amount TEXT NOT NULL CHECK(typeof(amount) = 'text'),
    UNIQUE(command_id, reservation_id),
    FOREIGN KEY(reservation_id) REFERENCES virtual_order_reservations(reservation_id),
    FOREIGN KEY(portfolio_id, order_id, command_id)
        REFERENCES virtual_order_commands(portfolio_id, order_id, operation_id),
    FOREIGN KEY(event_id) REFERENCES virtual_order_events(event_id),
    FOREIGN KEY(command_id, reservation_id)
        REFERENCES virtual_transition_outcomes(command_id, release_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TRIGGER virtual_transition_baselines_no_update BEFORE UPDATE ON virtual_transition_baselines
BEGIN SELECT RAISE(ABORT, 'Immutable transition baseline'); END;
CREATE TRIGGER virtual_transition_baselines_no_delete BEFORE DELETE ON virtual_transition_baselines
BEGIN SELECT RAISE(ABORT, 'Immutable transition baseline'); END;
CREATE TRIGGER virtual_transition_outcomes_no_update BEFORE UPDATE ON virtual_transition_outcomes
BEGIN SELECT RAISE(ABORT, 'Immutable transition outcome'); END;
CREATE TRIGGER virtual_transition_outcomes_no_delete BEFORE DELETE ON virtual_transition_outcomes
BEGIN SELECT RAISE(ABORT, 'Immutable transition outcome'); END;
CREATE TRIGGER virtual_reservation_releases_no_update BEFORE UPDATE ON virtual_reservation_releases
BEGIN SELECT RAISE(ABORT, 'Immutable reservation release'); END;
CREATE TRIGGER virtual_reservation_releases_no_delete BEFORE DELETE ON virtual_reservation_releases
BEGIN SELECT RAISE(ABORT, 'Immutable reservation release'); END;
PRAGMA user_version = 4;

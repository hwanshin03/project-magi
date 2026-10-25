# Phase 8.2 — Isolated virtual accounting core

This implements only the accounting foundation of the approved
[closed-beta architecture](beta-paper-trading-architecture.md). It is **not a
launch-ready Paper Trading application**. There is no public API, authentication,
team authorization, order queue, quote provider, reservation system, or deployment.
All fill examples and tests are synthetic; no live prices or broker data are used.

## Scope and storage

`magi.virtual` is a separate package with standard-library dependencies only:

- `models.py`: immutable inputs, events and reconstructed values; validation and
  bounded Decimal arithmetic.
- `accounting.py`: pure ordered event replay, cash, positions and realized P&L.
- `storage.py`: explicitly configured virtual SQLite transactions and durable
  idempotency. No connection exists at import time.
- `schema.sql`: virtual-only portfolios, instrument allowlist, immutable events,
  foreign keys, uniqueness constraints and update/delete rejection triggers.
- `__init__.py`: a small internal entry point; no automatic service initialization.

No existing production module, owner schema, migration, dependency or CLI changes.
The package does not import owner storage, portfolio services, brokers, providers,
credentials, environment loaders, HTTP clients or agents.

`VirtualLedger` requires an explicit absolute `*.virtual.sqlite3` location outside
the checkout. The caller must provide an existing, privately controlled parent
directory. There is no default, owner database fallback, or parent creation.
New files have mode 0600. Symlink leaves, hardlinks, broad file permissions,
wrong ownership, repository paths and configuration directories are rejected.
The file identity is checked before each connection. These checks do not provide
protection against an adversary controlling the parent directory or the process.

Existing files must match the dedicated application ID (1297503281), schema
version 1, and exact schema objects. Empty, foreign, altered or future schemas
fail closed; there is no automatic migration. SQLite foreign keys are enabled on
every connection. Read connections use read-only URI mode and query-only mode.
Write connections use `BEGIN IMMEDIATE`, validating current committed history and
the proposed event within the same transaction. SQLite errors expose a fixed
`STORAGE_UNAVAILABLE` code, not database paths or underlying exception text.

Schema version 1 fixes this replay/rounding contract. Any future change to its
meaning requires explicit version/migration design; it must not reinterpret
existing history silently. There is no disk cache or materialized balance table.
Amounts, quantities and prices are stored as decimal text, never binary floats.

## Internal contracts

Portfolio, instrument, fill, event, provenance and idempotency identities are
canonical UUID strings from an independent namespace. They do not represent owner
account references or authenticated ownership. The explicit instrument allowlist
accepts uppercase US symbols and USD only; registration is not listing discovery.

Creating a portfolio gives it zero cash and no events. `fund` requires an explicit
portfolio, positive Decimal amount, idempotency key and `confirmed=True`.
Confirmation is a trusted-caller acknowledgement, **not authorization**. Funding
occurs once; the generic engine does not hardcode USD 100,000. Tests use that
amount and other explicit values. There is no reset, transfer or withdrawal API.

`apply_synthetic_fill` accepts only a validated `SyntheticFill` and an idempotency
key. The fill records its identity, instrument, BUY/SELL side, whole-share quantity,
price, explicit fees, USD currency and opaque synthetic provenance UUID. It is an
internal, non-deployable accounting boundary. A UUID is not evidence of a trusted
price or authorization. A future execution service must establish those guarantees
and enforce its own price, fee, timestamp and reservation policy before calling an
appropriate future boundary. Users cannot submit trades through this package.
Research evidence is never used as execution-price evidence.

Fees are supported as an accounting primitive and exercised synthetically. This
does not activate a nonzero-fee competition policy; the proposed initial competition
policy remains explicit zero fees. No order lifecycle or partial-fill policy is
implemented here; partial **position** sales are supported.

## Accounting and rounding

All authoritative arithmetic uses Decimal with precision 80 and ROUND_HALF_EVEN.
Each operation uses a fresh context, independent of the caller's active context.
Funding, fees, gross trade amounts and partial-sale basis allocations round to USD
cents. Zero-valued fees normalize to positive zero for canonical duplicate replay.
Floats, booleans, negative/nonfinite values and precision beyond eight decimal
places are rejected. Positive funding or trade gross that rounds to zero fails.
Quantity is a positive whole number at most 1,000,000 per fill; price is positive
and at most USD 1,000,000. Monetary inputs and cash, aggregate book cost and absolute
aggregate realized P&L are bounded to USD 1,000,000,000,000.

For quantity `q`, price `p`, fee `f`, held quantity `Q` and book cost `B`:

- `gross = round_cent(q * p)`.
- BUY debits `gross + f`, adds `q` shares and adds that debit to book cost. It fails
  if unfunded or if cash is insufficient.
- SELL requires `q <= Q`. It releases `B` on full closure, otherwise
  `round_cent(B * q / Q)`. Cash changes by `gross - f`; realized P&L changes by
  that net amount minus released basis. A fee larger than proceeds is allowed
  only when existing cash covers it; cash may never become negative.
- Average cost is `B / Q`; it is never rounded and fed back into authoritative
  basis. A full close releases all remaining cents. Closed positions retain
  realized P&L and may later reopen.
- After each event, `cash + total book cost = funding + total realized P&L`.

There is no unrealized P&L or valuation without a future trusted valuation contract.
The owner's weighted-average and full-close principles are adapted, not imported
into production. A test-only pure reference comparison verifies overlapping exact
cent vectors. Owner accounting's unrounded fractional basis allocation is not
silently changed; new virtual tests separately verify cents allocation/remainders.
Owner writer/CLI/opening-balance/migration/valuation services are not reused because
they carry owner identities, persistence or price/provider assumptions. Immutable
value-model and transactional SQLite patterns are reused conceptually only.

## Atomicity, idempotency and history

The authoritative event and its canonical request association are one row committed
atomically. Cash and positions are derived; there are no independent balance writes
that can be left half-applied. Exceptions roll back the transaction. No implicit
retries or provider calls occur. On lock contention the operation fails safely;
the caller may retry with the same key.

Idempotency keys are scoped to the portfolio and shared by funding/fill requests.
Equivalent normalized inputs replay the original event, including its timestamps
and identity. Changed normalized accounting inputs or provenance under the same
key fail with `IDEMPOTENCY_CONFLICT`. A fill identity reused under another key fails
with `DUPLICATE_FILL`. Funding under a new key after initial funding fails with
`ALREADY_FUNDED`. Failed requests reserve neither a key nor a balance. Database
uniqueness enforces one funding event, unique portfolio/key, portfolio/fill and
portfolio/sequence, and globally unique event identities.

Every read verifies schema and reconstructs/validates ledger content. Events carry
portfolio-local contiguous sequence, event/portfolio identity, kind, currency,
funding amount or fill fields, equal effective/recording UTC timestamps and the
idempotency key. The ledger-owned clock sets both timestamps; fill callers have no
backdating parameter. Time cannot regress, while equal timestamps are ordered by
sequence. Clock injection is for trusted offline tests only. Future execution-time
semantics remain outside this milestone. Update/delete triggers prevent ordinary
historical rewriting; a database administrator can bypass them, so this is not
cryptographic tamper-proof storage.

Multiple workers use separate SQLite connections and serialize writes before
checking cash/holdings/history. Tests verify concurrent distinct requests, spending
the same cash, overselling, duplicate fill/funding submissions, lock contention,
post-insert rollback, reopen and a fresh-process idempotent replay. Reconstructed
state remains immutable and deterministically ordered by instrument identity.

## Offline validation and isolation

`tests/test_virtual_accounting.py` covers funding, accounting, cents rounding,
invalid inputs, independent portfolios, uniqueness, foreign keys, immutable history,
corruption/schema rejection, filesystem boundaries, concurrency, restart and rollback.
An isolated subprocess rejects owner/broker/HTTP/environment-loader imports while
creating and using the virtual ledger. An AST import-closure check guards the new
package. Temporary databases and synthetic inputs are the only persistence used.
The full existing suite also protects owner portfolio, AnalysisResult, agents,
providers and voting behavior.

Existing fixture-only Toss adapter tests may run with synthetic credentials and
in-memory `MockTransport`, under explicit user authorization. Those test-only
constructions are reported separately from real broker initialization. This
exception does not apply to the new virtual package or to live broker operations.
Final validation uses an empty credential environment, blocked real transports and
socket operations, blocked real `.env` reads and blocked production DB access.

## Remaining release blockers and later work

No multiuser authorization, trusted execution prices, exchange calendar, pending
orders/reservations, corporate actions, valuation, competition lifecycle, public UI
or deployment is provided. Performance currently replays complete history; there is
no unverified cached projection. The file parent and calling process remain trusted.
A file/lock failure may leave a newly created empty file requiring explicit operator
review; existing empty files are deliberately not silently initialized.

The next proposed milestone is trusted virtual execution policy and authorization/
price-provenance boundary design, with separate approval. It has not been started.
A later README maintenance milestone should link this implemented core, distinguish
it from beta readiness, and describe future execution/authentication requirements.
README and the approved architecture document remain unchanged in Phase 8.2.

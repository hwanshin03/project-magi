"""Synthetic virtual-ledger tests. No brokers, credentials, production DB or network."""
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext, Inexact, ROUND_DOWN
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from magi.virtual import VirtualLedger, Instrument, SyntheticFill, Side, VirtualError, ErrorCode
from magi.virtual.accounting import reconstruct
from magi.virtual.models import Event, EventKind

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def uid():
    return str(uuid4())


class VirtualAccountingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / 'ledger.virtual.sqlite3'
        self.now = NOW
        self.ledger = VirtualLedger(self.path, clock=lambda: self.now)
        self.portfolio = uid()
        self.ledger.create_portfolio(self.portfolio)
        self.instrument = Instrument(uid(), 'SYNTH')
        self.ledger.register_instrument(self.instrument)
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo',
                       'socket.create_connection', 'socket.socket.sendto'):
            guard = patch(target, side_effect=AssertionError('No network in virtual tests'))
            guard.start(); self.addCleanup(guard.stop)

    def assert_code(self, code, call):
        with self.assertRaises(VirtualError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code.value)

    def fund(self, amount='100000', key=None, portfolio=None):
        return self.ledger.fund(portfolio or self.portfolio, D(amount),
                                idempotency_key=key or uid(), confirmed=True)

    def fill(self, side=Side.BUY, quantity='10', price='100', fees='0', **kwargs):
        return SyntheticFill(kwargs.pop('fill_id', uid()), kwargs.pop('instrument_id', self.instrument.instrument_id),
                             side, D(quantity), D(price), kwargs.pop('provenance_id', uid()), D(fees), **kwargs)

    def apply(self, fill=None, key=None, portfolio=None):
        return self.ledger.apply_synthetic_fill(portfolio or self.portfolio, fill or self.fill(),
                                                idempotency_key=key or uid())

    def state(self):
        return self.ledger.state(self.portfolio)

    def test_empty_unfunded_state(self):
        s = self.state()
        self.assertEqual((s.cash, s.funding, s.positions, s.event_count), (D(0), D(0), (), 0))
        self.assert_code(ErrorCode.NOT_FUNDED, self.apply)
        self.assertEqual(self.ledger.history(self.portfolio), ())

    def test_funding_amount_is_explicit_and_not_hardcoded(self):
        event = self.fund('321.50')
        self.assertEqual((self.state().cash, self.state().funding), (D('321.50'), D('321.50')))
        self.assertEqual((event.kind, event.effective_at, event.recorded_at), (EventKind.FUNDING, NOW, NOW))
        self.assertEqual(event.amount, D('321.50'))

    def test_funding_confirmation_required(self):
        for confirmed in (False, None, 1, 'true'):
            with self.subTest(confirmed=confirmed):
                self.assert_code(ErrorCode.CONFIRMATION_REQUIRED, lambda: self.ledger.fund(
                    self.portfolio, D(100000), idempotency_key=uid(), confirmed=confirmed))
        self.assertEqual(self.state().cash, 0)

    def test_duplicate_funding_same_key_replays(self):
        key = uid(); first = self.fund(key=key)
        self.assertEqual(self.fund(key=key), first)
        self.assertEqual((self.state().cash, self.state().event_count), (D(100000), 1))

    def test_second_funding_new_key_rejected(self):
        self.fund()
        self.assert_code(ErrorCode.ALREADY_FUNDED, self.fund)
        self.assertEqual(self.state().event_count, 1)

    def test_conflicting_funding_key_rejected(self):
        key = uid(); self.fund(key=key)
        self.assert_code(ErrorCode.IDEMPOTENCY_CONFLICT, lambda: self.fund('200000', key))

    def test_buy_cash_and_position(self):
        self.fund(); event = self.apply()
        s = self.state(); p = s.positions[0]
        self.assertEqual((s.cash, p.quantity, p.book_cost, p.average_cost), (D(99000), D(10), D(1000), D(100)))
        self.assertEqual(event.fill, self.ledger.history(self.portfolio)[1].fill)

    def test_weighted_cost_and_fees(self):
        self.fund(); self.apply(self.fill(fees='10')); self.apply(self.fill(price='200', fees='10'))
        p = self.state().positions[0]
        self.assertEqual((p.quantity, p.book_cost, p.average_cost), (D(20), D(3020), D(151)))
        self.assertEqual(self.state().cash, D(96980))

    def test_partial_sell_and_realized_pnl(self):
        self.fund(); self.apply(self.fill(fees='10')); self.apply(self.fill(price='200', fees='10'))
        self.apply(self.fill(Side.SELL, '5', '180', '5'))
        p = self.state().positions[0]
        self.assertEqual((p.quantity, p.book_cost, p.realized_pnl), (D(15), D(2265), D(140)))
        self.assertEqual(self.state().cash, D(97875))

    def test_full_close_absorbs_cent_rounding_remainder(self):
        self.fund(); self.apply(self.fill(quantity='3', price='1', fees='1'))
        self.apply(self.fill(Side.SELL, '1', '2'))
        p = self.state().positions[0]
        self.assertEqual((p.quantity, p.book_cost, p.realized_pnl), (D(2), D('2.67'), D('.67')))
        self.apply(self.fill(Side.SELL, '2', '2'))
        p = self.state().positions[0]
        self.assertEqual((p.quantity, p.book_cost, p.average_cost, p.realized_pnl), (D(0), D(0), D(0), D(2)))

    def test_close_reopen_retains_realized(self):
        self.fund(); self.apply(); self.apply(self.fill(Side.SELL, '10', '90')); self.apply(self.fill(quantity='1', price='20'))
        p = self.state().positions[0]
        self.assertEqual((p.quantity, p.book_cost, p.realized_pnl), (D(1), D(20), D(-100)))

    def test_round_half_even_gross_fees_and_funding(self):
        self.fund('100.005'); self.apply(self.fill(quantity='1', price='1.005', fees='.015'))
        self.assertEqual((self.state().cash, self.state().positions[0].book_cost), (D('98.98'), D('1.02')))
        self.apply(self.fill(quantity='1', price='1.015', fees='.005'))
        self.assertEqual(self.state().positions[0].book_cost, D('2.04'))

    def test_hostile_decimal_context_does_not_change_results(self):
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_DOWN; context.traps[Inexact] = True
            self.fund(); self.apply(self.fill(quantity='3', price='1', fees='1'))
            self.apply(self.fill(Side.SELL, '1', '2'))
            self.assertEqual(self.state().positions[0].book_cost, D('2.67'))
            self.assertEqual(self.state().positions[0].average_cost, D('1.335'))

    def test_insufficient_cash_is_atomic(self):
        self.fund('100'); before = self.ledger.history(self.portfolio)
        self.assert_code(ErrorCode.INSUFFICIENT_CASH, self.apply)
        self.assertEqual(self.ledger.history(self.portfolio), before)

    def test_buy_fee_cannot_overdraw_cash(self):
        self.fund('100')
        self.assert_code(ErrorCode.INSUFFICIENT_CASH, lambda: self.apply(self.fill(quantity='1', fees='.01')))
        self.assertEqual(self.state().cash, D(100))

    def test_sell_fee_can_exceed_proceeds_only_if_cash_sufficient(self):
        self.fund('100'); self.apply(self.fill(quantity='1', price='90'))
        self.apply(self.fill(Side.SELL, '1', '1', '5'))
        self.assertEqual((self.state().cash, self.state().realized_pnl), (D(6), D(-94)))

    def test_sell_fee_cannot_overdraw_cash(self):
        self.fund('100'); self.apply(self.fill(quantity='1'))
        before = self.state()
        self.assert_code(ErrorCode.INSUFFICIENT_CASH, lambda: self.apply(self.fill(Side.SELL, '1', '1', '2')))
        self.assertEqual(self.state(), before)

    def test_oversell_rejected_with_no_mutation(self):
        self.fund(); self.apply(); before = self.state()
        self.assert_code(ErrorCode.INSUFFICIENT_SHARES, lambda: self.apply(self.fill(Side.SELL, '11')))
        self.assertEqual(self.state(), before)

    def test_sell_absent_position_rejected(self):
        self.fund()
        self.assert_code(ErrorCode.INSUFFICIENT_SHARES, lambda: self.apply(self.fill(Side.SELL)))
        self.assertEqual(self.state().positions, ())

    def test_unknown_instrument_rejected(self):
        self.fund()
        self.assert_code(ErrorCode.UNKNOWN_INSTRUMENT, lambda: self.apply(self.fill(instrument_id=uid())))

    def test_unknown_portfolio_cannot_write_or_read(self):
        missing = uid()
        for operation in (lambda: self.fund(portfolio=missing), lambda: self.apply(portfolio=missing),
                          lambda: self.ledger.state(missing), lambda: self.ledger.history(missing)):
            self.assert_code(ErrorCode.UNKNOWN_PORTFOLIO, operation)

    def test_invalid_funding_values(self):
        for value in (D(0), D('-1'), D('NaN'), D('Infinity'), D('1e13'), D('.001'), 1.0, True, '100'):
            with self.subTest(value=str(value)):
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: self.ledger.fund(self.portfolio, value, idempotency_key=uid(), confirmed=True))

    def test_invalid_quantity_values(self):
        for value in (D(0), D(-1), D('1.5'), D('NaN'), D('Infinity'), D('1000001'), True, 1.0, '1'):
            with self.subTest(value=str(value)):
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.fill(), quantity=value))

    def test_invalid_price_values(self):
        for value in (D(0), D(-1), D('NaN'), D('Infinity'), D('1000001'), D('1e-9'), D('.00000001'), True, 1.0):
            with self.subTest(value=str(value)):
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.fill(quantity='1'), price=value))

    def test_invalid_fee_values(self):
        for value in (D(-1), D('NaN'), D('Infinity'), D('1e13'), True, 1.0):
            with self.subTest(value=str(value)):
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.fill(), fees=value))

    def test_currency_is_usd_only(self):
        for value in ('KRW', 'usd', '', None):
            with self.subTest(value=value):
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.fill(), currency=value))
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: self.ledger.create_portfolio(uid(), currency=value))
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.instrument, currency=value))

    def test_identifiers_and_sides_are_strict(self):
        for value in ('', 'owner-account', '../secret', 'Bearer synthetic-token', 1, None):
            with self.subTest(value=value):
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: self.ledger.create_portfolio(value))
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.fill(), provenance_id=value))
                self.assert_code(ErrorCode.INVALID_INPUT, lambda: self.ledger.apply_synthetic_fill(
                    self.portfolio, self.fill(), idempotency_key=value))
        self.assert_code(ErrorCode.INVALID_INPUT, lambda: replace(self.fill(), side='BUY'))
        self.assert_code(ErrorCode.INVALID_INPUT, lambda: self.ledger.apply_synthetic_fill(self.portfolio, {}, idempotency_key=uid()))

    def test_allowlist_registration_conflicts(self):
        self.ledger.register_instrument(self.instrument)
        self.assert_code(ErrorCode.IDENTITY_CONFLICT, lambda: self.ledger.register_instrument(replace(self.instrument, symbol='OTHER')))
        self.assert_code(ErrorCode.IDENTITY_CONFLICT, lambda: self.ledger.register_instrument(replace(self.instrument, instrument_id=uid())))
        self.assert_code(ErrorCode.INVALID_INPUT, lambda: Instrument(uid(), 'bad symbol'))

    def test_duplicate_fill_key_replays_exact_event(self):
        self.fund(); fill = self.fill(); key = uid(); first = self.apply(fill, key)
        self.now += timedelta(days=1)
        self.assertEqual(self.apply(fill, key), first)
        self.assertEqual(self.state().event_count, 2)

    def test_duplicate_fill_identity_with_new_key_rejected(self):
        self.fund(); fill = self.fill(); self.apply(fill)
        self.assert_code(ErrorCode.DUPLICATE_FILL, lambda: self.apply(fill))
        self.assertEqual(self.state().event_count, 2)

    def test_conflicting_fill_idempotency_payload(self):
        self.fund(); fill = self.fill(); key = uid(); self.apply(fill, key)
        for different in (replace(fill, price=D(110)), replace(fill, quantity=D(11)), replace(fill, fees=D(1)),
                          replace(fill, provenance_id=uid()), replace(fill, fill_id=uid()), replace(fill, side=Side.SELL)):
            self.assert_code(ErrorCode.IDEMPOTENCY_CONFLICT, lambda: self.apply(different, key))

    def test_funding_and_fill_share_portfolio_key_namespace(self):
        key = uid(); self.fund(key=key)
        self.assert_code(ErrorCode.IDEMPOTENCY_CONFLICT, lambda: self.apply(key=key))

    def test_decimal_representation_replay_is_canonical(self):
        self.fund(); fill = self.fill(); key = uid(); first = self.apply(fill, key)
        self.assertEqual(self.apply(replace(fill, quantity=D('10.0'), price=D('100.00')), key), first)

    def test_independent_portfolios_and_scoped_keys(self):
        other = uid(); self.ledger.create_portfolio(other); key = uid()
        self.fund(key=key); self.fund('500', key=key, portfolio=other)
        fill = self.fill(quantity='1'); key = uid(); self.apply(fill, key); self.apply(fill, key, other)
        self.assertEqual(self.state().cash, D(99900))
        self.assertEqual(self.ledger.state(other).cash, D(400))
        self.assertEqual(self.ledger.history(other)[-1].sequence, 2)

    def test_equal_time_uses_per_portfolio_sequence(self):
        self.fund(); self.apply(); self.apply(self.fill(Side.SELL, '3', '110'))
        events = self.ledger.history(self.portfolio)
        self.assertEqual([e.sequence for e in events], [1, 2, 3])
        self.assertTrue(all(e.effective_at == e.recorded_at == NOW for e in events))
        self.assertEqual(self.state(), reconstruct(self.portfolio, events))

    def test_clock_regression_rejected_except_duplicate_replay(self):
        key = uid(); event = self.fund(key=key); self.now -= timedelta(seconds=1)
        self.assertEqual(self.fund(key=key), event)
        self.assert_code(ErrorCode.CLOCK_REGRESSION, self.apply)
        self.assertEqual(self.state().event_count, 1)

    def test_no_caller_effective_timestamp_parameter(self):
        self.fund()
        with self.assertRaises(TypeError):
            self.ledger.apply_synthetic_fill(self.portfolio, self.fill(), idempotency_key=uid(), effective_at=NOW)
        self.now = datetime(2026, 1, 1)
        self.assert_code(ErrorCode.INVALID_INPUT, self.apply)

    def test_history_and_state_are_immutable(self):
        self.fund(); event = self.apply(); state = self.state()
        for item, field, value in ((event, 'sequence', 50), (event.fill, 'price', D(1)), (state, 'cash', D(1)),
                                   (state.positions[0], 'quantity', D(99))):
            with self.assertRaises(FrozenInstanceError): setattr(item, field, value)

    def test_reopen_and_replay(self):
        self.fund(); fill = self.fill(); key = uid(); first = self.apply(fill, key)
        reopened = VirtualLedger(self.path)
        self.assertEqual(reopened.state(self.portfolio), self.state())
        self.assertEqual(reopened.history(self.portfolio), self.ledger.history(self.portfolio))
        self.assertEqual(reopened.apply_synthetic_fill(self.portfolio, fill, idempotency_key=key), first)

    def test_process_restart_preserves_idempotency_and_state(self):
        self.fund(); fill = self.fill(); key = uid(); event = self.apply(fill, key)
        script = '''
import sys, pathlib, json
root=pathlib.Path(sys.argv[1]); path=pathlib.Path(sys.argv[2]).resolve()
def guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.sendto'): raise AssertionError('network')
    if event in ('open','sqlite3.connect') and args and isinstance(args[0],str):
        p=pathlib.Path(args[0].removeprefix('file:').split('?')[0]).absolute()
        if p==root/'.env' or (p.is_relative_to(root) and (event=='sqlite3.connect' or p.suffix=='.db')): raise AssertionError('owner access')
sys.addaudithook(guard); sys.path.insert(0,str(root))
from decimal import Decimal as D
from magi.virtual import VirtualLedger,SyntheticFill,Side
ledger=VirtualLedger(path)
fill=SyntheticFill(sys.argv[4],sys.argv[5],Side.BUY,D(10),D(100),sys.argv[6])
event=ledger.apply_synthetic_fill(sys.argv[3],fill,idempotency_key=sys.argv[7])
s=ledger.state(sys.argv[3]); print(json.dumps([str(s.cash),s.event_count,event.event_id]))
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT), str(self.path), self.portfolio,
                                 fill.fill_id, fill.instrument_id, fill.provenance_id, key],
                                capture_output=True, text=True, check=True, env={'PATH': '/usr/bin:/bin'})
        self.assertEqual(json.loads(result.stdout), ['99000.00', 2, event.event_id])

    def race(self, operations):
        barrier = Barrier(len(operations))
        def invoke(operation):
            ledger = VirtualLedger(self.path, clock=lambda: NOW)
            barrier.wait(timeout=10)
            try: return operation(ledger)
            except VirtualError as error: return error.code
        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            futures = [executor.submit(invoke, op) for op in operations]
            return [f.result(timeout=15) for f in futures]

    def test_concurrent_distinct_buys_both_commit(self):
        self.fund(); fills = [self.fill(), self.fill()]
        outcomes = self.race([lambda ledger, f=f: ledger.apply_synthetic_fill(self.portfolio, f, idempotency_key=uid()) for f in fills])
        self.assertTrue(all(isinstance(x, Event) for x in outcomes))
        self.assertEqual((self.state().cash, self.state().positions[0].quantity, self.state().event_count), (D(98000), D(20), 3))

    def test_concurrent_buy_double_spend_prevented(self):
        self.fund('1000'); fills = [self.fill(), self.fill()]
        outcomes = self.race([lambda ledger, f=f: ledger.apply_synthetic_fill(self.portfolio, f, idempotency_key=uid()) for f in fills])
        self.assertEqual(outcomes.count(ErrorCode.INSUFFICIENT_CASH), 1)
        self.assertEqual((self.state().cash, self.state().event_count), (D(0), 2))

    def test_concurrent_sells_prevent_overselling(self):
        self.fund(); self.apply(); fills = [self.fill(Side.SELL, '7'), self.fill(Side.SELL, '7')]
        outcomes = self.race([lambda ledger, f=f: ledger.apply_synthetic_fill(self.portfolio, f, idempotency_key=uid()) for f in fills])
        self.assertEqual(outcomes.count(ErrorCode.INSUFFICIENT_SHARES), 1)
        self.assertEqual((self.state().positions[0].quantity, self.state().event_count), (D(3), 3))

    def test_concurrent_duplicate_fill_workers_return_same_event(self):
        self.fund(); fill = self.fill(); key = uid()
        operation = lambda ledger: ledger.apply_synthetic_fill(self.portfolio, fill, idempotency_key=key)
        outcomes = self.race([operation, operation])
        self.assertEqual(outcomes[0], outcomes[1]); self.assertIsInstance(outcomes[0], Event)
        self.assertEqual(self.state().event_count, 2)

    def test_concurrent_duplicate_funding_keys(self):
        outcomes = self.race([lambda ledger: ledger.fund(self.portfolio, D(100000), idempotency_key=uid(), confirmed=True)] * 2)
        self.assertEqual(outcomes.count(ErrorCode.ALREADY_FUNDED), 1)
        self.assertEqual((self.state().cash, self.state().event_count), (D(100000), 1))

    def test_concurrent_same_funding_key(self):
        key = uid()
        operation = lambda ledger: ledger.fund(self.portfolio, D(100000), idempotency_key=key, confirmed=True)
        outcomes = self.race([operation, operation])
        self.assertEqual(outcomes[0], outcomes[1]); self.assertIsInstance(outcomes[0], Event)
        self.assertEqual(self.state().event_count, 1)

    def test_failure_after_insert_rolls_back_event_and_idempotency(self):
        self.fund(); before = self.state(); fill = self.fill(); key = uid()
        original = sqlite3.connect
        class FailedWrite(sqlite3.Connection):
            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if sql.startswith('INSERT INTO virtual_events'):
                    raise sqlite3.OperationalError('private synthetic diagnostic')
                return result
        def connect(*args, **kwargs): return original(*args, factory=FailedWrite, **kwargs)
        with patch('magi.virtual.storage.sqlite3.connect', side_effect=connect):
            self.assert_code(ErrorCode.STORAGE_UNAVAILABLE, lambda: self.apply(fill, key))
        self.assertEqual(self.state(), before)
        self.apply(fill, key)
        self.assertEqual(self.state().event_count, 2)

    def test_lock_contention_failure_no_mutation_and_safe_retry(self):
        self.fund(); contender = VirtualLedger(self.path, timeout=.01); fill = self.fill(); key = uid()
        with sqlite3.connect(self.path) as connection:
            connection.execute('BEGIN IMMEDIATE')
            self.assert_code(ErrorCode.STORAGE_UNAVAILABLE, lambda: contender.apply_synthetic_fill(self.portfolio, fill, idempotency_key=key))
        self.assertEqual(self.state().event_count, 1)
        contender.apply_synthetic_fill(self.portfolio, fill, idempotency_key=key)
        self.assertEqual(self.state().event_count, 2)

    def test_database_history_and_identities_append_only(self):
        self.fund(); self.apply()
        with sqlite3.connect(self.path) as connection:
            for table in ('virtual_events', 'virtual_portfolios', 'virtual_instruments'):
                for sql in (f'DELETE FROM {table}', f'UPDATE {table} SET rowid=rowid'):
                    with self.assertRaises(sqlite3.IntegrityError): connection.execute(sql)
        self.assertEqual(self.state().event_count, 2)

    def test_foreign_keys_enabled(self):
        with self.ledger._connection(write=True) as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_schema_tampering_and_future_version_rejected(self):
        with sqlite3.connect(self.path) as connection: connection.execute('PRAGMA user_version=2')
        before = self.path.read_bytes()
        self.assert_code(ErrorCode.INVALID_LEDGER, lambda: VirtualLedger(self.path))
        self.assertEqual(self.path.read_bytes(), before)
        self.assert_code(ErrorCode.INVALID_LEDGER, self.state)

    def test_missing_immutability_trigger_rejected(self):
        with sqlite3.connect(self.path) as connection: connection.execute('DROP TRIGGER virtual_events_no_update')
        self.assert_code(ErrorCode.INVALID_LEDGER, self.state)

    def test_corrupt_event_payload_rejected(self):
        self.fund()
        with sqlite3.connect(self.path) as connection:
            trigger = connection.execute("SELECT sql FROM sqlite_master WHERE name='virtual_events_no_update'").fetchone()[0]
            connection.execute('DROP TRIGGER virtual_events_no_update')
            connection.execute("UPDATE virtual_events SET amount='999999'")
            connection.execute(trigger)
        self.assert_code(ErrorCode.INVALID_LEDGER, self.state)

    def test_no_path_default_or_unsafe_location(self):
        with self.assertRaises(TypeError): VirtualLedger()
        for path in (None, '', ':memory:', 'relative.virtual.sqlite3', ROOT/'data'/'magi.db',
                     ROOT/'unsafe.virtual.sqlite3', self.root/'magi.db', self.root/'missing'/'x.virtual.sqlite3'):
            with self.subTest(path=str(path)):
                self.assert_code(ErrorCode.UNSAFE_STORAGE, lambda: VirtualLedger(path))
        self.assertFalse((self.root/'missing').exists())

    def test_symlink_and_hardlink_rejected(self):
        link = self.root/'link.virtual.sqlite3'; link.symlink_to(self.path)
        self.assert_code(ErrorCode.UNSAFE_STORAGE, lambda: VirtualLedger(link))
        link.unlink(); os.link(self.path, link)
        self.assert_code(ErrorCode.UNSAFE_STORAGE, lambda: VirtualLedger(link))
        self.assert_code(ErrorCode.UNSAFE_STORAGE, self.state)
        link.unlink()

    def test_world_readable_file_rejected(self):
        self.path.chmod(0o644)
        self.assert_code(ErrorCode.UNSAFE_STORAGE, lambda: VirtualLedger(self.path))

    def test_foreign_database_rejected_without_writing(self):
        foreign = self.root/'foreign.virtual.sqlite3'
        with sqlite3.connect(foreign) as c: c.execute('CREATE TABLE unrelated(value TEXT)')
        foreign.chmod(0o600); before = foreign.read_bytes()
        self.assert_code(ErrorCode.INVALID_LEDGER, lambda: VirtualLedger(foreign))
        self.assertEqual(foreign.read_bytes(), before)

    def test_existing_empty_database_is_not_silently_initialized(self):
        empty = self.root/'empty.virtual.sqlite3'; empty.touch(mode=0o600)
        self.assert_code(ErrorCode.INVALID_LEDGER, lambda: VirtualLedger(empty))
        self.assertEqual(empty.read_bytes(), b'')

    def test_only_explicit_temporary_database_is_created(self):
        self.fund(); self.apply(); self.state()
        self.assertEqual({p.name for p in self.root.iterdir()}, {self.path.name})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_import_closure_contains_no_owner_broker_or_network_modules(self):
        allowed = {'dataclasses','datetime','decimal','enum','uuid','re','pathlib','contextlib','json','os','sqlite3','stat'}
        for source in (ROOT/'magi'/'virtual').glob('*.py'):
            # Only the new pure contracts need Protocol and IANA timezone support.
            permitted = allowed | {'typing', 'zoneinfo'} if source.name == 'execution_contracts.py' else allowed
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(a.name.split('.')[0] in permitted for a in node.names), source.name)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    self.assertIn(node.module.split('.')[0], permitted, source.name)

    def test_pure_reconstruction_rejects_reordered_or_cross_portfolio_events(self):
        self.fund(); self.apply(); events = self.ledger.history(self.portfolio)
        for changed in ((events[1],events[0]), (replace(events[0], portfolio_id=uid()),),
                        (replace(events[0], sequence=2),), (events[0],events[0])):
            self.assert_code(ErrorCode.INVALID_LEDGER, lambda: reconstruct(self.portfolio, changed))


    def test_zero_fee_sign_does_not_change_idempotency(self):
        self.fund(); fill = self.fill(fees='-0.00'); key = uid()
        first = self.apply(fill, key)
        self.assertEqual(self.apply(replace(fill, fees=D('0.00')), key), first)

    def test_failed_request_does_not_reserve_key(self):
        fill = self.fill(); key = uid()
        self.assert_code(ErrorCode.NOT_FUNDED, lambda: self.apply(fill, key))
        self.fund()
        self.assertEqual(self.apply(fill, key).sequence, 2)

    def test_foreign_keys_reject_unknown_identity(self):
        self.fund()
        with self.ledger._connection(write=True) as connection:
            row = list(connection.execute('SELECT * FROM virtual_events').fetchone())
            row[0] = uid(); row[2] = uid(); row[14] = uid()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO virtual_events VALUES (' + ','.join('?' for _ in row) + ')', row)
        self.assertEqual(self.state().event_count, 1)

    def test_invalid_clock_does_not_commit(self):
        self.now = datetime(2026, 1, 1)
        self.assert_code(ErrorCode.INVALID_INPUT, self.fund)
        self.assertEqual(self.state().event_count, 0)

    def test_aggregate_limit_is_atomic(self):
        self.fund('1000000000000')
        self.apply(self.fill(quantity='1', price='1'))
        before = self.state()
        self.assert_code(ErrorCode.LIMIT_EXCEEDED, lambda: self.apply(self.fill(Side.SELL, '1', '2')))
        self.assertEqual(self.state(), before)

    def test_owner_weighted_cost_reference_vectors(self):
        # Test-only pure reference comparison. No owner service, DB, or adapters.
        from magi.portfolio import Transaction, TradeAction, _calculate
        self.fund(); reference = []
        for side, qty, price, fee in ((Side.BUY, '10', '10', '2'), (Side.BUY, '10', '20', '2'),
                                      (Side.SELL, '5', '30', '1'), (Side.SELL, '15', '8', '2')):
            fill = self.fill(side, qty, price, fee); event = self.apply(fill)
            reference.append(Transaction(transaction_id=event.event_id, timestamp=NOW.isoformat(),
                recorded_at=NOW.isoformat(), symbol='SYNTH', action=TradeAction(side.value),
                quantity=D(qty), price_per_share=D(price), fees=D(fee), currency='USD'))
            old = _calculate(reference)[-1].position
            new = self.state().positions[0]
            self.assertEqual((new.quantity, new.book_cost, new.average_cost, new.realized_pnl),
                             (old.shares_held, old.book_cost, old.average_book_cost, old.realized_pnl))

    def test_virtual_import_and_operations_cannot_initialize_brokers(self):
        script = """
import sys, pathlib, tempfile
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root))
def guard(event,args):
    if event=='import' and args[0].startswith(('magi.broker','magi.toss','magi.storage','magi.portfolio','httpx','dotenv')):
        raise AssertionError('Forbidden virtual dependency')
    if event in ('socket.connect','socket.getaddrinfo','socket.sendto'): raise AssertionError('Network')
    if event=='open' and args and isinstance(args[0],str) and pathlib.Path(args[0]).name=='.env': raise AssertionError('Credentials')
    if event=='sqlite3.connect' and not str(args[0]).startswith(('file:///private/','file:///var/')): raise AssertionError('Unsafe DB')
sys.addaudithook(guard)
from magi.virtual import VirtualLedger, Instrument, SyntheticFill, Side
from uuid import uuid4
from decimal import Decimal
with tempfile.TemporaryDirectory() as directory:
    ledger=VirtualLedger(pathlib.Path(directory).resolve()/'isolation.virtual.sqlite3')
    portfolio=str(uuid4());ledger.create_portfolio(portfolio)
    ledger.fund(portfolio,Decimal(100000),idempotency_key=str(uuid4()),confirmed=True)
    instrument=Instrument(str(uuid4()),'SYNTH');ledger.register_instrument(instrument)
    ledger.apply_synthetic_fill(portfolio,SyntheticFill(str(uuid4()),instrument.instrument_id,Side.BUY,
        Decimal(1),Decimal(10),str(uuid4())),idempotency_key=str(uuid4()))
    assert ledger.state(portfolio).cash==Decimal(99990)
print('isolated')
"""
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT)],
            capture_output=True, text=True, env={'PATH':'/usr/bin:/bin'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'isolated')


if __name__ == '__main__':
    unittest.main()

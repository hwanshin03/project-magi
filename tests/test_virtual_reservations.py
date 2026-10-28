"""Synthetic Package B fixtures; no admission, market prices or broker dependencies."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal as D, Inexact, localcontext
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from magi.virtual import VirtualLedger, Instrument, SyntheticFill, Side, VirtualError, ErrorCode
from magi.virtual.order_codec import PersistenceError
from magi.virtual.order_lifecycle import OrderState, transition_order
from magi.virtual.order_storage import OrderStore, migrate_order_schema
from magi.virtual.reservations import (Reservation, ReservationCode as Code, Resource,
    ReservationStatus, encode_reservation, decode_reservation)
from magi.virtual.reservation_storage import (ReservationStore, migrate_reservation_schema,
    _reserve_synthetic)
from test_virtual_order_persistence import seed, uid
from test_virtual_order_lifecycle import accepted, CUT, OPEN, DEADLINE, deadline_evidence

ROOT = Path(__file__).resolve().parents[1]


class ReservationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.path = self.root / 'reservations.virtual.sqlite3'
        self.initial = accepted()
        self.p = self.initial.intent.portfolio_id
        self.i = self.initial.intent.instrument_id
        self.other_p, self.other_i = uid(), uid()
        self.ledger = VirtualLedger(self.path, clock=lambda: self.initial.changed_at)
        for p, amount in ((self.p, '1000'), (self.other_p, '500')):
            self.ledger.create_portfolio(p)
            self.ledger.fund(p, D(amount), idempotency_key=uid(), confirmed=True)
        for i, symbol in ((self.i, 'SYNTH'), (self.other_i, 'OTHER')):
            self.ledger.register_instrument(Instrument(i, symbol))
        for p, i, q, price in ((self.p, self.i, '10', '10'), (self.p, self.other_i, '5', '20'),
                               (self.other_p, self.i, '3', '10')):
            self.ledger.apply_synthetic_fill(p, SyntheticFill(uid(), i, Side.BUY, D(q), D(price), uid()),
                                              idempotency_key=uid())
        self.posted = self.ledger.state(self.p)
        self.old_history = self.ledger.history(self.p)
        migrate_order_schema(self.path, confirmed=True)
        self.old_store = OrderStore(self.path)
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                       'socket.getaddrinfo', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('Offline reservations'))
            guard.start()
            self.addCleanup(guard.stop)

    def request(self, side=Side.BUY, amount='100', *, p=None, i=None, quantity='2', states=()):
        initial = replace(self.initial, intent=replace(self.initial.intent, order_id=uid(),
            idempotency_key=uid(), portfolio_id=p or self.p, instrument_id=i or self.i,
            side=side, quantity=D(quantity)))
        actor = uid()
        snapshots = [initial]
        for state, at, evidence in states:
            snapshots.append(transition_order(snapshots[-1], state, at=at, expiration=evidence))
        events = seed(self.path, snapshots, actor=actor)
        return Reservation(uid(), uid(), initial.intent.portfolio_id, initial.intent.order_id,
            initial.intent.instrument_id, events[0].command_id, events[0].event_id, actor,
            initial.intent.idempotency_key, Resource.CASH if side is Side.BUY else Resource.SHARES,
            D(amount), 'USD' if side is Side.BUY else None)

    def up(self):
        return migrate_reservation_schema(self.path, confirmed=True)

    def reserve(self, request):
        self.up()
        return _reserve_synthetic(self.path, request)

    def state(self, p=None):
        return ReservationStore(self.path).state(p or self.p)

    def shares(self, i=None, p=None):
        return dict(self.state(p).shares)[i or self.i]

    def rows(self, table):
        with sqlite3.connect(self.path) as c:
            return c.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()

    def error(self, code, call):
        with self.assertRaises(VirtualError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code.value)

    def corrupt(self, table, sql, args=(), *, deleting=False):
        with sqlite3.connect(self.path) as c:
            name = table + ('_no_delete' if deleting else '_no_update')
            trigger = c.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()[0]
            c.execute('DROP TRIGGER '+name)
            c.execute(sql, args)
            c.execute(trigger)

    def test_buy_cash_reservation_and_association(self):
        r = self.request()
        result = self.reserve(r)
        self.assertEqual((result.request, result.code), (r, Code.RESERVED))
        self.assertEqual(ReservationStore(self.path).reservation(self.p, r.order_id), r)
        self.assertEqual(self.state().cash.posted, D(800))
        self.assertEqual(self.state().cash.reserved, D(100))
        self.assertEqual(self.state().cash.available, D(700))

    def test_sell_share_reservation(self):
        r = self.request(Side.SELL, '3', quantity='3')
        self.assertEqual(self.reserve(r).code, Code.RESERVED)
        self.assertEqual((self.shares().posted, self.shares().reserved, self.shares().available), (D(10), D(3), D(7)))
        self.assertEqual(self.state().cash.available, D(800))

    def test_full_cash_reservation(self):
        self.reserve(self.request(amount='800'))
        self.assertEqual(self.state().cash.available, D(0))

    def test_full_share_reservation(self):
        self.reserve(self.request(Side.SELL, '10', quantity='10'))
        self.assertEqual(self.shares().available, D(0))

    def test_insufficient_cash_is_durable_denial_without_reservation(self):
        r = self.request(amount='801')
        outcome = self.reserve(r)
        self.assertEqual(outcome.code, Code.INSUFFICIENT_CASH)
        self.assertEqual(self.state().reservations, ())
        self.assertEqual(len(self.rows('virtual_reservation_operations')), 1)
        self.assertEqual(_reserve_synthetic(self.path, r), outcome)

    def test_insufficient_shares_is_durable_denial(self):
        r = self.request(Side.SELL, '11', quantity='11')
        self.assertEqual(self.reserve(r).code, Code.INSUFFICIENT_SHARES)
        self.assertEqual(self.shares().available, D(10))
        self.assertEqual(ReservationStore(self.path).reservation(self.p, r.order_id), None)

    def test_pending_buy_does_not_create_shares(self):
        self.reserve(self.request(quantity='100'))
        self.assertEqual(self.reserve(self.request(Side.SELL, '11', quantity='11')).code, Code.INSUFFICIENT_SHARES)

    def test_pending_sell_does_not_create_cash(self):
        self.reserve(self.request(Side.SELL, '10', quantity='10'))
        self.assertEqual(self.reserve(self.request(amount='801')).code, Code.INSUFFICIENT_CASH)

    def test_zero_negative_float_nonfinite_and_bounds_rejected(self):
        r = self.request()
        for amount in (D(0), D(-1), 1.0, 1, True, D('NaN'), D('sNaN'), D('Infinity'), D('1e13'), D('0.001')):
            with self.subTest(amount=str(amount)):
                self.error(ErrorCode.INVALID_INPUT, lambda: replace(r, amount=amount))

    def test_share_fractional_and_excess_quantity_rejected(self):
        r = self.request(Side.SELL, '2')
        for amount in (D('1.5'), D('1000001'), D(0), 2.0, D('NaN')):
            self.error(ErrorCode.INVALID_INPUT, lambda: replace(r, amount=amount))

    def test_cash_currency_and_share_currency_strict(self):
        r = self.request()
        for currency in (None, 'KRW', 1):
            self.error(ErrorCode.INVALID_INPUT, lambda: replace(r, currency=currency))
        r = self.request(Side.SELL, '2')
        self.error(ErrorCode.INVALID_INPUT, lambda: replace(r, currency='USD'))

    def test_enum_and_uuid_validation(self):
        r = self.request()
        for field, value in (('resource', 'CASH'), ('status', 'ACTIVE'), ('status', 'RELEASED'),
                             ('portfolio_id', 'bad'), ('operation_id', 'bad'), ('creation_event_id', 'bad')):
            self.error(ErrorCode.INVALID_INPUT, lambda: replace(r, **{field: value}))

    def test_immutable_values(self):
        r = self.request()
        with self.assertRaises(FrozenInstanceError):
            r.amount = D(200)
        outcome = self.reserve(r)
        with self.assertRaises(FrozenInstanceError):
            outcome.code = Code.INSUFFICIENT_CASH
        with self.assertRaises(FrozenInstanceError):
            self.state().cash.available = D(999)

    def test_codec_canonical_round_trip(self):
        r = self.request(amount='100.00')
        self.assertEqual(decode_reservation(encode_reservation(r)), r)
        self.assertEqual(encode_reservation(replace(r, amount=D('1e2'))), encode_reservation(r))

    def test_codec_rejects_unknown_missing_duplicate_and_noncanonical(self):
        text = encode_reservation(self.request())
        data = json.loads(text)
        values = [' '+text, text.replace('"version":1', '"version":1,"version":1'), '{}', '[]', 'NaN', 'x'*32769]
        for field, value in (('version', 2), ('version', True), ('amount', 100.0), ('amount', '1e2'),
                             ('amount', 'NaN'), ('status', 'RELEASED'), ('extra', 'x')):
            values.append(json.dumps(dict(data, **{field: value}), sort_keys=True, separators=(',', ':')))
        del data['command_id']
        values.append(json.dumps(data))
        for value in values:
            self.error(ErrorCode.INVALID_LEDGER, lambda: decode_reservation(value))

    def test_hostile_decimal_context_does_not_change_math(self):
        r = self.request(amount='799.99')
        self.up()
        with localcontext() as context:
            context.prec = 1
            context.traps[Inexact] = True
            _reserve_synthetic(self.path, r)
            self.assertEqual(self.state().cash.available, D('.01'))

    def test_forged_frozen_object_revalidated(self):
        r = self.request()
        object.__setattr__(r, 'amount', D(-1))
        self.error(ErrorCode.INVALID_INPUT, lambda: encode_reservation(r))

    def test_wrong_direction(self):
        r = replace(self.request(), resource=Resource.SHARES, amount=D(2), currency=None)
        self.up()
        self.error(ErrorCode.INVALID_LEDGER, lambda: _reserve_synthetic(self.path, r))

    def test_wrong_portfolio_or_missing_order(self):
        r = self.request()
        self.up()
        for change in ({'portfolio_id': self.other_p}, {'order_id': uid()}):
            with self.assertRaises(PersistenceError):
                _reserve_synthetic(self.path, replace(r, **change))
        self.assertEqual(self.rows('virtual_reservation_operations'), [])

    def test_wrong_instrument_command_event_actor_or_key(self):
        r = self.request()
        self.up()
        for field in ('instrument_id', 'command_id', 'creation_event_id', 'actor_id', 'idempotency_key'):
            self.error(ErrorCode.INVALID_LEDGER, lambda: _reserve_synthetic(self.path, replace(r, **{field: uid()})))

    def test_sell_quantity_must_equal_entire_order(self):
        r = replace(self.request(Side.SELL, '2'), amount=D(1))
        self.up()
        self.error(ErrorCode.INVALID_LEDGER, lambda: _reserve_synthetic(self.path, r))

    def test_noninitial_or_terminal_orders_cannot_reserve(self):
        chains = (((OrderState.FROZEN, CUT, None),),
                  ((OrderState.FROZEN, CUT, None), (OrderState.AWAITING_PRICE, OPEN, None)),
                  ((OrderState.CANCELLED, self.initial.changed_at, None),),
                  ((OrderState.EXPIRED, DEADLINE, deadline_evidence()),))
        for chain in chains:
            r = self.request(states=chain)
            self.up()
            self.error(ErrorCode.INVALID_LEDGER, lambda: _reserve_synthetic(self.path, r))

    def test_identical_replay_after_reopen(self):
        r = self.request()
        first = self.reserve(r)
        before = self.path.read_bytes()
        self.assertEqual(_reserve_synthetic(self.path, replace(r, amount=D('100.00'))), first)
        self.assertEqual(ReservationStore(self.path).outcome(self.p, r.actor_id, r.idempotency_key), first)
        self.assertEqual(self.path.read_bytes(), before)

    def test_conflicting_replay_fails_closed(self):
        r = self.request()
        self.reserve(r)
        before = self.path.read_bytes()
        for field, value in (('amount', D(200)), ('reservation_id', uid()), ('operation_id', uid()), ('order_id', uid())):
            self.error(ErrorCode.IDEMPOTENCY_CONFLICT, lambda: _reserve_synthetic(self.path, replace(r, **{field: value})))
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_reservation_identity_different_order(self):
        r = self.request()
        other = replace(self.request(), reservation_id=r.reservation_id)
        self.reserve(r)
        self.error(ErrorCode.IDENTITY_CONFLICT, lambda: _reserve_synthetic(self.path, other))
        self.assertEqual(len(self.state().reservations), 1)

    def test_cross_portfolio_isolation(self):
        a, b = self.request(amount='800'), self.request(p=self.other_p, amount='470')
        self.reserve(a)
        self.reserve(b)
        self.assertEqual(self.state().cash.available, D(0))
        self.assertEqual(self.state(self.other_p).cash.available, D(0))
        with self.assertRaises(PersistenceError):
            ReservationStore(self.path).reservation(self.other_p, a.order_id)
        self.assertIsNone(ReservationStore(self.path).outcome(self.other_p, a.actor_id, a.idempotency_key))

    def test_different_instruments_are_separate(self):
        self.reserve(self.request(Side.SELL, '10', quantity='10'))
        self.reserve(self.request(Side.SELL, '5', quantity='5', i=self.other_i))
        self.assertEqual(self.shares().available, D(0))
        self.assertEqual(self.shares(self.other_i).available, D(0))
        self.assertEqual(self.state().cash.available, D(800))

    def test_unknown_scope_not_silently_zero(self):
        self.up()
        self.error(ErrorCode.UNKNOWN_PORTFOLIO, lambda: ReservationStore(self.path).state(uid()))
        with self.assertRaises(TypeError):
            ReservationStore(self.path).state()

    def test_read_only_and_deterministic_reconstruction(self):
        for amount in ('10', '20', '30'):
            self.reserve(self.request(amount=amount))
        before = self.path.read_bytes()
        a, b = self.state(), self.state()
        self.assertEqual(a, b)
        self.assertEqual([r.reservation_id for r in a.reservations], sorted(r.reservation_id for r in a.reservations))
        self.assertEqual(self.path.read_bytes(), before)

    def race(self, requests):
        self.up()
        barrier = Barrier(len(requests))
        def invoke(r):
            barrier.wait(timeout=10)
            try:
                return _reserve_synthetic(self.path, r).code
            except VirtualError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            futures = [pool.submit(invoke, r) for r in requests]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_buy_competes_for_cash(self):
        outcomes = self.race([self.request(amount='500'), self.request(amount='500')])
        self.assertCountEqual(outcomes, [Code.RESERVED, Code.INSUFFICIENT_CASH])
        self.assertEqual(self.state().cash.available, D(300))

    def test_concurrent_sell_competes_for_shares(self):
        outcomes = self.race([self.request(Side.SELL, '7', quantity='7'), self.request(Side.SELL, '7', quantity='7')])
        self.assertCountEqual(outcomes, [Code.RESERVED, Code.INSUFFICIENT_SHARES])
        self.assertEqual(self.shares().available, D(3))

    def test_concurrent_identical_requests(self):
        r = self.request()
        self.assertEqual(self.race([r, r]), [Code.RESERVED, Code.RESERVED])
        self.assertEqual(len(self.rows('virtual_reservation_operations')), 1)
        self.assertEqual(len(self.rows('virtual_order_reservations')), 1)

    def test_concurrent_conflicting_key(self):
        r = self.request()
        self.assertCountEqual(self.race([r, replace(r, amount=D(200))]), [Code.RESERVED, ErrorCode.IDEMPOTENCY_CONFLICT])
        self.assertEqual(len(self.state().reservations), 1)

    def test_concurrent_separate_portfolios_and_instruments(self):
        outcomes = self.race([self.request(Side.SELL, '10', quantity='10'),
            self.request(Side.SELL, '5', quantity='5', i=self.other_i),
            self.request(Side.SELL, '3', quantity='3', p=self.other_p)])
        self.assertEqual(outcomes, [Code.RESERVED]*3)

    def test_concurrent_reader_sees_complete_snapshot(self):
        r = self.request()
        self.up()
        reached, release = Event(), Event()
        original = sqlite3.connect
        class Paused(sqlite3.Connection):
            def execute(connection, sql, *args):
                result = super().execute(sql, *args)
                if sql.startswith('INSERT INTO virtual_order_reservations'):
                    reached.set()
                    if not release.wait(10): raise AssertionError('reader did not finish')
                return result
        with patch('sqlite3.connect', side_effect=lambda *a, **kw: original(*a, factory=Paused, **kw)):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_reserve_synthetic, self.path, r)
                try:
                    self.assertTrue(reached.wait(10))
                    self.assertEqual(self.state().cash.available, D(800))
                finally:
                    release.set()
                self.assertEqual(future.result(timeout=10).code, Code.RESERVED)
        self.assertEqual(self.state().cash.available, D(700))

    def test_lock_timeout_and_safe_retry(self):
        r = self.request()
        self.up()
        with sqlite3.connect(self.path) as c:
            c.execute('BEGIN IMMEDIATE')
            self.error(ErrorCode.STORAGE_UNAVAILABLE, lambda: _reserve_synthetic(self.path, r, timeout=.01))
        self.assertEqual(self.state().reservations, ())
        self.assertEqual(_reserve_synthetic(self.path, r).code, Code.RESERVED)

    def test_faults_after_each_write_and_before_commit_roll_back(self):
        r = self.request()
        self.up()
        original = sqlite3.connect
        for failure in ('INSERT INTO virtual_reservation_operations', 'INSERT INTO virtual_order_reservations', 'COMMIT'):
            class Failed(sqlite3.Connection):
                def execute(connection, sql, *args):
                    result = super().execute(sql, *args)
                    if sql.startswith(failure): raise sqlite3.OperationalError('synthetic failure')
                    return result
                def commit(connection):
                    if failure == 'COMMIT': raise sqlite3.OperationalError('synthetic commit failure')
                    return super().commit()
            before = self.path.read_bytes()
            with patch('sqlite3.connect', side_effect=lambda *a, **kw: original(*a, factory=Failed, **kw)):
                self.error(ErrorCode.STORAGE_UNAVAILABLE, lambda: _reserve_synthetic(self.path, r))
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(self.state().reservations, ())

    def test_failure_in_final_reconstruction_rolls_back(self):
        from magi.virtual import reservation_storage as module
        r = self.request()
        self.up()
        original = module._replay
        calls = []
        def fail(connection):
            calls.append(None)
            if len(calls) == 2: raise VirtualError(ErrorCode.INVALID_LEDGER)
            return original(connection)
        with patch.object(module, '_replay', side_effect=fail):
            self.error(ErrorCode.INVALID_LEDGER, lambda: _reserve_synthetic(self.path, r))
        self.assertEqual(self.state().reservations, ())
        self.assertEqual(self.rows('virtual_reservation_operations'), [])

    def test_uncertain_success_retries_original_outcome(self):
        r = self.request()
        self.up()
        original = sqlite3.connect
        class LostReply(sqlite3.Connection):
            def commit(connection):
                super().commit()
                raise sqlite3.OperationalError('reply lost after success')
        with patch('sqlite3.connect', side_effect=lambda *a, **kw: original(*a, factory=LostReply, **kw)):
            self.error(ErrorCode.STORAGE_UNAVAILABLE, lambda: _reserve_synthetic(self.path, r))
        self.assertEqual(_reserve_synthetic(self.path, r).code, Code.RESERVED)
        self.assertEqual(len(self.state().reservations), 1)
        self.assertEqual(len(self.rows('virtual_reservation_operations')), 1)

    def child(self, request, extra=''):
        script = '''
import sys, pathlib, sqlite3, os
sys.path.insert(0, sys.argv[1])
from magi.virtual.reservations import decode_reservation
from magi.virtual.models import VirtualError
from magi.virtual.reservation_storage import _reserve_synthetic
path=pathlib.Path(sys.argv[2]); request=decode_reservation(sys.argv[3])
''' + extra + '''
try:
    print(_reserve_synthetic(path, request).code.value, flush=True)
except VirtualError as error:
    print(error.code.value, flush=True)
'''
        return [sys.executable, '-I', '-B', '-c', script, str(ROOT), str(self.path), encode_reservation(request)]

    def test_fresh_process_retry_after_commit(self):
        r = self.request()
        self.reserve(r)
        before = self.path.read_bytes()
        result = subprocess.run(self.child(r), env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'RESERVED')
        self.assertEqual(self.path.read_bytes(), before)

    def test_process_interruption_before_commit_has_no_effect(self):
        r = self.request()
        self.up()
        script = '''
original=sqlite3.connect
class Crash(sqlite3.Connection):
    def execute(self, sql, *args):
        result=super().execute(sql,*args)
        if sql.startswith('INSERT INTO virtual_order_reservations'): os._exit(23)
        return result
sqlite3.connect=lambda *a,**kw: original(*a,factory=Crash,**kw)
'''
        result = subprocess.run(self.child(r, script), env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(self.state().reservations, ())
        self.assertEqual(self.rows('virtual_reservation_operations'), [])
        self.assertEqual(_reserve_synthetic(self.path, r).code, Code.RESERVED)

    def process_race(self, requests):
        self.up()
        children = [subprocess.Popen(self.child(r, "print('ready',flush=True); sys.stdin.readline()"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={'PATH': '/usr/bin:/bin'}) for r in requests]
        try:
            for child in children: self.assertEqual(child.stdout.readline().strip(), 'ready')
            for child in children:
                child.stdin.write('go\n'); child.stdin.flush()
            results = [child.communicate(timeout=20) for child in children]
            for child, (_, error) in zip(children, results): self.assertEqual(child.returncode, 0, error)
            return [out.strip() for out, _ in results]
        finally:
            for child in children:
                if child.poll() is None: child.kill(); child.wait()

    def test_independent_process_buy_race(self):
        self.assertCountEqual(self.process_race([self.request(amount='500'), self.request(amount='500')]),
                              ['RESERVED', 'INSUFFICIENT_CASH'])

    def test_independent_process_sell_race(self):
        self.assertCountEqual(self.process_race([self.request(Side.SELL, '7', quantity='7'),
            self.request(Side.SELL, '7', quantity='7')]), ['RESERVED', 'INSUFFICIENT_SHARES'])

    def test_independent_process_identical_retries(self):
        r = self.request()
        self.assertEqual(self.process_race([r, r]), ['RESERVED', 'RESERVED'])
        self.assertEqual(len(self.rows('virtual_reservation_operations')), 1)
        self.assertEqual(len(self.state().reservations), 1)

    def test_independent_process_conflicting_retries(self):
        r = self.request()
        self.assertCountEqual(self.process_race([r, replace(r, amount=D(200))]),
                              ['RESERVED', 'IDEMPOTENCY_CONFLICT'])
        self.assertEqual(len(self.state().reservations), 1)

    def test_denial_after_restart_preserves_identity_and_has_no_effect(self):
        r = self.request(amount='900')
        self.reserve(r)
        result = subprocess.run(self.child(r), env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'INSUFFICIENT_CASH')
        self.assertEqual(len(self.rows('virtual_reservation_operations')), 1)
        self.assertEqual(self.state().reservations, ())

    def test_validly_encoded_impossible_cap_is_corrupt(self):
        r = self.request()
        self.reserve(r)
        bad = encode_reservation(replace(r, amount=D(801)))
        self.corrupt('virtual_reservation_operations',
                     'UPDATE virtual_reservation_operations SET request_json=?', (bad,))
        self.corrupt('virtual_order_reservations',
                     "UPDATE virtual_order_reservations SET amount='801',reservation_json=?", (bad,))
        self.error(ErrorCode.INVALID_LEDGER, self.state)

    def test_validly_encoded_impossible_shares_are_corrupt(self):
        r = self.request(Side.SELL, '2')
        self.reserve(r)
        bad = encode_reservation(replace(r, amount=D(11)))
        self.corrupt('virtual_reservation_operations',
                     'UPDATE virtual_reservation_operations SET request_json=?', (bad,))
        self.corrupt('virtual_order_reservations',
                     "UPDATE virtual_order_reservations SET amount='11',reservation_json=?", (bad,))
        self.error(ErrorCode.INVALID_LEDGER, self.state)

    def test_corrupt_reservation_payload_or_index_rejected(self):
        self.reserve(self.request())
        for sql in ("UPDATE virtual_order_reservations SET reservation_json='{}'",
                    "UPDATE virtual_order_reservations SET amount='999'",
                    "UPDATE virtual_order_reservations SET amount='NaN'"):
            before = self.path.read_bytes()
            self.corrupt('virtual_order_reservations', sql)
            self.error(ErrorCode.INVALID_LEDGER, self.state)
            self.path.write_bytes(before)

    def test_orphan_missing_effect_and_wrong_scope_rejected(self):
        self.reserve(self.request())
        for table, sql in (('virtual_order_reservations', 'DELETE FROM virtual_order_reservations'),
                           ('virtual_reservation_operations', 'DELETE FROM virtual_reservation_operations')):
            before = self.path.read_bytes()
            self.corrupt(table, sql, deleting=True)
            with self.assertRaises(ValueError): self.state()
            self.path.write_bytes(before)
        self.corrupt('virtual_order_reservations', 'UPDATE virtual_order_reservations SET portfolio_id=?', (self.other_p,))
        with self.assertRaises(ValueError): self.state()

    def test_corrupted_operation_outcome_and_sequence_rejected(self):
        self.reserve(self.request())
        for sql in ("UPDATE virtual_reservation_operations SET request_json='{}'",
                    'UPDATE virtual_reservation_operations SET sequence=2',
                    "UPDATE virtual_reservation_operations SET outcome='INSUFFICIENT_CASH',result_reservation_id=NULL"):
            before = self.path.read_bytes()
            self.corrupt('virtual_reservation_operations', sql)
            with self.assertRaises(ValueError): self.state()
            self.path.write_bytes(before)

    def test_corrupt_order_or_ledger_rejected_before_reserve(self):
        r = self.request()
        self.up()
        for table, sql in (('virtual_orders', "UPDATE virtual_orders SET initial_json='{}'"),
                           ('virtual_events', "UPDATE virtual_events SET amount='999' WHERE kind='FUNDING'")):
            before = self.path.read_bytes()
            self.corrupt(table, sql)
            with self.assertRaises(ValueError): _reserve_synthetic(self.path, r)
            self.path.write_bytes(before)
        self.assertEqual(self.rows('virtual_order_reservations'), [])

    def test_sql_immutability_and_duplicate_effect_constraints(self):
        self.reserve(self.request())
        with sqlite3.connect(self.path) as c:
            c.execute('PRAGMA foreign_keys=ON')
            for table in ('virtual_reservation_operations', 'virtual_order_reservations'):
                for sql in (f'UPDATE {table} SET rowid=rowid', f'DELETE FROM {table}', f'INSERT INTO {table} SELECT * FROM {table}'):
                    with self.assertRaises(sqlite3.IntegrityError): c.execute(sql)

    def test_no_reservation_without_matching_operation_at_commit(self):
        r = self.request()
        self.up()
        from magi.virtual.reservation_storage import _reservation_row
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(self.path) as c:
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('INSERT INTO virtual_order_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', _reservation_row(r))
        self.assertEqual(self.state().reservations, ())

    def test_migration_preserves_all_existing_rows_and_histories(self):
        r = self.request(states=((OrderState.FROZEN, CUT, None),))
        tables = ('virtual_portfolios', 'virtual_instruments', 'virtual_events', 'virtual_orders',
                  'virtual_order_commands', 'virtual_order_events')
        rows = {t: self.rows(t) for t in tables}
        history = self.old_store.history(self.p, r.order_id)
        self.assertEqual(self.up(), 3)
        self.assertEqual({t: self.rows(t) for t in tables}, rows)
        from magi.virtual.reservation_storage import _ReservationDatabase
        from magi.virtual.order_storage import _history
        with _ReservationDatabase(self.path)._connection() as c:
            self.assertEqual(VirtualLedger._events(c, self.p), self.old_history)
            self.assertEqual(_history(c, self.p, r.order_id)[1], history)
        self.assertEqual(self.state().cash.posted, self.posted.cash)

    def test_repeat_migration_verified_noop_with_reservations(self):
        self.reserve(self.request())
        before = self.path.read_bytes()
        self.assertEqual(self.up(), 3)
        self.assertEqual(self.path.read_bytes(), before)

    def test_migration_requires_exact_confirmation(self):
        before = self.path.read_bytes()
        for flag in (False, None, 1, 'yes'):
            self.error(ErrorCode.CONFIRMATION_REQUIRED, lambda: migrate_reservation_schema(self.path, confirmed=flag))
        self.assertEqual(self.path.read_bytes(), before)

    def test_old_v1_v2_readers_and_migrator_reject_v3(self):
        self.up()
        for call in (lambda: VirtualLedger(self.path), lambda: self.ledger.state(self.p),
                     lambda: self.old_store.orders(self.p), lambda: OrderStore(self.path),
                     lambda: migrate_order_schema(self.path, confirmed=True),
                     lambda: self.ledger.fund(self.p, D(1), idempotency_key=uid(), confirmed=True)):
            self.error(ErrorCode.INVALID_LEDGER, call)

    def test_new_reads_never_migrate_v2(self):
        before = self.path.read_bytes()
        self.error(ErrorCode.INVALID_LEDGER, lambda: ReservationStore(self.path))
        self.assertEqual(self.path.read_bytes(), before)

    def test_reservation_migration_rejects_v1(self):
        path = self.root/'v1.virtual.sqlite3'
        VirtualLedger(path)
        before = path.read_bytes()
        self.error(ErrorCode.INVALID_LEDGER, lambda: migrate_reservation_schema(path, confirmed=True))
        self.assertEqual(path.read_bytes(), before)

    def test_unknown_modified_and_extra_schema_rejected(self):
        for sql in ('PRAGMA user_version=999', 'CREATE TABLE extra(x)', 'DROP TRIGGER virtual_orders_no_update'):
            copy = self.root/(uid()+'.virtual.sqlite3')
            copy.write_bytes(self.path.read_bytes()); copy.chmod(0o600)
            with sqlite3.connect(copy) as c: c.execute(sql)
            before = copy.read_bytes()
            self.error(ErrorCode.INVALID_LEDGER, lambda: migrate_reservation_schema(copy, confirmed=True))
            self.assertEqual(copy.read_bytes(), before)

    def test_partial_or_modified_v3_rejected(self):
        self.up()
        with sqlite3.connect(self.path) as c: c.execute('DROP TRIGGER virtual_order_reservations_no_delete')
        self.error(ErrorCode.INVALID_LEDGER, self.state)
        self.error(ErrorCode.INVALID_LEDGER, self.up)

    def test_corrupt_v2_history_blocks_migration_before_ddl(self):
        self.request()
        self.corrupt('virtual_orders', "UPDATE virtual_orders SET initial_json='{}'")
        before = self.path.read_bytes()
        with self.assertRaises(PersistenceError): self.up()
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertFalse(c.execute("SELECT name FROM sqlite_master WHERE name='virtual_order_reservations'").fetchall())

    def test_final_migration_validation_failure_rolls_back(self):
        before = self.path.read_bytes()
        with patch('magi.virtual.reservation_storage._replay', side_effect=VirtualError(ErrorCode.INVALID_LEDGER)):
            self.error(ErrorCode.INVALID_LEDGER, self.up)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(OrderStore(self.path).ledger_state(self.p), self.posted)

    def test_migration_lock_timeout_does_not_change_v2(self):
        with sqlite3.connect(self.path) as c:
            c.execute('BEGIN IMMEDIATE')
            self.error(ErrorCode.STORAGE_UNAVAILABLE, lambda: migrate_reservation_schema(
                self.path, confirmed=True, timeout=.01))
        self.assertEqual(OrderStore(self.path).ledger_state(self.p), self.posted)

    def test_migration_ddl_validation_and_commit_faults_rollback(self):
        original = sqlite3.connect
        for failure in ('CREATE TABLE virtual_reservation_operations', 'CREATE TABLE virtual_order_reservations',
                        'PRAGMA user_version = 3', 'COMMIT'):
            class Failed(sqlite3.Connection):
                def execute(connection, sql, *args):
                    result = super().execute(sql, *args)
                    if sql.startswith(failure): raise sqlite3.OperationalError('migration failed')
                    return result
                def commit(connection):
                    if failure == 'COMMIT': raise sqlite3.OperationalError('commit failed')
                    return super().commit()
            before = self.path.read_bytes()
            with patch('sqlite3.connect', side_effect=lambda *a, **kw: original(*a, factory=Failed, **kw)):
                self.error(ErrorCode.STORAGE_UNAVAILABLE, self.up)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(OrderStore(self.path).ledger_state(self.p), self.posted)

    def test_missing_foreign_and_owner_paths_not_opened(self):
        missing = self.root/'missing.virtual.sqlite3'
        for path in (missing, ROOT/'magi.db', ROOT/'bad.virtual.sqlite3', ':memory:', 'relative.virtual.sqlite3'):
            with self.assertRaises(VirtualError): ReservationStore(path)
            with self.assertRaises(VirtualError): migrate_reservation_schema(path, confirmed=True)
        self.assertFalse(missing.exists())
        foreign = self.root/'foreign.virtual.sqlite3'
        foreign.touch(mode=0o600)
        before = foreign.read_bytes()
        with self.assertRaises(VirtualError): migrate_reservation_schema(foreign, confirmed=True)
        self.assertEqual(foreign.read_bytes(), before)

    def test_no_ledger_or_order_mutation_and_no_release_or_fill_api(self):
        r = self.request()
        tables = ('virtual_events', 'virtual_orders', 'virtual_order_commands', 'virtual_order_events')
        rows = {t: self.rows(t) for t in tables}
        self.reserve(r)
        self.assertEqual({t: self.rows(t) for t in tables}, rows)
        self.assertEqual({n for n in dir(ReservationStore) if not n.startswith('_')}, {'state', 'reservation', 'outcome'})
        self.assertEqual(tuple(ReservationStatus), (ReservationStatus.ACTIVE,))
        with self.assertRaises(ValueError): replace(self.initial, state=OrderState.FILLED)

    def test_import_and_read_cannot_load_owner_broker_credentials_or_network(self):
        r = self.request()
        self.reserve(r)
        script = '''
import sys,pathlib
root=pathlib.Path(sys.argv[1]); path=pathlib.Path(sys.argv[2])
def guard(event,args):
    if event=='import' and args[0].startswith(('magi.broker','magi.toss','magi.storage','magi.portfolio','httpx','dotenv')): raise AssertionError('forbidden import')
    if event.startswith('socket.') and event!='socket.__new__': raise AssertionError('network')
    if event=='open' and isinstance(args[0],str) and pathlib.Path(args[0]).name=='.env': raise AssertionError('credentials')
    if event=='sqlite3.connect' and str(args[0])!=path.as_uri()+'?mode=ro': raise AssertionError('unexpected DB')
sys.addaudithook(guard);sys.path.insert(0,str(root))
from magi.virtual.reservation_storage import ReservationStore
assert ReservationStore(path).state(sys.argv[3]).cash.available==700
print('isolated')
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT), str(self.path), self.p],
            env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'isolated')


if __name__ == '__main__':
    unittest.main()

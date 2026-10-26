"""Package A fixtures only: no admission, reservation, trusted price or broker."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext, Inexact
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from magi.virtual import VirtualLedger, Instrument, SyntheticFill, Side, VirtualError, ErrorCode
from magi.virtual.order_codec import (OrderEvent, PersistenceError, PersistenceCode,
    encode_snapshot, decode_snapshot, encode_event, decode_event, reconstruct_order)
from magi.virtual.order_storage import OrderStore, migrate_order_schema
from magi.virtual.order_lifecycle import OrderState, transition_order
from test_virtual_order_lifecycle import accepted, CUT, OPEN, DEADLINE, deadline_evidence, invalidation

ROOT = Path(__file__).resolve().parents[1]


def uid():
    return str(uuid4())


def event(snapshot, revision=1, prior=None, **changes):
    values = dict(event_id=uid(), command_id=uid(), revision=revision, prior_state=prior,
                  snapshot=snapshot, recorded_at=snapshot.changed_at+timedelta(seconds=1))
    values.update(changes)
    return OrderEvent(**values)


def seed(path, snapshots, *, actor=None, commands=None):
    """Privileged test fixture SQL; deliberately absent from production modules."""
    initial = snapshots[0]
    i = initial.intent
    events = tuple(event(s, n, None if n == 1 else snapshots[n-2].state,
                         **({} if commands is None else {'command_id': commands[n-1]}))
                   for n, s in enumerate(snapshots, 1))
    actor = actor or uid()
    with sqlite3.connect(path) as c:
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO virtual_orders VALUES (?,?,?,?,?,?)',
            (i.order_id, i.portfolio_id, i.instrument_id, 'USD', events[0].command_id, encode_snapshot(initial)))
        for key in dict.fromkeys(e.command_id for e in events):
            last = [e for e in events if e.command_id == key][-1]
            c.execute('INSERT INTO virtual_order_commands VALUES (?,?,?,?,?,?,?)',
                (key, i.portfolio_id, i.order_id, actor, 'SUBMIT' if last.revision == 1 else 'TRANSITION',
                 i.idempotency_key if last.revision == 1 else uid(), last.revision))
        for e in events:
            c.execute('INSERT INTO virtual_order_events VALUES (?,?,?,?,?,?,?,?,?,?)',
                (e.event_id, i.portfolio_id, i.order_id, e.revision, e.command_id,
                 None if e.prior_state is None else e.prior_state.value, e.snapshot.state.value,
                 e.snapshot.changed_at.isoformat(timespec='microseconds'),
                 e.recorded_at.isoformat(timespec='microseconds'), encode_event(e)))
    return events


class CodecTests(unittest.TestCase):
    def invalid(self, call):
        with self.assertRaises(PersistenceError) as caught:
            call()
        self.assertIs(caught.exception.code, PersistenceCode.INVALID_RECORD)
        self.assertEqual(str(caught.exception), 'INVALID_RECORD')

    def changed(self, mutate):
        data = json.loads(encode_snapshot(accepted()))
        mutate(data)
        return json.dumps(data, sort_keys=True, separators=(',', ':'))

    def test_snapshot_round_trip(self):
        value = accepted()
        self.assertEqual(decode_snapshot(encode_snapshot(value)), value)
        self.assertEqual(encode_snapshot(decode_snapshot(encode_snapshot(value))), encode_snapshot(value))

    def test_all_supported_states_and_expiration_evidence_round_trip(self):
        a = accepted(); f = transition_order(a, OrderState.FROZEN, at=CUT)
        values = (a, f, transition_order(f, OrderState.AWAITING_PRICE, at=OPEN),
                  transition_order(a, OrderState.CANCELLED, at=a.changed_at),
                  transition_order(a, OrderState.EXPIRED, at=DEADLINE, expiration=deadline_evidence()),
                  transition_order(a, OrderState.EXPIRED, at=CUT, expiration=invalidation()))
        for value in values:
            with self.subTest(state=value.state):
                self.assertEqual(decode_snapshot(encode_snapshot(value)), value)

    def test_microseconds_and_timezone_round_trip(self):
        a = accepted(); at = a.changed_at+timedelta(microseconds=123)
        a = replace(a, selection=replace(a.selection, accepted_at=at), changed_at=at)
        self.assertEqual(decode_snapshot(encode_snapshot(a)).changed_at.microsecond, 123)
        self.assertIn('+00:00', encode_snapshot(a))
        self.assertEqual(decode_snapshot(encode_snapshot(a)).selection.session.trading_timezone, 'America/New_York')

    def test_decimal_canonical_round_trip_under_hostile_context(self):
        a = accepted(); a = replace(a, intent=replace(a.intent, quantity=D('999999.00')))
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = True
            b = decode_snapshot(encode_snapshot(a))
        self.assertEqual(a, b)
        self.assertIn('"quantity":"999999"', encode_snapshot(a))

    def test_optional_deadline_remains_none(self):
        a = accepted(); a = replace(a, selection=replace(a.selection,
            policy=replace(a.selection.policy, price_wait_after_close=None)))
        self.assertIsNone(decode_snapshot(encode_snapshot(a)).selection.deadline_at)

    def test_missing_and_unknown_fields_at_every_level(self):
        for path in ((), ('intent',), ('selection',), ('selection','policy'),
                     ('selection','session'), ('selection','session','source')):
            for unknown in (False, True):
                def mutate(data):
                    for name in path: data = data[name]
                    if unknown: data['extra'] = 'forbidden'
                    else: del data[next(iter(data))]
                with self.subTest(path=path, unknown=unknown):
                    self.invalid(lambda: decode_snapshot(self.changed(mutate)))

    def test_invalid_uuids(self):
        for field in ('order_id','portfolio_id','instrument_id','idempotency_key'):
            self.invalid(lambda: decode_snapshot(self.changed(lambda d: d['intent'].__setitem__(field,'bad'))))

    def test_quantity_does_not_coerce(self):
        for value in (10, 10.0, True, None, '1.5', 'NaN', 'Infinity', '-1', '1000001', '1e2'):
            with self.subTest(value=value):
                self.invalid(lambda: decode_snapshot(self.changed(lambda d: d['intent'].__setitem__('quantity',value))))

    def test_enum_and_version_validation(self):
        for field,value in (('state','FILLED'),('state','REJECTED'),('state','accepted'),('version',True),('version',2)):
            self.invalid(lambda: decode_snapshot(self.changed(lambda d: d.__setitem__(field,value))))
        self.invalid(lambda: decode_snapshot(self.changed(lambda d: d['intent'].__setitem__('side','HOLD'))))

    def test_timestamp_requires_aware_canonical_precision(self):
        for value in (None, 123, '2026-03-09', '2026-03-09T13:24:00',
                      '2026-03-09T13:24:00.0000001+00:00', '2026-03-09T09:24:00-04:00'):
            self.invalid(lambda: decode_snapshot(self.changed(lambda d: d.__setitem__('changed_at',value))))

    def test_dst_invalid_calendar_is_not_weakened(self):
        def mutate(d):
            s=d['selection']['session']; s['session_date']='2026-03-08'
            s['regular_open']='2026-03-08T02:30:00.000000-05:00'
        self.invalid(lambda: decode_snapshot(self.changed(mutate)))

    def test_policy_duration_rejects_float_bool_missing(self):
        for value in (True, 1.5, 0, -1, None):
            self.invalid(lambda: decode_snapshot(self.changed(lambda d: d['selection']['policy'].__setitem__('freeze_before_open_us',value))))

    def test_duplicate_json_keys_and_noncanonical_json(self):
        data=encode_snapshot(accepted())
        for value in (data.replace('"version":1','"version":1,"version":1',1), ' '+data,
                      '{}', '[]', '{', 'NaN', 'x'*32769):
            self.invalid(lambda: decode_snapshot(value))

    def test_codec_errors_do_not_echo_input(self):
        self.invalid(lambda: decode_snapshot('private synthetic diagnostic'))
        self.invalid(lambda: decode_event('private synthetic diagnostic'))

    def test_event_round_trip_and_immutability(self):
        e=event(accepted()); self.assertEqual(decode_event(encode_event(e)),e)
        with self.assertRaises(FrozenInstanceError): e.revision=99

    def test_event_malformed_fields(self):
        for field,value in (('event_id','bad'),('command_id','bad'),('revision',True),('revision',0),
                            ('prior_state','FILLED'),('recorded_at',None),('version',2)):
            data=json.loads(encode_event(event(accepted()))); data[field]=value
            self.invalid(lambda: decode_event(json.dumps(data,sort_keys=True,separators=(',',':'))))
        data=json.loads(encode_event(event(accepted()))); data['extra']=1
        self.invalid(lambda: decode_event(json.dumps(data)))

    def test_record_time_before_effect_rejected(self):
        a=accepted()
        with self.assertRaises(ValueError): event(a,recorded_at=a.changed_at-timedelta(seconds=1))

    def test_reconstruction_valid_chain(self):
        a=accepted(); f=transition_order(a,OrderState.FROZEN,at=CUT)
        w=transition_order(f,OrderState.AWAITING_PRICE,at=OPEN)
        events=(event(a),event(f,2,a.state),event(w,3,f.state))
        self.assertEqual(reconstruct_order(a,events),w)

    def test_reconstruction_rejects_missing_duplicate_reordered(self):
        a=accepted(); f=transition_order(a,OrderState.FROZEN,at=CUT)
        one,two=event(a),event(f,2,a.state)
        for events in ((),(two,),(one,one),(two,one),(one,replace(two,revision=3)),
                       (one,replace(two,event_id=one.event_id))):
            self.invalid(lambda: reconstruct_order(a,events))

    def test_reconstruction_rejects_changed_identity_and_intent(self):
        a=accepted(); f=transition_order(a,OrderState.FROZEN,at=CUT)
        for field,value in (('portfolio_id',uid()),('order_id',uid()),('quantity',D(11)),('idempotency_key',uid())):
            changed=replace(f,intent=replace(f.intent,**{field:value}))
            self.invalid(lambda: reconstruct_order(a,(event(a),event(changed,2,a.state))))

    def test_impossible_edge_and_terminal_resurrection_rejected(self):
        a=accepted(); f=transition_order(a,OrderState.FROZEN,at=CUT)
        w=transition_order(f,OrderState.AWAITING_PRICE,at=OPEN)
        c=transition_order(a,OrderState.CANCELLED,at=a.changed_at)
        for events in ((event(a),event(w,2,a.state)),
                       (event(a),event(c,2,a.state),event(f,3,c.state))):
            self.invalid(lambda: reconstruct_order(a,events))

    def test_recording_time_regression_rejected(self):
        a=accepted(); f=transition_order(a,OrderState.FROZEN,at=CUT)
        one=event(a,recorded_at=OPEN+timedelta(seconds=20)); two=event(f,2,a.state,recorded_at=OPEN)
        self.invalid(lambda: reconstruct_order(a,(one,two)))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root=Path(directory.name).resolve(); self.path=self.root/'orders.virtual.sqlite3'
        self.initial=accepted(); i=self.initial.intent
        self.ledger=VirtualLedger(self.path,clock=lambda:self.initial.changed_at)
        self.ledger.create_portfolio(i.portfolio_id)
        self.ledger.register_instrument(Instrument(i.instrument_id,'SYNTH'))
        for target in ('socket.socket.connect','socket.socket.connect_ex','socket.socket.sendto',
                       'socket.getaddrinfo','socket.create_connection'):
            guard=patch(target,side_effect=AssertionError('Offline Package A test'))
            guard.start(); self.addCleanup(guard.stop)

    def migrate(self):
        return migrate_order_schema(self.path,confirmed=True)

    def scope(self):
        return self.initial.intent.portfolio_id,self.initial.intent.order_id

    def seed(self, states=None, **kwargs):
        self.migrate()
        return seed(self.path,states or (self.initial,),**kwargs)

    def corrupt(self, table, sql, args=()):
        with sqlite3.connect(self.path) as c:
            trigger=c.execute('SELECT sql FROM sqlite_master WHERE name=?',(table+'_no_update',)).fetchone()[0]
            c.execute('DROP TRIGGER '+table+'_no_update'); c.execute(sql,args); c.execute(trigger)

    def test_migration_requires_exact_confirmation(self):
        before=self.path.read_bytes()
        for flag in (False,None,1,'yes'):
            with self.assertRaises(VirtualError): migrate_order_schema(self.path,confirmed=flag)
        self.assertEqual(self.path.read_bytes(),before)

    def test_fresh_v1_migrates_to_exact_v2(self):
        self.assertEqual(self.migrate(),2)
        store=OrderStore(self.path)
        self.assertEqual(store.orders(self.scope()[0]),())
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables,{'virtual_portfolios','virtual_instruments','virtual_events',
                                'virtual_orders','virtual_order_commands','virtual_order_events'})

    def test_funded_history_balances_identities_and_idempotency_preserved(self):
        p,i=self.scope()[0],self.initial.intent.instrument_id
        key=uid(); self.ledger.fund(p,D(1000),idempotency_key=key,confirmed=True)
        for side,quantity,price,fees in ((Side.BUY,'3','10','1'),(Side.SELL,'1','20','1')):
            self.ledger.apply_synthetic_fill(p,SyntheticFill(uid(),i,side,D(quantity),D(price),uid(),D(fees)),idempotency_key=uid())
        history,state=self.ledger.history(p),self.ledger.state(p)
        with sqlite3.connect(self.path) as c: rows=c.execute('SELECT * FROM virtual_events').fetchall()
        self.migrate(); store=OrderStore(self.path)
        self.assertEqual(store.ledger_history(p),history); self.assertEqual(store.ledger_state(p),state)
        with sqlite3.connect(self.path) as c: self.assertEqual(c.execute('SELECT * FROM virtual_events').fetchall(),rows)
        self.assertEqual(store.ledger_history(p)[0].idempotency_key,key)
        for operation in (lambda:VirtualLedger(self.path),lambda:self.ledger.state(p),
                          lambda:self.ledger.fund(p,D(1000),idempotency_key=key,confirmed=True)):
            with self.assertRaises(VirtualError): operation()

    def test_repeat_migration_is_verified_noop(self):
        self.seed(); before=self.path.read_bytes()
        self.assertEqual(self.migrate(),2)
        self.assertEqual(self.path.read_bytes(),before)

    def test_read_does_not_migrate_v1(self):
        before=self.path.read_bytes()
        with self.assertRaises(VirtualError): OrderStore(self.path)
        self.assertEqual(self.path.read_bytes(),before)

    def test_missing_path_never_created(self):
        missing=self.root/'missing.virtual.sqlite3'
        for operation in (lambda:OrderStore(missing),lambda:migrate_order_schema(missing,confirmed=True)):
            with self.assertRaises(VirtualError): operation()
        self.assertFalse(missing.exists())

    def test_unsafe_paths_rejected(self):
        for path in (None,'',':memory:','relative.virtual.sqlite3',ROOT/'magi.db',self.root/'magi.db',ROOT/'bad.virtual.sqlite3'):
            with self.subTest(path=str(path)):
                with self.assertRaises(VirtualError): migrate_order_schema(path,confirmed=True)
                with self.assertRaises(VirtualError): OrderStore(path)

    def test_symlinks_hardlinks_and_permissions_rejected(self):
        link=self.root/'link.virtual.sqlite3'; link.symlink_to(self.path)
        with self.assertRaises(VirtualError): migrate_order_schema(link,confirmed=True)
        link.unlink(); os.link(self.path,link)
        with self.assertRaises(VirtualError): self.migrate()
        link.unlink(); self.path.chmod(0o644)
        with self.assertRaises(VirtualError): self.migrate()

    def test_file_replacement_rejected(self):
        self.migrate(); store=OrderStore(self.path)
        old=self.path.with_name('old.virtual.sqlite3'); self.path.rename(old)
        VirtualLedger(self.path)
        with self.assertRaises(VirtualError): store.orders(self.scope()[0])

    def test_foreign_and_empty_databases_rejected_unchanged(self):
        for populated in (False,True):
            path=self.root/('foreign' if populated else 'empty'); path=path.with_suffix('.virtual.sqlite3')
            path.touch(mode=0o600)
            if populated:
                with sqlite3.connect(path) as c: c.execute('CREATE TABLE owner_data(value TEXT)')
            before=path.read_bytes()
            with self.assertRaises(VirtualError): migrate_order_schema(path,confirmed=True)
            self.assertEqual(path.read_bytes(),before)

    def test_unknown_version_or_extra_schema_rejected(self):
        for sql in ('PRAGMA user_version=999','CREATE TABLE unexpected(x)',
                    'DROP TRIGGER virtual_events_no_update'):
            with self.subTest(sql=sql):
                copy=self.root/(uid()+'.virtual.sqlite3'); copy.write_bytes(self.path.read_bytes()); copy.chmod(0o600)
                with sqlite3.connect(copy) as c: c.execute(sql)
                before=copy.read_bytes()
                with self.assertRaises(VirtualError): migrate_order_schema(copy,confirmed=True)
                self.assertEqual(copy.read_bytes(),before)

    def test_partial_migration_rejected(self):
        with sqlite3.connect(self.path) as c: c.execute('CREATE TABLE virtual_orders(order_id TEXT)')
        before=self.path.read_bytes()
        with self.assertRaises(VirtualError): self.migrate()
        self.assertEqual(self.path.read_bytes(),before)

    def test_corrupt_ledger_rejected_before_migration(self):
        p=self.scope()[0]; self.ledger.fund(p,D(100),idempotency_key=uid(),confirmed=True)
        self.corrupt('virtual_events',"UPDATE virtual_events SET amount='999'")
        before=self.path.read_bytes()
        with self.assertRaises(VirtualError): self.migrate()
        self.assertEqual(self.path.read_bytes(),before)

    def test_ddl_and_commit_failures_roll_back_everything(self):
        original=sqlite3.connect
        for failure in ('CREATE TABLE virtual_order_events','PRAGMA user_version = 2','COMMIT'):
            class Failed(sqlite3.Connection):
                def execute(self,sql,*args):
                    result=super().execute(sql,*args)
                    if sql.startswith(failure): raise sqlite3.OperationalError('synthetic failure')
                    return result
                def commit(self):
                    if failure=='COMMIT': raise sqlite3.OperationalError('synthetic commit failure')
                    return super().commit()
            before=self.path.read_bytes()
            with patch('sqlite3.connect',side_effect=lambda *a,**kw:original(*a,factory=Failed,**kw)):
                with self.assertRaises(VirtualError) as caught: self.migrate()
            self.assertEqual(str(caught.exception),'STORAGE_UNAVAILABLE')
            self.assertEqual(self.path.read_bytes(),before)
            self.assertEqual(VirtualLedger(self.path).history(self.scope()[0]),())

    def test_lock_timeout_does_not_migrate(self):
        with sqlite3.connect(self.path) as c:
            c.execute('BEGIN IMMEDIATE')
            with self.assertRaises(VirtualError): migrate_order_schema(self.path,confirmed=True,timeout=.01)
        self.assertEqual(VirtualLedger(self.path).history(self.scope()[0]),())
        self.migrate()

    def test_reopen_reads_immutable_order_and_history(self):
        a=self.initial; f=transition_order(a,OrderState.FROZEN,at=CUT)
        expected=self.seed((a,f)); store=OrderStore(self.path)
        self.assertEqual(store.history(*self.scope()),expected)
        self.assertEqual(store.order(*self.scope()),f)
        self.assertEqual(OrderStore(self.path).order(*self.scope()),f)

    def test_one_command_can_record_consecutive_edges(self):
        a=self.initial; f=transition_order(a,OrderState.FROZEN,at=CUT); w=transition_order(f,OrderState.AWAITING_PRICE,at=OPEN)
        command=uid(); self.seed((a,f,w),commands=(uid(),command,command))
        self.assertEqual(OrderStore(self.path).order(*self.scope()),w)

    def test_scoped_missing_and_cross_portfolio(self):
        other=uid(); self.ledger.create_portfolio(other); self.seed(); store=OrderStore(self.path)
        for scope in ((other,self.scope()[1]),(self.scope()[0],uid()),(uid(),self.scope()[1])):
            with self.assertRaises(PersistenceError) as caught: store.order(*scope)
            self.assertIs(caught.exception.code,PersistenceCode.MISSING_ORDER)
        self.assertEqual(store.orders(other),())
        with self.assertRaises(TypeError): store.orders()
        with self.assertRaises(TypeError): store.order(self.scope()[1])

    def test_listing_is_deterministically_scoped(self):
        self.seed(); other=replace(self.initial,intent=replace(self.initial.intent,order_id=uid(),idempotency_key=uid()))
        seed(self.path,(other,)); values=OrderStore(self.path).orders(self.scope()[0])
        self.assertEqual([x.intent.order_id for x in values],sorted((self.scope()[1],other.intent.order_id)))

    def test_reads_have_no_write_or_ledger_effect(self):
        self.seed(); before=self.path.read_bytes(); store=OrderStore(self.path)
        for _ in range(2):
            store.order(*self.scope()); store.history(*self.scope()); store.orders(self.scope()[0]); store.ledger_state(self.scope()[0])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(store.ledger_history(self.scope()[0]),())
        self.assertEqual({p.name for p in self.root.iterdir()},{self.path.name})

    def test_no_production_mutation_methods(self):
        self.migrate(); store=OrderStore(self.path)
        names={name for name in dir(store) if not name.startswith('_')}
        self.assertEqual(names,{'order','orders','history','ledger_history','ledger_state'})

    def test_sql_immutable_tables(self):
        self.seed()
        with sqlite3.connect(self.path) as c:
            for table in ('virtual_orders','virtual_order_commands','virtual_order_events','virtual_events'):
                for sql in (f'UPDATE {table} SET rowid=rowid',f'DELETE FROM {table}'):
                    if table=='virtual_events': continue  # Empty original ledger; tested in its own suite.
                    with self.assertRaises(sqlite3.IntegrityError): c.execute(sql)

    def test_sql_duplicate_event_revision_identity_and_order(self):
        self.seed()
        with sqlite3.connect(self.path) as c:
            c.execute('PRAGMA foreign_keys=ON')
            for table in ('virtual_orders','virtual_order_commands','virtual_order_events'):
                with self.assertRaises(sqlite3.IntegrityError): c.execute(f'INSERT INTO {table} SELECT * FROM {table}')

    def test_sql_orphans_and_cross_scope_rejected(self):
        self.migrate()
        for portfolio,instrument in ((uid(),self.initial.intent.instrument_id),(self.scope()[0],uid())):
            a=replace(self.initial,intent=replace(self.initial.intent,portfolio_id=portfolio,instrument_id=instrument))
            with self.assertRaises(sqlite3.IntegrityError): seed(self.path,(a,))
        self.assertEqual(OrderStore(self.path).orders(self.scope()[0]),())

    def test_sql_missing_command_event_pair_rejected_at_commit(self):
        self.migrate(); i=self.initial.intent
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(self.path) as c:
                c.execute('PRAGMA foreign_keys=ON')
                c.execute('INSERT INTO virtual_orders VALUES (?,?,?,?,?,?)',
                    (i.order_id,i.portfolio_id,i.instrument_id,'USD',uid(),encode_snapshot(self.initial)))
        self.assertEqual(OrderStore(self.path).orders(i.portfolio_id),())

    def test_sql_filled_and_revision_gap_rejected(self):
        self.seed()
        with sqlite3.connect(self.path) as c:
            row=list(c.execute('SELECT * FROM virtual_order_events').fetchone())
            for revision,state in ((3,'FROZEN'),(2,'FILLED')):
                changed=row.copy(); changed[0]=uid(); changed[3]=revision; changed[5]='ACCEPTED'; changed[6]=state
                with self.assertRaises(sqlite3.IntegrityError): c.execute('INSERT INTO virtual_order_events VALUES (?,?,?,?,?,?,?,?,?,?)',changed)

    def test_corrupted_json_and_mismatched_columns_fail_closed(self):
        self.seed()
        for sql,args in (("UPDATE virtual_orders SET initial_json='{}'",()),
                         ('UPDATE virtual_order_events SET event_json=?',('{}',))):
            before=self.path.read_bytes()
            self.corrupt('virtual_orders' if 'virtual_orders ' in sql else 'virtual_order_events',sql,args)
            with self.assertRaises(PersistenceError): OrderStore(self.path).order(*self.scope())
            self.path.write_bytes(before)

    def test_command_key_mismatch_rejected(self):
        self.seed(); self.corrupt('virtual_order_commands','UPDATE virtual_order_commands SET idempotency_key=?',(uid(),))
        with self.assertRaises(PersistenceError): OrderStore(self.path).order(*self.scope())

    def test_command_scope_uniqueness_is_database_enforced(self):
        actor=uid(); self.seed(actor=actor)
        other=replace(self.initial,intent=replace(self.initial.intent,order_id=uid()))
        with self.assertRaises(sqlite3.IntegrityError): seed(self.path,(other,),actor=actor)
        self.assertEqual(len(OrderStore(self.path).orders(self.scope()[0])),1)
        # Another actor's deliberate intent has a separate scoped key.
        seed(self.path,(other,),actor=uid())
        self.assertEqual(len(OrderStore(self.path).orders(self.scope()[0])),2)

    def test_cross_portfolio_command_reference_rejected(self):
        other=uid(); self.ledger.create_portfolio(other); self.seed()
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(self.path) as c:
                c.execute('PRAGMA foreign_keys=ON')
                row=list(c.execute('SELECT * FROM virtual_order_commands').fetchone())
                row[0]=uid(); row[1]=other
                c.execute('INSERT INTO virtual_order_commands VALUES (?,?,?,?,?,?,?)',row)

    def test_sql_temporal_order_rejects_regression(self):
        self.seed()
        with sqlite3.connect(self.path) as c:
            row=list(c.execute('SELECT * FROM virtual_order_events').fetchone())
            row[0]=uid(); row[3]=2; row[5]='ACCEPTED'; row[6]='FROZEN'
            row[7]='2000-01-01T00:00:00.000000+00:00'
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute('INSERT INTO virtual_order_events VALUES (?,?,?,?,?,?,?,?,?,?)',row)

    def test_valid_json_with_changed_intent_is_corruption(self):
        a=self.initial; f=transition_order(a,OrderState.FROZEN,at=CUT)
        events=self.seed((a,f))
        wrong=replace(f,intent=replace(f.intent,quantity=D(11)))
        changed=replace(events[1],snapshot=wrong)
        self.corrupt('virtual_order_events','UPDATE virtual_order_events SET event_json=? WHERE revision=2',
                     (encode_event(changed),))
        with self.assertRaises(PersistenceError): OrderStore(self.path).order(*self.scope())

    def test_event_column_and_payload_mismatch_rejected(self):
        self.seed(); self.corrupt('virtual_order_events','UPDATE virtual_order_events SET event_id=?',(uid(),))
        with self.assertRaises(PersistenceError): OrderStore(self.path).history(*self.scope())

    def test_read_refuses_altered_v2_manifest(self):
        self.migrate(); store=OrderStore(self.path)
        with sqlite3.connect(self.path) as c: c.execute('CREATE INDEX unexpected ON virtual_orders(instrument_id)')
        before=self.path.read_bytes()
        with self.assertRaises(VirtualError): store.orders(self.scope()[0])
        with self.assertRaises(VirtualError): self.migrate()
        self.assertEqual(self.path.read_bytes(),before)

    def test_repeat_migration_rejects_corrupted_order(self):
        self.seed(); self.corrupt('virtual_orders',"UPDATE virtual_orders SET initial_json='{}'")
        before=self.path.read_bytes()
        with self.assertRaises(PersistenceError): self.migrate()
        self.assertEqual(self.path.read_bytes(),before)

    def test_import_alone_never_opens_or_migrates(self):
        before=self.path.read_bytes()
        script='''
import sys
def guard(event,args):
    if event=='sqlite3.connect': raise AssertionError('Import must not open a DB')
sys.addaudithook(guard);sys.path.insert(0,sys.argv[1])
import magi.virtual.order_codec,magi.virtual.order_storage
print('no database')
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',script,str(ROOT)],
                              env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'no database'); self.assertEqual(self.path.read_bytes(),before)

    def test_missing_history_fail_closed_even_with_restored_manifest(self):
        self.seed()
        with sqlite3.connect(self.path) as c:
            trigger=c.execute("SELECT sql FROM sqlite_master WHERE name='virtual_order_events_no_delete'").fetchone()[0]
            c.execute('DROP TRIGGER virtual_order_events_no_delete'); c.execute('DELETE FROM virtual_order_events'); c.execute(trigger)
        with self.assertRaises(PersistenceError): OrderStore(self.path)

    def test_restart_and_import_isolation(self):
        self.seed(); before=self.path.read_bytes()
        script='''
import sys,pathlib
root=pathlib.Path(sys.argv[1]); path=pathlib.Path(sys.argv[2]).resolve()
def guard(event,args):
    if event=='import' and args[0].startswith(('magi.broker','magi.toss','magi.storage','magi.portfolio','httpx','dotenv')): raise AssertionError('forbidden import')
    if event.startswith('socket.') and event!='socket.__new__': raise AssertionError('network')
    if event=='open' and args and isinstance(args[0],str) and pathlib.Path(args[0]).name=='.env': raise AssertionError('credentials')
    if event=='sqlite3.connect' and str(args[0]) != path.as_uri()+'?mode=ro': raise AssertionError('unexpected DB/write')
sys.addaudithook(guard); sys.path.insert(0,str(root))
from magi.virtual.order_storage import OrderStore
store=OrderStore(path)
assert store.order(sys.argv[3],sys.argv[4]).state.value=='ACCEPTED'
print('isolated read')
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',script,str(ROOT),str(self.path),*self.scope()],
            env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'isolated read'); self.assertEqual(self.path.read_bytes(),before)


if __name__=='__main__':
    unittest.main()

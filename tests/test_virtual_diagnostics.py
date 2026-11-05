"""Synthetic D1 source immutability and complete version-specific replay."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import test_virtual_atomic_transitions as fixtures
from magi.virtual import VirtualLedger
from magi.virtual.diagnostics import diagnose, Status


class _Fixture:
    setUp = fixtures.AtomicTransitionTests.setUp
    request = fixtures.AtomicTransitionTests.request
    prepare = fixtures.AtomicTransitionTests.prepare
    store = fixtures.AtomicTransitionTests.store
    command = fixtures.AtomicTransitionTests.command
    apply = fixtures.AtomicTransitionTests.apply
    corrupt = fixtures.AtomicTransitionTests.corrupt

    def inspect(self, path=None):
        path = path or self.path
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        report = diagnose(path)
        after = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report.acknowledgment_status, Status.UNVERIFIED)
        self.assertEqual(report, diagnose(path))
        self.assertFalse(hasattr(report, 'safe'))
        return report


class DiagnosticTests(_Fixture, unittest.TestCase):
    def test_v1(self):
        p=self.root/'v1.virtual.sqlite3'; VirtualLedger(p)
        result=self.inspect(p);self.assertEqual(result.local_status,Status.HEALTHY)
        self.assertEqual(result.schema_version,1)
        self.assertEqual(dict((c.name,c.result) for c in result.checks)['orders'],'NOT_APPLICABLE')

    def test_v2(self):
        self.request();self.assertEqual(self.inspect().local_status,Status.HEALTHY)

    def test_v3(self):
        from magi.virtual.reservation_storage import migrate_reservation_schema,_reserve_synthetic
        r=self.request();migrate_reservation_schema(self.path,confirmed=True);_reserve_synthetic(self.path,r)
        self.assertEqual(self.inspect().local_status,Status.HEALTHY)

    def test_v4(self):
        r=self.prepare();self.apply(self.command(r))
        self.assertEqual(self.inspect().local_status,Status.HEALTHY)

    def test_missing_never_created(self):
        p=self.root/'missing.virtual.sqlite3'
        self.assertEqual(diagnose(p).local_status,Status.UNVERIFIED);self.assertFalse(p.exists())

    def test_physical_corruption(self):
        self.path.write_bytes(b'x'*4096)
        self.assertEqual(self.inspect().local_status,Status.CORRUPT)

    def test_sidecars_fail_closed(self):
        for suffix in ('-wal','-shm','-journal'):
            p=Path(str(self.path)+suffix);p.write_bytes(b'synthetic')
            try:self.assertEqual(self.inspect().local_status,Status.UNVERIFIED)
            finally:p.unlink()

    def test_wal_rejected_without_sidecars_created(self):
        with sqlite3.connect(self.path) as c:c.execute('PRAGMA journal_mode=WAL')
        self.assertEqual(self.inspect().local_status,Status.UNVERIFIED)

    def test_writer_lock_fails_closed(self):
        with sqlite3.connect(self.path) as c:
            c.execute('BEGIN EXCLUSIVE')
            # Do not open/close the source in this process: POSIX closes drop SQLite locks.
            self.assertEqual(diagnose(self.path).local_status,Status.UNVERIFIED)

    def test_sqlite_only_opens_explicit_readonly_source(self):
        connect=sqlite3.connect
        def checked(path,*a,**kw):
            self.assertEqual(path,self.path.as_uri()+'?mode=ro')
            self.assertTrue(kw['uri']);self.assertEqual(kw['timeout'],0)
            return connect(path,*a,**kw)
        with patch('sqlite3.connect',side_effect=checked):
            self.assertEqual(self.inspect().local_status,Status.HEALTHY)

    def test_readonly_query_transaction(self):
        real=sqlite3.connect;statements=[]
        def connect(*a,**k):
            c=real(*a,**k);c.set_trace_callback(statements.append);return c
        with patch('sqlite3.connect',side_effect=connect):self.inspect()
        self.assertIn('PRAGMA query_only = ON',statements)
        self.assertIn('BEGIN',statements)
        self.assertFalse(any(s.startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','BEGIN IMMEDIATE')) for s in statements))

    def test_limits_and_paths(self):
        for limit in (True,0,65*1024*1024):
            self.assertEqual(diagnose(self.path,max_bytes=limit).local_status,Status.UNVERIFIED)
        for path in ('magi.db','',None):
            self.assertEqual(diagnose(path).local_status,Status.UNVERIFIED)

    def test_no_partial_v4_replay(self):
        self.prepare()
        with patch('magi.virtual.diagnostics._transition_replay',side_effect=ValueError('synthetic secret')):
            r=self.inspect();self.assertEqual(r.local_status,Status.INCONSISTENT)
            self.assertNotIn('synthetic secret',repr(r))
            self.assertIsNone(next((c for c in r.checks if c.name=='reservations' and c.result=='PASS'),None))

    def test_immutable(self):
        with self.assertRaises(FrozenInstanceError):self.inspect().issue_code='x'

    def test_unsafe_links(self):
        p=self.root/'link.virtual.sqlite3';p.symlink_to(self.path)
        self.assertEqual(diagnose(p).local_status,Status.UNVERIFIED)


def schema_case(sql):
    def test(self):
        with sqlite3.connect(self.path) as c:c.execute(sql)
        self.assertEqual(self.inspect().local_status,Status.UNSUPPORTED_SCHEMA)
    return test
for name,sql in {
    'version':'PRAGMA user_version=99','app':'PRAGMA application_id=0',
    'extra':'CREATE TABLE extra(x)','trigger':'DROP TRIGGER virtual_order_events_no_update'
}.items():setattr(DiagnosticTests,'test_schema_'+name,schema_case(sql))


def corrupt_case(table,sql,deleting=False):
    def test(self):
        r=self.prepare();self.apply(self.command(r))
        self.corrupt(table,sql,deleting=deleting)
        self.assertEqual(self.inspect().local_status,Status.INCONSISTENT)
    return test
for name,(table,sql,deleting) in {
 'release_amount':('virtual_reservation_releases',"UPDATE virtual_reservation_releases SET amount='999'",False),
 'missing_release':('virtual_reservation_releases','DELETE FROM virtual_reservation_releases',True),
 'missing_outcome':('virtual_transition_outcomes','DELETE FROM virtual_transition_outcomes',True),
 'missing_initial':('virtual_order_events','DELETE FROM virtual_order_events WHERE revision=1',True),
 'missing_terminal':('virtual_order_events','DELETE FROM virtual_order_events WHERE revision=2',True),
 'missing_reservation':('virtual_order_reservations','DELETE FROM virtual_order_reservations',True),
 'canonical':('virtual_transition_outcomes',"UPDATE virtual_transition_outcomes SET request_json='{}'",False),
 'decimal':('virtual_order_reservations',"UPDATE virtual_order_reservations SET amount='NaN'",False),
 'boundary':('virtual_transition_baselines','UPDATE virtual_transition_baselines SET revision=2',False),
 'revision':('virtual_order_events','UPDATE virtual_order_events SET revision=9 WHERE revision=2',False),
 'posted':('virtual_events',"UPDATE virtual_events SET amount='-1' WHERE kind='FUNDING'",False),
}.items():setattr(DiagnosticTests,'test_corrupt_'+name,corrupt_case(table,sql,deleting))

# R1: SQLite-owned read snapshots and fail-closed opening races.
class SnapshotTests(_Fixture, unittest.TestCase):
    def test_sidecars_rejected_before_sqlite_open(self):
        p = Path(str(self.path) + '-shm')
        p.write_bytes(b'synthetic')
        with patch('magi.virtual.diagnostics.sqlite3.connect') as connect:
            self.assertEqual(diagnose(self.path).local_status, Status.UNVERIFIED)
        connect.assert_not_called()

    def test_connection_closes_after_success_and_failure(self):
        real = sqlite3.connect
        opened = []
        def connect(*a, **k):
            c = real(*a, **k); opened.append(c); return c
        with patch('sqlite3.connect', side_effect=connect):
            self.inspect()
            with patch('magi.virtual.diagnostics._schema', side_effect=ValueError('private')):
                self.assertEqual(diagnose(self.path).local_status, Status.INCONSISTENT)
        for c in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                c.execute('SELECT 1')

    def test_enforcement_blocks_mutations_during_replay(self):
        from magi.virtual.storage import _schema
        real = sqlite3.connect
        opened = []
        def connect(*a, **k):
            c = real(*a, **k); opened.append(c); return c
        def schema():
            c = opened[0]
            self.assertEqual(c.execute('PRAGMA query_only').fetchone()[0], 1)
            self.assertTrue(c.in_transaction)
            for sql in ('CREATE TABLE unexpected(x)', 'PRAGMA user_version=99',
                        'DELETE FROM virtual_events', "UPDATE virtual_portfolios SET currency='KRW'"):
                with self.assertRaises(sqlite3.OperationalError): c.execute(sql)
            return _schema()
        with patch('sqlite3.connect', side_effect=connect), patch('magi.virtual.diagnostics._schema', side_effect=schema):
            self.assertEqual(diagnose(self.path).local_status, Status.HEALTHY)

    def test_every_replay_uses_one_connection_and_transaction(self):
        from magi.virtual import diagnostics as d
        self.prepare()
        seen = []
        def wrap(fn):
            def call(c, *a, **k):
                self.assertTrue(c.in_transaction); seen.append(c)
                return fn(c, *a, **k)
            return call
        real = sqlite3.connect
        with patch('sqlite3.connect', wraps=real) as connect, \
             patch.object(d.VirtualLedger, '_events', side_effect=wrap(d.VirtualLedger._events)), \
             patch.object(d, '_history', side_effect=wrap(d._history)), \
             patch.object(d, '_transition_replay', side_effect=wrap(d._transition_replay)):
            self.assertEqual(diagnose(self.path).local_status, Status.HEALTHY)
        self.assertEqual(connect.call_count, 1)
        self.assertGreater(len(seen), 3)
        self.assertEqual(len({id(c) for c in seen}), 1)

    def test_reserved_writer_without_changes_allows_old_committed_snapshot(self):
        # RESERVED alone does not change the DB or create a journal. Do not open/
        # close the source using file APIs while either SQLite connection is live.
        writer = sqlite3.connect(self.path)
        try:
            writer.execute('BEGIN IMMEDIATE')
            self.assertEqual(diagnose(self.path).local_status, Status.HEALTHY)
        finally: writer.close()

    def test_concurrent_writer_cannot_mix_schema_versions(self):
        from concurrent.futures import ThreadPoolExecutor
        from magi.virtual.storage import _schema
        observed = []
        def writer():
            c = sqlite3.connect(self.path, timeout=0)
            try:
                c.execute('BEGIN IMMEDIATE'); c.execute('PRAGMA user_version=99')
                with self.assertRaises(sqlite3.OperationalError): c.commit()
                observed.append('commit blocked')
            finally: c.close()
        def during_snapshot():
            with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(writer).result()
            return _schema()
        with patch('magi.virtual.diagnostics._schema', side_effect=during_snapshot):
            r = diagnose(self.path)
        self.assertEqual(observed, ['commit blocked'])
        self.assertEqual((r.local_status, r.schema_version), (Status.HEALTHY, 2))
        c = sqlite3.connect(self.path, timeout=0)
        try: c.execute('PRAGMA user_version=99')
        finally: c.close()
        self.assertEqual(diagnose(self.path).local_status, Status.UNSUPPORTED_SCHEMA)

    def test_writer_sidecar_appearing_during_replay_fails_closed(self):
        from magi.virtual.storage import _schema
        writer = sqlite3.connect(self.path, timeout=0)
        def schema():
            writer.execute('BEGIN IMMEDIATE'); writer.execute('PRAGMA user_version=99')
            return _schema()
        try:
            with patch('magi.virtual.diagnostics._schema', side_effect=schema):
                self.assertEqual(diagnose(self.path).local_status, Status.UNVERIFIED)
            self.assertTrue(Path(str(self.path)+'-journal').exists())
        finally: writer.close()

    def test_wal_switch_between_preflight_and_connect_has_no_artifacts(self):
        real = sqlite3.connect
        def connect(*a, **k):
            writer = real(self.path)
            try: self.assertEqual(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            finally: writer.close()
            before = {p.name:p.read_bytes() for p in self.root.iterdir()}
            c = real(*a, **k)
            self.before = before
            return c
        with patch('sqlite3.connect', side_effect=connect):
            self.assertEqual(diagnose(self.path).local_status, Status.UNVERIFIED)
        self.assertEqual(self.before, {p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_wal_switch_after_connect_before_first_read_has_no_artifacts(self):
        real = sqlite3.connect
        outer = self
        class Connection(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == 'BEGIN':
                    w = real(outer.path)
                    try: w.execute('PRAGMA journal_mode=WAL')
                    finally: w.close()
                    outer.before = {p.name:p.read_bytes() for p in outer.root.iterdir()}
                return super().execute(sql, *args)
        def connect(*a, **k): return real(*a, **k, factory=Connection)
        with patch('sqlite3.connect', side_effect=connect):
            self.assertEqual(diagnose(self.path).local_status, Status.UNVERIFIED)
        self.assertEqual(self.before, {p.name:p.read_bytes() for p in self.root.iterdir()})

    def hot_journal(self):
        import subprocess, sys
        child = """import os, sqlite3, sys
c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA cache_size=1')
c.execute('BEGIN IMMEDIATE'); c.execute('CREATE TABLE hot_fixture(value BLOB)')
c.executemany('INSERT INTO hot_fixture VALUES (?)',[(b'x'*4096,)]*100)
os._exit(0)
"""
        subprocess.run([sys.executable, '-B', '-c', child, str(self.path)], check=True)
        journal = Path(str(self.path)+'-journal').read_bytes()
        self.assertGreater(len(journal), 512)
        self.assertNotEqual(journal[:8], b'\0'*8)

    def test_hot_journal_rejected_without_recovery(self):
        self.hot_journal()
        self.assertEqual(self.inspect().local_status, Status.UNVERIFIED)

    def test_raced_hot_journal_is_not_recovered(self):
        real = sqlite3.connect
        def connect(*a, **k):
            self.hot_journal()
            self.before = {p.name:p.read_bytes() for p in self.root.iterdir()}
            return real(*a, **k)
        with patch('sqlite3.connect', side_effect=connect):
            self.assertEqual(diagnose(self.path).local_status, Status.UNVERIFIED)
        self.assertEqual(self.before, {p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_empty_file_is_not_initialized(self):
        p = self.root/'empty.virtual.sqlite3'; p.touch(mode=0o600)
        self.assertEqual(self.inspect(p).local_status, Status.UNVERIFIED)
        self.assertEqual(p.stat().st_size, 0)

    def test_permission_and_resource_failures_are_sanitized(self):
        with patch('sqlite3.connect', side_effect=sqlite3.OperationalError('private path')):
            r = diagnose(self.path)
            self.assertEqual(r.local_status, Status.UNVERIFIED)
            self.assertNotIn('private', repr(r))
        self.path.chmod(0o644)
        self.assertEqual(diagnose(self.path).local_status, Status.UNVERIFIED)

    def test_pending_checks_not_passed_after_schema_failure(self):
        with sqlite3.connect(self.path) as c: c.execute('PRAGMA user_version=99')
        r = self.inspect()
        self.assertEqual(dict((c.name,c.result) for c in r.checks)['ledger'], 'NOT_RUN')


class ReconciliationTests(_Fixture, unittest.TestCase):
    def test_cash_shares_cost_basis_and_realized_profit_use_existing_replay(self):
        from decimal import Decimal as D
        from magi.virtual import Instrument, SyntheticFill, Side
        from magi.virtual.accounting import reconstruct
        from magi.virtual.storage import VirtualLedger
        from test_virtual_order_persistence import uid
        p = self.root/'financial.virtual.sqlite3'
        ledger = VirtualLedger(p, clock=lambda:self.initial.changed_at)
        ledger.create_portfolio(self.p); ledger.register_instrument(Instrument(self.i,'SYNTH'))
        ledger.fund(self.p,D(1000),idempotency_key=uid(),confirmed=True)
        for side,q,price in ((Side.BUY,10,10),(Side.BUY,10,20),(Side.SELL,5,25)):
            ledger.apply_synthetic_fill(self.p,SyntheticFill(uid(),self.i,side,D(q),D(price),uid()),idempotency_key=uid())
        real = VirtualLedger._events; observed = []
        def events(c,p):
            result = real(c,p); observed.append(reconstruct(p,result)); return result
        with patch.object(VirtualLedger,'_events',side_effect=events):
            self.assertEqual(diagnose(p).local_status,Status.HEALTHY)
        state = observed[0]; position = state.positions[0]
        self.assertEqual((state.cash,position.quantity,position.book_cost,position.realized_pnl),
                         (D(825),D(15),D(225),D(50)))

    def test_buy_sell_obligations_and_available_resources_reconciled(self):
        from decimal import Decimal as D
        from magi.virtual import Side
        from magi.virtual.reservation_storage import migrate_reservation_schema,_reserve_synthetic
        from magi.virtual import diagnostics as d
        buy=self.request(amount='100'); sell=self.request(Side.SELL,'3',quantity='3')
        migrate_reservation_schema(self.path,confirmed=True)
        _reserve_synthetic(self.path,buy);_reserve_synthetic(self.path,sell)
        real=d._reservation_replay; observed=[]
        def replay(c):
            result=real(c);observed.append(result[0]);return result
        with patch.object(d,'_reservation_replay',side_effect=replay):
            self.assertEqual(diagnose(self.path).local_status,Status.HEALTHY)
        state=observed[0][self.p];shares=dict(state.shares)[self.i]
        self.assertEqual((state.cash.posted,state.cash.reserved,state.cash.available),(D(800),D(100),D(700)))
        self.assertEqual((shares.posted,shares.reserved,shares.available),(D(10),D(3),D(7)))
        self.assertEqual(observed[0][self.other_p].cash.available,D(470))

    def test_nonterminal_states_retain_obligations(self):
        from magi.virtual.order_lifecycle import OrderState as S
        from test_virtual_order_lifecycle import CUT,OPEN
        from magi.virtual import diagnostics as d
        r=self.prepare();self.apply(self.command(r,S.FROZEN,at=CUT))
        self.apply(self.command(r,S.AWAITING_PRICE,at=OPEN))
        real=d._transition_replay;observed=[]
        def replay(c):
            result=real(c);observed.append(result[0][self.p]);return result
        with patch.object(d,'_transition_replay',side_effect=replay):
            self.assertEqual(diagnose(self.path).local_status,Status.HEALTHY)
        self.assertEqual(observed[0].cash.available,700)
        self.assertEqual(len(observed[0].reservations),1)

    def test_expiration_release_complete_replay(self):
        from magi.virtual.order_lifecycle import OrderState as S
        from test_virtual_order_lifecycle import DEADLINE,deadline_evidence
        from magi.virtual import diagnostics as d
        r=self.prepare();self.apply(self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()))
        real=d._transition_replay;observed=[]
        def replay(c):
            result=real(c);observed.append(result[0][self.p]);return result
        with patch.object(d,'_transition_replay',side_effect=replay):
            self.assertEqual(diagnose(self.path).local_status,Status.HEALTHY)
        self.assertEqual(observed[0].cash.available,800)
        self.assertEqual(observed[0].reservations,())

    def test_failed_command_has_no_fabricated_outcome(self):
        from dataclasses import replace
        r=self.prepare();command=self.command(r)
        with self.assertRaises(ValueError):self.apply(replace(command,reservation_id=fixtures.uid()))
        self.assertIsNone(self.store().outcome(self.p,command.actor_id,command.idempotency_key))
        self.assertEqual(self.inspect().local_status,Status.HEALTHY)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM virtual_transition_outcomes').fetchone()[0],0)

    def test_v4_admission_remains_unavailable(self):
        from magi.virtual.reservation_storage import _reserve_synthetic
        r=self.prepare()
        with self.assertRaises(ValueError):_reserve_synthetic(self.path,r)
        self.assertEqual(self.inspect().local_status,Status.HEALTHY)

    def test_whole_database_does_not_ignore_other_portfolio(self):
        self.corrupt('virtual_events',"UPDATE virtual_events SET amount='-1' WHERE portfolio_id=? AND kind='FUNDING'",(self.other_p,))
        self.assertEqual(self.inspect().local_status,Status.INCONSISTENT)

    def test_owner_path_rejected_before_sqlite(self):
        for p in (Path.cwd()/'data/magi.db',Path.cwd()/'owner.virtual.sqlite3'):
            with patch('sqlite3.connect') as connect:
                self.assertEqual(diagnose(p).local_status,Status.UNVERIFIED)
            connect.assert_not_called()

    def test_missing_funding_is_not_zero_balance_success(self):
        self.corrupt('virtual_events',"DELETE FROM virtual_events WHERE kind='FUNDING'",deleting=True)
        self.assertEqual(self.inspect().local_status,Status.INCONSISTENT)

    def test_duplicate_release_and_reservation_cannot_be_inserted(self):
        r=self.prepare();self.apply(self.command(r))
        for table in ('virtual_order_reservations','virtual_reservation_releases'):
            with sqlite3.connect(self.path) as c:
                with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO '+table+' SELECT * FROM '+table)
        self.assertEqual(self.inspect().local_status,Status.HEALTHY)

    def test_report_has_no_financial_details_or_authority(self):
        r=self.prepare();result=self.inspect()
        for value in (self.p,self.other_p,self.i,r.order_id):self.assertNotIn(value,repr(result))
        self.assertEqual(result.scope,'WHOLE_VIRTUAL_DATABASE')
        self.assertEqual(tuple(c.name for c in result.checks),
            ('snapshot','schema','physical','foreign_keys','ledger','orders','reservations','transitions'))
        self.assertEqual(result.acknowledgment_status,Status.UNVERIFIED)
        self.assertFalse(any(hasattr(result,name) for name in ('authorized','ready','balance','receipt')))


def semantic_case(table,sql):
    def test(self):
        r=self.prepare();self.apply(self.command(r))
        self.corrupt(table,sql)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(self.inspect().local_status,Status.INCONSISTENT)
    return test
for name,(table,sql) in {
 'wrong_instrument':('virtual_order_reservations',"UPDATE virtual_order_reservations SET instrument_id=(SELECT instrument_id FROM virtual_instruments WHERE symbol='OTHER')"),
 'wrong_reservation_amount':('virtual_order_reservations',"UPDATE virtual_order_reservations SET amount='999999'"),
 'wrong_release_amount_sql_valid':('virtual_reservation_releases',"UPDATE virtual_reservation_releases SET amount='0'"),
 'wrong_command_actor':('virtual_order_commands',"UPDATE virtual_order_commands SET actor_id='00000000-0000-4000-8000-000000000001' WHERE operation_kind='TRANSITION'"),
 'wrong_outcome_key':('virtual_transition_outcomes',"UPDATE virtual_transition_outcomes SET idempotency_key='00000000-0000-4000-8000-000000000001'"),
 'conflicting_revision':('virtual_order_events',"UPDATE virtual_order_events SET event_json=replace(event_json, '\"revision\":2', '\"revision\":99') WHERE revision=2"),
 'forged_filled':('virtual_order_events',"UPDATE virtual_order_events SET event_json=replace(event_json,'CANCELLED','FILLED') WHERE revision=2"),
 'noncanonical_event':('virtual_order_events',"UPDATE virtual_order_events SET event_json=' '||event_json"),
 'conflicting_chronology':('virtual_order_events',"UPDATE virtual_order_events SET effective_at='2000-01-01T00:00:00.000000+00:00',recorded_at='2000-01-01T00:00:00.000000+00:00' WHERE revision=2"),
 'nan_reservation':('virtual_order_reservations',"UPDATE virtual_order_reservations SET amount='NaN'"),
}.items():setattr(ReconciliationTests,'test_semantic_'+name,semantic_case(table,sql))

class IsolationTests(_Fixture, unittest.TestCase):
    def test_foreign_key_corruption_is_classified(self):
        self.prepare()
        self.corrupt('virtual_order_reservations',
            "UPDATE virtual_order_reservations SET instrument_id='00000000-0000-4000-8000-000000000001'")
        r=self.inspect()
        self.assertEqual(r.local_status,Status.INCONSISTENT)
        self.assertEqual(r.issue_code,'FOREIGN_KEY_INTEGRITY')
        self.assertEqual(dict((c.name,c.result) for c in r.checks)['ledger'],'NOT_RUN')

    def test_no_file_copy_native_lock_worker_or_writer_boundary(self):
        import ast
        from magi.virtual import diagnostics as d
        source=Path(d.__file__).read_text();tree=ast.parse(source)
        imports={a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names}
        self.assertEqual(imports,{'os','sqlite3','stat'})
        forbidden={'__import__','eval','exec','open','backup','deserialize','serialize',
                   'read_bytes','write_bytes','pread','pwrite','system','popen',
                   'migrate_order_schema','migrate_reservation_schema','migrate_transition_schema'}
        calls={n.func.id if isinstance(n.func,ast.Name) else n.func.attr
               for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,(ast.Name,ast.Attribute))}
        self.assertFalse(calls & forbidden)
        self.assertNotIn('immutable=1',source)
        self.assertNotIn('nolock=1',source)

    def test_non_delete_journal_observation_fails_closed(self):
        # Injection exercises the explicit policy branch without asking SQLite to
        # mutate the real source's journal mode during diagnostics.
        real=sqlite3.connect
        class Connection(sqlite3.Connection):
            def execute(self,sql,*args):
                if sql=='PRAGMA journal_mode':return super().execute("SELECT 'memory'")
                return super().execute(sql,*args)
        with patch('sqlite3.connect',side_effect=lambda *a,**k:real(*a,**k,factory=Connection)):
            self.assertEqual(self.inspect().local_status,Status.UNVERIFIED)

    def test_repeated_corrupt_snapshot_never_healthy(self):
        self.prepare()
        self.corrupt('virtual_transition_baselines','UPDATE virtual_transition_baselines SET revision=99')
        self.assertEqual(self.inspect().local_status,Status.INCONSISTENT)

class ManifestCollisionTests(_Fixture, unittest.TestCase):
    def version(self, version):
        from magi.virtual.order_storage import migrate_order_schema
        from magi.virtual.reservation_storage import migrate_reservation_schema
        from magi.virtual.transition_storage import migrate_transition_schema
        self.path = self.root / 'manifest.virtual.sqlite3'
        VirtualLedger(self.path)
        for minimum, migrate in ((2, migrate_order_schema), (3, migrate_reservation_schema),
                                 (4, migrate_transition_schema)):
            if version >= minimum:
                migrate(self.path, confirmed=True)

    def collision(self, version, kind, extra_first):
        self.version(version)
        c = sqlite3.connect(self.path)
        try:
            name, sql = c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name LIMIT 1").fetchone()
            if extra_first:
                c.execute('DROP TRIGGER ' + name)
            statement = {'table': 'CREATE TABLE '+name+'(unexpected TEXT)',
                         'view': 'CREATE VIEW '+name+' AS SELECT 1 AS unexpected',
                         'index': 'CREATE INDEX '+name+' ON virtual_portfolios(currency)'}[kind]
            c.execute(statement)
            if extra_first:
                c.execute(sql)
            c.commit()
            rows = c.execute('SELECT type,name,sql FROM sqlite_master WHERE name=?', (name,)).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r[0] for r in rows}, {kind, 'trigger'})
            self.assertIn(('trigger', name, sql), rows)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            c.close()
        result = self.inspect()
        self.assertEqual(result.local_status, Status.UNSUPPORTED_SCHEMA)
        self.assertEqual(result.issue_code, 'SCHEMA_MISMATCH')

    def reordered(self, version):
        self.version(version)
        c = sqlite3.connect(self.path)
        try:
            rows = c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name DESC").fetchall()
            for name, _ in rows: c.execute('DROP TRIGGER '+name)
            for _, sql in rows: c.execute(sql)
            c.commit()
        finally: c.close()
        result = self.inspect()
        self.assertEqual((result.local_status,result.schema_version), (Status.HEALTHY,version))

    def mismatch(self, sql):
        self.version(1)
        c = sqlite3.connect(self.path)
        try:
            if sql is None:
                original = c.execute("SELECT sql FROM sqlite_master WHERE name='virtual_events_no_update'").fetchone()[0]
                changed = original.replace('ON virtual_events', 'ON virtual_portfolios')
                self.assertNotEqual(original, changed)
                c.execute('DROP TRIGGER virtual_events_no_update')
                c.execute(changed)
                self.assertEqual(c.execute("SELECT tbl_name,sql FROM sqlite_master WHERE name='virtual_events_no_update'").fetchone(),
                                 ('virtual_portfolios', changed))
                c.commit()
            else:
                c.executescript(sql)
        finally: c.close()
        self.assertEqual(self.inspect().local_status, Status.UNSUPPORTED_SCHEMA)

for _v in (1,2,3,4):
    for _kind in ('table','view','index'):
        for _first in (False,True):
            def _test(self,v=_v,kind=_kind,first=_first): self.collision(v,kind,first)
            setattr(ManifestCollisionTests,f'test_v{_v}_{_kind}_extra_first_{_first}',_test)
    def _valid(self,v=_v): self.reordered(v)
    setattr(ManifestCollisionTests,f'test_v{_v}_valid_reordered',_valid)
for _name,_sql in {
    'unique_extra':'CREATE TABLE unrelated_extra(x)',
    'missing':'DROP TRIGGER virtual_events_no_update',
    'modified':"DROP TRIGGER virtual_events_no_update; CREATE TRIGGER virtual_events_no_update BEFORE UPDATE ON virtual_events BEGIN SELECT 1; END;",
    'wrong_type':'DROP TRIGGER virtual_events_no_update; CREATE TABLE virtual_events_no_update(x)',
    'wrong_owner':None,
}.items():
    def _test(self,sql=_sql): self.mismatch(sql)
    setattr(ManifestCollisionTests,'test_manifest_'+_name,_test)


class AutomaticIndexTests(_Fixture, unittest.TestCase):
    """R3: raw catalog multiplicity/ownership, independently of index_list()."""
    version = ManifestCollisionTests.version
    target = 'sqlite_autoindex_virtual_portfolios_1'
    original = ('index', target, 'virtual_portfolios', None)

    def catalog(self):
        c = sqlite3.connect(self.path)
        try:
            # Inspection only: allow raw rows to be read even when normal SQLite
            # schema loading rejects the deliberately malformed fixture.
            c.execute('PRAGMA writable_schema=ON')
            return c.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema').fetchall()
        finally:
            c.close()

    def check_sqlite(self, expected):
        observed = []
        for pragma in ('integrity_check', 'foreign_key_check'):
            c = sqlite3.connect(self.path)
            try:
                observed.append(c.execute('PRAGMA ' + pragma).fetchall())
            except sqlite3.DatabaseError as exc:
                observed.append(exc.sqlite_errorcode & 255)
            finally:
                c.close()
        self.assertEqual(observed, expected)

    def ordered_reports(self, status):
        real = sqlite3.connect
        reports = []
        for reverse in (False, True):
            def connect(*a, **k):
                c = real(*a, **k)
                c.execute('PRAGMA reverse_unordered_selects=' + str(int(reverse)))
                return c
            with patch('sqlite3.connect', side_effect=connect):
                reports.append(self.inspect())
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[0].local_status, status)
        if status is Status.UNSUPPORTED_SCHEMA:
            self.assertEqual(reports[0].issue_code, 'SCHEMA_MISMATCH')
            self.assertEqual(next(c.result for c in reports[0].checks if c.name == 'ledger'), 'NOT_RUN')

    def alter(self, sql, replacement, *, extra=False, sqlite_results=None,
              status=Status.UNSUPPORTED_SCHEMA):
        before = self.catalog()
        self.assertEqual(before.count(self.original), 1)
        expected = list(before)
        if not extra:
            expected.remove(self.original)
        expected.extend(replacement)
        c = sqlite3.connect(self.path)
        try:
            c.execute('PRAGMA writable_schema=ON')
            c.execute(sql)
            c.execute('PRAGMA writable_schema=OFF')
            c.commit()
        finally:
            c.close()
        # A new connection observes the exact changed row(s), including type,
        # identity, owner, NULL status, and all unchanged rows. No set collapse.
        actual = self.catalog()
        self.assertCountEqual(actual, expected)
        self.assertEqual(len(actual), len(expected))
        if self.original[3] is None and all(r[3] is None for r in replacement):
            self.assertCountEqual([r for r in actual if r[3] is not None],
                                  [r for r in before if r[3] is not None])
        self.check_sqlite(sqlite_results if sqlite_results is not None else [[('ok',)], []])
        self.ordered_reports(status)

    def valid(self, version):
        self.version(version)
        rows = self.catalog()
        automatic = [r for r in rows if r[3] is None]
        self.assertEqual(len(automatic), {1: 9, 2: 19, 3: 28, 4: 37}[version])
        self.assertEqual(automatic.count(self.original), 1)
        tables = [r[1] for r in rows if r[0] == 'table']
        for kind, name, owner, sql in automatic:
            self.assertEqual(kind, 'index')
            self.assertIn(owner, tables)
            self.assertIsNone(sql)
        self.check_sqlite([[('ok',)], []])
        self.ordered_reports(Status.HEALTHY)

    def changed(self, version, variant, *, populated=False):
        if populated:
            request = self.prepare()
            self.apply(self.command(request))
            self.assertEqual(self.inspect().local_status, Status.HEALTHY)
            c = sqlite3.connect(self.path)
            try:
                for table in ('virtual_events', 'virtual_order_events', 'virtual_order_reservations',
                              'virtual_transition_baselines', 'virtual_reservation_releases',
                              'virtual_transition_outcomes'):
                    self.assertGreater(c.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
            finally:
                c.close()
        else:
            self.version(version)
        if variant == 'duplicate':
            self.alter("INSERT INTO sqlite_master SELECT * FROM sqlite_master WHERE name='"+self.target+"'",
                       [self.original], extra=True)
        elif variant == 'owner':
            self.alter("UPDATE sqlite_master SET tbl_name='virtual_events' WHERE name='"+self.target+"'",
                       [('index', self.target, 'virtual_events', None)])
        else:
            self.alter("UPDATE sqlite_master SET type='trigger' WHERE name='"+self.target+"'",
                       [('trigger', self.target, 'virtual_portfolios', None)])

    def test_populated_v4_duplicate(self):
        self.changed(4, 'duplicate', populated=True)

    def test_populated_v4_owner(self):
        self.changed(4, 'owner', populated=True)

    def test_three_identical_rows_preserve_multiplicity(self):
        self.version(1)
        self.alter("INSERT INTO sqlite_master SELECT * FROM sqlite_master WHERE name='"+self.target+"' UNION ALL SELECT * FROM sqlite_master WHERE name='"+self.target+"'",
                   [self.original, self.original], extra=True)

    def test_missing_automatic_index(self):
        self.version(1)
        self.alter("DELETE FROM sqlite_master WHERE name='"+self.target+"'", [],
                   sqlite_results=[sqlite3.SQLITE_CORRUPT, []])

    def test_unexpected_automatic_name(self):
        self.version(1)
        self.alter("UPDATE sqlite_master SET name='sqlite_autoindex_virtual_portfolios_99' WHERE name='"+self.target+"'",
                   [('index', 'sqlite_autoindex_virtual_portfolios_99', 'virtual_portfolios', None)],
                   sqlite_results=[sqlite3.SQLITE_CORRUPT]*2, status=Status.CORRUPT)

    def test_extra_null_index_is_not_a_prefix_exemption(self):
        self.version(1)
        self.alter("INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) VALUES ('index','sqlite_autoindex_virtual_portfolios_99','virtual_portfolios',0,NULL)",
                   [('index', 'sqlite_autoindex_virtual_portfolios_99', 'virtual_portfolios', None)], extra=True,
                   sqlite_results=[sqlite3.SQLITE_CORRUPT]*2, status=Status.CORRUPT)

    def test_unexpected_null_table(self):
        self.version(1)
        self.alter("INSERT INTO sqlite_master(type,name,tbl_name,rootpage,sql) VALUES ('table','unexpected','unexpected',0,NULL)",
                   [('table', 'unexpected', 'unexpected', None)], extra=True,
                   sqlite_results=[sqlite3.SQLITE_CORRUPT]*2, status=Status.CORRUPT)

    def test_automatic_index_cannot_have_explicit_sql(self):
        self.version(1)
        sql = 'CREATE INDEX '+self.target+' ON virtual_portfolios(portfolio_id)'
        self.alter("UPDATE sqlite_master SET sql='"+sql+"' WHERE name='"+self.target+"'",
                   [('index', self.target, 'virtual_portfolios', sql)],
                   sqlite_results=[sqlite3.SQLITE_CORRUPT]*2, status=Status.CORRUPT)

    def test_explicit_index_cannot_hide_in_null_catalog(self):
        self.version(1)
        self.target = 'virtual_one_funding'
        self.original = next(r for r in self.catalog() if r[1] == self.target)
        self.alter("UPDATE sqlite_master SET sql=NULL WHERE name='virtual_one_funding'",
                   [('index', self.target, 'virtual_events', None)],
                   sqlite_results=[sqlite3.SQLITE_CORRUPT]*2, status=Status.CORRUPT)

    def test_explicit_collisions_reject_reversed_enumeration(self):
        real = sqlite3.connect
        def connect(*a, **k):
            c = real(*a, **k); c.execute('PRAGMA reverse_unordered_selects=ON'); return c
        for version in (1, 2, 3, 4):
            for kind in ('table', 'view', 'index'):
                with self.subTest(version=version, kind=kind), patch('sqlite3.connect', side_effect=connect):
                    ManifestCollisionTests.collision(self, version, kind, False)
                    self.path.unlink()


for _v in (1, 2, 3, 4):
    def _valid(self, version=_v): self.valid(version)
    setattr(AutomaticIndexTests, f'test_valid_v{_v}_automatic_indexes', _valid)
    for _variant in ('duplicate', 'owner', 'type'):
        def _test(self, version=_v, variant=_variant): self.changed(version, variant)
        setattr(AutomaticIndexTests, f'test_v{_v}_{_variant}_automatic_catalog', _test)


class ExplicitIdentityTests(_Fixture, unittest.TestCase):
    """R4: full explicit identity, including byte-confirmed NUL alterations."""
    version = ManifestCollisionTests.version
    catalog = AutomaticIndexTests.catalog
    check_sqlite = AutomaticIndexTests.check_sqlite
    ordered_reports = AutomaticIndexTests.ordered_reports

    def changed(self, version, target, field, nul=True, populated=False):
        if populated:
            request = self.prepare()
            self.apply(self.command(request))
        else:
            self.version(version)
        self.assertEqual(self.inspect().local_status, Status.HEALTHY)
        before = self.catalog()
        original = next(r for r in before if r[1] == target)
        self.assertIsNotNone(original[3])
        index = {'type': 0, 'tbl_name': 2}[field]
        replacement = list(original)
        replacement[index] = original[index] + '\0unexpected' if nul else original[index].upper()
        replacement = tuple(replacement)
        self.assertNotEqual(original[index], replacement[index])
        c = sqlite3.connect(self.path)
        try:
            tables = [r[1] for r in before if r[0] == 'table']
            records = {t: c.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in tables}
            if populated:
                for table in ('virtual_events', 'virtual_order_events', 'virtual_reservation_releases',
                              'virtual_transition_outcomes'):
                    self.assertTrue(records[table])
            c.execute('PRAGMA writable_schema=ON')
            c.execute('UPDATE sqlite_master SET '+field+'=? WHERE name=?', (replacement[index], target))
            c.execute('PRAGMA writable_schema=OFF')
            c.commit()
        finally:
            c.close()
        expected = list(before)
        expected.remove(original)
        expected.append(replacement)
        self.assertCountEqual(self.catalog(), expected)
        c = sqlite3.connect(self.path)
        try:
            raw = c.execute('SELECT name,sql,CAST('+field+' AS BLOB) FROM sqlite_schema WHERE name=?', (target,)).fetchone()
            self.assertEqual(raw[:2], (original[1], original[3]))
            self.assertEqual(raw[2], replacement[index].encode())
            if nul:
                self.assertIn(b'\0unexpected', raw[2])
        finally:
            c.close()
        self.check_sqlite([[('ok',)], []])
        self.ordered_reports(Status.UNSUPPORTED_SCHEMA)
        c = sqlite3.connect(self.path)
        try:
            self.assertEqual(records, {t: c.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in tables})
        finally:
            c.close()

    def test_populated_v4_type(self):
        self.changed(4, 'virtual_portfolios', 'type', populated=True)

    def test_populated_v4_owner(self):
        self.changed(4, 'virtual_portfolios', 'tbl_name', populated=True)

    def canonical(self, version):
        from magi.virtual import diagnostics as d
        self.path = self.root / 'canonical.virtual.sqlite3'
        c = sqlite3.connect(self.path)
        try:
            for filename in ('schema.sql', 'order_schema.sql', 'reservation_schema.sql', 'transition_schema.sql')[:version]:
                c.executescript(Path(d.__file__).with_name(filename).read_text())
            rows = c.execute('SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL').fetchall()
        finally:
            c.close()
        self.path.chmod(0o600)
        manifest = d._schema()[1]
        for minimum, extension in ((2, d._orders), (3, d._reservations), (4, d._transitions)):
            if version >= minimum:
                manifest.update(extension()[1])
        self.assertCountEqual(d._explicit_catalog(manifest), rows)
        self.ordered_reports(Status.HEALTHY)

    def test_unrecognized_trusted_header_has_no_fallback(self):
        from magi.virtual.diagnostics import _explicit_catalog
        for definition in ('CREATE VIEW x AS SELECT 1', 'CREATE TABLE x(y)',
                           'CREATE TRIGGER x AFTER DELETE ON absent',
                           'CREATE UNIQUE INDEX x ON absent(y)'):
            with self.subTest(definition=definition), self.assertRaises(ValueError):
                _explicit_catalog({'x': definition})


for _v in (1, 2, 3, 4):
    def _canonical(self, version=_v): self.canonical(version)
    setattr(ExplicitIdentityTests, f'test_canonical_v{_v}', _canonical)
    for _target in ('virtual_portfolios', 'virtual_one_funding', 'virtual_portfolios_no_delete'):
        for _field in ('type', 'tbl_name'):
            for _nul in (False, True):
                def _test(self, version=_v, target=_target, field=_field, nul=_nul):
                    self.changed(version, target, field, nul)
                setattr(ExplicitIdentityTests, f'test_v{_v}_{_target}_{_field}_nul_{_nul}', _test)

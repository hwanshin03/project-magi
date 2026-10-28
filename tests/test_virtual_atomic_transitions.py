"""Package C synthetic-only transactions, process faults and release conservation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace, FrozenInstanceError
from datetime import timedelta
from decimal import Decimal as D, localcontext, Inexact
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier
import unittest
from unittest.mock import patch

import test_virtual_reservations as fixtures
from test_virtual_order_persistence import uid
from test_virtual_order_lifecycle import CUT, OPEN, DEADLINE, deadline_evidence, invalidation
from magi.virtual import VirtualLedger, Side, ErrorCode, VirtualError
from magi.virtual.order_codec import OrderEvent, PersistenceError
from magi.virtual.order_lifecycle import OrderState as S, SealStatus, transition_order
from magi.virtual.order_storage import OrderStore
from magi.virtual.reservations import ReservationCode
from magi.virtual.reservation_storage import ReservationStore, _reserve_synthetic, migrate_reservation_schema
from magi.virtual.atomic_transitions import (SyntheticContext, SyntheticTransition, TransitionOutcome,
    encode_transition, decode_transition)
from magi.virtual.transition_storage import (TransitionStore, migrate_transition_schema,
    _transition_synthetic, _TransitionDatabase)

ROOT = Path(__file__).resolve().parents[1]


class AtomicTransitionTests(unittest.TestCase):
    # Reuse fixture construction, not Package B's test methods.
    setUp = fixtures.ReservationTests.setUp
    request = fixtures.ReservationTests.request
    rows = fixtures.ReservationTests.rows
    corrupt = fixtures.ReservationTests.corrupt
    error = fixtures.ReservationTests.error

    def prepare(self, side=Side.BUY, amount='100', *, funded=True, states=()):
        r = self.request(side, amount, quantity=amount if side is Side.SELL else '2', states=states)
        migrate_reservation_schema(self.path, confirmed=True)
        if funded:
            _reserve_synthetic(self.path, r)
        migrate_transition_schema(self.path, confirmed=True)
        return r

    def store(self):
        return TransitionStore(self.path)

    def command(self, r, state=S.CANCELLED, *, at=None, evidence=None, before=None):
        before = before or self.store().history(r.portfolio_id, r.order_id)[-1]
        at = at or before.recorded_at+timedelta(seconds=1)
        snapshot = transition_order(before.snapshot, state, at=at, expiration=evidence)
        event = OrderEvent(uid(), uid(), before.revision+1, before.snapshot.state, snapshot, at)
        return SyntheticTransition(uid(), uid(), r.reservation_id, SyntheticContext(uid(), at), event)

    def apply(self, command):
        return _transition_synthetic(self.path, command)

    def state(self):
        return self.store().state(self.p)

    def dump(self):
        with sqlite3.connect(self.path) as c:
            return tuple(c.iterdump())

    def fails_unchanged(self, call):
        before = self.dump()
        with self.assertRaises((ValueError, sqlite3.Error)):
            call()
        self.assertEqual(before, self.dump())

    def test_accepted_to_frozen_preserves_buy(self):
        r = self.prepare(); before = self.state()
        out = self.apply(self.command(r, S.FROZEN, at=CUT))
        self.assertFalse(out.released)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.store().history(self.p,r.order_id)[-1].snapshot.state,S.FROZEN)

    def test_awaiting_preserves_buy(self):
        r=self.prepare(); before=self.state()
        self.apply(self.command(r,S.FROZEN,at=CUT))
        self.apply(self.command(r,S.AWAITING_PRICE,at=OPEN))
        self.assertEqual(self.state(),before)
        self.assertEqual(len(self.store().history(self.p,r.order_id)),3)

    def test_freeze_preserves_sell(self):
        r=self.prepare(Side.SELL,'3'); before=self.state()
        self.apply(self.command(r,S.FROZEN,at=CUT))
        self.assertEqual(self.state(),before)

    def test_awaiting_preserves_sell(self):
        r=self.prepare(Side.SELL,'3'); before=self.state()
        self.apply(self.command(r,S.FROZEN,at=CUT))
        self.apply(self.command(r,S.AWAITING_PRICE,at=OPEN))
        self.assertEqual(self.state(),before)

    def test_cancel_buy_restores_cash(self):
        r=self.prepare(); self.assertEqual(self.state().cash.available,D(700))
        out=self.apply(self.command(r))
        self.assertTrue(out.released)
        self.assertEqual((self.state().cash.posted,self.state().cash.available),(D(800),D(800)))
        self.assertEqual(self.state().reservations,())

    def test_cancel_sell_restores_shares(self):
        r=self.prepare(Side.SELL,'3'); self.apply(self.command(r))
        resource=dict(self.state().shares)[self.i]
        self.assertEqual((resource.posted,resource.reserved,resource.available),(D(10),D(0),D(10)))

    def test_expire_buy_restores_cash(self):
        r=self.prepare(); self.apply(self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()))
        self.assertEqual(self.state().cash.available,D(800))

    def test_expire_sell_restores_shares(self):
        r=self.prepare(Side.SELL,'10')
        self.apply(self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()))
        self.assertEqual(dict(self.state().shares)[self.i].available,D(10))

    def test_expire_frozen_with_evidence(self):
        r=self.prepare(); self.apply(self.command(r,S.FROZEN,at=CUT))
        self.apply(self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()))
        self.assertFalse(self.state().reservations)

    def test_expire_awaiting_with_evidence(self):
        r=self.prepare(); self.apply(self.command(r,S.FROZEN,at=CUT))
        self.apply(self.command(r,S.AWAITING_PRICE,at=OPEN))
        self.apply(self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()))
        self.assertEqual(len(self.store().history(self.p,r.order_id)),4)

    def test_calendar_invalidation_expiration(self):
        r=self.prepare(); self.apply(self.command(r,S.EXPIRED,at=CUT,evidence=invalidation()))
        self.assertEqual(self.state().cash.available,D(800))

    def test_cancel_exact_cutoff(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,at=CUT)))

    def test_cancel_after_cutoff_without_worker(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,at=OPEN)))

    def test_missing_expiry_evidence(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,S.EXPIRED,at=DEADLINE)))

    def test_unknown_expiry_evidence(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,S.EXPIRED,at=DEADLINE,
            evidence=replace(deadline_evidence(),seal_status=SealStatus.UNKNOWN))))

    def test_sealed_blocks_expiry(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,S.EXPIRED,at=DEADLINE,
            evidence=replace(deadline_evidence(),seal_status=SealStatus.SEALED))))

    def test_expiry_before_deadline(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,S.EXPIRED,
            at=DEADLINE-timedelta(microseconds=1),evidence=deadline_evidence())))

    def test_no_automatic_rollover_or_clock_advance(self):
        r=self.prepare(); before=self.dump()
        with patch('time.time',return_value=9999999999):
            e=self.store().history(self.p,r.order_id)[-1]
            self.assertEqual(e.snapshot.state,S.ACCEPTED)
            self.assertEqual(e.snapshot.selection,self.initial.selection)
        self.assertEqual(before,self.dump())

    def test_posted_ledger_cash_shares_basis_and_pnl_unchanged(self):
        r=self.prepare(); before=self.rows('virtual_events')
        self.apply(self.command(r))
        self.assertEqual(before,self.rows('virtual_events'))
        from magi.virtual.accounting import reconstruct
        with sqlite3.connect(self.path) as c:
            c.row_factory=sqlite3.Row
            self.assertEqual(reconstruct(self.p,VirtualLedger._events(c,self.p)),self.posted)

    def test_original_order_and_reservation_records_unchanged(self):
        r=self.prepare(); tables=('virtual_orders','virtual_order_reservations','virtual_reservation_operations')
        before={t:self.rows(t) for t in tables}; self.apply(self.command(r))
        self.assertEqual(before,{t:self.rows(t) for t in tables})

    def test_cancel_identical_replay(self):
        r=self.prepare(); c=self.command(r); out=self.apply(c); before=self.dump()
        self.assertEqual(self.apply(c),out); self.assertEqual(self.dump(),before)
        self.assertEqual(self.store().outcome(self.p,c.actor_id,c.idempotency_key),out)

    def test_expire_identical_replay(self):
        r=self.prepare(); c=self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence())
        out=self.apply(c); before=self.dump()
        self.assertEqual(self.apply(c),out); self.assertEqual(before,self.dump())

    def test_conflicting_key(self):
        r=self.prepare(); c=self.command(r); self.apply(c)
        altered=replace(c,event=replace(c.event,event_id=uid(),command_id=uid()))
        self.error(ErrorCode.IDEMPOTENCY_CONFLICT,lambda:self.apply(altered))
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_conflicting_command_id(self):
        r=self.prepare(); c=self.command(r); self.apply(c)
        self.error(ErrorCode.IDEMPOTENCY_CONFLICT,lambda:self.apply(replace(c,idempotency_key=uid())))

    def test_new_key_duplicate_terminal_not_success(self):
        r=self.prepare(); c=self.command(r); self.apply(c)
        self.fails_unchanged(lambda:self.apply(replace(c,idempotency_key=uid(),event=replace(c.event,command_id=uid(),event_id=uid()))))

    def test_stale_revision_rejected(self):
        r=self.prepare(); c=self.command(r); self.apply(self.command(r,S.FROZEN,at=CUT))
        self.error(ErrorCode.IDENTITY_CONFLICT,lambda:self.apply(c))

    def race(self, commands):
        barrier=Barrier(len(commands))
        def run(c):
            barrier.wait()
            try: return self.apply(c)
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=len(commands)) as pool:
            return list(pool.map(run,commands))

    def test_identical_cancellations_race(self):
        r=self.prepare(); c=self.command(r); results=self.race([c,c])
        self.assertEqual(results[0],results[1]); self.assertIsNotNone(results[0])
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_distinct_cancellations_race(self):
        r=self.prepare(); results=self.race([self.command(r),self.command(r)])
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_cancel_expire_race(self):
        r=self.prepare(); results=self.race([self.command(r),self.command(r,S.EXPIRED,at=CUT,evidence=invalidation())])
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_cancel_freeze_race(self):
        r=self.prepare(); results=self.race([self.command(r),self.command(r,S.FROZEN,at=CUT)])
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(len(self.rows('virtual_reservation_releases')),int(next(x for x in results if x).released))

    def test_concurrent_freeze(self):
        r=self.prepare(); results=self.race([self.command(r,S.FROZEN,at=CUT),self.command(r,S.FROZEN,at=CUT)])
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(self.state().cash.reserved,D(100))

    def test_concurrent_expirations(self):
        r=self.prepare(); results=self.race([self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()) for _ in range(2)])
        self.assertEqual(sum(x is not None for x in results),1)
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_wrong_portfolio(self):
        r=self.prepare(); c=self.command(r)
        snapshot=replace(c.event.snapshot,intent=replace(c.event.snapshot.intent,portfolio_id=self.other_p))
        self.fails_unchanged(lambda:self.apply(replace(c,event=replace(c.event,snapshot=snapshot))))
        with self.assertRaises(PersistenceError): self.store().history(self.other_p,r.order_id)

    def test_wrong_instrument(self):
        r=self.prepare(); c=self.command(r)
        snapshot=replace(c.event.snapshot,intent=replace(c.event.snapshot.intent,instrument_id=self.other_i))
        self.fails_unchanged(lambda:self.apply(replace(c,event=replace(c.event,snapshot=snapshot))))

    def test_wrong_reservation_identity(self):
        r=self.prepare(); c=self.command(r)
        self.fails_unchanged(lambda:self.apply(replace(c,reservation_id=uid())))

    def test_missing_reservation(self):
        r=self.prepare(funded=False); self.fails_unchanged(lambda:self.apply(self.command(r)))

    def test_denied_reservation_cannot_transition(self):
        r=self.prepare(amount='801'); self.fails_unchanged(lambda:self.apply(self.command(r)))

    def test_terminal_resurrection(self):
        r=self.prepare(); self.apply(self.command(r))
        self.fails_unchanged(lambda:self.apply(self.command(r,S.FROZEN,at=CUT)))

    def test_filled_remains_blocked(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,S.FILLED,at=OPEN)))

    def test_no_skip_freeze(self):
        r=self.prepare(); self.fails_unchanged(lambda:self.apply(self.command(r,S.AWAITING_PRICE,at=OPEN)))

    def test_pure_validator_called_in_transaction(self):
        r=self.prepare(); c=self.command(r)
        with patch('magi.virtual.transition_storage.transition_order',side_effect=ValueError('blocked')):
            self.fails_unchanged(lambda:self.apply(c))

    def test_receipt_after_transition_rejected(self):
        r=self.prepare(); c=self.command(r)
        self.fails_unchanged(lambda:replace(c,context=replace(c.context,received_at=OPEN)))

    def test_old_receipt_cannot_extend_cutoff(self):
        r=self.prepare()
        self.fails_unchanged(lambda:self.command(r,at=OPEN))
        c=self.command(r)
        bad=replace(c.event.snapshot)
        object.__setattr__(bad,'changed_at',OPEN)
        self.fails_unchanged(lambda:self.apply(replace(c,event=replace(c.event,snapshot=bad))))

    def test_naive_context_rejected(self):
        r=self.prepare(); c=self.command(r)
        self.fails_unchanged(lambda:replace(c.context,received_at=c.context.received_at.replace(tzinfo=None)))

    def test_context_is_required_not_default_authority(self):
        r=self.prepare(); c=self.command(r)
        self.fails_unchanged(lambda:replace(c,context=None))
        self.assertNotIn('is_trusted',c.context.__dataclass_fields__)

    def test_codec_roundtrip_and_immutable(self):
        r=self.prepare(); c=self.command(r)
        self.assertEqual(decode_transition(encode_transition(c)),c)
        with self.assertRaises(FrozenInstanceError): c.actor_id=uid()

    def test_codec_rejects_unknown_duplicate_noncanonical_and_version(self):
        r=self.prepare(); text=encode_transition(self.command(r))
        for bad in (text+' ',text.replace('"version":1','"version":2'),text.replace('{','{"extra":1,',1),
                    text.replace('"version":1','"version":1,"version":1'), 'x'*32769):
            with self.subTest(bad=bad[:20]):
                self.error(ErrorCode.INVALID_LEDGER,lambda:decode_transition(bad))

    def test_hostile_decimal_context(self):
        r=self.prepare()
        with localcontext() as context:
            context.prec=1; context.traps[Inexact]=True
            self.apply(self.command(r)); self.assertEqual(self.state().cash.available,D(800))

    def inject(self, command, *, after=None, commit=False, lost=False):
        real=sqlite3.connect
        class Fault(sqlite3.Connection):
            def execute(self,sql,*args,**kwargs):
                result=super().execute(sql,*args,**kwargs)
                if after and sql.startswith(after): raise RuntimeError('synthetic fault')
                return result
            def commit(self):
                if commit: raise sqlite3.OperationalError('synthetic commit failure')
                super().commit()
                if lost: raise RuntimeError('synthetic reply lost')
        def connect(*args,**kwargs): return real(*args,**kwargs,factory=Fault)
        before=self.dump()
        with patch('sqlite3.connect',side_effect=connect):
            with self.assertRaises((RuntimeError,VirtualError)): self.apply(command)
        if not lost: self.assertEqual(before,self.dump())

    def test_rollback_after_command_association(self):
        r=self.prepare(); self.inject(self.command(r),after='INSERT INTO virtual_order_commands')

    def test_rollback_after_event(self):
        r=self.prepare(); self.inject(self.command(r),after='INSERT INTO virtual_order_events')

    def test_rollback_after_release(self):
        r=self.prepare(); self.inject(self.command(r),after='INSERT INTO virtual_reservation_releases')

    def test_rollback_after_outcome(self):
        r=self.prepare(); self.inject(self.command(r),after='INSERT INTO virtual_transition_outcomes')

    def test_rollback_before_commit(self):
        r=self.prepare(); self.inject(self.command(r),commit=True)

    def test_before_transaction_failure(self):
        r=self.prepare(); c=self.command(r); before=self.dump()
        with patch.object(_TransitionDatabase,'_safe_path',side_effect=RuntimeError('synthetic fault')):
            with self.assertRaises(RuntimeError): self.apply(c)
        self.assertEqual(before,self.dump())

    def test_lost_reply_recovers_original_outcome(self):
        r=self.prepare(); c=self.command(r); self.inject(c,lost=True)
        before=self.dump(); self.assertEqual(self.apply(c),TransitionOutcome(c,True))
        self.assertEqual(before,self.dump())

    def test_retry_after_rollback(self):
        r=self.prepare(); c=self.command(r); self.inject(c,after='INSERT INTO virtual_reservation_releases')
        self.assertEqual(self.apply(c),TransitionOutcome(c,True))
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def child(self,c,prefix=''):
        code="""import sys
sys.path.insert(0,sys.argv[1])
from magi.virtual.atomic_transitions import decode_transition, encode_transition
from magi.virtual.transition_storage import _transition_synthetic
"""+prefix+"\nc=decode_transition(sys.argv[3]); out=_transition_synthetic(sys.argv[2],c); print(encode_transition(out.command))"
        return [sys.executable,'-I','-B','-c',code,str(ROOT),str(self.path),encode_transition(c)]

    def test_fresh_process_replay(self):
        r=self.prepare(); c=self.command(r); self.apply(c); before=self.dump()
        result=subprocess.run(self.child(c),env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),encode_transition(c)); self.assertEqual(before,self.dump())

    def test_process_crash_before_commit(self):
        r=self.prepare(); c=self.command(r); before=self.dump()
        prefix="""import sqlite3, os
real=sqlite3.connect
class Crash(sqlite3.Connection):
    def commit(self): os._exit(23)
sqlite3.connect=lambda *a,**k:real(*a,**k,factory=Crash)
"""
        result=subprocess.run(self.child(c,prefix),env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,23,result.stderr); self.assertEqual(before,self.dump())
        self.apply(c)

    def test_independent_process_identical_commands(self):
        r=self.prepare(); c=self.command(r)
        children=[subprocess.Popen(self.child(c,"print('ready',flush=True); sys.stdin.readline()"),
            env={'PATH':'/usr/bin:/bin'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        try:
            for child in children: self.assertEqual(child.stdout.readline().strip(),'ready')
            for child in children: child.stdin.write('\n'); child.stdin.flush()
            outputs=[child.communicate(timeout=20) for child in children]
            for child,(out,err) in zip(children,outputs):
                self.assertEqual(child.returncode,0,err); self.assertEqual(out.strip(),encode_transition(c))
        finally:
            for child in children:
                if child.poll() is None: child.kill(); child.wait()
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_lock_timeout_no_effect(self):
        r=self.prepare(); c=self.command(r); before=self.dump()
        with sqlite3.connect(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            self.error(ErrorCode.STORAGE_UNAVAILABLE,lambda:_transition_synthetic(self.path,c,timeout=.01))
        self.assertEqual(before,self.dump()); self.apply(c)

    def test_migration_preserves_all_v3_records(self):
        r=self.request(); migrate_reservation_schema(self.path,confirmed=True); _reserve_synthetic(self.path,r)
        with sqlite3.connect(self.path) as conn:
            tables=[x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before={t:self.rows(t) for t in tables}
        migrate_transition_schema(self.path,confirmed=True)
        self.assertEqual(before,{t:self.rows(t) for t in tables})
        after=self.dump(); migrate_transition_schema(self.path,confirmed=True); self.assertEqual(after,self.dump())

    def test_migration_retains_legacy_unreserved_transition_history(self):
        r=self.prepare(funded=False,states=((S.FROZEN,CUT,None),))
        self.assertEqual(len(self.store().history(self.p,r.order_id)),2)
        self.fails_unchanged(lambda:self.apply(self.command(r,S.AWAITING_PRICE,at=OPEN)))

    def test_explicit_migration_required(self):
        self.request(); migrate_reservation_schema(self.path,confirmed=True); before=self.dump()
        self.error(ErrorCode.CONFIRMATION_REQUIRED,lambda:migrate_transition_schema(self.path))
        with self.assertRaises(VirtualError): self.store()
        self.assertEqual(before,self.dump())

    def test_legacy_runtimes_reject_v4(self):
        r=self.prepare()
        for call in (lambda:ReservationStore(self.path),lambda:OrderStore(self.path),lambda:VirtualLedger(self.path),
                     lambda:_reserve_synthetic(self.path,r),lambda:self.ledger.fund(self.p,D(1),idempotency_key=uid(),confirmed=True)):
            self.fails_unchanged(call)

    def test_v2_rejected(self):
        self.fails_unchanged(lambda:migrate_transition_schema(self.path,confirmed=True))

    def test_package_b_outcome_preserved_after_release(self):
        r=self.prepare(); expected=self.store().reservation_outcome(self.p,r.actor_id,r.idempotency_key)
        self.apply(self.command(r))
        self.assertEqual(self.store().reservation_outcome(self.p,r.actor_id,r.idempotency_key),expected)
        self.assertEqual(expected.code,ReservationCode.RESERVED)

    def test_denial_not_recomputed_after_release(self):
        a=self.request(amount='700'); b=self.request(amount='200')
        migrate_reservation_schema(self.path,confirmed=True)
        _reserve_synthetic(self.path,a); denied=_reserve_synthetic(self.path,b)
        migrate_transition_schema(self.path,confirmed=True); self.apply(self.command(a))
        self.assertEqual(self.store().reservation_outcome(self.p,b.actor_id,b.idempotency_key),denied)
        self.assertEqual(denied.code,ReservationCode.INSUFFICIENT_CASH)

    def test_corrupt_reservation_amount(self):
        r=self.prepare(); self.corrupt('virtual_order_reservations',"UPDATE virtual_order_reservations SET amount='999'")
        with self.assertRaises(ValueError): self.store()

    def test_corrupt_release_amount(self):
        r=self.prepare(); self.apply(self.command(r))
        self.corrupt('virtual_reservation_releases',"UPDATE virtual_reservation_releases SET amount='99'")
        with self.assertRaises(ValueError): self.store()

    def test_missing_event_history(self):
        r=self.prepare(); self.apply(self.command(r))
        self.corrupt('virtual_order_events','DELETE FROM virtual_order_events WHERE revision=2',deleting=True)
        with self.assertRaises(ValueError): self.store()

    def test_missing_outcome(self):
        r=self.prepare(); self.apply(self.command(r,S.FROZEN,at=CUT))
        self.corrupt('virtual_transition_outcomes','DELETE FROM virtual_transition_outcomes',deleting=True)
        with self.assertRaises(ValueError): self.store()

    def test_orphan_release(self):
        r=self.prepare(); self.apply(self.command(r))
        self.corrupt('virtual_reservation_releases','UPDATE virtual_reservation_releases SET command_id=?',(uid(),))
        with self.assertRaises(ValueError): self.store()

    def test_unknown_schema_and_extra_object(self):
        self.prepare()
        with sqlite3.connect(self.path) as c: c.execute('CREATE TABLE unexpected (x)')
        with self.assertRaises(VirtualError): self.store()

    def test_immutable_new_tables(self):
        r=self.prepare(); self.apply(self.command(r))
        for table in ('virtual_transition_baselines','virtual_transition_outcomes','virtual_reservation_releases'):
            with self.subTest(table=table),sqlite3.connect(self.path) as c:
                for sql in ('DELETE FROM '+table,'UPDATE '+table+' SET order_id=order_id'):
                    with self.assertRaises(sqlite3.IntegrityError): c.execute(sql)

    def test_no_public_admission_or_financial_writer(self):
        self.prepare(); store=self.store()
        for name in ('submit','release','fill','fund','apply_synthetic_fill','register_instrument','create_portfolio'):
            self.assertFalse(hasattr(store,name)); self.assertFalse(hasattr(store._database,name))
        import magi.virtual
        self.assertFalse(hasattr(magi.virtual,'_transition_synthetic'))

    def test_fresh_process_import_isolation(self):
        code="""import sys
sys.path.insert(0,sys.argv[1])
class Deny:
    def find_spec(self,name,*args):
        if name.startswith(('magi.broker','magi.toss','magi.storage','magi.portfolio','magi.market','magi.analysis','dotenv','httpx','requests')):
            raise AssertionError('forbidden import')
sys.meta_path.insert(0,Deny())
import sqlite3,socket
sqlite3.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('database on import'))
socket.create_connection=lambda *a,**k:(_ for _ in ()).throw(AssertionError('network'))
import magi.virtual.transition_storage
"""
        result=subprocess.run([sys.executable,'-I','-B','-c',code,str(ROOT)],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_migration_rollback_after_ddl_and_version(self):
        self.request(); migrate_reservation_schema(self.path,confirmed=True); before=self.dump()
        real=sqlite3.connect
        for target in ('CREATE TABLE virtual_reservation_releases','PRAGMA user_version = 4',
                       'INSERT INTO virtual_transition_baselines'):
            class Fault(sqlite3.Connection):
                def execute(self,sql,*a,**k):
                    result=super().execute(sql,*a,**k)
                    if sql.startswith(target): raise RuntimeError('synthetic migration fault')
                    return result
            with self.subTest(target=target),patch('sqlite3.connect',side_effect=lambda *a,**k:real(*a,**k,factory=Fault)):
                with self.assertRaises(RuntimeError): migrate_transition_schema(self.path,confirmed=True)
            self.assertEqual(before,self.dump())
        self.assertEqual(migrate_transition_schema(self.path,confirmed=True),4)

    def test_migration_commit_failure_rolls_back(self):
        self.request(); migrate_reservation_schema(self.path,confirmed=True); before=self.dump()
        real=sqlite3.connect
        class Fault(sqlite3.Connection):
            def commit(self): raise sqlite3.OperationalError('synthetic fault')
        with patch('sqlite3.connect',side_effect=lambda *a,**k:real(*a,**k,factory=Fault)):
            self.error(ErrorCode.STORAGE_UNAVAILABLE,lambda:migrate_transition_schema(self.path,confirmed=True))
        self.assertEqual(before,self.dump())

    def test_failure_after_validation_before_any_write(self):
        r=self.prepare(); c=self.command(r); before=self.dump()
        from magi.virtual import transition_storage as storage
        real=storage._validate_edge
        def fail(*a): real(*a); raise RuntimeError('synthetic fault')
        with patch.object(storage,'_validate_edge',side_effect=fail):
            with self.assertRaises(RuntimeError): self.apply(c)
        self.assertEqual(before,self.dump())

    def test_final_reconciliation_failure_rolls_back(self):
        r=self.prepare(); c=self.command(r); before=self.dump()
        from magi.virtual import transition_storage as storage
        real=storage._replay; calls=[]
        def fail(conn):
            result=real(conn); calls.append(True)
            if len(calls)==2: raise RuntimeError('synthetic reconciliation fault')
            return result
        with patch.object(storage,'_replay',side_effect=fail):
            with self.assertRaises(RuntimeError): self.apply(c)
        self.assertEqual(before,self.dump())

    def test_cross_order_reservation_cannot_release(self):
        a=self.request(); b=self.request(p=self.other_p)
        migrate_reservation_schema(self.path,confirmed=True)
        _reserve_synthetic(self.path,a); _reserve_synthetic(self.path,b)
        migrate_transition_schema(self.path,confirmed=True)
        c=self.command(a)
        self.fails_unchanged(lambda:self.apply(replace(c,reservation_id=b.reservation_id)))
        self.apply(c)
        self.assertEqual(self.store().state(self.other_p).reservations,(b,))

    def test_wrong_order_identity(self):
        r=self.prepare(); c=self.command(r)
        snapshot=replace(c.event.snapshot,intent=replace(c.event.snapshot.intent,order_id=uid()))
        self.fails_unchanged(lambda:self.apply(replace(c,event=replace(c.event,snapshot=snapshot))))

    def test_reused_submission_command_identity_rejected(self):
        r=self.prepare(); c=self.command(r)
        self.fails_unchanged(lambda:self.apply(replace(c,event=replace(c.event,command_id=r.command_id))))

    def test_duplicate_event_identity_rejected(self):
        r=self.prepare(); c=self.command(r)
        self.fails_unchanged(lambda:self.apply(replace(c,event=replace(c.event,event_id=r.creation_event_id))))

    def test_corrupt_command_actor_detected(self):
        r=self.prepare(); c=self.command(r); self.apply(c)
        self.corrupt('virtual_order_commands','UPDATE virtual_order_commands SET actor_id=? WHERE operation_id=?',
                     (uid(),c.event.command_id))
        with self.assertRaises(ValueError): self.store()

    def test_corrupt_prior_revision_detected(self):
        r=self.prepare(); self.apply(self.command(r,S.FROZEN,at=CUT))
        self.corrupt('virtual_order_events','UPDATE virtual_order_events SET revision=3 WHERE revision=2')
        with self.assertRaises(ValueError): self.store()

    def test_missing_release_fails_closed(self):
        r=self.prepare(); self.apply(self.command(r))
        self.corrupt('virtual_reservation_releases','DELETE FROM virtual_reservation_releases',deleting=True)
        with self.assertRaises(ValueError): self.store()

    def test_corrupted_expiration_payload_fails_closed(self):
        r=self.prepare(); c=self.command(r,S.EXPIRED,at=DEADLINE,evidence=deadline_evidence()); self.apply(c)
        data=json.loads(encode_transition(c)); data['event']['snapshot']['expiration']=None
        self.corrupt('virtual_transition_outcomes','UPDATE virtual_transition_outcomes SET request_json=?',
                     (json.dumps(data,sort_keys=True,separators=(',',':')),))
        with self.assertRaises(ValueError): self.store()

    def test_unknown_version_rejected(self):
        self.prepare()
        with sqlite3.connect(self.path) as c: c.execute('PRAGMA user_version=99')
        self.fails_unchanged(lambda:self.store())
        self.fails_unchanged(lambda:migrate_transition_schema(self.path,confirmed=True))

    def test_read_snapshot_during_uncommitted_release(self):
        r=self.prepare(); c=self.command(r); before=self.state()
        from magi.virtual import transition_storage as storage
        real=storage._replay; calls=[]
        def inspect(conn):
            result=real(conn); calls.append(True)
            if len(calls)==2:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertEqual(pool.submit(lambda:self.store().state(self.p)).result(timeout=5),before)
            return result
        with patch.object(storage,'_replay',side_effect=inspect): self.apply(c)
        self.assertEqual(self.state().cash.available,D(800))

    def test_independent_process_conflicting_commands(self):
        r=self.prepare(); a=self.command(r); b=self.command(r)
        children=[subprocess.Popen(self.child(c,"print('ready',flush=True); sys.stdin.readline()"),
            env={'PATH':'/usr/bin:/bin'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for c in (a,b)]
        try:
            for child in children: self.assertEqual(child.stdout.readline().strip(),'ready')
            for child in children: child.stdin.write('\n'); child.stdin.flush()
            for child in children: child.communicate(timeout=20)
            self.assertEqual(sum(child.returncode==0 for child in children),1)
        finally:
            for child in children:
                if child.poll() is None: child.kill(); child.wait()
        self.assertEqual(len(self.rows('virtual_reservation_releases')),1)

    def test_process_crash_after_commit_replays(self):
        r=self.prepare(); c=self.command(r)
        prefix="""import sqlite3, os
real=sqlite3.connect
class Crash(sqlite3.Connection):
    def commit(self): super().commit(); os._exit(24)
sqlite3.connect=lambda *a,**k:real(*a,**k,factory=Crash)
"""
        result=subprocess.run(self.child(c,prefix),env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,24,result.stderr)
        before=self.dump(); self.assertEqual(self.apply(c),TransitionOutcome(c,True)); self.assertEqual(before,self.dump())

    def test_read_only_and_deterministic_reopen(self):
        r=self.prepare(); c=self.command(r); self.apply(c)
        before=self.dump(); a=self.store(); b=self.store()
        self.assertEqual(a.state(self.p),b.state(self.p)); self.assertEqual(a.history(self.p,r.order_id),b.history(self.p,r.order_id))
        with a._database._connection() as conn:
            with self.assertRaises(sqlite3.OperationalError): conn.execute('INSERT INTO virtual_portfolios VALUES (?,?)',(uid(),'USD'))
        self.assertEqual(before,self.dump())

    def test_expiration_preserves_evidence_and_original_session(self):
        r=self.prepare(); c=self.command(r,S.EXPIRED,at=CUT,evidence=invalidation()); self.apply(c)
        last=self.store().history(self.p,r.order_id)[-1]
        self.assertEqual(last.snapshot.expiration,c.event.snapshot.expiration)
        self.assertEqual(last.snapshot.selection,self.initial.selection)
        self.assertEqual(last.snapshot.intent,c.event.snapshot.intent)

"""Pure policy on invented XSIM schedules; no official exchange history or trades."""
import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D, localcontext, Inexact
from itertools import permutations
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from magi.virtual.models import Side, VirtualError
from magi.virtual.execution_contracts import (CalendarCoverage as Coverage, CalendarSession,
                                             SessionStatus as Status, SyntheticCalendar)
from magi.virtual.session_policy import (SessionPolicy, SessionSelection, SessionEligibility as Eligibility,
                                        PolicyCode as Code, PolicyError, classify_session, select_session)
from magi.virtual.order_lifecycle import (OrderIntent, OrderSnapshot, OrderState as State,
                                        ExpirationEvidence, ExpirationReason as Reason, SealStatus,
                                        prepare_order, transition_order, validate_transition)
from test_virtual_execution_contracts import session, closed, CALENDAR, DAY, NY, observation

ROOT = Path(__file__).resolve().parents[1]
POLICY = SessionPolicy('synthetic-next-open-v1', timedelta(minutes=5), timedelta(hours=2))
AT = datetime(2026, 3, 9, 9, 24, tzinfo=NY)
CUT = AT.replace(minute=25)
OPEN = AT.replace(minute=30)
DEADLINE = AT.replace(hour=18, minute=0)
UUIDS = tuple(f'{i:08d}-1111-4111-8111-111111111111' for i in range(1, 5))


def intent(**changes):
    values = dict(order_id=UUIDS[0], portfolio_id=UUIDS[1], instrument_id=UUIDS[2], exchange='XSIM',
                  side=Side.BUY, quantity=D(10), submitted_at=AT-timedelta(seconds=2), idempotency_key=UUIDS[3])
    values.update(changes)
    return OrderIntent(**values)


def calendar(*sessions):
    return SyntheticCalendar('XSIM', NY.key, CALENDAR, tuple(sessions))


def accepted(**changes):
    values = dict(calendar=calendar(session()), policy=POLICY, accepted_at=AT, through_date=DAY)
    values.update(changes)
    return prepare_order(intent(), **values)


def deadline_evidence(seal=SealStatus.NO_SEAL):
    return ExpirationEvidence(Reason.PRICE_DEADLINE, seal)


def revised(status=Status.EXCEPTIONAL_CLOSURE, **changes):
    values = dict(source=replace(CALENDAR, revision_id='revision-2'), status=status)
    values.update(changes)
    if status in (Status.CLOSED, Status.EXCEPTIONAL_CLOSURE, Status.UNKNOWN):
        return closed(**values)
    return session(**values)


def invalidation(**changes):
    values = dict(reason=Reason.SESSION_INVALIDATED, seal_status=SealStatus.NO_SEAL,
                  revised_session=revised())
    values.update(changes)
    return ExpirationEvidence(**values)


class OrderPolicyTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                       'socket.create_connection', 'socket.getaddrinfo', 'sqlite3.connect',
                       'magi.virtual.storage.VirtualLedger.__init__'):
            guard = patch(target, side_effect=AssertionError('Policy tests have no I/O'))
            guard.start(); self.addCleanup(guard.stop)

    def error(self, code, call):
        with self.assertRaises(PolicyError) as caught:
            call()
        self.assertIs(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code.value)

    def invalid(self, call):
        with self.assertRaises(VirtualError) as caught:
            call()
        self.assertEqual(str(caught.exception), 'INVALID_INPUT')

    def choose(self, sessions, at=AT, through=DAY, **kwargs):
        return select_session(calendar(*sessions), exchange='XSIM', accepted_at=at,
                              policy=kwargs.get('policy', POLICY), through_date=through)

    def test_explicit_policy_no_operational_defaults(self):
        with self.assertRaises(TypeError): SessionPolicy('v1')
        self.assertEqual(POLICY.freeze_before_open, timedelta(minutes=5))
        self.assertIsNone(replace(POLICY, price_wait_after_close=None).price_wait_after_close)
        self.invalid(lambda: self.choose((session(),), policy=None))

    def test_invalid_cutoff_and_deadline_configurations(self):
        for value in (None, 300, 300.0, True, timedelta(0), timedelta(seconds=-1), timedelta(days=1)):
            self.invalid(lambda: replace(POLICY, freeze_before_open=value))
        for value in (True, 7200, timedelta(0), timedelta(seconds=-1)):
            self.invalid(lambda: replace(POLICY, price_wait_after_close=value))
        for version in ('', None, 'https://fixture.invalid/policy'):
            self.invalid(lambda: replace(POLICY, version=version))

    def test_regular_session_is_eligible(self):
        chosen = self.choose((session(),))
        self.assertEqual(chosen.session, session())
        self.assertEqual(chosen.cutoff_at, CUT)
        self.assertEqual(chosen.deadline_at, DEADLINE)
        self.assertEqual(chosen.accepted_at, AT)

    def test_classification_four_outcomes(self):
        cases = ((session(), AT, Eligibility.ELIGIBLE), (session(), CUT, Eligibility.ELAPSED),
                 (closed(), AT, Eligibility.INELIGIBLE),
                 (closed(coverage=Coverage.UNKNOWN, status=Status.UNKNOWN), AT, Eligibility.UNKNOWN))
        for value, at, expected in cases:
            with self.subTest(expected=expected):
                self.assertIs(classify_session(value, exchange='XSIM', accepted_at=at, policy=POLICY), expected)

    def test_weekend_closures_require_explicit_records(self):
        friday = date(2026, 3, 6)
        at = datetime(2026, 3, 6, 17, tzinfo=NY)
        days = (session(friday), closed(date(2026, 3, 7)), closed(date(2026, 3, 8)), session())
        self.assertEqual(self.choose(days, at, DAY).session.session_date, DAY)

    def test_synthetic_holiday_and_early_close(self):
        tomorrow = DAY + timedelta(days=1)
        early = session(tomorrow, early_close=True,
                        regular_close=datetime(2026, 3, 10, 13, tzinfo=NY))
        choice = self.choose((closed(), early), through=tomorrow)
        self.assertEqual(choice.session, early)
        self.assertEqual(choice.deadline_at, datetime(2026, 3, 10, 15, tzinfo=NY))
        self.assertEqual(choice.cutoff_at, datetime(2026, 3, 10, 9, 25, tzinfo=NY))

    def test_confirmed_exceptional_sessions_are_skipped(self):
        tomorrow = DAY + timedelta(days=1)
        for status in (Status.CLOSED, Status.EXCEPTIONAL_CLOSURE, Status.DELAYED_OPEN,
                       Status.HALTED, Status.SUSPENDED):
            today = closed(status=status) if status in (Status.CLOSED, Status.EXCEPTIONAL_CLOSURE) else session(status=status)
            with self.subTest(status=status):
                self.assertEqual(self.choose((today, session(tomorrow)), through=tomorrow).session.session_date, tomorrow)

    def test_unknown_today_blocks_later_convenient_session(self):
        tomorrow = DAY + timedelta(days=1)
        unknown = closed(coverage=Coverage.UNKNOWN, status=Status.UNKNOWN)
        self.error(Code.CALENDAR_UNAVAILABLE, lambda: self.choose((unknown, session(tomorrow)), through=tomorrow))

    def test_missing_today_blocks_later_session(self):
        tomorrow = DAY + timedelta(days=1)
        self.error(Code.CALENDAR_UNAVAILABLE, lambda: self.choose((session(tomorrow),), through=tomorrow))

    def test_missing_weekend_day_blocks_later_session(self):
        friday = date(2026, 3, 6)
        days = (session(friday), closed(date(2026, 3, 7)), session())
        self.error(Code.CALENDAR_UNAVAILABLE, lambda: self.choose(days, datetime(2026, 3, 6, 17, tzinfo=NY), DAY))

    def test_unknown_after_selected_session_is_not_required(self):
        self.assertEqual(self.choose((session(),), through=DAY+timedelta(days=30)).session, session())

    def test_no_eligible_session_distinct_from_unknown(self):
        self.error(Code.NO_ELIGIBLE_SESSION, lambda: self.choose((closed(),)))
        self.error(Code.CALENDAR_UNAVAILABLE, lambda: self.choose(()))
        self.error(Code.NO_ELIGIBLE_SESSION, lambda: self.choose((session(),), through=DAY-timedelta(days=1)))

    def test_already_elapsed_session_not_selected(self):
        for at in (CUT, OPEN, OPEN+timedelta(seconds=1), OPEN.replace(hour=17)):
            with self.subTest(at=at):
                self.error(Code.NO_ELIGIBLE_SESSION, lambda: self.choose((session(),), at))

    def test_cutoff_boundary_selects_next_session(self):
        tomorrow = DAY + timedelta(days=1)
        days = (session(), session(tomorrow))
        for at in (CUT, CUT+timedelta(microseconds=1), OPEN-timedelta(microseconds=1), OPEN,
                   OPEN+timedelta(microseconds=1), OPEN.replace(hour=17)):
            with self.subTest(at=at):
                self.assertEqual(self.choose(days, at, tomorrow).session.session_date, tomorrow)
        self.assertEqual(self.choose(days, CUT-timedelta(microseconds=1), tomorrow).session.session_date, DAY)

    def test_cutoff_is_configured_not_five_minutes_always(self):
        policy = replace(POLICY, freeze_before_open=timedelta(minutes=10))
        self.error(Code.NO_ELIGIBLE_SESSION, lambda: self.choose((session(),), AT, policy=policy))
        self.assertEqual(self.choose((session(),), AT-timedelta(minutes=10), policy=policy).cutoff_at,
                         OPEN-timedelta(minutes=10))

    def test_shuffled_calendar_input_preserves_first_eligible(self):
        tomorrow, after = DAY+timedelta(days=1), DAY+timedelta(days=2)
        days = (closed(), session(tomorrow), session(after))
        outcomes = [self.choose(p, through=after) for p in permutations(days)]
        self.assertTrue(all(value == outcomes[0] for value in outcomes))
        self.assertEqual(outcomes[0].session.session_date, tomorrow)

    def test_conflicting_calendar_rejected_by_existing_contract(self):
        self.invalid(lambda: calendar(session(), session(early_close=True)))
        self.invalid(lambda: calendar(session(), session(source=replace(CALENDAR, revision_id='other'))))

    def test_exchange_mismatch(self):
        self.error(Code.CALENDAR_MISMATCH, lambda: select_session(calendar(session()), exchange='XNAS',
                   accepted_at=AT, policy=POLICY, through_date=DAY))
        self.error(Code.CALENDAR_MISMATCH, lambda: classify_session(session(), exchange='XNAS', accepted_at=AT, policy=POLICY))
        self.error(Code.CALENDAR_MISMATCH, lambda: prepare_order(intent(exchange='XNAS'), calendar=calendar(session()),
                   policy=POLICY, accepted_at=AT, through_date=DAY))

    def test_only_synthetic_calendar_can_be_called(self):
        class LiveLike:
            def get_session(self, *args): raise AssertionError('Must not be called')
        self.invalid(lambda: select_session(LiveLike(), exchange='XSIM', accepted_at=AT, policy=POLICY, through_date=DAY))

    def test_wrong_types_rejected(self):
        self.invalid(lambda: select_session(calendar(session()), exchange='US', accepted_at=AT, policy=POLICY, through_date=DAY))
        self.invalid(lambda: self.choose((session(),), through=AT))
        self.invalid(lambda: classify_session(None, exchange='XSIM', accepted_at=AT, policy=POLICY))
        self.invalid(lambda: SessionSelection(None, POLICY, AT))

    def test_utc_new_york_and_los_angeles_agree(self):
        expected = self.choose((session(),))
        for zone in (timezone.utc, NY, ZoneInfo('America/Los_Angeles')):
            self.assertEqual(self.choose((session(),), AT.astimezone(zone)), expected)

    def test_exchange_date_not_client_or_utc_date(self):
        at = datetime(2026, 3, 9, 1, tzinfo=timezone.utc)  # Sunday evening in fixture exchange zone.
        self.assertEqual(self.choose((closed(date(2026, 3, 8)), session()), at).session.session_date, DAY)
        self.error(Code.CALENDAR_UNAVAILABLE, lambda: self.choose((session(),), at))

    def test_spring_and_fall_dst_offsets(self):
        for day, expected_hour in ((date(2026, 3, 6), 14), (DAY, 13),
                                   (date(2026, 10, 30), 13), (date(2026, 11, 2), 14)):
            at = datetime(day.year, day.month, day.day, 9, tzinfo=NY)
            with self.subTest(day=day):
                chosen = self.choose((session(day),), at, day)
                self.assertEqual(chosen.cutoff_at.hour, expected_hour)
                self.assertEqual(chosen.cutoff_at.minute, 25)

    def test_naive_and_invalid_timestamps_fail(self):
        for at in (AT.replace(tzinfo=None), AT.isoformat(), DAY, None, True):
            self.invalid(lambda: self.choose((session(),), at))
            self.invalid(lambda: intent(submitted_at=at))

    def test_ambiguous_and_nonexistent_times_fail(self):
        for at in (datetime(2026, 3, 8, 2, 30, tzinfo=NY),
                   datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=0),
                   datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=1),
                   datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo('America/Los_Angeles'))):
            self.invalid(lambda: self.choose((session(),), at))
            self.invalid(lambda: intent(submitted_at=at))

    def test_deadline_overflow_rejected(self):
        self.invalid(lambda: self.choose((session(),), policy=replace(POLICY, price_wait_after_close=timedelta.max)))

    def test_valid_intent_reuses_side_and_decimal_contract(self):
        for side in Side:
            value = intent(side=side, quantity=D('1000000'))
            self.assertIs(value.side, side)
            self.assertEqual(value.quantity, D('1000000'))
            self.assertIs(value.submitted_at.tzinfo, timezone.utc)

    def test_invalid_quantities(self):
        for qty in (0, 1, 1.0, True, '1', None, D(0), D(-1), D('0.5'), D('NaN'), D('sNaN'),
                    D('Infinity'), D('1000001'), D('1.000000000')):
            self.invalid(lambda: intent(quantity=qty))

    def test_quantity_context_independence(self):
        with localcontext() as context:
            context.prec = 1; context.traps[Inexact] = True
            self.assertEqual(intent(quantity=D('999999')).quantity, D('999999'))

    def test_invalid_side_and_identity(self):
        for side in ('BUY', 'SELL', 'HOLD', None, True):
            self.invalid(lambda: intent(side=side))
        for field in ('order_id', 'portfolio_id', 'instrument_id', 'idempotency_key'):
            for value in ('', 'owner-account', None, 1):
                self.invalid(lambda: intent(**{field:value}))

    def test_submission_and_acceptance_distinct(self):
        order = accepted()
        self.assertLess(order.intent.submitted_at, order.selection.accepted_at)
        self.assertEqual(order.changed_at, order.selection.accepted_at)
        self.error(Code.TIMING_VIOLATION, lambda: accepted(accepted_at=AT-timedelta(seconds=3)))

    def test_queued_request_cannot_backdate_acceptance(self):
        tomorrow = DAY+timedelta(days=1)
        order = accepted(calendar=calendar(session(),session(tomorrow)), accepted_at=CUT, through_date=tomorrow)
        self.assertEqual(order.selection.session.session_date, tomorrow)
        self.assertEqual(order.intent.submitted_at, intent().submitted_at)
        self.assertEqual(order.selection.accepted_at, CUT)

    def test_failed_selection_creates_no_order(self):
        self.error(Code.CALENDAR_UNAVAILABLE, lambda: accepted(calendar=calendar()))
        self.error(Code.NO_ELIGIBLE_SESSION, lambda: accepted(calendar=calendar(closed())))
        self.invalid(lambda: prepare_order(None, calendar=calendar(session()),policy=POLICY,accepted_at=AT,through_date=DAY))

    def test_frozen_structures(self):
        order = accepted()
        for value, field in ((order,'state'), (order.intent,'quantity'), (order.selection,'session'), (POLICY,'version')):
            with self.assertRaises(FrozenInstanceError): setattr(value, field, None)

    def test_accepted_freezes_at_cutoff_then_waits_at_open(self):
        order = accepted()
        frozen = transition_order(order, State.FROZEN, at=CUT)
        awaiting = transition_order(frozen, State.AWAITING_PRICE, at=OPEN)
        self.assertIs(order.state, State.ACCEPTED)
        self.assertIs(frozen.state, State.FROZEN)
        self.assertIs(awaiting.state, State.AWAITING_PRICE)
        self.assertEqual((awaiting.intent, awaiting.selection), (order.intent, order.selection))
        self.assertEqual(awaiting.changed_at, OPEN)

    def test_freeze_before_cutoff_rejected(self):
        self.error(Code.TIMING_VIOLATION, lambda: transition_order(accepted(),State.FROZEN,at=CUT-timedelta(microseconds=1)))

    def test_awaiting_before_open_rejected(self):
        frozen=transition_order(accepted(),State.FROZEN,at=CUT)
        self.error(Code.TIMING_VIOLATION,lambda:transition_order(frozen,State.AWAITING_PRICE,at=OPEN-timedelta(microseconds=1)))

    def test_cancellation_before_cutoff(self):
        order=accepted()
        cancelled=transition_order(order,State.CANCELLED,at=CUT-timedelta(microseconds=1))
        self.assertIs(cancelled.state,State.CANCELLED)
        self.assertEqual(cancelled.intent,order.intent)
        self.assertIsNone(cancelled.expiration)

    def test_late_worker_does_not_extend_cancellation(self):
        for at in (CUT,CUT+timedelta(microseconds=1),OPEN,DEADLINE):
            self.error(Code.TIMING_VIOLATION,lambda:transition_order(accepted(),State.CANCELLED,at=at))

    def test_frozen_cannot_cancel_even_if_caller_backdates(self):
        frozen=transition_order(accepted(),State.FROZEN,at=CUT)
        self.error(Code.TIMING_VIOLATION,lambda:transition_order(frozen,State.CANCELLED,at=CUT-timedelta(seconds=1)))
        self.error(Code.TIMING_VIOLATION,lambda:transition_order(frozen,State.CANCELLED,at=CUT))

    def test_invalid_edges_and_duplicates(self):
        order=accepted(); frozen=transition_order(order,State.FROZEN,at=CUT)
        waiting=transition_order(frozen,State.AWAITING_PRICE,at=OPEN)
        for before,state,at in ((order,State.ACCEPTED,AT),(order,State.AWAITING_PRICE,OPEN),
                                (frozen,State.FROZEN,CUT),(waiting,State.AWAITING_PRICE,OPEN),
                                (waiting,State.FROZEN,OPEN)):
            with self.subTest(before=before.state,state=state):
                self.error(Code.INVALID_TRANSITION,lambda:transition_order(before,state,at=at))

    def test_unknown_states_and_unsupported_fields_rejected(self):
        for state in ('FROZEN','REJECTED',None,False):
            self.invalid(lambda:transition_order(accepted(),state,at=CUT))
        with self.assertRaises(TypeError): transition_order(accepted(),State.FROZEN,at=CUT,quantity=D(20))
        with self.assertRaises(TypeError): replace(intent(),execution_price=D(1))
        self.invalid(lambda:transition_order(None,State.FROZEN,at=CUT))
        self.invalid(lambda:validate_transition(None,accepted()))

    def test_transition_clock_cannot_regress(self):
        frozen=transition_order(accepted(),State.FROZEN,at=OPEN+timedelta(seconds=2))
        self.error(Code.TIMING_VIOLATION,lambda:transition_order(frozen,State.AWAITING_PRICE,at=OPEN))
        self.invalid(lambda:transition_order(accepted(),State.FROZEN,at=CUT.replace(tzinfo=None)))

    def test_same_instant_different_edges_are_ordered_explicitly(self):
        frozen=transition_order(accepted(),State.FROZEN,at=OPEN)
        self.assertIs(transition_order(frozen,State.AWAITING_PRICE,at=OPEN).state,State.AWAITING_PRICE)

    def test_pure_edge_validation_rejects_every_intent_mutation(self):
        order=accepted(); after=transition_order(order,State.FROZEN,at=CUT)
        updates=dict(order_id=UUIDS[1],portfolio_id=UUIDS[2],instrument_id=UUIDS[3],side=Side.SELL,
                     quantity=D(20),submitted_at=AT-timedelta(seconds=3),idempotency_key=UUIDS[0])
        for field,value in updates.items():
            changed=replace(after,intent=replace(order.intent,**{field:value}))
            self.error(Code.INTENT_CHANGED,lambda:validate_transition(order,changed))

    def test_policy_acceptance_session_and_provenance_cannot_mutate(self):
        order=accepted(); after=transition_order(order,State.FROZEN,at=OPEN)
        alternatives=(replace(order.selection,policy=replace(POLICY,version='v2')),
                      replace(order.selection,policy=replace(POLICY,freeze_before_open=timedelta(minutes=4))),
                      replace(order.selection,accepted_at=AT-timedelta(seconds=1)),
                      replace(order.selection,session=session(early_close=True)),
                      replace(order.selection,session=session(source=replace(CALENDAR,revision_id='v2'))))
        for selection in alternatives:
            self.error(Code.INTENT_CHANGED,lambda:validate_transition(order,replace(after,selection=selection)))

    def test_deadline_expiry_at_and_after_boundary(self):
        for at in (DEADLINE,DEADLINE+timedelta(microseconds=1)):
            expired=transition_order(accepted(),State.EXPIRED,at=at,expiration=deadline_evidence())
            self.assertIs(expired.state,State.EXPIRED)
            self.assertIs(expired.expiration.reason,Reason.PRICE_DEADLINE)
        self.error(Code.TIMING_VIOLATION,lambda:transition_order(accepted(),State.EXPIRED,
                   at=DEADLINE-timedelta(microseconds=1),expiration=deadline_evidence()))

    def test_missing_deadline_is_not_two_hours_by_default(self):
        order=accepted(policy=replace(POLICY,price_wait_after_close=None))
        self.error(Code.DEADLINE_REQUIRED,lambda:transition_order(order,State.EXPIRED,at=DEADLINE,expiration=deadline_evidence()))

    def test_early_close_changes_expiry_deadline(self):
        early=session(regular_close=OPEN.replace(hour=13,minute=0),early_close=True)
        order=accepted(calendar=calendar(early))
        self.assertEqual(order.selection.deadline_at,OPEN.replace(hour=15,minute=0))
        self.assertIs(transition_order(order,State.EXPIRED,at=order.selection.deadline_at,
                                      expiration=deadline_evidence()).state,State.EXPIRED)

    def test_sealed_and_unknown_evidence_cannot_expire(self):
        for seal in (SealStatus.SEALED,SealStatus.UNKNOWN):
            self.error(Code.EVIDENCE_UNRESOLVED,lambda:transition_order(accepted(),State.EXPIRED,at=DEADLINE,
                       expiration=deadline_evidence(seal)))
            self.error(Code.EVIDENCE_UNRESOLVED,lambda:transition_order(accepted(),State.EXPIRED,at=CUT,
                       expiration=invalidation(seal_status=seal)))

    def test_expiry_from_each_nonterminal_state(self):
        order=accepted(); frozen=transition_order(order,State.FROZEN,at=CUT)
        waiting=transition_order(frozen,State.AWAITING_PRICE,at=OPEN)
        for before in (order,frozen,waiting):
            self.assertIs(transition_order(before,State.EXPIRED,at=DEADLINE,
                                           expiration=deadline_evidence()).state,State.EXPIRED)

    def test_preopen_confirmed_invalidation_expires_without_rollover(self):
        for status in (Status.CLOSED,Status.EXCEPTIONAL_CLOSURE,Status.DELAYED_OPEN,Status.HALTED,Status.SUSPENDED):
            order=accepted()
            outcome=transition_order(order,State.EXPIRED,at=CUT,
                                     expiration=invalidation(revised_session=revised(status)))
            self.assertEqual(outcome.selection,order.selection)
            self.assertEqual(outcome.intent,order.intent)
            self.assertNotEqual(outcome.expiration.revised_session.source,order.selection.session.source)

    def test_postopen_status_without_effective_history_cannot_invalidate(self):
        self.error(Code.EVIDENCE_UNRESOLVED,lambda:transition_order(accepted(),State.EXPIRED,
                   at=OPEN+timedelta(microseconds=1),expiration=invalidation()))

    def test_unknown_revised_calendar_not_confirmation(self):
        unknown=revised(Status.UNKNOWN,coverage=Coverage.UNKNOWN)
        self.error(Code.CALENDAR_UNAVAILABLE,lambda:transition_order(accepted(),State.EXPIRED,at=CUT,
                   expiration=invalidation(revised_session=unknown)))

    def test_regular_revision_not_invalidation(self):
        self.error(Code.EVIDENCE_UNRESOLVED,lambda:transition_order(accepted(),State.EXPIRED,at=CUT,
                   expiration=invalidation(revised_session=revised(Status.REGULAR))))

    def test_invalidation_revision_identity_must_match(self):
        cases=(revised(exchange='XNAS'),revised(session_date=DAY+timedelta(days=1)),
               revised(trading_timezone='America/Los_Angeles'),revised(session_id='other'),
               revised(source=CALENDAR),revised(source=replace(CALENDAR,revision_id='v2',provider='other')),
               revised(source=replace(CALENDAR,revision_id='v2',dataset='other')))
        for change in cases:
            self.error(Code.CALENDAR_MISMATCH,lambda:transition_order(accepted(),State.EXPIRED,at=CUT,
                       expiration=invalidation(revised_session=change)))

    def test_invalid_expiration_inputs(self):
        for values in (dict(reason='PRICE_DEADLINE'),dict(seal_status=False),dict(revised_session=session())):
            self.invalid(lambda:replace(deadline_evidence(),**values))
        self.invalid(lambda:ExpirationEvidence(Reason.SESSION_INVALIDATED,SealStatus.NO_SEAL))
        self.invalid(lambda:transition_order(accepted(),State.EXPIRED,at=DEADLINE))
        self.invalid(lambda:transition_order(accepted(),State.FROZEN,at=CUT,expiration=deadline_evidence()))

    def test_terminal_states_cannot_reopen_or_finalize_twice(self):
        order=accepted()
        cancelled=transition_order(order,State.CANCELLED,at=AT)
        expired=transition_order(order,State.EXPIRED,at=DEADLINE,expiration=deadline_evidence())
        for before in (cancelled,expired):
            for after in (replace(before),OrderSnapshot(order.intent,order.selection,State.FROZEN,before.changed_at)
                          if before.changed_at>=CUT else order):
                with self.assertRaises(PolicyError): validate_transition(before,after)
        self.error(Code.INVALID_TRANSITION,lambda:transition_order(expired,State.EXPIRED,at=DEADLINE,expiration=deadline_evidence()))
        self.error(Code.INVALID_TRANSITION,lambda:transition_order(cancelled,State.EXPIRED,at=DEADLINE,expiration=deadline_evidence()))

    def test_filled_construction_and_transitions_always_rejected(self):
        order=accepted()
        self.error(Code.SETTLEMENT_REQUIRED,lambda:OrderSnapshot(order.intent,order.selection,State.FILLED,OPEN))
        for before in (order,transition_order(order,State.FROZEN,at=CUT),
                       transition_order(order,State.CANCELLED,at=AT),
                       transition_order(order,State.EXPIRED,at=DEADLINE,expiration=deadline_evidence())):
            self.error(Code.SETTLEMENT_REQUIRED,lambda:transition_order(before,State.FILLED,at=DEADLINE))

    def test_prices_never_authorize_or_expire_orders(self):
        price=observation()
        self.assertFalse(any(f.name in ('price','fill','owner','authorized','trusted') for f in fields(OrderSnapshot)))
        self.invalid(lambda:transition_order(accepted(),State.EXPIRED,at=DEADLINE,expiration=price))
        with self.assertRaises(TypeError): transition_order(accepted(),State.FILLED,at=OPEN,price=price)

    def test_no_hidden_idempotency_registry(self):
        self.assertEqual(accepted(),accepted())
        changed=prepare_order(intent(quantity=D(20)),calendar=calendar(session()),policy=POLICY,accepted_at=AT,through_date=DAY)
        self.assertEqual(changed.intent.idempotency_key,accepted().intent.idempotency_key)
        self.assertNotEqual(changed.intent,accepted().intent)
        # Conflicting submissions need a future scoped durable store, not an in-memory promise.

    def test_safe_errors_do_not_echo_private_input(self):
        with self.assertRaises(VirtualError) as error:
            intent(portfolio_id='private fixture reference')
        self.assertNotIn('private fixture',str(error.exception))

    def test_new_modules_have_only_pure_imports(self):
        for filename in ('session_policy.py','order_lifecycle.py'):
            tree=ast.parse((ROOT/'magi'/'virtual'/filename).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):
                    self.fail('No external module imports needed')
                elif isinstance(node,ast.ImportFrom):
                    self.assertTrue((node.level==0 and node.module in {'dataclasses','datetime','decimal','enum'})
                                    or (node.level==1 and node.module in {'models','execution_contracts','session_policy'}))

    def test_fresh_process_cannot_access_owner_broker_network_or_db(self):
        script='''
import sys
sys.path.insert(0,sys.argv[1])
def guard(event,args):
    if event=='import' and args[0].startswith(('magi.broker','magi.toss','magi.storage','magi.portfolio',
                                              'magi.market','magi.research','httpx','dotenv')):
        raise AssertionError('Forbidden dependency')
    if event in ('sqlite3.connect','socket.connect','socket.getaddrinfo','socket.sendto','socket.bind'):
        raise AssertionError('Forbidden I/O')
    if event=='open' and isinstance(args[0],str) and args[0].endswith('.env'):
        raise AssertionError('Credential access')
sys.addaudithook(guard)
from magi.virtual.order_lifecycle import *
from magi.virtual.session_policy import *
from magi.virtual.execution_contracts import *
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
uid='11111111-1111-4111-8111-111111111111'
source=SourceReference('fixture','fixture','fixture',uid,'v1')
s=CalendarSession('XSIM',date(2026,1,1),'UTC',source,CalendarCoverage.KNOWN,SessionStatus.REGULAR,
    'fixture-day',datetime(2026,1,1,9,tzinfo=timezone.utc),datetime(2026,1,1,16,tzinfo=timezone.utc),False)
policy=SessionPolicy('fixture-v1',timedelta(minutes=5),None)
at=datetime(2026,1,1,8,tzinfo=timezone.utc)
i=OrderIntent(uid,uid,uid,'XSIM',Side.BUY,Decimal(1),at,uid)
o=prepare_order(i,calendar=SyntheticCalendar('XSIM','UTC',source,(s,)),policy=policy,accepted_at=at,through_date=s.session_date)
assert transition_order(o,OrderState.CANCELLED,at=at).state is OrderState.CANCELLED
assert not any(n.startswith(('magi.toss','magi.broker','magi.storage','magi.portfolio')) for n in sys.modules)
print('isolated')
'''
        result=subprocess.run([sys.executable,'-I','-B','-c',script,str(ROOT)],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'isolated')


if __name__=='__main__':
    unittest.main()

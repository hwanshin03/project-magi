"""Invented XSIM schedules/prices only, never historical exchange assertions."""
import ast
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D, localcontext, Inexact
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from magi.virtual.execution_contracts import (
    AdjustmentStatus as Adjustment, CalendarCoverage as Coverage, CalendarSession,
    ExchangeCalendar, FinalityStatus as Finality, OfficialStatus as Official,
    PriceClassification as Kind, PriceObservation, SessionStatus as Status,
    SourceReference, SyntheticCalendar, TimestampPrecision as Precision,
    validate_observation_time, validate_price_session,
)
from magi.virtual.models import VirtualError, ErrorCode

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo('America/New_York')
DAY = date(2026, 3, 9)  # Synthetic schedule on a real timezone-transition date.
EVENT = datetime(2026, 3, 9, 9, 30, tzinfo=NY)
SOURCE = SourceReference('fixture-provider', 'fixture-prices', 'opening-1',
                         '11111111-1111-4111-8111-111111111111', 'revision-1')
CALENDAR = replace(SOURCE, dataset='fixture-calendar', record_id='snapshot-1')
INSTRUMENT = '22222222-2222-4222-8222-222222222222'


def observation(**changes):
    values = dict(source=SOURCE, provider_instrument_id='SYNTH.A', exchange='XSIM', currency='USD',
                  session_id='fixture-2026-03-09', price=D('123.45678901'),
                  classification=Kind.OFFICIAL_OPEN, event_at=EVENT, precision=Precision.SECOND,
                  observed_at=EVENT + timedelta(seconds=1), retrieved_at=EVENT + timedelta(seconds=2),
                  adjustment=Adjustment.UNADJUSTED, official_status=Official.OFFICIAL,
                  finality=Finality.FINAL, instrument_id=INSTRUMENT)
    values.update(changes)
    return PriceObservation(**values)


def session(day=DAY, **changes):
    values = dict(exchange='XSIM', session_date=day, trading_timezone=NY.key, source=CALENDAR,
                  coverage=Coverage.KNOWN, status=Status.REGULAR, session_id=f'fixture-{day}',
                  regular_open=datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY),
                  regular_close=datetime(day.year, day.month, day.day, 16, tzinfo=NY), early_close=False)
    values.update(changes)
    return CalendarSession(**values)


def closed(day=DAY, **changes):
    values = dict(regular_open=None, regular_close=None, early_close=None, status=Status.CLOSED)
    values.update(changes)
    return session(day, **values)


class ContractsTests(unittest.TestCase):
    def setUp(self):
        # New tests never construct adapters, write a ledger or call real transports.
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.socket.sendto',
                       'socket.create_connection', 'socket.getaddrinfo', 'sqlite3.connect',
                       'magi.virtual.storage.VirtualLedger.__init__'):
            guard = patch(target, side_effect=AssertionError('No I/O in contract tests'))
            guard.start(); self.addCleanup(guard.stop)

    def invalid(self, call):
        with self.assertRaises(VirtualError) as error:
            call()
        self.assertEqual(error.exception.code, ErrorCode.INVALID_INPUT)
        self.assertEqual(str(error.exception), 'INVALID_INPUT')

    def test_exact_decimal_preserves_precision(self):
        p = observation()
        self.assertEqual(p.price.as_tuple(), D('123.45678901').as_tuple())
        self.assertEqual(observation(price=D('0.00000001')).price, D('0.00000001'))
        self.assertEqual(observation(price=D('1000000')).price, D('1000000'))

    def test_invalid_numeric_prices(self):
        for value in (D(0), D('-0'), D(-1), D('NaN'), D('sNaN'), D('Infinity'), D('-Infinity'),
                      D('0.000000001'), D('1000001'), D('1E+9'), 10.0, 10, '10', True, None):
            with self.subTest(value=str(value)):
                self.invalid(lambda: observation(price=value))

    def test_decimal_context_independent(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            self.assertEqual(observation().price, D('123.45678901'))

    def test_missing_provider_and_dataset(self):
        for name in ('provider', 'dataset', 'record_id'):
            for value in ('', None, False):
                with self.subTest(field=name, value=value):
                    self.invalid(lambda: replace(SOURCE, **{name: value}))

    def test_provider_references_are_not_urls_or_payloads(self):
        for value in ('https://fixture.invalid/price', '../record', 'a/b', 'a?key=x', 'a@b',
                      'a\nb', 'Bearer fixture-only', 'x' * 97, 'a..b', '한글', []):
            with self.subTest(value=value):
                self.invalid(lambda: replace(SOURCE, record_id=value))

    def test_provenance_uuid_required(self):
        for value in ('', 'source-id', None, 1):
            self.invalid(lambda: replace(SOURCE, provenance_id=value))

    def test_revision_can_be_unknown(self):
        self.assertIsNone(replace(SOURCE, revision_id=None).revision_id)

    def test_explicit_revision_lineage(self):
        revised = replace(SOURCE, revision_id='revision-2', supersedes_revision_id='revision-1')
        self.assertEqual(revised.supersedes_revision_id, 'revision-1')
        self.invalid(lambda: replace(revised, revision_id='revision-1'))
        self.invalid(lambda: replace(revised, revision_id=None))
        self.assertIsNone(SOURCE.supersedes_revision_id)

    def test_no_duplicate_provenance_fields(self):
        # There is one provenance structure, so competing top-level claims cannot coexist.
        with self.assertRaises(TypeError):
            PriceObservation(**{f.name: getattr(observation(), f.name) for f in fields(PriceObservation)},
                             provider='other-provider')
        self.invalid(lambda: observation(source={'provider': 'other-provider'}))

    def test_optional_identity_and_action_unknown_remain_none(self):
        p = observation(instrument_id=None)
        self.assertIsNone(p.instrument_id)
        self.assertIsNone(p.corporate_action_ref)
        self.assertEqual(observation(corporate_action_ref='action-snapshot-1').corporate_action_ref,
                         'action-snapshot-1')
        for value in ('', False, 0, 'https://fixture.invalid/action'):
            self.invalid(lambda: observation(corporate_action_ref=value))

    def test_invalid_instrument_identifiers(self):
        for value in ('', 'SYNTH', 1, False):
            self.invalid(lambda: observation(instrument_id=value))
        for value in ('', '../SYNTH', 'SYNTH/USD', None):
            self.invalid(lambda: observation(provider_instrument_id=value))

    def test_exchange_shape_is_explicit(self):
        for value in ('US', 'xsim', 'X-SIM', None, 1234):
            self.invalid(lambda: observation(exchange=value))
        self.assertEqual(observation(exchange='XNAS').exchange, 'XNAS')  # Shape, not registry proof.

    def test_currency_mismatch_fails_without_fx(self):
        for value in ('KRW', 'EUR', 'usd', 'ZZZ', None, True):
            self.invalid(lambda: observation(currency=value))

    def test_invalid_enums_no_string_coercion(self):
        for name in ('classification', 'precision', 'adjustment', 'official_status', 'finality'):
            for value in ('UNKNOWN', 'invented', None, False):
                with self.subTest(field=name, value=value):
                    self.invalid(lambda: observation(**{name: value}))

    def test_unknown_claim_states_are_representable(self):
        p = observation(classification=Kind.INTRADAY_QUOTE, official_status=Official.UNKNOWN,
                        finality=Finality.UNKNOWN, adjustment=Adjustment.UNKNOWN)
        self.assertIs(p.finality, Finality.UNKNOWN)

    def test_official_and_indicative_distinct(self):
        p = observation(classification=Kind.INDICATIVE_OPEN, official_status=Official.NONOFFICIAL,
                        finality=Finality.PRELIMINARY)
        self.assertNotEqual(p.classification, observation().classification)
        self.invalid(lambda: replace(p, official_status=Official.OFFICIAL))
        self.invalid(lambda: observation(official_status=Official.UNKNOWN))

    def test_price_categories_and_adjustment_remain_distinct(self):
        for category in Kind:
            values = dict(classification=category)
            if category is Kind.INDICATIVE_OPEN:
                values['official_status'] = Official.NONOFFICIAL
            if category is Kind.HISTORICAL_ADJUSTED:
                values['adjustment'] = Adjustment.ADJUSTED
            with self.subTest(category=category):
                self.assertIs(observation(**values).classification, category)
        self.invalid(lambda: observation(classification=Kind.HISTORICAL_ADJUSTED))
        # An adjusted source opening claim may be represented, never promoted to an unadjusted fill.
        self.assertIs(observation(adjustment=Adjustment.ADJUSTED).adjustment, Adjustment.ADJUSTED)

    def test_timezone_aware_instants_normalized(self):
        p = observation()
        self.assertEqual(p.event_at, datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc))
        self.assertIs(p.event_at.tzinfo, timezone.utc)
        self.assertNotEqual(p.event_at, p.retrieved_at)

    def test_naive_and_invalid_timestamp_types(self):
        for name in ('event_at', 'observed_at', 'retrieved_at'):
            for value in (EVENT.replace(tzinfo=None), DAY, EVENT.isoformat(), False, 1):
                with self.subTest(field=name, value=repr(value)):
                    self.invalid(lambda: observation(**{name: value}))
        self.invalid(lambda: observation(retrieved_at=None))

    def test_missing_provider_observation_time_not_invented(self):
        self.assertIsNone(observation(observed_at=None).observed_at)

    def test_ordering_is_event_observation_retrieval(self):
        self.invalid(lambda: observation(observed_at=EVENT - timedelta(seconds=1)))
        self.invalid(lambda: observation(observed_at=EVENT + timedelta(seconds=3)))
        self.invalid(lambda: observation(event_at=EVENT + timedelta(seconds=3)))
        self.assertEqual(observation(observed_at=EVENT, retrieved_at=EVENT).event_at,
                         EVENT.astimezone(timezone.utc))

    def test_event_precision_not_silently_truncated(self):
        self.invalid(lambda: observation(event_at=EVENT.replace(microsecond=1)))
        self.invalid(lambda: observation(event_at=EVENT.replace(microsecond=1001), precision=Precision.MILLISECOND))
        self.assertEqual(observation(event_at=EVENT.replace(microsecond=1000), precision=Precision.MILLISECOND)
                         .event_at.microsecond, 1000)
        self.assertEqual(observation(event_at=EVENT.replace(microsecond=1), precision=Precision.MICROSECOND)
                         .event_at.microsecond, 1)

    def test_coarse_time_cannot_masquerade_as_an_instant(self):
        for precision in (Precision.SESSION_DATE, Precision.UNKNOWN):
            self.invalid(lambda: observation(precision=precision))
            self.assertIsNone(observation(precision=precision, event_at=None).event_at)
        self.invalid(lambda: observation(event_at=None))

    def test_validation_as_of_future_and_backdated_observations(self):
        p = observation()
        validate_observation_time(p, validated_at=p.retrieved_at, as_of=p.retrieved_at)
        self.invalid(lambda: validate_observation_time(p, validated_at=EVENT, as_of=p.retrieved_at))
        self.invalid(lambda: validate_observation_time(p, validated_at=p.retrieved_at,
                                                       as_of=p.retrieved_at-timedelta(microseconds=1)))
        self.invalid(lambda: validate_observation_time(p, validated_at=p.retrieved_at,
                                                       as_of=EVENT.replace(tzinfo=None)))

    def test_late_retrieval_cannot_prove_historical_availability(self):
        p = observation(retrieved_at=EVENT + timedelta(days=2))
        self.invalid(lambda: validate_observation_time(p, validated_at=p.retrieved_at,
                                                       as_of=EVENT + timedelta(days=1)))
        self.assertIsNone(validate_observation_time(p, validated_at=p.retrieved_at, as_of=p.retrieved_at))

    def test_immutable_contract_values(self):
        for item, field in ((SOURCE, 'provider'), (observation(), 'price'), (session(), 'regular_open')):
            with self.assertRaises(FrozenInstanceError):
                setattr(item, field, None)

    def test_no_authorization_or_fill_contract(self):
        p = observation()
        self.assertIsNone(validate_price_session(p, session()))
        for name in ('is_trusted', 'authorized', 'create_fill', 'execute', 'settle', 'eligible'):
            self.assertFalse(hasattr(p, name))
        with self.assertRaises(TypeError):
            replace(p, is_trusted=True)
        self.assertNotIn('fill_id', {f.name for f in fields(p)})

    def test_existing_quotes_and_fills_cannot_substitute(self):
        from magi.market.models import Quote
        quote = Quote('SYNTH', 'US', 'USD', D(10), EVENT, 'fixture', EVENT)
        self.invalid(lambda: validate_observation_time(quote, validated_at=EVENT, as_of=EVENT))
        self.invalid(lambda: validate_price_session(quote, session()))
        self.invalid(lambda: validate_price_session(observation(), object()))

    def test_ordinary_synthetic_session(self):
        s = session()
        self.assertEqual(s.regular_close - s.regular_open, timedelta(hours=6, minutes=30))
        self.assertIs(s.early_close, False)
        self.assertEqual(s.session_date, DAY)

    def test_weekend_and_holiday_are_explicit_fixtures(self):
        weekend, holiday = closed(date(2026, 3, 7)), closed(date(2026, 3, 10))
        calendar = SyntheticCalendar('XSIM', NY.key, CALENDAR, (weekend, holiday))
        self.assertIs(calendar.get_session('XSIM', weekend.session_date).status, Status.CLOSED)
        self.assertIs(calendar.get_session('XSIM', holiday.session_date).status, Status.CLOSED)
        self.assertIsNone(holiday.early_close)
        self.assertIsNone(holiday.regular_open)

    def test_no_weekday_inference(self):
        calendar = SyntheticCalendar('XSIM', NY.key, CALENDAR, ())
        for day in (date(2026, 3, 7), DAY):
            s = calendar.get_session('XSIM', day)
            self.assertIs(s.coverage, Coverage.UNKNOWN)
            self.assertIs(s.status, Status.UNKNOWN)
            self.assertIsNone(s.session_id)
            self.assertIsNone(s.regular_open)

    def test_explicit_unknown_coverage(self):
        s = closed(coverage=Coverage.UNKNOWN, status=Status.UNKNOWN)
        self.invalid(lambda: replace(s, status=Status.REGULAR))
        self.invalid(lambda: replace(s, coverage=Coverage.KNOWN))
        self.invalid(lambda: replace(s, regular_open=EVENT))

    def test_early_close_not_a_different_open(self):
        s = session(regular_close=EVENT.replace(hour=13, minute=0), early_close=True)
        self.assertEqual(s.regular_open, session().regular_open)
        self.assertEqual(s.regular_close, datetime(2026, 3, 9, 17, tzinfo=timezone.utc))

    def test_dst_changes_offset_not_synthetic_local_hours(self):
        before, after = session(date(2026, 3, 6)), session()
        self.assertEqual((before.regular_open.hour, after.regular_open.hour), (14, 13))
        before_fall, after_fall = session(date(2026, 10, 30)), session(date(2026, 11, 2))
        self.assertEqual((before_fall.regular_open.hour, after_fall.regular_open.hour), (13, 14))

    def test_exceptional_closure_has_no_invented_hours(self):
        s = closed(status=Status.EXCEPTIONAL_CLOSURE)
        self.assertIsNone(s.regular_close)
        self.invalid(lambda: replace(s, regular_open=EVENT))
        self.invalid(lambda: validate_price_session(observation(), s))

    def test_delayed_open_preserves_schedule(self):
        s = session(status=Status.DELAYED_OPEN, delayed_open_at=EVENT + timedelta(minutes=30))
        self.assertEqual(s.regular_open, session().regular_open)
        self.assertEqual(s.delayed_open_at, s.regular_open + timedelta(minutes=30))
        self.assertIsNone(session(status=Status.DELAYED_OPEN).delayed_open_at)
        for value in (EVENT, EVENT - timedelta(seconds=1), EVENT.replace(hour=16, minute=0)):
            self.invalid(lambda: session(status=Status.DELAYED_OPEN, delayed_open_at=value))
        self.invalid(lambda: session(delayed_open_at=EVENT + timedelta(minutes=30)))

    def test_halt_and_suspension_are_not_closure_or_tradability(self):
        for status in (Status.HALTED, Status.SUSPENDED):
            s = session(status=status)
            self.assertEqual(s.regular_open, session().regular_open)
            self.assertNotEqual(s.status, Status.REGULAR)
            self.assertFalse(hasattr(s, 'is_tradable'))

    def test_missing_session_is_unknown_not_closed(self):
        calendar: ExchangeCalendar = SyntheticCalendar('XSIM', NY.key, CALENDAR, (session(),))
        s = calendar.get_session('XSIM', DAY + timedelta(days=1))
        self.assertIs(s.status, Status.UNKNOWN)
        self.invalid(lambda: validate_price_session(observation(), s))

    def test_conflicting_duplicate_days_and_ids_rejected(self):
        for entries in ((session(), session()), (session(), session(early_close=True)),
                        (session(), session(DAY + timedelta(days=1), session_id=session().session_id))):
            self.invalid(lambda: SyntheticCalendar('XSIM', NY.key, CALENDAR, entries))

    def test_snapshot_provenance_cannot_mix_versions_or_sources(self):
        for source in (replace(CALENDAR, revision_id='revision-2'), replace(CALENDAR, provider='other'),
                       replace(CALENDAR, provenance_id=INSTRUMENT)):
            self.invalid(lambda: SyntheticCalendar('XSIM', NY.key, CALENDAR, (session(source=source),)))
        self.invalid(lambda: session(source=replace(CALENDAR, revision_id=None)))

    def test_wrong_exchange_rejected_in_snapshot_and_lookup(self):
        self.invalid(lambda: SyntheticCalendar('XNAS', NY.key, CALENDAR, (session(),)))
        calendar = SyntheticCalendar('XSIM', NY.key, CALENDAR, (session(),))
        self.invalid(lambda: calendar.get_session('XNAS', DAY))
        self.invalid(lambda: validate_price_session(observation(exchange='XNAS'), session()))

    def test_invalid_timezone_names(self):
        for zone in ('No/Such_Zone', '', None, '../UTC', 1):
            self.invalid(lambda: session(trading_timezone=zone))

    def test_incorrect_timezone_not_silently_reinterpreted(self):
        self.invalid(lambda: session(trading_timezone='America/Los_Angeles'))
        self.invalid(lambda: SyntheticCalendar('XSIM', 'America/Los_Angeles', CALENDAR, (session(),)))
        self.invalid(lambda: session(regular_open=EVENT.replace(tzinfo=timezone(timedelta(hours=-5)))))

    def test_utc_session_inputs_must_match_exchange_local_date(self):
        self.assertEqual(session(regular_open=EVENT.astimezone(timezone.utc)), session())
        self.invalid(lambda: session(regular_open=datetime(2026, 3, 9, 1, tzinfo=timezone.utc)))

    def test_nonexistent_local_time_rejected(self):
        nonexistent = datetime(2026, 3, 8, 2, 30, tzinfo=NY)
        self.invalid(lambda: session(date(2026, 3, 8), regular_open=nonexistent))
        self.invalid(lambda: observation(event_at=nonexistent))

    def test_ambiguous_local_time_rejected_even_with_fold(self):
        for fold in (0, 1):
            ambiguous = datetime(2026, 11, 1, 1, 30, tzinfo=NY, fold=fold)
            self.invalid(lambda: session(date(2026, 11, 1), regular_open=ambiguous))
            self.invalid(lambda: session(date(2026, 11, 1), regular_open=ambiguous.astimezone(timezone.utc)))
            self.invalid(lambda: observation(event_at=ambiguous, retrieved_at=ambiguous))

    def test_invalid_session_types_and_ordering(self):
        for values in ({'session_date': EVENT}, {'session_id': ''}, {'source': None},
                       {'coverage': 'KNOWN'}, {'status': 'REGULAR'}, {'early_close': 0},
                       {'early_close': None}, {'regular_open': None},
                       {'regular_open': EVENT.replace(tzinfo=None)}, {'regular_close': EVENT}):
            self.invalid(lambda: session(**values))

    def test_closed_status_does_not_report_false_early_close(self):
        self.invalid(lambda: closed(early_close=False))
        self.invalid(lambda: closed(delayed_open_at=EVENT))

    def test_synthetic_snapshot_is_immutable(self):
        calendar = SyntheticCalendar('XSIM', NY.key, CALENDAR, (session(),))
        with self.assertRaises(FrozenInstanceError):
            calendar.sessions = ()
        self.invalid(lambda: replace(calendar, sessions=[session()]))
        self.invalid(lambda: replace(calendar, sessions=(None,)))
        self.invalid(lambda: calendar.get_session('XSIM', EVENT))

    def test_matching_price_session_checks_not_execution_policy(self):
        p = observation()
        self.assertIsNone(validate_price_session(p, session()))
        # This check is identity/containment only; no official-opening policy is installed.
        self.assertIsNone(validate_price_session(replace(p, classification=Kind.VALUATION_ESTIMATE), session()))
        self.invalid(lambda: validate_price_session(replace(p, session_id='other-session'), session()))
        self.invalid(lambda: validate_price_session(replace(p, event_at=EVENT-timedelta(seconds=1)), session()))
        self.invalid(lambda: validate_price_session(observation(event_at=None, precision=Precision.SESSION_DATE), session()))
        later = EVENT + timedelta(days=1)
        self.invalid(lambda: validate_price_session(observation(event_at=later, observed_at=later,
                                                                 retrieved_at=later), session()))

    def test_fixed_errors_do_not_echo_inputs(self):
        for text in ('private fixture text', 'https://fixture.invalid/?credential=synthetic'):
            with self.assertRaises(VirtualError) as caught:
                replace(SOURCE, record_id=text)
            self.assertEqual(str(caught.exception), 'INVALID_INPUT')

    def test_new_module_has_only_pure_imports(self):
        tree = ast.parse((ROOT / 'magi/virtual/execution_contracts.py').read_text())
        allowed = {'dataclasses', 'datetime', 'decimal', 'enum', 're', 'typing', 'zoneinfo'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name in allowed for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertTrue(node.module in allowed or (node.level == 1 and node.module == 'models'))

    def test_isolated_import_validation_without_owner_broker_db_or_network(self):
        script = '''
import sys
sys.path.insert(0,sys.argv[1])
def guard(event,args):
    if event == 'import' and args[0].startswith(('magi.broker','magi.toss','magi.storage',
            'magi.portfolio','httpx','dotenv','magi.market')):
        raise AssertionError('Forbidden contract dependency')
    if event in ('sqlite3.connect','socket.connect','socket.getaddrinfo','socket.sendto','socket.bind'):
        raise AssertionError('Forbidden I/O')
    if event == 'open' and isinstance(args[0],str) and args[0].endswith('.env'):
        raise AssertionError('Credential access')
sys.addaudithook(guard)
from magi.virtual.execution_contracts import *
from datetime import date
source=SourceReference('fixture','fixture','fixture','11111111-1111-4111-8111-111111111111','v1')
calendar=SyntheticCalendar('XSIM','America/New_York',source,())
assert calendar.get_session('XSIM',date(2026,3,9)).coverage is CalendarCoverage.UNKNOWN
assert not any(n.startswith(('magi.toss','magi.broker','magi.storage','magi.portfolio')) for n in sys.modules)
print('isolated')
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(ROOT)],
                                env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'isolated')


if __name__ == '__main__':
    unittest.main()

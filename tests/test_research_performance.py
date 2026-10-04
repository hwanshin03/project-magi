"""Security, artifact equivalence and operation-local validation regression."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from test_balancing_foundation import request, pack, news, regulatory
from magi.storage import check_sensitive, sensitive_operation, StorageError
from magi.research.validation import operation
from magi.research.balancing.inputs import build_universe, resolve, temporal_observations, validate_container
from magi.research.balancing.models import fingerprint
from magi.research.balancing.grouping import GroupedEvidence
from magi.research.balancing.assessment import AssessmentSet
from magi.research.balancing.selection import EvidenceSelection
from magi.research.serialization import dumps


class ResearchPerformanceTests(unittest.TestCase):
    def test_preoptimization_artifact_goldens(self):
        # Captured from unmodified 62f71f9. Hash includes the COMPLETE serialized
        # graph, each resolved object's fingerprint and every temporal decision.
        expected=('5c7f368e68ee2705cac54a618e0b85e29a02876da0ebe6a98e0cf40d6ed7ff44',
                  'b2a15a75b03c80b52a07436dcd6e1b7ca15dd29c43e6d3930257bd857d310096',
                  'c460171c2e240c2963873df665538b2e797fc05f737fafe11acb41c9d479f380',
                  '5b414f63aec2de630a07d51e7faafcc09a9135f1957440d664f1b1c5aa810405')
        from test_balancing_assessment import target
        from test_balancing_selection import contested
        cases=((request(),()),(request(),(pack(),)),
               (request(),(pack(value=1),pack(value=2),news(),regulatory())),(target(),(contested(),)))
        for (req,inputs),digest in zip(cases,expected):
            u=build_universe(req,inputs);s=EvidenceSelection(AssessmentSet(GroupedEvidence(u)))
            row={'serialized':dumps(s),'fingerprint':fingerprint(s),
                 'resolved':[fingerprint(resolve(u,r)) for r in u.references],
                 'temporal':[[dumps(x) for x in temporal_observations(u,r)] for r in u.references]}
            self.assertEqual(hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest(),digest)

    def test_secrets_equivalent_nested_values_and_patterns(self):
        samples=('ordinary synthetic text',{'key':['fixture-known-credential']},
                 'Bearer synthetic-authorization','sk-abcdefghijklmnop',
                 'AIzaabcdefghijklmnopqrst',{'fixture-known-credential':'value'})
        with patch.dict(os.environ,{'SYNTHETIC_API_KEY':'fixture-known-credential'}):
            def rejects(value):
                try:check_sensitive(value)
                except StorageError:return True
                return False
            expected=[rejects(x) for x in samples]
            with sensitive_operation():actual=[rejects(x) for x in samples]
            self.assertEqual(actual,expected)
            self.assertEqual(actual,[False,True,True,True,True,True])

    def test_one_capture_nested_and_fresh_next_operation(self):
        import magi.storage as storage
        with patch.object(storage,'_capture_secrets',wraps=storage._capture_secrets) as capture:
            with sensitive_operation():
                check_sensitive('synthetic text')
                with sensitive_operation():check_sensitive('synthetic text')
            self.assertEqual(capture.call_count,1)
            with sensitive_operation():check_sensitive('synthetic text')
            self.assertEqual(capture.call_count,2)

    def test_env_file_reloaded_and_failure_closed(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'env-fixture'
            with patch('magi.storage.ENV_PATH',path),patch.dict(os.environ,{},clear=True):
                path.write_text('SYNTHETIC_VALUE=fixture-first-value\nSEC_USER_AGENT=public identification\n')
                with sensitive_operation():
                    with self.assertRaises(StorageError):check_sensitive('fixture-first-value')
                    check_sensitive('public identification')
                path.write_text('SYNTHETIC_VALUE=fixture-second-value\n')
                with sensitive_operation():
                    check_sensitive('fixture-first-value')
                    with self.assertRaises(StorageError):check_sensitive('fixture-second-value')
                with patch.object(Path,'read_text',side_effect=OSError):
                    with self.assertRaises(StorageError):
                        with sensitive_operation():pass
                    with self.assertRaises(ValueError):pack()

    def test_operation_reset_after_exception(self):
        with patch.dict(os.environ,{'SYNTHETIC_API_KEY':'fixture-before-error'}):
            with self.assertRaises(RuntimeError):
                with sensitive_operation():raise RuntimeError('synthetic')
        with patch.dict(os.environ,{'SYNTHETIC_API_KEY':'fixture-after-error'}):
            with self.assertRaises(StorageError):check_sensitive('fixture-after-error')

    def test_receipt_rejects_mutation_and_does_not_cross_operations(self):
        p=build_universe(request(),(pack(),))
        @operation
        def check():
            validate_container(p)
            object.__setattr__(p,'universe_id','BU_'+'0'*64)
            with self.assertRaises(ValueError):validate_container(p)
        check()
        with self.assertRaises(ValueError):validate_container(p)

    def test_exact_receipt_skips_only_redundant_reconstruction(self):
        import magi.research.serialization as codec
        p=pack()
        @operation
        def check():
            validate_container(p)
            with patch.object(codec,'decode',side_effect=AssertionError('Repeated decode')):
                validate_container(p)
                with self.assertRaises(AssertionError):validate_container(replace(p))
        check()
        with patch.object(codec,'decode',side_effect=AssertionError('Fresh operation')):
            with self.assertRaises(AssertionError):validate_container(p)

    def test_runtime_index_tampering_is_not_a_trusted_handoff(self):
        from types import MappingProxyType
        u=build_universe(request(),(pack(),))
        @operation
        def check():
            validate_container(u)
            object.__setattr__(u,'_objects',MappingProxyType({}))
            with self.assertRaises(ValueError):validate_container(u)
        check()
        with self.assertRaises(ValueError):GroupedEvidence(u)

    def test_reference_resolution_does_not_fingerprint_container(self):
        u=build_universe(request(),(pack(),))
        ref=next(r for r in u.references if r.object_kind=='EvidenceItem')
        import magi.research.balancing.inputs as inputs
        original=inputs.fingerprint
        def guard(value):
            self.assertIsNot(value,u.inputs[0].container)
            return original(value)
        with patch.object(inputs,'fingerprint',side_effect=guard):
            for _ in range(20):self.assertEqual(resolve(u,ref).evidence_id,ref.object_id)

    def test_index_not_serialized_and_target_tampering_rejected(self):
        u=build_universe(request(),(pack(),));self.assertNotIn('_reference',dumps(u.inputs[0]))
        ref=next(r for r in u.references if r.object_kind=='EvidenceItem');obj=resolve(u,ref)
        object.__setattr__(obj,'statement','changed synthetic statement')
        with self.assertRaises(ValueError):resolve(u,ref)

if __name__=='__main__':unittest.main()

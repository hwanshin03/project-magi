"""Synthetic staging acceptance and preservation tests."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import hashlib
import os
import shutil
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_virtual_backup as fixtures
from magi.virtual import backup as b
from magi.virtual import staged_restore as s
from magi.virtual.diagnostics import diagnose, Status


class StagingTests(unittest.TestCase):
    setUp = fixtures.BackupTests.setUp
    new_destination = fixtures.BackupTests.new_destination
    schema = fixtures.BackupTests.schema

    def prepare(self, version=4):
        self.schema(version)
        self.manifest = b.create_backup(self.workspace, self.source, self.destination)
        self.operation = str(uuid4())
        self.staging = b.create_workspace(self.root / ('restore-' + self.operation))
        self.candidate = self.staging.root / self.destination.name
        self.before = {p.name:p.read_bytes() for p in self.destination.iterdir()}

    def run_stage(self, **changes):
        args = dict(operation_id=self.operation, expected_schema_version=self.manifest.schema_version)
        args.update(changes)
        return s.stage_candidate(self.workspace, self.destination, self.staging, **args)

    def unchanged(self):
        self.assertEqual(self.before, {p.name:p.read_bytes() for p in self.destination.iterdir()})

    def test_source_database_not_required(self):
        self.prepare(); self.source.unlink()
        self.assertEqual(self.run_stage().outcome,'LOCALLY_VALIDATED_CANDIDATE')
        self.assertFalse(self.source.exists()); self.unchanged()

    def test_independent_manifest_and_artifact(self):
        self.prepare(); result=self.run_stage()
        self.assertEqual(result.acknowledgment,'UNVERIFIED')
        for name in self.before:
            p=self.candidate/name
            self.assertEqual(p.read_bytes(),self.before[name])
            self.assertNotEqual(p.stat().st_ino,(self.destination/name).stat().st_ino)
            self.assertEqual(p.stat().st_nlink,1)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertEqual(hashlib.sha256((self.candidate/b._ARTIFACT).read_bytes()).hexdigest(),self.manifest.sha256)
        with self.assertRaises(FrozenInstanceError): result.outcome='RESTORED'
        self.unchanged()

    def test_retry_and_validation(self):
        self.prepare(); self.run_stage()
        before={p.name:p.read_bytes() for p in self.candidate.iterdir()}
        self.assertEqual(self.run_stage().outcome,'DESTINATION_EXISTS')
        a=s.validate_staged_candidate(self.workspace,self.destination,self.staging,operation_id=self.operation,expected_schema_version=4)
        self.assertEqual(a,s.validate_staged_candidate(self.workspace,self.destination,self.staging,operation_id=self.operation,expected_schema_version=4))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.candidate.iterdir()})
        (self.candidate/b._ARTIFACT).write_bytes(b'x'*4096)
        self.assertNotEqual(s.validate_staged_candidate(self.workspace,self.destination,self.staging,operation_id=self.operation,expected_schema_version=4).outcome,'LOCALLY_VALIDATED_CANDIDATE')
        self.unchanged()

    def test_unknown_file_preserved(self):
        self.prepare(); p=self.staging.root/'unknown'; p.write_text('fixture')
        self.assertEqual(self.run_stage().outcome,'DESTINATION_EXISTS'); self.assertEqual(p.read_text(),'fixture')

    def test_copy_failure_cleanup(self):
        self.prepare(); original=s._copy
        def fail(*args):
            original(*args)
            raise OSError('sensitive fixture error')
        with patch.object(s,'_copy',side_effect=fail): result=self.run_stage()
        self.assertEqual(result.outcome,'COPY_INTERRUPTED');self.assertEqual(result.cleanup,'REMOVED')
        self.assertNotIn('sensitive',repr(result));self.assertFalse(self.candidate.exists());self.unchanged()

    def test_unknown_file_stops_cleanup(self):
        self.prepare()
        def fail(*args):
            (self.candidate/'unknown').write_text('preserve')
            raise OSError()
        with patch.object(s,'_copy',side_effect=fail):result=self.run_stage()
        self.assertEqual(result.cleanup,'RETAINED_UNVERIFIED');self.assertEqual((self.candidate/'unknown').read_text(),'preserve')
        self.unchanged()

    def test_no_partial_candidate_success(self):
        self.prepare(); verify=b.verify_backup
        def fail(ws,path):
            if ws==self.staging:raise b.BackupError('LOCAL_INCONSISTENT')
            return verify(ws,path)
        with patch.object(b,'verify_backup',side_effect=fail):result=self.run_stage()
        self.assertEqual(result.outcome,'LOCAL_INCONSISTENT');self.assertEqual(result.cleanup,'RETAINED_UNVERIFIED')
        self.unchanged()

    def test_original_reverified(self):
        self.prepare(); verify=b.verify_backup; calls=[]
        def observe(ws,path):
            calls.append(path)
            return verify(ws,path)
        with patch.object(b,'verify_backup',side_effect=observe):self.run_stage()
        self.assertEqual(calls,[self.destination,self.destination,self.candidate])

    def test_original_substitution_rejected(self):
        self.prepare(); original=s._copy
        def changed(*args):
            original(*args)
            if args[0].name==b._MANIFEST:
                p=self.destination/b._ARTIFACT
                data=p.read_bytes();p.unlink();p.write_bytes(data);p.chmod(0o600)
        with patch.object(s,'_copy',side_effect=changed):result=self.run_stage()
        self.assertEqual(result.outcome,'ORIGINAL_CHANGED')

    def test_populated_financial_history(self):
        from test_virtual_atomic_transitions import AtomicTransitionTests
        f=AtomicTransitionTests();f.setUp();self.addCleanup(f.doCleanups)
        r=f.prepare();f.apply(f.command(r))
        self.prepare();shutil.copyfile(f.path,self.source)
        self.destination=self.new_destination();self.manifest=b.create_backup(self.workspace,self.source,self.destination)
        self.candidate=self.staging.root/self.destination.name
        result=self.run_stage();self.assertEqual(result.outcome,'LOCALLY_VALIDATED_CANDIDATE')
        self.assertEqual(diagnose(self.candidate/b._ARTIFACT).local_status,Status.HEALTHY)
        with sqlite3.connect(f.path) as a,sqlite3.connect(self.candidate/b._ARTIFACT) as c:
            for (table,) in a.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                self.assertEqual(a.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall(),c.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall())


def version_case(version):
    def test(self):
        self.prepare(version);result=self.run_stage()
        self.assertEqual(result.outcome,'LOCALLY_VALIDATED_CANDIDATE')
        self.assertEqual(result.manifest.schema_version,version);self.unchanged()
    return test
for version in (1,2,3,4):setattr(StagingTests,'test_version_'+str(version),version_case(version))


def malformed_case(kind):
    def test(self):
        self.prepare();p=self.destination/b._ARTIFACT;m=self.destination/b._MANIFEST
        if kind=='missing_artifact':p.unlink()
        elif kind=='missing_manifest':m.unlink()
        elif kind=='pending':(self.destination/'manifest.pending').write_text('x')
        elif kind=='invalid_manifest':m.write_text('{}')
        elif kind=='truncate':p.write_bytes(p.read_bytes()[:-1])
        elif kind=='byte':
            data=bytearray(p.read_bytes());data[-1]^=1;p.write_bytes(data)
        elif kind=='digest':m.write_text(b.encode_manifest(replace(self.manifest,sha256='0'*64)))
        elif kind=='size':m.write_text(b.encode_manifest(replace(self.manifest,byte_size=self.manifest.byte_size+1)))
        elif kind=='swapped_manifest':m.write_text(b.encode_manifest(replace(self.manifest,backup_id=str(uuid4()))))
        elif kind=='symlink':p.unlink();p.symlink_to(self.source)
        elif kind=='hardlink':os.link(p,self.root/'alias')
        elif kind=='permissions':p.chmod(0o644)
        elif kind=='fifo':p.unlink();os.mkfifo(p,0o600)
        result=self.run_stage()
        self.assertNotEqual(result.outcome,'LOCALLY_VALIDATED_CANDIDATE');self.assertFalse(self.candidate.exists())
        self.assertEqual(result.acknowledgment,'UNVERIFIED')
    return test
for kind in ('missing_artifact','missing_manifest','pending','invalid_manifest','truncate','byte','digest','size','swapped_manifest','symlink','hardlink','permissions','fifo'):
    setattr(StagingTests,'test_reject_'+kind,malformed_case(kind))


def input_case(key,value):
    def test(self):
        self.prepare();self.assertNotEqual(self.run_stage(**{key:value}).outcome,'LOCALLY_VALIDATED_CANDIDATE')
        self.assertFalse(self.candidate.exists());self.unchanged()
    return test
for name,key,value in [('schema','expected_schema_version',5),('schema_bool','expected_schema_version',True),('format','expected_format_version',2),('limit','max_bytes',65*1024*1024),('limit_bool','max_bytes',True),('operation','operation_id','bad'),('pin','expected_sha256','0'*64),('backup','expected_backup_id',str(uuid4()))]:
    setattr(StagingTests,'test_input_'+name,input_case(key,value))


def boundary_case(kind):
    def test(self):
        self.prepare()
        if kind=='same':self.staging=self.workspace
        elif kind=='nested':self.staging=b.create_workspace(self.workspace.root/('restore-'+self.operation))
        elif kind=='relative':self.destination=Path('relative')
        elif kind=='traversal':self.destination=self.workspace.root/'child'/'..'/self.destination.name
        elif kind=='root_replaced':
            self.staging.root.rename(self.root/'old');self.staging.root.mkdir(mode=0o700)
        elif kind=='root_symlink':
            self.staging.root.rename(self.root/'old');self.staging.root.symlink_to(self.root/'old')
        elif kind=='root_permissions':self.staging.root.chmod(0o755)
        elif kind=='bundle_symlink':
            old=self.destination;old.rename(self.workspace.root/'old');old.symlink_to(self.workspace.root/'old')
        elif kind=='existing_candidate':self.candidate.mkdir(mode=0o700)
        result=self.run_stage()
        self.assertNotEqual(result.outcome,'LOCALLY_VALIDATED_CANDIDATE')
    return test
for kind in ('same','nested','relative','traversal','root_replaced','root_symlink','root_permissions','bundle_symlink','existing_candidate'):
    setattr(StagingTests,'test_boundary_'+kind,boundary_case(kind))


def interruption_case(when):
    def test(self):
        self.prepare();copy=s._copy;verify=b.verify_backup;calls=[]
        def copying(*args):
            if when=='before_copy':raise KeyboardInterrupt()
            copy(*args)
            if when==args[0].name:raise KeyboardInterrupt()
        def verifying(ws,path):
            calls.append(path)
            if when=='original_recheck' and len(calls)==2:raise KeyboardInterrupt()
            if when=='candidate' and ws==self.staging:raise KeyboardInterrupt()
            return verify(ws,path)
        with patch.object(s,'_copy',side_effect=copying),patch.object(b,'verify_backup',side_effect=verifying):
            with self.assertRaises(KeyboardInterrupt):self.run_stage()
        self.unchanged()
        if when=='candidate':self.assertTrue(self.candidate.exists())
        else:self.assertFalse(self.candidate.exists())
    return test
for when in ('before_copy','backup.virtual.sqlite3','manifest.json','original_recheck','candidate'):
    setattr(StagingTests,'test_interruption_'+when.replace('.','_'),interruption_case(when))


def crash_case(point):
    def test(self):
        import subprocess, sys
        self.prepare()
        script = """import os,sys
from pathlib import Path
from magi.virtual import backup as b,staged_restore as s
a,c=map(Path,sys.argv[1:3]);ws=b.Workspace(a,(a.stat().st_dev,a.stat().st_ino));stage=b.Workspace(c,(c.stat().st_dev,c.stat().st_ino))
point=sys.argv[5];copy=s._copy;verify=b.verify_backup;mkdir=Path.mkdir;write=os.write;sync=s._sync
copies=0;verifications=0
def crash():os._exit(71)
def creating(path,*args,**kwargs):
 if point=='before_create':crash()
 result=mkdir(path,*args,**kwargs)
 if point=='after_create':crash()
 return result
def copying(*args):
 global copies
 copies+=1
 copy(*args)
 if (copies==1 and point=='after_artifact') or (copies==2 and point=='after_manifest'):crash()
def writing(fd,data):
 if (copies==1 and point=='during_artifact') or (copies==2 and point=='during_manifest'):
  write(fd,data[:1]);crash()
 return write(fd,data)
def verifying(*args):
 global verifications
 verifications+=1
 if verifications==2 and point=='original_recheck':crash()
 if verifications==3 and point=='candidate_verify':crash()
 result=verify(*args)
 if verifications==3 and point=='after_validation':crash()
 return result
def syncing(path):
 sync(path)
 if path==c and point=='uncertain_return':crash()
Path.mkdir=creating;s._copy=copying;os.write=writing;b.verify_backup=verifying;s._sync=syncing
s.stage_candidate(ws,a/sys.argv[3],stage,operation_id=sys.argv[4],expected_schema_version=4)
"""
        child=subprocess.run([sys.executable,'-I','-B','-c',script,str(self.workspace.root),str(self.staging.root),self.destination.name,self.operation,point])
        self.assertEqual(child.returncode,71);self.unchanged()
        if point=='before_create':
            self.assertFalse(self.candidate.exists())
            self.assertEqual(self.run_stage().outcome,'LOCALLY_VALIDATED_CANDIDATE')
            return
        before={p.name:p.read_bytes() for p in self.candidate.iterdir()}
        self.assertEqual(self.run_stage().outcome,'DESTINATION_EXISTS')
        result=s.validate_staged_candidate(self.workspace,self.destination,self.staging,operation_id=self.operation,expected_schema_version=4)
        complete=point in ('after_manifest','original_recheck','candidate_verify','after_validation','uncertain_return')
        self.assertEqual(result.outcome=='LOCALLY_VALIDATED_CANDIDATE',complete)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.candidate.iterdir()})
    return test
for point in ('before_create','after_create','during_artifact','after_artifact','during_manifest','after_manifest','original_recheck','candidate_verify','after_validation','uncertain_return'):
    setattr(StagingTests,'test_process_crash_'+point,crash_case(point))


def _semantic_corruption(self):
    from test_virtual_atomic_transitions import AtomicTransitionTests
    f=AtomicTransitionTests();f.setUp();self.addCleanup(f.doCleanups)
    r=f.prepare();f.apply(f.command(r))
    self.prepare()
    f.corrupt('virtual_reservation_releases',"UPDATE virtual_reservation_releases SET amount='0'")
    p=self.destination/b._ARTIFACT;shutil.copyfile(f.path,p)
    with sqlite3.connect(p) as c:
        self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertIsNone(c.execute('PRAGMA foreign_key_check').fetchone())
    data=p.read_bytes();m=replace(self.manifest,byte_size=len(data),sha256=hashlib.sha256(data).hexdigest())
    (self.destination/b._MANIFEST).write_text(b.encode_manifest(m))
    self.assertEqual(self.run_stage().outcome,'LOCAL_INCONSISTENT');self.assertFalse(self.candidate.exists())
StagingTests.test_semantic_corruption= _semantic_corruption


def _unsupported_rehashed_schema(self):
    self.prepare();p=self.destination/b._ARTIFACT
    with sqlite3.connect(p) as c:c.execute('PRAGMA user_version=5')
    data=p.read_bytes();m=replace(self.manifest,sha256=hashlib.sha256(data).hexdigest())
    (self.destination/b._MANIFEST).write_text(b.encode_manifest(m))
    self.assertEqual(self.run_stage().outcome,'LOCAL_UNSUPPORTED_SCHEMA')
StagingTests.test_unsupported_rehashed_schema= _unsupported_rehashed_schema


def _copy_buffer_and_short_write(self):
    self.prepare();read=os.read;write=os.write;sizes=[]
    def bounded(fd,size):sizes.append(size);return read(fd,size)
    def short(fd,data):return write(fd,data[:max(1,len(data)//2)])
    with patch.object(s.os,'read',side_effect=bounded),patch.object(s.os,'write',side_effect=short):result=self.run_stage()
    self.assertEqual(result.outcome,'LOCALLY_VALIDATED_CANDIDATE');self.assertLessEqual(max(sizes),65536);self.unchanged()
StagingTests.test_copy_buffer_and_short_write= _copy_buffer_and_short_write


def _cleanup_denial(self):
    self.prepare();copy=s._copy
    def fail(*args):copy(*args);raise OSError()
    with patch.object(s,'_copy',side_effect=fail),patch.object(Path,'unlink',side_effect=PermissionError()):result=self.run_stage()
    self.assertEqual(result.cleanup,'RETAINED_UNVERIFIED');self.assertTrue(self.candidate.exists());self.unchanged()
StagingTests.test_cleanup_denial= _cleanup_denial


def failure_case(kind):
    def test(self):
        self.prepare();copy=s._copy;sync=s._sync;mkdir=Path.mkdir
        def copying(*args):
            copy(*args)
            if args[0].name==b._ARTIFACT and kind=='cleanup_replacement':
                p=args[1];p.rename(self.root/'owned-removed');p.write_bytes(b'unknown');p.chmod(0o600)
                raise OSError()
            if args[0].name==b._MANIFEST and kind=='candidate_corruption':
                (self.candidate/b._ARTIFACT).write_bytes(b'x'*4096)
        def syncing(path):
            if kind=='sync_failure':raise OSError('fixture-only failure')
            return sync(path)
        def creating(path,*args,**kwargs):
            if kind=='create_failure' and path==self.candidate:raise OSError()
            return mkdir(path,*args,**kwargs)
        with patch.object(s,'_copy',side_effect=copying),patch.object(s,'_sync',side_effect=syncing),patch.object(Path,'mkdir',creating):result=self.run_stage()
        self.assertNotEqual(result.outcome,'LOCALLY_VALIDATED_CANDIDATE');self.unchanged()
        if kind=='create_failure':self.assertEqual(result.outcome,'STAGING_CREATION_FAILED')
        else:self.assertEqual(result.cleanup,'RETAINED_UNVERIFIED')
        if kind=='cleanup_replacement':self.assertEqual((self.candidate/b._ARTIFACT).read_bytes(),b'unknown')
    return test
for kind in ('cleanup_replacement','candidate_corruption','sync_failure','create_failure'):
    setattr(StagingTests,'test_failure_'+kind,failure_case(kind))


def _source_open_forbidden(self):
    import builtins
    self.prepare();original_open=builtins.open;original_os=os.open
    def checked(call):
        def invoke(path,*args,**kwargs):
            if isinstance(path,(str,Path)) and Path(path)==self.source:raise AssertionError('Source A accessed')
            return call(path,*args,**kwargs)
        return invoke
    with patch.object(builtins,'open',checked(original_open)),patch.object(os,'open',checked(original_os)):
        self.assertEqual(self.run_stage().outcome,'LOCALLY_VALIDATED_CANDIDATE')
    self.unchanged()
StagingTests.test_source_open_forbidden = _source_open_forbidden


def corrupt_case(rehash):
    def test(self):
        self.prepare();p=self.destination/b._ARTIFACT
        # A structurally invalid, same-size replacement, with and without a valid binding.
        data=b'not sqlite'+b'\0'*(self.manifest.byte_size-10);p.write_bytes(data)
        if rehash:
            (self.destination/b._MANIFEST).write_text(b.encode_manifest(replace(self.manifest,sha256=hashlib.sha256(data).hexdigest())))
        result=self.run_stage()
        self.assertIn(result.outcome,('LOCAL_CORRUPT','LOCAL_UNSUPPORTED_SCHEMA','LOCAL_UNVERIFIED') if rehash else ('DIGEST_MISMATCH',))
        self.assertFalse(self.candidate.exists())
    return test
StagingTests.test_swapped_artifact=corrupt_case(False)
StagingTests.test_corrupt_database_valid_digest=corrupt_case(True)


def _ledger_reconstruction(self):
    from decimal import Decimal as D
    from magi.virtual import VirtualLedger, Instrument, Side, SyntheticFill
    from magi.virtual.order_storage import migrate_order_schema
    from magi.virtual.reservation_storage import migrate_reservation_schema
    from magi.virtual.transition_storage import migrate_transition_schema, _TransitionDatabase
    ledger=VirtualLedger(self.source);portfolio=str(uuid4());instrument=str(uuid4())
    ledger.create_portfolio(portfolio);ledger.register_instrument(Instrument(instrument,'SYNTH'))
    ledger.fund(portfolio,D(1000),idempotency_key=str(uuid4()),confirmed=True)
    for side,q,price in ((Side.BUY,10,10),(Side.SELL,3,15)):
        ledger.apply_synthetic_fill(portfolio,SyntheticFill(str(uuid4()),instrument,side,D(q),D(price),str(uuid4())),idempotency_key=str(uuid4()))
    expected=ledger.state(portfolio)
    self.assertEqual((expected.cash,expected.positions[0].quantity,expected.positions[0].book_cost,expected.realized_pnl),(D(945),D(7),D(70),D(15)))
    for migrate in (migrate_order_schema,migrate_reservation_schema,migrate_transition_schema):migrate(self.source,confirmed=True)
    self.manifest=b.create_backup(self.workspace,self.source,self.destination)
    self.operation=str(uuid4());self.staging=b.create_workspace(self.root/('restore-'+self.operation))
    self.candidate=self.staging.root/self.destination.name
    self.before={p.name:p.read_bytes() for p in self.destination.iterdir()}
    self.assertEqual(self.run_stage().outcome,'LOCALLY_VALIDATED_CANDIDATE')
    from magi.virtual.accounting import reconstruct
    with _TransitionDatabase(self.candidate/b._ARTIFACT)._connection() as connection:
        self.assertEqual(reconstruct(portfolio,VirtualLedger._events(connection,portfolio)),expected)
    self.unchanged()
StagingTests.test_ledger_reconstruction = _ledger_reconstruction

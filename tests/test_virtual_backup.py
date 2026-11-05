"""Synthetic D2 backups, strict manifest and publication failure boundaries."""
import contextlib
from dataclasses import replace, FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from magi.virtual import backup as b
from magi.virtual.diagnostics import diagnose, Status


class BackupTests(unittest.TestCase):
    def setUp(self):
        parent = Path(tempfile.gettempdir()).resolve()
        if not parent.is_relative_to('/private/tmp'):
            parent = Path('/private/tmp')
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.workspace = b.create_workspace(self.root / 'fixture')
        self.source = self.workspace.root / 'source.virtual.sqlite3'
        self.destination = self.new_destination()

    def new_destination(self):
        return self.workspace.root / ('backup-' + str(uuid4()))

    def schema(self, version=4):
        with contextlib.closing(sqlite3.connect(self.source)) as c:
            for filename in ('schema.sql', 'order_schema.sql', 'reservation_schema.sql', 'transition_schema.sql')[:version]:
                c.executescript(Path(b.__file__).with_name(filename).read_text())
        self.source.chmod(0o600)

    def create(self):
        return b.create_backup(self.workspace, self.source, self.destination)

    def verify(self):
        return b.verify_backup(self.workspace, self.destination)

    def assert_failure(self, code=None):
        return self.assertRaisesRegex(b.BackupError, code or '.')

    def test_populated_v4_preserves_every_financial_table(self):
        from test_virtual_atomic_transitions import AtomicTransitionTests
        from magi.virtual import Side
        fixture = AtomicTransitionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        request = fixture.prepare(Side.SELL, '3')
        fixture.apply(fixture.command(request))
        shutil.copyfile(fixture.path, self.source)
        self.source.chmod(0o600)
        before = self.source.read_bytes()
        manifest = self.create()
        artifact = self.destination / manifest.artifact
        def records(path):
            with contextlib.closing(sqlite3.connect(path)) as c:
                tables = [r[0] for r in c.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
                return {t:c.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in tables}
        self.assertEqual(records(self.source), records(artifact))
        for table in ('virtual_events','virtual_order_events','virtual_order_reservations',
                      'virtual_reservation_releases','virtual_transition_outcomes'):
            self.assertTrue(records(artifact)[table])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(diagnose(artifact).local_status, Status.HEALTHY)
        self.assertEqual(self.verify(), manifest)

    def test_manifest_roundtrip_and_immutability(self):
        self.schema();m=self.create()
        self.assertEqual(b.decode_manifest(b.encode_manifest(m)),m)
        with self.assertRaises(FrozenInstanceError):m.byte_size=1
        self.assertEqual(m.acknowledgment,'UNVERIFIED')
        self.assertEqual(m.environment,'SYNTHETIC_FIXTURE_CLAIM')

    def test_duplicate_missing_extra_and_noncanonical_json(self):
        self.schema();text=b.encode_manifest(self.create());values=json.loads(text)
        missing=dict(values);missing.pop('sha256')
        extra=dict(values, unexpected='x')
        for bad in ('{"backup_id":"x",'+text[1:],json.dumps(missing),json.dumps(extra),' '+text,'[]','null'):
            with self.subTest(bad=bad[:20]),self.assert_failure('MANIFEST_INVALID'):b.decode_manifest(bad)

    def test_missing_source_never_created(self):
        with self.assert_failure():self.create()
        self.assertFalse(self.source.exists());self.assertFalse(self.destination.exists())

    def test_outside_source_rejected_before_sqlite(self):
        self.schema()
        for source in (Path('source.virtual.sqlite3'),self.root/'magi.db',self.root/'other.virtual.sqlite3'):
            with self.subTest(source=source),patch.object(b.sqlite3,'connect') as connect,self.assert_failure():
                b.create_backup(self.workspace,source,self.destination)
            connect.assert_not_called()

    def test_same_source_destination_and_path_traversal(self):
        self.schema()
        for dest in (self.source,self.root/'outside',Path('relative'),self.workspace.root/'..'/'backup-other'):
            with self.subTest(dest=dest),self.assert_failure():b.create_backup(self.workspace,self.source,dest)

    def test_existing_destination_preserved(self):
        self.schema();m=self.create();before={p.name:p.read_bytes() for p in self.destination.iterdir()}
        with self.assert_failure('DESTINATION_EXISTS'):self.create()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.destination.iterdir()})
        self.assertEqual(self.verify(),m)

    def test_symlink_source_and_destination_rejected(self):
        self.schema();other=self.workspace.root/'other.virtual.sqlite3';self.source.rename(other);self.source.symlink_to(other)
        with self.assert_failure():self.create()
        self.source.unlink();other.rename(self.source);self.destination.symlink_to(self.root,target_is_directory=True)
        with self.assert_failure():self.create()
        self.assertTrue(self.destination.is_symlink())

    def test_workspace_parent_symlink_and_permissions(self):
        link=self.root/'link';link.symlink_to(self.workspace.root,target_is_directory=True)
        with self.assert_failure():b.create_workspace(link/'child')
        self.workspace.root.chmod(0o755)
        with self.assert_failure():self.create()

    def test_source_permissions_and_hardlink(self):
        self.schema();self.source.chmod(0o644)
        with self.assert_failure():self.create()
        self.source.chmod(0o600);os.link(self.source,self.root/'alias')
        with self.assert_failure():self.create()

    def test_incomplete_destination_is_preserved_on_retry(self):
        self.schema();self.destination.mkdir(mode=0o700);(self.destination/'partial').write_bytes(b'fixture')
        with self.assert_failure('ARTIFACT_INCOMPLETE'):self.verify()
        with self.assert_failure('DESTINATION_EXISTS'):self.create()
        self.assertEqual((self.destination/'partial').read_bytes(),b'fixture')

    def test_manifest_artifact_swapping(self):
        self.schema();first=self.create();old=self.destination;self.destination=self.new_destination();second=self.create()
        (self.destination/'manifest.json').write_text(b.encode_manifest(first))
        with self.assert_failure('MANIFEST_MISMATCH'):self.verify()
        (self.destination/'manifest.json').write_text(b.encode_manifest(second))
        # A different valid schema artifact must fail binding even if it is locally healthy.
        self.source.unlink();self.schema(1);self.destination=self.new_destination();self.create();third=self.destination
        shutil.copyfile(third/b._ARTIFACT,old/b._ARTIFACT);self.destination=old
        with self.assert_failure('DIGEST_MISMATCH'):self.verify()

    def test_lock_contention_fails_closed(self):
        self.schema()
        with contextlib.closing(sqlite3.connect(self.source)) as c:
            c.execute('BEGIN EXCLUSIVE')
            with self.assert_failure():self.create()
        self.assertFalse(self.destination.exists())

    def test_concurrent_writer_cannot_commit_inside_snapshot(self):
        self.schema();real=sqlite3.connect;observed=[];source=self.source
        class Connection(sqlite3.Connection):
            def backup(inner,*a,**kw):
                with contextlib.closing(real(source,timeout=0)) as writer:
                    writer.execute('BEGIN IMMEDIATE');writer.execute('PRAGMA user_version=99')
                    with self.assertRaises(sqlite3.OperationalError):writer.commit()
                    writer.rollback();observed.append(True)
                return super().backup(*a,**kw)
        with patch.object(b.sqlite3,'connect',side_effect=lambda *a,**kw:real(*a,**kw,factory=Connection)):
            manifest=self.create()
        self.assertEqual(observed,[True]);self.assertEqual(manifest.schema_version,4)
        self.assertEqual(diagnose(self.source).local_status,Status.HEALTHY)

    def test_backup_interruption_cleanup_and_retry(self):
        self.schema();real=sqlite3.connect
        class Connection(sqlite3.Connection):
            def backup(inner,*a,**kw):raise sqlite3.OperationalError('synthetic interruption')
        with patch.object(b.sqlite3,'connect',side_effect=lambda *a,**kw:real(*a,**kw,factory=Connection)),self.assert_failure('BACKUP_UNAVAILABLE'):self.create()
        self.assertFalse(self.destination.exists());self.create()

    def test_unsupported_journal_and_sidecars(self):
        self.schema()
        for suffix in ('-wal','-shm','-journal'):
            p=Path(str(self.source)+suffix);p.write_bytes(b'fixture')
            with self.assert_failure():self.create()
            self.assertEqual(p.read_bytes(),b'fixture');p.unlink()
        with contextlib.closing(sqlite3.connect(self.source)) as c:c.execute('PRAGMA journal_mode=WAL')
        before={p.name:p.read_bytes() for p in self.workspace.root.iterdir()}
        with self.assert_failure():self.create()
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.workspace.root.iterdir()})

    def test_sql_valid_semantic_corruption_rejected(self):
        from test_virtual_atomic_transitions import AtomicTransitionTests
        f=AtomicTransitionTests();f.setUp();self.addCleanup(f.doCleanups)
        r=f.prepare();f.apply(f.command(r));f.corrupt('virtual_reservation_releases',"UPDATE virtual_reservation_releases SET amount='0'")
        shutil.copyfile(f.path,self.source);self.source.chmod(0o600)
        with self.assert_failure('LOCAL_INCONSISTENT'):self.create()
        self.assertFalse(self.destination.exists())

    def test_digest_after_target_closed(self):
        self.schema();real=sqlite3.connect;opened=[];digest=b._digest
        def connect(*a,**kw):
            c=real(*a,**kw);opened.append(c);return c
        def checked(path):
            for c in opened:
                with self.assertRaises(sqlite3.ProgrammingError):c.execute('SELECT 1')
            return digest(path)
        with patch.object(b.sqlite3,'connect',side_effect=connect),patch.object(b,'_digest',side_effect=checked):self.create()


def version_case(version):
    def test(self):
        self.schema(version);before=self.source.read_bytes();m=self.create();artifact=self.destination/m.artifact
        self.assertEqual(m.schema_version,version);self.assertEqual(self.verify(),m)
        self.assertEqual(m.sha256,hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertEqual(m.byte_size,artifact.stat().st_size)
        self.assertEqual(self.source.read_bytes(),before)
        for p in (artifact,self.destination/'manifest.json'):self.assertEqual(p.stat().st_mode&0o777,0o600)
        self.assertEqual(self.destination.stat().st_mode&0o777,0o700)
    return test
for version in (1,2,3,4):setattr(BackupTests,f'test_version_{version}',version_case(version))


def bad_manifest(field,value):
    def test(self):
        self.schema();m=self.create()
        with self.assert_failure('MANIFEST_INVALID'):replace(m,**{field:value})
    return test
for field,value in {'format_version':True,'schema_version':5,'completed_at':'2026-11-04',
                    'sha256':'A'*64,'byte_size':False,'artifact':'../evil','backup_id':'bad',
                    'acknowledgment':'VERIFIED','environment':'PRODUCTION'}.items():
    setattr(BackupTests,'test_invalid_manifest_'+field,bad_manifest(field,value))


def corrupt_artifact(variant):
    def test(self):
        self.schema();m=self.create();p=self.destination/m.artifact
        if variant=='byte':
            data=bytearray(p.read_bytes());data[-1]^=1;p.write_bytes(data)
        elif variant=='truncate':p.write_bytes(p.read_bytes()[:-1])
        else:
            m=replace(m,**({'sha256':'0'*64} if variant=='digest' else {'byte_size':m.byte_size+1}))
            (self.destination/'manifest.json').write_text(b.encode_manifest(m))
        with self.assert_failure('DIGEST_MISMATCH'):self.verify()
    return test
for variant in ('byte','truncate','digest','size'):setattr(BackupTests,'test_tamper_'+variant,corrupt_artifact(variant))


def failure_case(target):
    def test(self):
        self.schema();before=self.source.read_bytes()
        with patch(target,side_effect=OSError('synthetic disk/permission failure')),self.assert_failure():self.create()
        self.assertFalse(self.destination.exists());self.assertEqual(self.source.read_bytes(),before)
        self.create()
    return test
for name,target in {'manifest':'magi.virtual.backup._write_manifest','publication':'magi.virtual.backup.os.link',
                    'sync':'magi.virtual.backup._sync','digest':'magi.virtual.backup._digest'}.items():
    setattr(BackupTests,'test_failure_'+name,failure_case(target))


def schema_failure(sql):
    def test(self):
        self.schema()
        with contextlib.closing(sqlite3.connect(self.source)) as c:c.execute(sql);c.commit()
        with self.assert_failure('LOCAL_UNSUPPORTED_SCHEMA'):self.create()
        self.assertFalse(self.destination.exists())
    return test
for name,sql in {'version':'PRAGMA user_version=5','app':'PRAGMA application_id=0',
                 'extra':'CREATE TABLE unexpected(x)','missing':'DROP TRIGGER virtual_events_no_update'}.items():
    setattr(BackupTests,'test_schema_'+name,schema_failure(sql))


class ImportPolicyTests(unittest.TestCase):
    def test_exact_exception_and_negative_cases(self):
        import test_virtual_accounting as original
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);virtual=root/'magi'/'virtual';virtual.mkdir(parents=True)
            for filename,module,allowed in (('backup.py','hashlib',True),('backup_extra.py','hashlib',False),
                    ('storage.py','hashlib',False),('backup_manifest.py','hashlib',False),
                    ('backup.py','ctypes',False),('backup.py','subprocess',False),('backup.py','tempfile',False),
                    ('backup.py','sqlite3',True),('execution_contracts.py','zoneinfo',True)):
                with self.subTest(filename=filename,module=module):
                    source=virtual/filename;source.write_text('import '+module+'\n')
                    case=original.VirtualAccountingTests()
                    with patch.object(original,'ROOT',root):
                        if allowed:case.test_import_closure_contains_no_owner_broker_or_network_modules()
                        else:
                            with self.assertRaises(AssertionError):case.test_import_closure_contains_no_owner_broker_or_network_modules()
                    source.unlink()


class BoundaryTests(unittest.TestCase):
    setUp = BackupTests.setUp
    new_destination = BackupTests.new_destination
    schema = BackupTests.schema
    create = BackupTests.create
    verify = BackupTests.verify
    assert_failure = BackupTests.assert_failure

    def test_rehashed_corrupt_artifact_still_requires_d1(self):
        self.schema();m=self.create();p=self.destination/m.artifact
        with contextlib.closing(sqlite3.connect(p)) as c:c.execute('PRAGMA user_version=99')
        size,digest=b._digest(p);m=replace(m,byte_size=size,sha256=digest)
        (self.destination/b._MANIFEST).write_text(b.encode_manifest(m))
        with self.assert_failure('LOCAL_UNSUPPORTED_SCHEMA'):self.verify()

    def test_corrupt_source(self):
        self.source.write_bytes(b'x'*4096);self.source.chmod(0o600)
        with self.assert_failure():self.create()
        self.assertFalse(self.destination.exists())

    def test_destination_permission_denial(self):
        self.schema();mkdir=Path.mkdir
        def denied(path,*a,**kw):
            if path==self.destination:raise PermissionError('fixture')
            return mkdir(path,*a,**kw)
        with patch.object(Path,'mkdir',denied),self.assert_failure():self.create()
        self.assertFalse(self.destination.exists())

    def test_disk_full_during_sqlite_backup(self):
        self.schema();real=sqlite3.connect
        class Connection(sqlite3.Connection):
            def backup(inner,*a,**kw):raise sqlite3.OperationalError('database or disk is full')
        with patch.object(b.sqlite3,'connect',side_effect=lambda *a,**kw:real(*a,**kw,factory=Connection)),self.assert_failure():self.create()
        self.assertFalse(self.destination.exists())

    def test_progress_busy_and_keyboard_interrupt(self):
        self.schema();real=sqlite3.connect
        for mode in ('busy','interrupt'):
            class Connection(sqlite3.Connection):
                def backup(inner,*a,**kw):
                    if mode=='busy':kw['progress'](sqlite3.SQLITE_BUSY,1,1)
                    else:raise KeyboardInterrupt()
            with patch.object(b.sqlite3,'connect',side_effect=lambda *a,**kw:real(*a,**kw,factory=Connection)):
                with self.assertRaises(b.BackupError if mode=='busy' else KeyboardInterrupt):self.create()
            self.assertFalse(self.destination.exists())

    def test_partial_manifest_write_cleanup(self):
        self.schema()
        def fail(path,manifest):path.write_bytes(b'{');raise OSError('fixture')
        with patch.object(b,'_write_manifest',side_effect=fail),self.assert_failure():self.create()
        self.assertFalse(self.destination.exists());self.create()

    def test_manifest_serialization_failure_cleanup(self):
        self.schema()
        with patch.object(b,'encode_manifest',side_effect=b.BackupError('MANIFEST_INVALID')),self.assert_failure('MANIFEST_INVALID'):
            self.create()
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.create(),self.verify())

    def test_failure_after_completion_marker(self):
        self.schema()
        with patch.object(b,'verify_backup',side_effect=b.BackupError('fixture')),self.assert_failure():self.create()
        self.assertFalse(self.destination.exists())

    def test_process_crash_leaves_incomplete_or_verifiable_pair(self):
        import subprocess,sys
        self.schema()
        child="""import os,sys
from pathlib import Path
from magi.virtual import backup as b
root=Path(sys.argv[1]);ws=b.Workspace(root,b._private(root,True))
if sys.argv[3]=='before':
 b._write_manifest=lambda *a:os._exit(77)
else:
 original=b.verify_backup
 def done(*a):
  original(*a);os._exit(78)
 b.verify_backup=done
b.create_backup(ws,root/'source.virtual.sqlite3',Path(sys.argv[2]))
"""
        for stage,code in (('before',77),('after',78)):
            self.destination=self.new_destination()
            result=subprocess.run([sys.executable,'-B','-c',child,str(self.workspace.root),str(self.destination),stage])
            self.assertEqual(result.returncode,code)
            if stage=='before':
                with self.assert_failure('ARTIFACT_INCOMPLETE'):self.verify()
            else:self.verify()
            before={p.name:p.read_bytes() for p in self.destination.iterdir()}
            with self.assert_failure('DESTINATION_EXISTS'):self.create()
            self.assertEqual(before,{p.name:p.read_bytes() for p in self.destination.iterdir()})

    def test_unapproved_workspace_and_replaced_root(self):
        ws=b.Workspace(Path('/Users'),(0,0))
        with self.assert_failure():b.create_backup(ws,Path('/Users/source.virtual.sqlite3'),Path('/Users/backup-'+str(uuid4())))
        self.workspace.root.rename(self.root/'moved');self.workspace.root.mkdir(mode=0o700)
        with self.assert_failure():self.create()

    def test_manifest_file_symlink_and_extra_artifact(self):
        self.schema();self.create();p=self.destination/b._MANIFEST;data=p.read_bytes()
        p.unlink();other=self.root/'manifest-copy';other.write_bytes(data);p.symlink_to(other)
        with self.assert_failure():self.verify()
        p.unlink();p.write_bytes(data);p.chmod(0o600)
        (self.destination/'unexpected').write_bytes(b'fixture')
        with self.assert_failure('ARTIFACT_INCOMPLETE'):self.verify()

    def test_manifest_timestamps_and_strict_numeric_types(self):
        self.schema();m=self.create()
        for field,values in {'completed_at':['bad','2026-11-04T12:00:00+01:00','2026-11-04T12:00:00Z',0],
                             'byte_size':[0,-1,100.0,2**40], 'schema_version':[True,1.0,0],
                             'sha256':['0'*63,'g'*64,None]}.items():
            for value in values:
                with self.subTest(field=field,value=value),self.assert_failure():replace(m,**{field:value})

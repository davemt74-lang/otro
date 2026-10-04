"""Real SQLite/filesystem transfer retries, rollback, deletion and retention."""
import base64
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.database import db, initialize_database
from app.services import governed_recordings as rec, knowledge_backups as backup, knowledge, local_transcription_sessions as tx

class TransferRetention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='transfer-retention-')
        previous = settings.data_dir
        # Production settings are frozen; isolate each fixture explicitly.
        object.__setattr__(settings, 'data_dir', Path(self.temp.name))
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(object.__setattr__, settings, 'data_dir', previous)
        initialize_database()
        self.identity = {'app_key': 'vp3', 'scope': {'knowledge_kinds': ['transcription']}}
        self.source = 'vp3-transcript:session-42'
        self.item = backup.upsert_external_knowledge(self.identity, source_key=self.source, title='Shared transcript', kind='transcription', content='Consented source text.')['id']
        self.data = b'private recording bytes'
        self.args = dict(source_key=self.source, asset_key='recording:abc12345', original_name='private.wav', media_type='audio/wav', size_bytes=len(self.data), sha256=hashlib.sha256(self.data).hexdigest())

    def begin(self):
        return backup.begin_asset_upload(self.identity, **self.args)

    def fill(self, upload):
        return backup.append_asset_chunk(self.identity, upload_id=upload, offset=0, data_base64=base64.b64encode(self.data).decode())

    def clip(self, rid='a'*32, *, expired=False, contents=b'a'*64):
        path=rec._file(rid,'audio');path.write_bytes(contents)
        now=rec._now();expiry=now+timedelta(days=-1 if expired else 1)
        with db() as c:
            c.execute('INSERT INTO governed_recordings(recording_id,media_type,duration_seconds,size_bytes,sha256,created_at,delete_after) VALUES(?,?,?,?,?,?,?)',(rid,'audio',3,len(contents),hashlib.sha256(contents).hexdigest(),now.isoformat(),expiry.isoformat()))
        return path

    def test_begin_lost_ack_reuses_upload_and_offset(self):
        first=self.begin();self.fill(first['upload_id']);again=self.begin()
        self.assertEqual(first['upload_id'],again['upload_id']);self.assertEqual(again['received_bytes'],len(self.data))
        self.assertEqual(len(list(backup._incoming_dir().glob('*.json'))),1)

    def test_chunk_replay_resynchronizes_without_duplicate_bytes(self):
        upload=self.begin()['upload_id'];self.fill(upload);again=self.fill(upload)
        self.assertTrue(again['resync']);self.assertEqual(backup._upload_paths(upload)[0].read_bytes(),self.data)

    def test_commit_lost_ack_converges_via_verified_begin(self):
        upload=self.begin()['upload_id'];self.fill(upload);backup.commit_asset_upload(self.identity,upload_id=upload)
        self.assertTrue(self.begin()['already_present']);self.assertEqual(len(list(backup._asset_dir().iterdir())),1)

    def test_restricted_kind_cannot_continue_upload(self):
        upload=self.begin()['upload_id'];denied={'app_key':'vp3','scope':{'knowledge_kinds':['recipe']}}
        with self.assertRaises(backup.KnowledgeBackupError):backup.append_asset_chunk(denied,upload_id=upload,offset=0,data_base64=base64.b64encode(self.data).decode())
        self.assertEqual(backup._upload_paths(upload)[0].stat().st_size,0)

    def test_foreign_application_cannot_continue_upload(self):
        upload=self.begin()['upload_id']
        with self.assertRaises(backup.KnowledgeBackupError):backup.append_asset_chunk({'app_key':'foreign'},upload_id=upload,offset=0,data_base64='YQ==')

    def test_deleted_source_does_not_accept_or_recreate_attachment(self):
        upload=self.begin()['upload_id'];self.fill(upload);knowledge.delete_knowledge_item(self.item)
        with self.assertRaises(backup.KnowledgeBackupError):backup.commit_asset_upload(self.identity,upload_id=upload)
        self.assertFalse(list(backup._asset_dir().iterdir()))

    def test_database_failure_preserves_resumable_bytes(self):
        upload=self.begin()['upload_id'];self.fill(upload)
        with db() as c:c.execute("CREATE TRIGGER fail_asset BEFORE INSERT ON activity_log WHEN NEW.action='knowledge.asset_backed_up' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(Exception):backup.commit_asset_upload(self.identity,upload_id=upload)
        self.assertEqual(backup._upload_paths(upload)[0].read_bytes(),self.data);self.assertFalse(list(backup._asset_dir().iterdir()))
        with db() as c:c.execute('DROP TRIGGER fail_asset')
        self.assertTrue(backup.commit_asset_upload(self.identity,upload_id=upload)['committed'])

    def test_explicit_backup_repairs_corrupt_committed_attachment(self):
        upload=self.begin()['upload_id'];self.fill(upload);backup.commit_asset_upload(self.identity,upload_id=upload)
        old=next(backup._asset_dir().iterdir());old.write_bytes(b'x'*len(self.data))
        again=self.begin();self.assertFalse(again['already_present']);self.fill(again['upload_id'])
        backup.commit_asset_upload(self.identity,upload_id=again['upload_id'])
        self.assertFalse(old.exists());self.assertEqual(next(backup._asset_dir().iterdir()).read_bytes(),self.data)

    def test_pending_upload_quota_rejects_new_reservations_but_allows_resume(self):
        first=self.begin()
        with patch.object(backup,'MAX_PENDING_UPLOADS',1):
            self.assertEqual(self.begin()['upload_id'],first['upload_id'])
            with self.assertRaises(backup.KnowledgeBackupError):backup.begin_asset_upload(self.identity,**{**self.args,'asset_key':'recording:another'})

    def test_stale_incoming_and_orphan_files_are_pruned(self):
        upload=self.begin()['upload_id'];part,sidecar=backup._upload_paths(upload)
        old=time.time()-backup.UPLOAD_TTL_SECONDS-10;os.utime(part,(old,old));os.utime(sidecar,(old,old))
        orphan=backup._incoming_dir()/('z'*32+'.part');orphan.write_bytes(b'old');os.utime(orphan,(old,old))
        attachment=backup._asset_dir()/('f'*32+'.wav');attachment.write_bytes(b'orphan');os.utime(attachment,(old,old))
        backup.maintain_transfer_retention();self.assertFalse(part.exists());self.assertFalse(sidecar.exists());self.assertFalse(orphan.exists());self.assertFalse(attachment.exists())

    def test_expired_recording_symlink_keeps_external_file(self):
        path=self.clip('f'*32,expired=True);external=Path(self.temp.name)/'owner.wav';external.write_bytes(b'keep');path.unlink()
        try:path.symlink_to(external)
        except OSError:self.skipTest('Native symlink creation unavailable')
        rec.maintain_retention();self.assertFalse(path.is_symlink());self.assertEqual(external.read_bytes(),b'keep')

    def test_knowledge_delete_symlink_keeps_external_file(self):
        upload=self.begin()['upload_id'];self.fill(upload);backup.commit_asset_upload(self.identity,upload_id=upload)
        path=next(backup._asset_dir().iterdir());external=Path(self.temp.name)/'owner-backup.wav';external.write_bytes(b'keep');path.unlink()
        try:path.symlink_to(external)
        except OSError:self.skipTest('Native symlink creation unavailable')
        self.assertTrue(knowledge.delete_knowledge_item(self.item));self.assertFalse(path.is_symlink());self.assertEqual(external.read_bytes(),b'keep')

    def test_retention_does_not_wait_behind_capture(self):
        rec._CAPTURE.acquire()
        try:self.assertTrue(rec.maintain_retention()['deferred'])
        finally:rec._CAPTURE.release()

    def test_delete_expired_missing_and_damaged_recordings(self):
        for i in range(3):
            rid=str(i+1)*32;path=self.clip(rid,expired=i==0)
            if i==1:path.unlink()
            if i==2:path.write_bytes(b'changed')
            self.assertTrue(rec.delete(rid)['deleted']);self.assertFalse(path.exists());self.assertTrue(rec.delete(rid)['deleted'])
        with db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM governed_recordings').fetchone()[0],0)

    def test_expiry_during_inference_does_not_publish_transcript(self):
        rid='b'*32;self.clip(rid)
        def inference(_):
            with db() as c:c.execute('UPDATE governed_recordings SET delete_after=? WHERE recording_id=?',((rec._now()-timedelta(seconds=1)).isoformat(),rid))
            return {'text':'expired private result'}
        with patch.object(rec.local_voice,'status',return_value={'stt':{'available':True}}),patch.object(rec.local_voice,'transcribe',side_effect=inference):
            with self.assertRaises(rec.RecordingError):rec.transcribe_saved(rid)
        with db() as c:self.assertIsNone(c.execute('SELECT transcript FROM governed_recordings WHERE recording_id=?',(rid,)).fetchone()[0])

    def test_capture_enforces_retention_before_success(self):
        self.clip('c'*32)
        with patch.object(rec,'_preflight'),patch.object(rec,'_capture_audio',side_effect=lambda seconds,path:path.write_bytes(b'z'*64)),patch.object(rec,'MAX_CLIPS',1):
            result=rec.capture('audio',3,consent=True,capture_ack=True)
        with db() as c:rows=c.execute('SELECT recording_id FROM governed_recordings').fetchall()
        self.assertEqual([r[0] for r in rows],[result['recording']['id']]);self.assertFalse(rec._file('c'*32,'audio').exists())

    def test_recording_retention_removes_only_managed_orphans(self):
        old=rec._store()/('d'*32+'.pending.wav');old.write_bytes(b'old');unrelated=rec._store()/'owner-notes.txt';unrelated.write_text('keep')
        stamp=time.time()-3700;os.utime(old,(stamp,stamp));rec.maintain_retention()
        self.assertFalse(old.exists());self.assertTrue(unrelated.exists())

    def test_shared_fetch_has_single_consent_snapshot(self):
        sid=tx.start()['session']['id'];tx.append(sid,'consented words','e'*32);tx.stop(sid);tx.share_with_cloud(sid,True)
        original=tx._payload
        def revoke_then_read(connection,row,with_segments=False):
            if with_segments:tx.share_with_cloud(sid,False)
            return original(connection,row,with_segments)
        with patch.object(tx,'_payload',side_effect=revoke_then_read):result=tx.get(sid,paired=True)
        self.assertEqual(result['session']['segments'][0]['text'],'consented words')
        with self.assertRaises(tx.TranscriptError):tx.get(sid,paired=True)

if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(TransferRetention)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():sys.exit(1)
    print(f'TRANSFER_RETENTION_SECTION7=PASS ({result.testsRun} real SQLite/filesystem cases)')

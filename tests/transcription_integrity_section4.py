"""Canonical local transcript service against real temporary SQLite databases.
The dependency shim replaces only application boot, not service SQL or behavior.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
import types

ROOT=Path(__file__).resolve().parents[1]
for name in ('section4','section4.services'):
    package=types.ModuleType(name);package.__path__=[];sys.modules[name]=package
module=types.ModuleType('section4.database')
with tempfile.TemporaryDirectory(prefix='transcript-integrity-') as temp:
    path=Path(temp)/'transcripts.sqlite'
    @contextmanager
    def db():
        connection=sqlite3.connect(path,timeout=10)
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback();raise
        finally:connection.close()
    module.db=db;sys.modules[module.__name__]=module
    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript((ROOT/'database/local_transcription_sessions.sql').read_text())
    spec=importlib.util.spec_from_file_location('section4.services.transcripts',ROOT/'app/services/local_transcription_sessions.py')
    service=importlib.util.module_from_spec(spec);spec.loader.exec_module(service)
    def rejects(fn,code):
        try:fn()
        except service.TranscriptError as error:assert error.status_code==code;return
        raise AssertionError('Expected transcript rejection')
    session=service.start('integrity')['session'];sid=session['id']
    keys=[secrets.token_hex(16) for _ in range(3)]
    service.append(sid,'first',keys[0],900)
    service.append(sid,'second after page restart',keys[1],10)
    service.append(sid,'third',keys[2],1000)
    result=service.get(sid)['session']
    assert [s['text'] for s in result['segments']]==['first','second after page restart','third']
    assert [s['segment_index'] for s in result['segments']]==[0,1,2]
    assert [s['started_ms'] for s in result['segments']]==[900,900,1000]
    assert result['timeline_ms']==1000
    print('PASS append order and monotonic session timing survive page clock reset')
    rejects(lambda:service.append(sid,'conflicting words',keys[0],900),409)
    assert service.get(sid)['session']['segment_count']==3
    print('PASS an idempotency key cannot acknowledge different text')
    service.stop(sid)
    assert service.append(sid,'first',keys[0],900)['duplicate'] is True
    rejects(lambda:service.append(sid,'new words',secrets.token_hex(16),1001),409)
    assert service.get(sid)['session']['segment_count']==3
    print('PASS lost acknowledgement replays after Stop without reopening or new writes')
    rejects(lambda:service.get(sid,paired=True),403)
    service.share_with_cloud(sid,True)
    assert service.get(sid,paired=True)['raw_audio_included'] is False
    service.share_with_cloud(sid,False)
    rejects(lambda:service.get(sid,paired=True),403)
    print('PASS ordering repairs preserve explicit completed text-only Cloud sharing')
    barrier=threading.Barrier(8)
    def start_parallel(_):
        barrier.wait()
        try:return service.start()['session']['id']
        except service.TranscriptError as error:assert error.status_code==409;return None
    with ThreadPoolExecutor(max_workers=8) as pool:started=list(pool.map(start_parallel,range(8)))
    assert len([x for x in started if x])==1
    concurrent=next(x for x in started if x)
    print('PASS concurrent Start creates exactly one active document')
    barrier=threading.Barrier(8);shared_key=secrets.token_hex(16)
    def replay(_):barrier.wait();return service.append(concurrent,'same text',shared_key,1)
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(replay,range(8)))
    assert sum(not result['duplicate'] for result in results)==1
    assert service.get(concurrent)['session']['segment_count']==1
    print('PASS concurrent lost-ack replays commit one segment')
    original_limit=service.MAX_SESSION_CHARS;service.MAX_SESSION_CHARS=12
    service.stop(concurrent);unicode_sid=service.start()['session']['id']
    service.append(unicode_sid,'你好',secrets.token_hex(16),1)
    service.append(unicode_sid,'你好',secrets.token_hex(16),2)
    rejects(lambda:service.append(unicode_sid,'a',secrets.token_hex(16),3),409)
    assert service.get(unicode_sid)['session']['segment_count']==2
    service.MAX_SESSION_CHARS=original_limit
    print('PASS document size limit consistently counts UTF-8 bytes')
    service.stop(unicode_sid);limited=service.start()['session']['id'];service.MAX_SEGMENTS=2
    barrier=threading.Barrier(8)
    def append_parallel(index):
        barrier.wait()
        try:service.append(limited,str(index),secrets.token_hex(16),index);return True
        except service.TranscriptError as error:assert error.status_code==409;return False
    with ThreadPoolExecutor(max_workers=8) as pool:outcomes=list(pool.map(append_parallel,range(8)))
    assert sum(outcomes)==2 and service.get(limited)['session']['segment_count']==2
    print('PASS concurrent appends cannot exceed the segment limit')
print('TRANSCRIPTION_INTEGRITY_SECTION4=PASS (8 SQLite service cases)')

"""Exercise canonical overlap, content-bound caching and durable corrections."""
from __future__ import annotations
import contextlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services import meeting_speaker_context as context, speaker_attribution

voice=speaker_attribution.fuse([{"source":"verified_voice","participant_identity":"tracky:one","confidence":.95}])
track=speaker_attribution.fuse([{"source":"livekit_track","participant_identity":"vp3p-two","confidence":1}])
rows=context.normalize([
    {"speaker_name":"One","start_ms":0,"end_ms":2000,"text":"I will do it","speaker_attribution":voice},
    {"speaker_name":"Two","start_ms":1000,"end_ms":3000,"text":"Who?","speaker_attribution":track},
])
assert all(row["overlap"] for row in rows)
assert not rows[0]["speaker_attribution"]["speaker_identity_verified"]
assert rows[1]["speaker_attribution"]["speaker_identity_verified"]
assert "overlapping speech" in context.transcript_text(rows)
assert "identity unverified" in context.transcript_text(rows)
assert "provider_speaker_id" not in json.dumps(context.attribution({"provider_speaker_id":"secret"},"Room"))
edge=context.normalize([{**rows[0],"end_ms":1000},{**rows[1],"start_ms":1000,"overlap":False,"speaker_attribution":track}])
assert not edge[1]["overlap"]

with tempfile.TemporaryDirectory() as directory:
    db_path=Path(directory)/'sessions.sqlite'
    @contextlib.contextmanager
    def db():
        conn=sqlite3.connect(db_path);conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
        try:
            yield conn;conn.commit()
        except BaseException:
            conn.rollback();raise
        finally:conn.close()
    with db() as conn:conn.executescript((ROOT/'database/local_transcription_sessions.sql').read_text())
    stub=ModuleType('app.database');stub.db=db;sys.modules['app.database']=stub
    from app.services import local_transcription_sessions as sessions
    sid=sessions.start('Review')['session']['id']
    first=sessions.append(sid,'Ship Friday','a'*32,0,speaker_label='Speaker 1',ended_ms=1000)['session']
    def rejects(call,code):
        try:call()
        except sessions.TranscriptError as exc:assert exc.status_code==code
        else:raise AssertionError('expected rejection')
    segment=sessions.get(sid)['session']['segments'][0]
    rejects(lambda:sessions.correct_speaker(sid,segment['id'],'Dave'),409)
    sessions.stop(sid);sessions.share_with_cloud(sid,True)
    corrected=sessions.correct_speaker(sid,segment['id'],'Dave')['session']
    assert corrected['segments'][0]['speaker']=='Dave'
    assert corrected['segments'][0]['correction_revision']==1
    assert not corrected['segments'][0]['speaker_identity_verified']
    assert not corrected['cloud_shared']
    rejects(lambda:sessions.get(sid,paired=True),403)
    rejects(lambda:sessions.correct_speaker(sid,segment['id'],'Stale',0),409)
    other=sessions.start('Other')['session']['id'];sessions.stop(other)
    rejects(lambda:sessions.correct_speaker(other,segment['id'],'Wrong meeting'),404)
    with db() as conn:
        assert conn.execute('SELECT text FROM local_transcription_segments WHERE id=?',(segment['id'],)).fetchone()[0]=='Ship Friday'
        assert conn.execute('SELECT speaker_label FROM local_transcription_segment_attribution WHERE segment_id=?',(segment['id'],)).fetchone()[0]=='Speaker 1'
    sessions.share_with_cloud(sid,True)
    shared=sessions.get(sid,paired=True)['session']['segments'][0]
    assert shared['speaker']=='Dave' and not shared['attribution']['participant_identity']
    assert shared['attribution']['source']=='unknown'
    sessions.delete(sid)
    with db() as conn:assert conn.execute('SELECT COUNT(*) FROM local_transcription_speaker_corrections').fetchone()[0]==0

# Only the inference/provider boundary is substituted. Production normalization,
# authorization, cache lookup, prompt and result code execute unchanged.
for name in ('agent_routing','canonical_context','providers'):
    sys.modules['app.services.'+name]=ModuleType('app.services.'+name)
from app.services import meeting_intelligence as intelligence
intelligence.status=lambda:{'ready':True}
intelligence.agent_routing.resolve_agent=lambda *args,**kwargs:{'id':1}
intelligence.canonical_context.build_authorized_context=lambda **kwargs:SimpleNamespace(total_context_chars=0,source_refs=[])
intelligence.canonical_context.system_prompt=lambda *args:'Authorized context'
class ProviderError(Exception):pass
intelligence.providers.ProviderError=ProviderError
calls=[]
def generate(messages,**kwargs):
    calls.append(messages)
    return {'content':json.dumps({'summary':'Review candidates only.'}),'model':'fixture'}
intelligence.providers.generate_ollama=generate
public='b'*32;source='c'*64
payload={'contract':intelligence.CONTRACT,'meeting':public,'source_hash':source,'mode':'live','idempotency_key':f'vp3-meeting-intelligence:{public}:{source}','cloud_processing_allowed':False,'requested_compute':'homeserver','segments':rows}
identity={'app_key':'fixture','permissions':['agent.chat']}
intelligence.analyze(payload,identity);intelligence.analyze(payload,identity)
assert len(calls)==1
assert 'leave ownership unassigned' in calls[0][0]['content']
intelligence.analyze({**payload,'mode':'final'},identity);assert len(calls)==2
changed=[{**row,'speaker_name':'Corrected'} for row in rows]
intelligence.analyze({**payload,'segments':changed},identity);assert len(calls)==3
try:intelligence.analyze(payload,{'app_key':'fixture','permissions':[]})
except intelligence.MeetingIntelligenceError as exc:assert exc.status_code==403
else:raise AssertionError('cached result bypassed permission')
print('MEETING_RECORDING_SECTION9=PASS (overlap, identity boundaries, correction persistence/revision/revocation/cascade, content/mode/permission cache)')

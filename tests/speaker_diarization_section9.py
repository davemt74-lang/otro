from __future__ import annotations

from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from app.services import speaker_diarization

def check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)

cases=0

payload={
 "model_id":"scribe_v2","language_code":"en","words":[
  {"type":"word","text":"Hello","start":0.0,"end":0.3,"speaker_id":"speaker_A"},
  {"type":"spacing","text":" ","start":0.3,"end":0.31,"speaker_id":"speaker_A"},
  {"type":"word","text":"there","start":0.31,"end":0.7,"speaker_id":"speaker_A"},
  {"type":"word","text":"Hi","start":0.8,"end":1.0,"speaker_id":"speaker_B"},
 ]}
result=speaker_diarization.parse_response(payload)
check(result["speaker_count"]==2 and len(result["turns"])==2,"speaker grouping failed")
check(result["turns"][0]["text"]=="Hello there" and result["turns"][1]["speaker_label"]=="Speaker 2","turn text failed")
check(not result["turns"][0]["attribution"]["speaker_identity_verified"],"diarization invented identity")
check("speaker_A" not in str(result),"raw speaker ID leaked")
cases+=1

overlap=speaker_diarization.parse_response({"words":[
 {"type":"word","text":"First","start":0.0,"end":1.0,"speaker_id":"a"},
 {"type":"word","text":"Second","start":0.7,"end":1.2,"speaker_id":"b"},
]})
check(overlap["turns"][0]["overlap"] and overlap["turns"][1]["overlap"],"overlap not preserved")
check(overlap["turns"][0]["attribution"]["overlap_group"]==overlap["turns"][1]["attribution"]["overlap_group"],"overlap group mismatch")
cases+=1

empty=speaker_diarization.parse_response({"words":[]})
check(empty["turns"]==[] and empty["speaker_count"]==0,"empty response failed")
cases+=1

try:
    speaker_diarization.parse_response({"words":[{"type":"word","text":"bad","start":2,"end":1,"speaker_id":"a"}]})
    raise AssertionError("invalid timing accepted")
except speaker_diarization.SpeakerDiarizationError:
    pass
cases+=1

try:
    speaker_diarization.parse_response({"text":"missing words"})
    raise AssertionError("missing timeline accepted")
except speaker_diarization.SpeakerDiarizationError:
    pass
cases+=1

check(speaker_diarization.status()["authentication_authority"] is False,"status authority changed")
cases+=1

print(f"SPEAKER_DIARIZATION_SECTION9B=PASS ({cases} provider/parser cases)")

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
index=(ROOT/'ui'/'index.html').read_text(encoding='utf-8')
workspace=(ROOT/'ui'/'transcription-workspace.js').read_text(encoding='utf-8')
capture=(ROOT/'ui'/'chat-dictation.js').read_text(encoding='utf-8')
fusion=(ROOT/'ui'/'tracky'/'speaker-fusion.js').read_text(encoding='utf-8')
participant=(ROOT/'ui'/'tracky'/'src'/'participant-core.js').read_text(encoding='utf-8')
api=(ROOT/'app'/'local_transcription_api.py').read_text(encoding='utf-8')
sessions=(ROOT/'app'/'services'/'local_transcription_sessions.py').read_text(encoding='utf-8')

assert '/assets/tracky/speaker-fusion.js?v=section9c-20261004' in index
for marker in [
    'Enroll next voice samples','raw enrollment audio is not stored','Start camera corroboration',
    'camera evidence can only confirm or challenge a voice match','stopCameraCorroboration',
    'speaker_evidence'
]:
    assert marker in workspace, marker
for marker in ['enrichSpeakerTurns','speaker_evidence:Array.isArray(turn.speaker_evidence)','HomeServerSpeakerFusion']:
    assert marker in capture, marker
for marker in [
    "from './src/participant-store.js'","bestVoiceParticipantMatch","bestParticipantMatch",
    "voiceEmbeddingFromPcm","voiceProfileSamples","rawAudioStored:false",
    "if(embedding&&!turn.overlap)","source:'verified_voice'","source:'visual_corroboration'",
    "VISUAL_MAX_AGE_MS=1600","privacyClear()","pagehide"
]:
    assert marker in fusion, marker
assert "fetch('/api/v1/control/onboarding/visual/status'" in fusion
assert 'vp3' not in fusion.lower() and '/cloud' not in fusion.lower(), 'fusion runtime must not send biometric/profile data to Cloud'
assert 'voiceRecognitionEnabled: input.voiceRecognitionEnabled===true&&voiceProfileReady' in participant
assert 'class SpeakerEvidence(BaseModel)' in api
assert 'model_config=ConfigDict(extra="forbid")' in api
assert 'speaker_evidence:list[SpeakerEvidence]|None' in api
assert 'speaker_attribution.fuse(rows)' in api
assert 'item["confidence"]<0.90' in api
assert 'Overlapping speech cannot establish a local voice identity.' in api
assert 'Speaker evidence cannot grant authentication authority.' in api
assert 'Identity-capable speaker attribution requires canonical evidence fusion.' in api
assert 'def _paired_attribution' in sessions
assert '"participant_identity":""' in sessions
assert '"speaker_identity_verified":False' in sessions
print('SPEAKER_FUSION_UI_SECTION9C=PASS')

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
capture=(ROOT/'ui'/'chat-dictation.js').read_text(encoding='utf-8')
workspace=(ROOT/'ui'/'transcription-workspace.js').read_text(encoding='utf-8')
voice_api=(ROOT/'app'/'local_voice_api.py').read_text(encoding='utf-8')
session_api=(ROOT/'app'/'local_transcription_api.py').read_text(encoding='utf-8')
provider=(ROOT/'app'/'services'/'provider_secrets.py').read_text(encoding='utf-8')
service=(ROOT/'app'/'services'/'speaker_diarization.py').read_text(encoding='utf-8')

for marker in [
    "/api/v1/control/voice/transcribe-diarized",
    "speakerDiarization",
    "homeserver:transcription-diarization-status",
    "postWav(DIARIZE_ENDPOINT",
    "postWav(TRANSCRIBE_ENDPOINT",
    "strictLocalEnabled()",
]:
    assert marker in capture, marker

for marker in [
    "hsTranscriptDiarization",
    "Enhanced speaker separation",
    "uses ElevenLabs Scribe for transient audio chunks",
    "Speaker separation is not identity verification",
    "speaker_label",
    "attribution",
]:
    assert marker in workspace, marker

assert 'PROVIDERS = ("anthropic", "openai", "openrouter", "elevenlabs")' in provider
assert '@router.post("/transcribe-diarized")' in voice_api
assert 'speaker_diarization.status()' in voice_api
assert 'attribution:dict[str,Any]|None=None' in session_api
assert 'ended_ms:int|None' in session_api
assert '"enable_logging": "false"' in service
assert '"diarize": "true"' in service
assert '"use_speaker_library"' in service
assert 'speaker_identity_verified": False' in service
assert 'authentication_authority": False' in service
assert "provider_speaker_id" in service
assert "raw_speaker_ids_included" in service
print("SPEAKER_DIARIZATION_UI_SECTION9B=PASS")

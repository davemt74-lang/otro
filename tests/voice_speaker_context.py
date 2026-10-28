import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services import voice_speaker_context
from app.brain_api import ChatRequest, _chat_or_http

hint={'source':'local_voice_similarity','confidence':.95,'display_name':'David','participant_id':'local-private-id','authentication_authority':False,'embedding':[1,2,3]}
text=voice_speaker_context.context(hint,owner=True)
assert 'David' in text and 'not authentication' in text
assert 'local-private-id' not in text and 'embedding' not in text
assert voice_speaker_context.context(hint,owner=False)==''
for bad in [{'confidence':float('nan')},{'confidence':.4},{'authentication_authority':True},{'source':'verified_voice'}]:
    assert voice_speaker_context.context({**hint,**bad},owner=True)==''
payload=ChatRequest(message='Who is speaking?',speaker_context=hint)
with patch('app.brain_api.context_chat.chat',return_value={'reply':'ok'}) as chat:
    _chat_or_http('owner',payload,owner_tools=True)
    assert chat.call_args.kwargs['speaker_context']==hint
    _chat_or_http('app:test',payload,owner_tools=False)
    assert 'speaker_context' not in chat.call_args.kwargs
assert 'owner_saved_conversation_note' in voice_speaker_context.context({**hint,'conversation_note':'Prefers brief answers'},owner=True)
assert 'Prefers brief answers' in voice_speaker_context.context({**hint,'conversation_note':'Prefers brief answers'},owner=True)
assert 'owner_saved_conversation_note' not in voice_speaker_context.context({**hint,'conversation_note':'Private'},owner=False)
assert 'David' in voice_speaker_context.context({'source':'manual_selection','display_name':'David','conversation_note':'Prefers short replies','authentication_authority':False},owner=True)
assert voice_speaker_context.context({'source':'manual_selection','display_name':'David'},owner=False)==''
print('VOICE_SPEAKER_CONTEXT=PASS')

import assert from 'node:assert/strict';
import {
  VOICE_PROFILE_MIN_SAMPLES,bestVoiceParticipantMatch,normalizeVoiceEmbedding,
  validVoiceEmbedding,voiceCosine,voiceEmbeddingFromPcm
} from '../ui/tracky/src/voice-profile-core.js';
import {participantRecord} from '../ui/tracky/src/participant-core.js';

function unit(index,size=32){
  const row=new Array(size).fill(0);row[index]=1;return row;
}
function tone(freq,rate=16000,seconds=1){
  const data=new Float32Array(rate*seconds);
  for(let i=0;i<data.length;i++){
    const t=i/rate;
    data[i]=(0.52*Math.sin(2*Math.PI*freq*t)+0.26*Math.sin(2*Math.PI*freq*2.15*t)+0.11*Math.sin(2*Math.PI*freq*3.7*t))
      *(0.75+0.25*Math.sin(2*Math.PI*3*t));
  }
  return data;
}

const extracted=voiceEmbeddingFromPcm(tone(185),16000);
assert.ok(validVoiceEmbedding(extracted),'local feature extractor must return a bounded finite vector');
assert.ok(voiceCosine(extracted,voiceEmbeddingFromPcm(tone(185),16000))>0.9999,'same waveform must produce deterministic features');
assert.equal(voiceEmbeddingFromPcm(new Float32Array(1000),16000),null,'too-short audio cannot become a profile');

const owner={id:'owner',name:'Owner',voiceRecognitionEnabled:true,voiceProfileReady:true,voiceEmbeddings:[unit(0),unit(0),unit(0)]};
const guest={id:'guest',name:'Guest',voiceRecognitionEnabled:true,voiceProfileReady:true,voiceEmbeddings:[unit(1),unit(1),unit(1)]};
let match=bestVoiceParticipantMatch(normalizeVoiceEmbedding([1,...new Array(31).fill(0)]),[owner,guest],0.90,0.045,VOICE_PROFILE_MIN_SAMPLES);
assert.equal(match.matched,true);assert.equal(match.participant.id,'owner');assert.ok(match.margin>0.9);

match=bestVoiceParticipantMatch(unit(0),[owner,{...guest,voiceEmbeddings:[unit(0),unit(0),unit(0)]}],0.90,0.045,3);
assert.equal(match.matched,false);assert.equal(match.ambiguous,true,'near-equal profiles must fail closed');

match=bestVoiceParticipantMatch(unit(0),[{...owner,voiceEmbeddings:[unit(0),unit(0)]}],0.90,0.045,3);
assert.equal(match.matched,false,'fewer than three enrollment samples cannot identify');

const sanitized=participantRecord({
  id:'p1',name:'P1',voiceEmbeddings:[unit(0),unit(0),unit(0),['bad']],
  voiceRecognitionEnabled:true,voiceProfileReady:true,
  voiceProfileSamples:[
    {capturedAt:'2026-10-04T12:00:00Z',durationMs:900,feature:'tracky-acoustic-v1',rawAudioStored:true}
  ]
});
assert.equal(sanitized.voiceEmbeddings.length,3);
assert.equal(sanitized.voiceProfileReady,true);
assert.equal(sanitized.voiceRecognitionEnabled,true);
assert.equal(sanitized.voiceProfileSamples[0].rawAudioStored,false);
assert.equal(participantRecord({id:'p2'}).voiceRecognitionEnabled,false,'voice recognition must not default on');

console.log('SPEAKER_FUSION_SECTION9C=PASS (local feature, profile and ambiguity contracts)');

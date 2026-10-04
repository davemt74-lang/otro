import {bestParticipantMatch} from './src/participant-core.js';
import {getParticipant,listParticipants,patchParticipant} from './src/participant-store.js';
import {HUMAN_ESM_URL,HUMAN_MODEL_BASE} from './src/model-config.js';
import {
  VOICE_FEATURE_VERSION,VOICE_PROFILE_MIN_SAMPLES,VOICE_PROFILE_MAX_SAMPLES,
  bestVoiceParticipantMatch,voiceEmbeddingFromPcm
} from './src/voice-profile-core.js';

const TRACKY_PREFIX='tracky:';
const VISUAL_MAX_AGE_MS=1600;
const VISUAL_HISTORY_MS=30000;
let participants=[],participantsLoadedAt=0;
let enrollment=null;
let cameraStream=null,cameraEngine=null,cameraVideo=null,cameraTimer=null,cameraGeneration=0,privacyCheckedAt=0;
let visualHistory=[];

function emit(state,message,extra={}){
  window.dispatchEvent(new CustomEvent('homeserver:speaker-fusion-status',{detail:{state,message,...extra}}));
}
function trackyIdentity(participant){return participant?.id?TRACKY_PREFIX+String(participant.id):'';}
function participantId(identity){
  const value=String(identity||'');return value.startsWith(TRACKY_PREFIX)?value.slice(TRACKY_PREFIX.length):'';
}
async function refreshParticipants(force=false){
  if(!force&&participantsLoadedAt&&Date.now()-participantsLoadedAt<3000)return participants;
  participants=await listParticipants();
  participantsLoadedAt=Date.now();
  return participants;
}
function cachedName(identity){
  const id=participantId(identity);
  const item=participants.find(p=>String(p.id)===id);
  return item?.name||item?.nickname||'';
}
function readU16(view,offset){return view.getUint16(offset,true);}
function readU32(view,offset){return view.getUint32(offset,true);}
async function pcmSlice(blob,startMs=0,endMs=null){
  const bytes=await blob.arrayBuffer(),view=new DataView(bytes);
  if(bytes.byteLength<44||readU32(view,0)!==0x46464952||readU32(view,8)!==0x45564157)throw new Error('Voice fusion requires PCM WAV audio.');
  let offset=12,format=null,dataOffset=-1,dataLength=0;
  while(offset+8<=bytes.byteLength){
    const id=readU32(view,offset),size=readU32(view,offset+4),body=offset+8;
    if(id===0x20746d66&&size>=16){
      format={audioFormat:readU16(view,body),channels:readU16(view,body+2),rate:readU32(view,body+4),bits:readU16(view,body+14)};
    }else if(id===0x61746164){dataOffset=body;dataLength=Math.min(size,bytes.byteLength-body);break;}
    offset=body+size+(size%2);
  }
  if(!format||format.audioFormat!==1||format.channels!==1||format.bits!==16||dataOffset<0)throw new Error('Voice fusion requires mono PCM16 WAV audio.');
  const total=Math.floor(dataLength/2),from=Math.max(0,Math.min(total,Math.floor(Number(startMs||0)*format.rate/1000)));
  const toMs=endMs==null?total/format.rate*1000:Number(endMs);
  const to=Math.max(from,Math.min(total,Math.ceil(toMs*format.rate/1000)));
  const samples=new Float32Array(to-from);
  for(let i=from;i<to;i++)samples[i-from]=view.getInt16(dataOffset+i*2,true)/32768;
  return {samples,rate:format.rate,durationMs:(to-from)/format.rate*1000};
}
function baseEvidence(turn){
  const a=turn?.attribution||{};
  const source=['provider_diarization','heuristic_acoustic','unknown'].includes(a.source)?a.source:'unknown';
  return {source,speaker_label:String(turn?.speaker_label||a.speaker_label||'Speaker 1').slice(0,80),
    confidence:Number(a.confidence||0),overlap:Boolean(turn?.overlap||a.overlap),
    overlap_group:String(a.overlap_group||'').slice(0,80)};
}
function nearestVisual(targetAt){
  let best=null,distance=Infinity;
  for(const item of visualHistory){
    const delta=Math.abs(Number(item.at)-Number(targetAt));
    if(delta<=VISUAL_MAX_AGE_MS&&delta<distance){best=item;distance=delta;}
  }
  return best;
}
async function enrollEmbedding(embedding,durationMs){
  if(!enrollment)return null;
  const selected=enrollment;
  const current=await getParticipant(selected.participantId);
  if(!current){enrollment=null;emit('stopped','Voice enrollment stopped because the participant was deleted.');return null;}
  const existing=(current.voiceEmbeddings||[]).filter(Array.isArray).slice(-(VOICE_PROFILE_MAX_SAMPLES-1));
  const embeddings=[...existing,Array.from(embedding)].slice(-VOICE_PROFILE_MAX_SAMPLES);
  const samples=[...(current.voiceProfileSamples||[]),{
    capturedAt:new Date().toISOString(),durationMs:Math.round(durationMs),feature:VOICE_FEATURE_VERSION,rawAudioStored:false
  }].slice(-VOICE_PROFILE_MAX_SAMPLES);
  const ready=embeddings.length>=VOICE_PROFILE_MIN_SAMPLES;
  const saved=await patchParticipant(current.id,{
    voiceEmbeddings:embeddings,voiceProfileSamples:samples,voiceProfileReady:ready,
    voiceRecognitionEnabled:true,voiceUpdatedAt:new Date().toISOString()
  });
  participantsLoadedAt=0;await refreshParticipants(true);
  selected.remaining=Math.max(0,VOICE_PROFILE_MIN_SAMPLES-embeddings.length);
  if(ready){
    enrollment=null;
    emit('ready',`${saved.name||'Participant'} voice profile ready · local feature samples only; not authentication.`,{participantId:saved.id});
  }else emit('enrolling',`Voice sample saved locally · ${selected.remaining} more clean sample${selected.remaining===1?'':'s'} needed.`,{participantId:saved.id,remaining:selected.remaining});
  return saved;
}
export async function beginVoiceEnrollment(participantIdValue){
  const rows=await refreshParticipants(true);
  const participant=rows.find(p=>String(p.id)===String(participantIdValue));
  if(!participant)throw new Error('Choose an existing local Tracky participant.');
  enrollment={participantId:participant.id,remaining:Math.max(1,VOICE_PROFILE_MIN_SAMPLES-(participant.voiceEmbeddings||[]).length)};
  emit('enrolling',`Voice enrollment armed for ${participant.name||'participant'} · speak alone for the next clean transcription chunks.`,{participantId:participant.id,remaining:enrollment.remaining});
  return {participantId:participant.id,remaining:enrollment.remaining};
}
export function cancelVoiceEnrollment(){if(enrollment){enrollment=null;emit('stopped','Voice enrollment cancelled. No raw audio was stored.');}}
export async function clearVoiceProfile(participantIdValue){
  const current=await getParticipant(participantIdValue);if(!current)throw new Error('Participant not found.');
  const saved=await patchParticipant(current.id,{voiceEmbeddings:[],voiceProfileSamples:[],voiceProfileReady:false,voiceRecognitionEnabled:false,voiceUpdatedAt:new Date().toISOString()});
  if(enrollment?.participantId===current.id)enrollment=null;
  participantsLoadedAt=0;await refreshParticipants(true);
  emit('cleared',`${saved.name||'Participant'} voice profile cleared.`,{participantId:saved.id});
  return saved;
}
export async function analyzeChunk(wav,turns,capturedAt=performance.now()){
  const rows=await refreshParticipants();
  const cleanTurns=Array.isArray(turns)?turns:[];
  const enrollmentEligible=Boolean(enrollment&&cleanTurns.length===1&&!cleanTurns[0]?.overlap);
  const output=[];
  for(const turn of cleanTurns){
    const start=Math.max(0,Number(turn.started_ms||0)),end=Math.max(start,Number(turn.ended_ms??start));
    let embedding=null,durationMs=0;
    try{
      const pcm=await pcmSlice(wav,start,end||null);durationMs=pcm.durationMs;
      embedding=voiceEmbeddingFromPcm(pcm.samples,pcm.rate);
    }catch(_){embedding=null;}
    if(enrollmentEligible&&embedding)await enrollEmbedding(embedding,durationMs);
    const evidence=[baseEvidence(turn)];
    if(embedding&&!turn.overlap){
      const match=bestVoiceParticipantMatch(embedding,await refreshParticipants());
      if(match.matched&&match.participant){
        evidence.push({source:'verified_voice',speaker_label:String(turn.speaker_label||'Speaker 1').slice(0,80),
          confidence:match.similarity,participant_identity:trackyIdentity(match.participant),observed_at:new Date().toISOString()});
      }
    }
    const midpoint=capturedAt+(start+Math.max(start,end))/2;
    const visual=nearestVisual(midpoint);
    if(visual)evidence.push({source:'visual_corroboration',speaker_label:String(turn.speaker_label||'Speaker 1').slice(0,80),
      confidence:visual.confidence,participant_identity:visual.participantIdentity,observed_at:visual.observedAt});
    output.push({...turn,speaker_evidence:evidence});
  }
  return output;
}
async function privacyClear(){
  const response=await fetch('/api/v1/control/onboarding/visual/status',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
  if(!response.ok)throw new Error('HomeServer camera privacy status is unavailable.');
  const state=await response.json();
  return state.privacy_engaged===false;
}
async function loadCameraEngine(){
  const mod=await import(HUMAN_ESM_URL),Human=mod.Human||mod.default;
  if(typeof Human!=='function')throw new Error('Pinned Tracky visual model unavailable.');
  const engine=new Human({backend:'webgl',debug:false,modelBasePath:HUMAN_MODEL_BASE,
    face:{enabled:true,detector:{enabled:true,maxDetected:4,minConfidence:0.68},mesh:{enabled:true},
      description:{enabled:true},iris:{enabled:false},emotion:{enabled:false},antispoof:{enabled:false},liveness:{enabled:false}},
    body:{enabled:false},hand:{enabled:false},object:{enabled:false},gesture:{enabled:false},segmentation:{enabled:false}});
  await engine.load();return engine;
}
async function cameraScan(token){
  if(token!==cameraGeneration||!cameraStream||!cameraVideo)return;
  try{
    if(Date.now()-privacyCheckedAt>2000){
      if(!await privacyClear())throw new Error('Camera privacy is engaged.');
      privacyCheckedAt=Date.now();
    }
    const result=await cameraEngine.detect(cameraVideo);
    if(token!==cameraGeneration||!cameraStream)return;
    const rows=await refreshParticipants();
    const matches=[];
    for(const face of Array.isArray(result?.face)?result.face:[]){
      const embedding=Array.isArray(face.embedding)?face.embedding:Array.from(face.embedding||[]);
      const match=bestParticipantMatch(embedding,rows,0.62,0.05,3);
      if(match.matched&&match.participant)matches.push(match);
    }
    const unique=new Map(matches.map(match=>[String(match.participant.id),match]));
    if(unique.size===1){
      const match=[...unique.values()][0];
      visualHistory.push({at:performance.now(),observedAt:new Date().toISOString(),
        participantIdentity:trackyIdentity(match.participant),confidence:match.similarity});
    }else if(unique.size>1){
      visualHistory.push({at:performance.now(),observedAt:new Date().toISOString(),ambiguous:true});
    }
    visualHistory=visualHistory.filter(item=>performance.now()-item.at<=VISUAL_HISTORY_MS);
  }catch(error){
    if(token===cameraGeneration){stopCameraCorroboration();emit('camera_stopped',String(error.message||'Camera corroboration stopped.'),{error:true});}
    return;
  }
  if(token===cameraGeneration&&cameraStream)cameraTimer=setTimeout(()=>{void cameraScan(token);},550);
}
export async function startCameraCorroboration(videoElement=null){
  if(cameraStream)return true;
  if(window.TrackyOwnerSelfCheck?.isActive()||window.HomeServerVisualEnrollment?.isCapturing?.()||
     window.TrackyOwnerEyes?.isActive()||window.TrackyNativeCamera?.isActive?.())
    throw new Error('Stop the other Tracky camera surface before starting transcription corroboration.');
  if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw new Error('A secure browser and camera permission are required.');
  if(!await privacyClear())throw new Error('Camera privacy is engaged.');
  await refreshParticipants(true);
  if(!participants.some(p=>p.recognitionEnabled!==false&&(p.embeddings||[]).length>=3))
    throw new Error('No enrolled local visual participant is available for corroboration.');
  const token=++cameraGeneration;
  cameraEngine=await loadCameraEngine();
  if(token!==cameraGeneration)return false;
  const stream=await navigator.mediaDevices.getUserMedia({audio:false,video:{width:{ideal:960},height:{ideal:720},facingMode:{ideal:'user'}}});
  if(token!==cameraGeneration){stream.getTracks().forEach(t=>t.stop());return false;}
  cameraStream=stream;cameraVideo=videoElement||document.createElement('video');
  cameraVideo.muted=true;cameraVideo.playsInline=true;cameraVideo.srcObject=stream;
  await cameraVideo.play();
  visualHistory=[];privacyCheckedAt=Date.now();
  emit('camera_ready','Camera corroboration active · local face similarity can only confirm or challenge a voice match.');
  void cameraScan(token);return true;
}
export function stopCameraCorroboration(){
  cameraGeneration+=1;if(cameraTimer!==null)clearTimeout(cameraTimer);cameraTimer=null;
  cameraStream?.getTracks().forEach(track=>track.stop());cameraStream=null;
  if(cameraVideo)cameraVideo.srcObject=null;cameraVideo=null;cameraEngine=null;visualHistory=[];
}
export async function profileSummary(){
  const rows=await refreshParticipants(true);
  return rows.map(p=>({id:p.id,name:p.name||p.nickname||'Unnamed participant',
    voiceReady:p.voiceProfileReady===true&&(p.voiceEmbeddings||[]).length>=VOICE_PROFILE_MIN_SAMPLES,
    voiceSamples:(p.voiceEmbeddings||[]).length,visualReady:p.recognitionEnabled!==false&&(p.embeddings||[]).length>=3}));
}
export function isCameraActive(){return Boolean(cameraStream);}
export function resolveParticipantName(identity){return cachedName(identity);}
export function enrollmentState(){return enrollment?{...enrollment}:null;}

window.HomeServerSpeakerFusion=Object.freeze({
  analyzeChunk,beginVoiceEnrollment,cancelVoiceEnrollment,clearVoiceProfile,profileSummary,
  startCameraCorroboration,stopCameraCorroboration,isCameraActive,resolveParticipantName,enrollmentState
});
window.addEventListener('tracky:visual-state-changed',()=>{participantsLoadedAt=0;});
window.addEventListener('pagehide',stopCameraCorroboration);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCameraCorroboration();});

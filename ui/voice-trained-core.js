/* Versioned, optional local speaker embeddings. Similarity is not probability. */
export const FEATURE='wavlm-sv-q8-v1';
export const MODEL='Xenova/wavlm-base-plus-sv';
export const REVISION='e61029603001bd11295c36d878698708bf59190f';
export const SDK='https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/dist/transformers.min.js';
export const MODEL_FILES=Object.freeze([
  {name:'config.json',bytes:58609,sha256:'75efe37fb015f22d82af367dcef974c39119fa4c6505d16a82d86f604a431f4e'},
  {name:'preprocessor_config.json',bytes:215,sha256:'99272fe8ccfab114b68b478681ea47ee3a1ce62bb788cb92dd6e4f69fb1f1da2'},
  {name:'onnx/model_quantized.onnx',bytes:101683453,sha256:'576bf6017796bdd179824d801b3a355a1dda2451559c6a398562175e33589f68'},
]);
export const RUNTIME_FILES=Object.freeze([
  {name:'runtime/transformers.min.js',bytes:888173,sha256:'aa5002b70e789798da263f5f99c62bd3e8fcd0c119258a493c40c180648365fa'},
  {name:'runtime/ort-wasm-simd-threaded.jsep.mjs',bytes:44484,sha256:'08fb86ec433c78bfb032c5d84a68b8e8e5a8d81268fa39e24314179a5767a5b9'},
  {name:'runtime/ort-wasm-simd-threaded.jsep.wasm',bytes:21596019,sha256:'c46655e8a94afc45338d4cb2b840475f88e5012d524509916e505079c00bfa39'},
]);
export const ASSET_FILES=Object.freeze([...MODEL_FILES,...RUNTIME_FILES]);
export const MIN_SAMPLES=3,MAX_SAMPLES=5,MATCH_THRESHOLD=.90,MATCH_MARGIN=.045;
export function unitVector(value){
  if(!value||value.length!==512)return null;
  const a=Array.from(value);if(a.some(x=>typeof x!=='number'||!Number.isFinite(x)))return null;
  const scale=Math.max(...a.map(Math.abs));if(scale<1e-9)return null;
  const norm=Math.sqrt(a.reduce((s,x)=>s+(x/scale)**2,0));return a.map(x=>x/scale/norm);
}
export function cleanProfile(value){
  if(value?.feature!==FEATURE||value?.revision!==REVISION||typeof value.consentedAt!=='string'||!Number.isFinite(Date.parse(value.consentedAt)))return null;
  const embeddings=(Array.isArray(value.embeddings)?value.embeddings:[]).slice(-MAX_SAMPLES).map(unitVector).filter(Boolean);
  return {feature:FEATURE,revision:REVISION,consentedAt:value.consentedAt,embeddings,rawAudioStored:false};
}
export function profileMatch(vector,people){
  const v=unitVector(vector);if(!v)return {matched:false,reason:'invalid-embedding'};
  const ranked=[];
  for(const person of people||[]){
    const p=cleanProfile(person.voiceTrainedProfile);if(!p||p.embeddings.length<MIN_SAMPLES)continue;
    const scores=p.embeddings.map(r=>Math.max(-1,Math.min(1,r.reduce((s,x,i)=>s+x*v[i],0)))).sort((a,b)=>b-a).slice(0,3);
    ranked.push({person,similarity:scores.reduce((s,x)=>s+x,0)/scores.length});
  }
  ranked.sort((a,b)=>b.similarity-a.similarity);const best=ranked[0],margin=best?best.similarity-(ranked[1]?.similarity||0):0;
  const matched=!!best&&best.similarity>=MATCH_THRESHOLD&&margin>=MATCH_MARGIN;
  return {matched,participant:matched?best.person:null,similarity:best?.similarity||0,margin,reason:matched?'matched':best?.similarity>=MATCH_THRESHOLD?'ambiguous':'unknown'};
}
export function assessPcm(input,rate,{enrollment=false,overlap=false}={}){
  if(overlap)return {ok:false,reason:'overlap',message:'Overlapping speech cannot be used for voice enrollment or identification.'};
  if(!(input instanceof Float32Array)||!Number.isFinite(rate)||rate<8000||rate>96000||input.length>rate*12||input.some(x=>!Number.isFinite(x)))return {ok:false,reason:'invalid-audio',message:'Voice input is invalid or too long.'};
  const seconds=input.length/rate;if(seconds<(enrollment?3:1.5))return {ok:false,reason:'too-short',message:enrollment?'Speak alone for at least three seconds.':'Speech is too short for a trained voice match.'};
  let power=0,clipped=0;const frames=[];const step=Math.max(1,Math.round(rate*.02));
  for(let at=0;at<input.length;at+=step){let sum=0,n=Math.min(step,input.length-at);for(let i=at;i<at+n;i++){const x=input[i];sum+=x*x;if(Math.abs(x)>=.995)clipped++;}power+=sum;frames.push(Math.sqrt(sum/n));}
  const rms=Math.sqrt(power/input.length),sorted=frames.slice().sort((a,b)=>a-b),floor=sorted[Math.floor(sorted.length*.15)]||0,peak=sorted[Math.floor(sorted.length*.85)]||0;
  if(clipped/input.length>.01)return {ok:false,reason:'clipping',message:'The microphone is clipping. Move back or reduce its level.'};
  if(rms<.008||peak<.012)return {ok:false,reason:'quiet',message:'Voice input is too quiet for a reliable profile.'};
  // This is an audio quality check, not an overlapping-speaker detector.
  if(enrollment&&peak<floor*1.35)return {ok:false,reason:'steady-noise',message:'Use a clean spoken phrase without steady background noise.'};
  return {ok:true,seconds,rms,clippedFraction:clipped/input.length};
}
export function resample16k(input,rate){
  const out=new Float32Array(Math.round(input.length*16000/rate));
  for(let i=0;i<out.length;i++){const at=i*rate/16000,lo=Math.floor(at),part=at-lo;out[i]=input[Math.min(lo,input.length-1)]*(1-part)+input[Math.min(lo+1,input.length-1)]*part;}
  return out;
}

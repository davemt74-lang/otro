export const VOICE_FEATURE_VERSION='tracky-acoustic-v1';
export const VOICE_MATCH_THRESHOLD=0.90;
export const VOICE_MATCH_MARGIN=0.045;
export const VOICE_PROFILE_MIN_SAMPLES=3;
export const VOICE_PROFILE_MAX_SAMPLES=5;

const FREQUENCIES=[120,180,260,360,500,700,950,1300,1750,2350,3150,4200,5500,6800];

function finite(value){return Number.isFinite(Number(value));}
export function validVoiceEmbedding(value,length=null){
  return Array.isArray(value)&&value.length>=16&&value.length<=64
    &&(length===null||value.length===length)&&value.every(finite);
}
export function normalizeVoiceEmbedding(value){
  if(!Array.isArray(value))return null;
  const vector=value.map(Number);
  if(!vector.length||vector.some(v=>!Number.isFinite(v)))return null;
  const norm=Math.sqrt(vector.reduce((sum,v)=>sum+v*v,0));
  if(!Number.isFinite(norm)||norm<1e-9)return null;
  return vector.map(v=>v/norm);
}
export function voiceCosine(a,b){
  if(!validVoiceEmbedding(a)||!validVoiceEmbedding(b,a.length))return -1;
  let dot=0,aa=0,bb=0;
  for(let i=0;i<a.length;i++){dot+=a[i]*b[i];aa+=a[i]*a[i];bb+=b[i]*b[i];}
  if(aa<=0||bb<=0)return -1;
  return Math.max(-1,Math.min(1,dot/Math.sqrt(aa*bb)));
}
function goertzel(samples,start,length,rate,frequency){
  const omega=2*Math.PI*frequency/rate,coeff=2*Math.cos(omega);
  let s0=0,s1=0,s2=0;
  for(let i=0;i<length;i++){
    const window=0.54-0.46*Math.cos(2*Math.PI*i/(length-1));
    s0=samples[start+i]*window+coeff*s1-s2;s2=s1;s1=s0;
  }
  return Math.max(1e-12,s1*s1+s2*s2-coeff*s1*s2);
}
function frameFeatures(samples,start,size,rate){
  let energy=0,zcr=0,last=samples[start];
  for(let i=0;i<size;i++){
    const value=samples[start+i];energy+=value*value;
    if(i&&((value>=0)!==(last>=0)))zcr+=1;last=value;
  }
  const rms=Math.sqrt(energy/size);
  if(rms<0.006)return null;
  const logs=FREQUENCIES.filter(f=>f<rate*0.47).map(f=>Math.log1p(goertzel(samples,start,size,rate,f)));
  const mean=logs.reduce((a,b)=>a+b,0)/logs.length;
  const centered=logs.map(v=>v-mean);
  centered.push(Math.log1p(rms*100),zcr/size);
  return centered;
}
export function voiceEmbeddingFromPcm(input,sampleRate=16000){
  if(!Number.isFinite(sampleRate)||sampleRate<8000||sampleRate>96000)return null;
  const samples=input instanceof Float32Array?input:Float32Array.from(input||[]);
  if(samples.length<Math.round(sampleRate*0.45))return null;
  const maxSamples=Math.min(samples.length,Math.round(sampleRate*12));
  const size=512;
  if(maxSamples<size*3)return null;
  const span=maxSamples-size;
  const targetFrames=28;
  const step=Math.max(1,Math.floor(span/Math.max(1,targetFrames-1)));
  const frames=[];
  for(let start=0;start+size<=maxSamples&&frames.length<targetFrames;start+=step){
    const features=frameFeatures(samples,start,size,sampleRate);
    if(features)frames.push(features);
  }
  if(frames.length<4)return null;
  const width=frames[0].length;
  const means=new Array(width).fill(0);
  for(const row of frames)for(let i=0;i<width;i++)means[i]+=row[i]/frames.length;
  const deviations=new Array(width).fill(0);
  for(const row of frames)for(let i=0;i<width;i++){
    const delta=row[i]-means[i];deviations[i]+=delta*delta/frames.length;
  }
  const vector=[...means,...deviations.map(Math.sqrt)];
  return normalizeVoiceEmbedding(vector);
}
export function robustVoiceSimilarity(embedding,references,topK=3){
  const scores=(references||[]).filter(v=>validVoiceEmbedding(v,embedding?.length))
    .map(v=>voiceCosine(embedding,v)).filter(Number.isFinite).sort((a,b)=>b-a);
  if(!scores.length)return -1;
  const count=Math.min(Math.max(1,topK),scores.length);
  return scores.slice(0,count).reduce((a,b)=>a+b,0)/count;
}
export function bestVoiceParticipantMatch(
  embedding,participants,threshold=VOICE_MATCH_THRESHOLD,minMargin=VOICE_MATCH_MARGIN,minSamples=VOICE_PROFILE_MIN_SAMPLES
){
  if(!validVoiceEmbedding(embedding))return {matched:false,participant:null,similarity:0,secondSimilarity:0,margin:0,ambiguous:false};
  const candidates=[];
  for(const participant of participants||[]){
    if(participant?.voiceRecognitionEnabled===false||participant?.voiceProfileReady!==true)continue;
    const references=(participant.voiceEmbeddings||[]).filter(v=>validVoiceEmbedding(v,embedding.length));
    if(references.length<minSamples)continue;
    candidates.push({participant,similarity:robustVoiceSimilarity(embedding,references)});
  }
  candidates.sort((a,b)=>b.similarity-a.similarity);
  const best=candidates[0]||null,second=candidates[1]||null;
  const secondSimilarity=second?.similarity||0;
  const margin=best?best.similarity-secondSimilarity:0;
  const matched=Boolean(best&&best.similarity>=threshold&&margin>=minMargin);
  return {matched,participant:matched?best.participant:null,similarity:best?.similarity||0,
    secondSimilarity,margin,ambiguous:Boolean(best&&best.similarity>=threshold&&margin<minMargin)};
}

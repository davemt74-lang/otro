import {assessPcm,resample16k,FEATURE} from './voice-trained-core.js';
export {cleanProfile,profileMatch,FEATURE,MIN_SAMPLES} from './voice-trained-core.js';
export function createTrainedVoiceClient({onProgress=()=>{}}={}){
  let worker=null,sequence=0,ready=false;const pending=new Map();
  function cancel(){ready=false;worker?.terminate();worker=null;for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('Voice inference cancelled.'));}pending.clear();}
  function start(){
    if(worker)return;worker=new Worker(new URL('./voice-trained-worker.js?v=v1346',import.meta.url),{type:'module'});
    worker.onmessage=({data})=>{const p=pending.get(data?.id);if(!p)return;if(data.progress){onProgress(String(data.progress));return;}clearTimeout(p.timer);pending.delete(data.id);if(data.error)p.reject(Error(data.error));else{if(data.result?.ready)ready=true;p.resolve(data.result);}};
    worker.onerror=()=>cancel();
  }
  function request(action,extra={},timeout=8000){
    start();const id=++sequence;
    return new Promise((resolve,reject)=>{const timer=setTimeout(()=>{pending.delete(id);cancel();reject(Error('Local voice inference timed out. Your microphone controls remain available.'));},timeout);pending.set(id,{resolve,reject,timer,action});worker.postMessage({id,action,...extra},extra.pcm?[extra.pcm.buffer]:[]);});
  }
  return {
    install:source=>request('install',{source},180000),
    prepare:()=>request('ready',{},30000),
    async extract(pcm,rate,options={}){const quality=assessPcm(pcm,rate,options);if(!quality.ok)return {quality};if(!ready)await request('ready',{},30000);return {...await request('infer',{pcm:resample16k(pcm,rate)}),quality};},
    cancel,cancelInference:()=>{if([...pending.values()].some(p=>p.action!=='install'))cancel();},isReady:()=>ready,feature:FEATURE,
  };
}

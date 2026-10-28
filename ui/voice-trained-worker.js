import {FEATURE,MODEL,REVISION,SDK,MODEL_FILES,ASSET_FILES,unitVector} from './voice-trained-core.js';
const CACHE='vp3-trained-voice-'+FEATURE;
const key=name=>new URL('./voice-model-cache/'+FEATURE+'/wavlm/'+name,import.meta.url).href;
let model=null,processor=null,loading=null;
const progress=(id,message)=>self.postMessage({id,progress:message});
async function verified(response,file){
  if(!response?.ok)throw Error('Voice model component is unavailable. Install it and retry.');
  const advertised=Number(response.headers.get('Content-Length'));if(advertised&&advertised!==file.bytes)throw Error('Voice model size mismatch.');
  const reader=response.body.getReader();let size=0;const chunks=[];
  for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>file.bytes){await reader.cancel();throw Error('Voice model exceeds its pinned size.');}chunks.push(value);}
  if(size!==file.bytes)throw Error('Voice model download is incomplete.');
  const bytes=new Uint8Array(size);let at=0;for(const chunk of chunks){bytes.set(chunk,at);at+=chunk.length;}
  const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
  if(hash!==file.sha256)throw Error('Voice model checksum failed. The component was not enabled.');
  return new Response(bytes,{headers:{'Content-Type':file.name.endsWith('.json')?'application/json':'application/octet-stream','Content-Length':String(size)}});
}
async function install(id,source){
  if(source!=='homeserver'&&source!=='cloud')throw Error('Invalid voice model source.');
  const cache=await caches.open(CACHE);const estimate=await navigator.storage?.estimate?.();
  if(estimate?.quota&&estimate.quota-estimate.usage<150*1024*1024&&!await cache.match(key(MODEL_FILES[2].name)))throw Error('Free at least 150 MB of browser storage for the voice component.');
  for(const file of ASSET_FILES){
    const cached=await cache.match(key(file.name));
    if(cached){try{await verified(cached,file);continue;}catch(_){await cache.delete(key(file.name));}}
    progress(id,'Downloading '+file.name+'…');
    const url=source==='homeserver'?'/api/v1/control/voice/profile-model/'+file.name:file.name.startsWith('runtime/')?new URL(file.name.slice(8),SDK).href:'https://huggingface.co/'+MODEL+'/resolve/'+REVISION+'/'+file.name;
    const response=await fetch(url,{credentials:source==='homeserver'?'same-origin':'omit',cache:file.name.startsWith('runtime/')?'force-cache':'no-store',redirect:'follow'});
    await cache.put(key(file.name),await verified(response,file));
  }
  await cache.put(key('source'),new Response(source));
}
async function ready(id){
  if(model)return;if(loading)return loading;
  loading=(async()=>{
    const cache=await caches.open(CACHE);
    const runtime={};
    for(const file of ASSET_FILES){progress(id,'Verifying '+file.name+'…');const checked=await verified(await cache.match(key(file.name)),file);if(file.name.startsWith('runtime/'))runtime[file.name.slice(8)]=await checked.blob();}
    progress(id,'Starting local voice inference…');
    const source=await(await cache.match(key('source')))?.text();if(!['homeserver','cloud'].includes(source))throw Error('Voice component installation is incomplete.');
    // Execute only bytes whose digest was verified, including the WASM loader.
    const scriptUrl=URL.createObjectURL(new Blob([runtime['transformers.min.js']],{type:'text/javascript'}));
    const wasmLoader=URL.createObjectURL(new Blob([runtime['ort-wasm-simd-threaded.jsep.mjs']],{type:'text/javascript'}));
    const wasmBinary=URL.createObjectURL(new Blob([runtime['ort-wasm-simd-threaded.jsep.wasm']],{type:'application/wasm'}));
    const {AutoProcessor,AutoModelForXVector,env}=await import(scriptUrl);
    env.allowLocalModels=true;env.allowRemoteModels=false;env.useBrowserCache=false;env.useFSCache=false;env.useCustomCache=true;
    env.localModelPath=new URL('./voice-model-cache/'+FEATURE+'/',import.meta.url).href;
    env.customCache={match:async request=>{const url=String(request);for(const file of MODEL_FILES)if(url===key(file.name))return (await cache.match(url))||new Response(null,{status:404});return new Response(null,{status:404});},put:async()=>{}};
    env.backends.onnx.wasm.numThreads=1;env.backends.onnx.wasm.proxy=false;
    env.backends.onnx.wasm.wasmPaths={mjs:wasmLoader,wasm:wasmBinary};
    const opts={local_files_only:true};
    processor=await AutoProcessor.from_pretrained('wavlm',opts);
    model=await AutoModelForXVector.from_pretrained('wavlm',{...opts,dtype:'q8',device:'wasm'});
  })();
  try{await loading;}catch(error){loading=null;throw error;}
}
let serial=Promise.resolve();
self.onmessage=event=>{
  const message=event.data;
  serial=serial.catch(()=>{}).then(async()=>{
    const {id,action}=message||{};if(!Number.isSafeInteger(id))return;
    try{
      if(action==='install'){await install(id,message.source);await ready(id);self.postMessage({id,result:{ready:true,feature:FEATURE}});}
      else if(action==='ready'){await ready(id);self.postMessage({id,result:{ready:true,feature:FEATURE}});}
      else if(action==='infer'){
        const pcm=message.pcm;if(!(pcm instanceof Float32Array)||pcm.length<24000||pcm.length>192000||pcm.some(x=>!Number.isFinite(x)))throw Error('Invalid voice inference input.');
        await ready(id);const at=performance.now(),output=await model(await processor(pcm)),embedding=unitVector(output.embeddings?.data);
        if(!embedding)throw Error('Voice model returned an invalid embedding.');
        self.postMessage({id,result:{embedding,feature:FEATURE,elapsedMs:Math.round(performance.now()-at)}});
      }else throw Error('Unknown voice inference request.');
    }catch(error){self.postMessage({id,error:String(error.message||'Local voice inference failed.')});}
  });
};

import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync('ui/connected-apps-v029.js','utf8');
const nodes=new Map(),listeners=new Map(),writes=[];
function node(id){if(!nodes.has(id))nodes.set(id,{dataset:{},innerHTML:'',textContent:'',addEventListener(){}});return nodes.get(id);}
let allowed=false;
const fileInput={dataset:{permission:'files.read',original:'0'},checked:false};
const document={getElementById:node,querySelector:()=>null,createElement:()=>({dataset:{}}),head:{appendChild(){}},addEventListener:(name,fn)=>listeners.set(name,fn),querySelectorAll:()=>[fileInput]};
const window={flash(){},state:{}};
const context={document,window,console,Date,fetch:async(path,options={})=>{
  if(options.method==='PUT'){
    writes.push({path,body:JSON.parse(options.body)});allowed=JSON.parse(options.body).allowed;
    return {ok:true,json:async()=>({updated:true})};
  }
  return {ok:true,json:async()=>({apps:[{id:7,name:'VP3',app_key:'vp3',status:'active',permissions:allowed?[{permission:'files.read',allowed:true}]:[],scope:{},scope_summary:{},recent_activity:[]}],pending:[],counts:{active:1},available_permissions:['files.read','memory.read']})};
}};
vm.runInNewContext(source,context);
await new Promise(resolve=>setImmediate(resolve));
assert.match(node('appsList').innerHTML,/data-permission="files.read"[^>]*>/);
assert.doesNotMatch(node('appsList').innerHTML,/data-permission="files.read"[^>]* checked/);
assert.equal(writes.length,0,'Rendering must never grant file access');
fileInput.checked=true;
const button={dataset:{v029SavePermissions:'7'},disabled:false};
await listeners.get('click')({target:{closest:selector=>selector==='[data-v029-save-permissions]'?button:null}});
assert.deepEqual(writes,[{path:'/api/v1/control/apps/7/permission',body:{permission:'files.read',allowed:true}}]);
assert.match(node('appsList').innerHTML,/data-permission="files.read"[^>]* checked/);
assert.equal(button.disabled,false);
console.log('PASS files.read is visible, remains ungranted until Save permissions, and uses the existing owner permission route');

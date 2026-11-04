'use strict';
// Execute the real UI response handler with an offline HTTP response, no browser/cloud.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/app.js'),'utf8');
const clear=source.slice(source.indexOf('function clearModels()'),source.indexOf('\nfunction updateModelRemovalButtons()',source.indexOf('function clearModels()')));
const handler=source.slice(source.indexOf('async function refreshModels('),source.indexOf('\nfunction clearGallery()',source.indexOf('async function refreshModels(')));
(async()=>{
  const summary={textContent:''},status={textContent:''},free={textContent:'old value'};
  const elements={'model-files':{open:true,querySelector:()=>summary},'model-files-status':status,'model-free-space':free,'model-files-list':{replaceChildren:()=>{}}};
  const sandbox={console,Map,Set,String,Number,Boolean,Promise,
    $:id=>elements[id],cloud:{instance:{id:42,status:'stopped'}},identityConfirmed:()=>true,
    modelsFetch:null,modelsInstance:undefined,modelsVersion:0,
    clearModelNotices:()=>{},modelRemoving:null,modelRows:new Map([['h3',{lastKnown:true}]]),updateModelRemovalButtons:()=>{},
    fetch:async()=>({ok:true,json:async()=>({instance_id:42,models:[],message:'Start the GPU to view its downloaded models.'})}),
  };
  vm.createContext(sandbox);vm.runInContext(clear+'\n'+handler,sandbox);await sandbox.refreshModels(true);
  assert.match(summary.textContent,/unavailable/);assert.doesNotMatch(summary.textContent,/0 installed/);
  assert.equal(status.textContent,'Start the GPU to view its downloaded models.');assert.equal(free.textContent,'');
  assert.equal(sandbox.modelRows.size,1,'Offline response must not erase the last known rows');
  // An old in-flight reply must not restore rows after destroy/new instance.
  let release;
  sandbox.fetch=()=>new Promise(resolve=>{release=resolve;});
  const pending=sandbox.refreshModels(true);
  sandbox.cloud.instance={id:43,status:'loading'};
  sandbox.clearModels();
  release({ok:true,json:async()=>({instance_id:42,models:[],message:'Old rental is offline.'})});
  await pending;
  assert.equal(sandbox.modelRows.size,0,'New rental must not inherit old model rows');
  assert.equal(summary.textContent,'Model preparation and files');
  assert.doesNotMatch(status.textContent,/Old rental/);
  console.log('Offline inventory: unknown same-instance files retained; instance change clears rows and rejects late old responses.');
})().catch(error=>{console.error(error);process.exitCode=1;});

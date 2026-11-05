'use strict';
// Real UI submit/status handlers against a tiny public HTTP fixture. No inference.
const assert=require('node:assert/strict'),fs=require('node:fs'),http=require('node:http'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[2]||require('node:path').join(__dirname,'../app/app.js'),'utf8');
const JOB='11111111-1111-4111-8111-111111111111',CLIENT='22222222-2222-4222-8222-222222222222';
let posts=0,gets=0,remoteComplete=false,resolveFirst,releaseFirst;
const firstSeen=new Promise(resolve=>resolveFirst=resolve);
const server=http.createServer((request,response)=>{
  if(request.url==='/api/generate'&&request.method==='POST'){
    posts++;let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{
      const data=JSON.parse(body);assert.equal(data.request_id,JOB);assert.equal(data.client_id,CLIENT);
      remoteComplete=true;request.socket.destroy(); // Accepted and finished, but reply lost.
    });return;
  }
  assert.equal(request.method,'GET');assert.match(request.url,/^\/api\/jobs\//);gets++;
  if(gets===1){releaseFirst=()=>{if(!response.destroyed)response.end(JSON.stringify({state:'unknown'}));};resolveFirst();return;}
  response.setHeader('Content-Type','application/json');response.end(JSON.stringify({state:remoteComplete?'complete':'unknown',videos:remoteComplete?['/video/public/0']:[]}));
});
const storage=new Map(),elements={model:{value:'turbowan'},batch:{value:'1'}};
const timers=new Map();let timerID=0,click,budget=0,gallery=0;
const context={Map,Set,String,Number,Boolean,Promise,AbortController,
  JOBS_KEY:'public-job-recovery',DEFAULT_MODEL:'turbowan',TERMINAL:new Set(['complete','failed','not_ready','cancelled']),
  jobs:[],cloud:{instance:{id:42,status:'running'},operation:{state:'idle'}},sessions:new Map(),cards:new Map(),
  posting:false,available:true,MODELS:{turbowan:{}},promptInput:{value:'A public red ball fixture',focus:()=>{}},
  $:id=>elements[id],button:{addEventListener:(event,handler)=>click=handler},
  localStorage:{setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
  uuid:(()=>{let ids=[JOB,CLIENT];return()=>ids.shift();})(),settings:()=>({model:'turbowan'}),operationBusy:()=>false,
  updateButton:()=>{},renderJob:()=>{},stage:()=>{},queueSummary:()=>{},connectEvents:()=>{},
  refreshCloud:()=>budget++,refreshGallery:()=>gallery++,refreshModels:()=>{},
  setTimeout:(handler,delay)=>{assert.equal(delay,15000);timers.set(++timerID,handler);return timerID;},clearTimeout:id=>timers.delete(id),
};
vm.createContext(context);
const part=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
vm.runInContext(part('function storedRemove(','const active =')+'\n'+part('const active =','const identityConfirmed =')+'\n'+part('function sessionFor(','function connectEvents(')+'\n'+part('function applyState(','async function cancelJob(')+'\n'+part('async function refreshJob(','function reconcileInstance()')+'\n'+part("button.addEventListener('click', async () => {","for (const id of ['duration'"),context);
const tick=()=>new Promise(setImmediate);
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base=`http://127.0.0.1:${server.address().port}`;
  context.fetch=(url,options)=>fetch(base+url,options);
  await click();await firstSeen;
  assert.equal(posts,1);assert.equal(context.jobs[0].state,'unknown');assert.equal(context.sessions.get(JOB).refreshing,true);
  const waiting=context.jobs[0];await context.refreshJob(waiting,true);releaseFirst();await tick();
  assert.equal(posts,1,'Recovery must never resubmit the lost reply');assert.equal(gets,2);
  assert.equal(context.jobs.length,0,'The completed request leaves the active UI');assert.equal(storage.has(context.JOBS_KEY),false);
  assert.equal(gallery,1);assert.equal(budget,1);assert.equal(timers.size,0);
  // A stale old GET must not overwrite a newer running result, even if abort is ignored.
  const next={id:JOB,client_id:CLIENT,instance_id:42,state:'unknown',config:{model:'turbowan'}};context.jobs=[next];
  let finishOld,oldSignal;
  context.fetch=(url,options)=>{oldSignal=options.signal;return new Promise(resolve=>finishOld=resolve);};
  const old=context.refreshJob(next);await tick();
  context.fetch=async()=>({ok:true,json:async()=>({state:'running'})});await context.refreshJob(next,true);
  assert.equal(oldSignal.aborted,true);finishOld({ok:true,json:async()=>({state:'unknown'})});await old;
  assert.equal(next.state,'running');assert.equal(context.sessions.get(JOB).refreshing,false);
  // The bounded timeout unlocks periodic recovery; missing metadata stays uncertain.
  context.fetch=(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Public timeout','AbortError'))));
  const timeout=context.refreshJob(next);assert.equal(timers.size,1);[...timers.values()][0]();await timeout;
  assert.equal(context.sessions.get(JOB).refreshing,false);
  context.fetch=async()=>({ok:true,json:async()=>({state:'unknown'})});await context.refreshJob(next);
  assert.equal(next.state,'unknown');assert.equal(context.jobs.length,1);assert.equal(posts,1);assert.equal(timers.size,0);
  console.log('Interrupted submit: existing completion recovered with one POST; hung status replaced on return; timeout unlocks retries; stale replies fenced; genuinely unknown requests retained. Public HTTP fixture only.');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>{if(releaseFirst)releaseFirst();server.closeAllConnections();server.close();});

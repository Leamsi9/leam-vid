'use strict';
// Public fixtures exercise actual browser storage/state handlers; no cloud or media.
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(process.argv[2] || require('node:path').join(__dirname, '../app/app.js'), 'utf8');
const terminal = new Set(['complete','failed','not_ready','cancelled']);
const fixture = (id, state) => ({id, client_id:`client-${id}`, instance_id:42, state, config:{model:'turbowan'}});
const storage = new Map([['public-jobs', JSON.stringify([
  ...[...terminal].map(state => fixture(state,state)),
  ...['running','queued','unknown','disconnected'].map(state => fixture(state,state)),
])]]);
const elements = Object.fromEntries(['jobs-section','active-jobs-heading','queued-jobs-section','queue-summary','queued-jobs-count'].map(id => [id,{hidden:false,textContent:''}]));
const sandbox = {
  jobs:[], JOBS_KEY:'public-jobs', TERMINAL:terminal, Map, Set, String, Number,
  localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
  sessions:new Map(), cards:new Map(), cloud:{instance:{id:42,status:'running'},operation:{state:'idle'}},
  $:id=>{assert.ok(elements[id],`Unexpected UI section: ${id}`);return elements[id];},
  clearTimeout:()=>{}, renderJob:()=>sandbox.queueSummary(), stage:()=>{}, updateButton:()=>{}, updateModelRemovalButtons:()=>{},
  refreshCloud:()=>{sandbox.budgetRefreshes++;},refreshGallery:()=>{sandbox.galleryRefreshes++;},refreshModels:()=>{},
  budgetRefreshes:0,galleryRefreshes:0,
};
vm.createContext(sandbox);
const initial = source.slice(source.indexOf('try { const saved = JSON.parse('),source.indexOf('const active ='));
const active = source.slice(source.indexOf('const active ='),source.indexOf('const identityConfirmed ='));
const summary = source.slice(source.indexOf('function queueSummary()'),source.indexOf('function renderJob('));
const sessions = source.slice(source.indexOf('function sessionFor('),source.indexOf('function connectEvents('));
const apply = source.slice(source.indexOf('function applyState('),source.indexOf('async function cancelJob('));
vm.runInContext(initial+'\n'+active+'\n'+summary+'\n'+sessions+'\n'+apply,sandbox);
assert.deepEqual(Array.from(sandbox.jobs,job=>job.state),['running','queued','unknown','disconnected']);
assert.deepEqual(JSON.parse(storage.get('public-jobs')).map(job=>job.state),['running','queued','unknown','disconnected']);
for (const state of terminal) {
  const job=fixture(`public-${state}`,'running'); sandbox.jobs=[job];
  let removed=0,closed=0;
  sandbox.cards.set(job.id,{remove:()=>removed++});
  sandbox.sessions.set(job.id,{timer:null,socket:{close:()=>closed++,onclose:()=>{}}});
  const galleryBefore=sandbox.galleryRefreshes;
  sandbox.applyState(job,{state});
  assert.equal(sandbox.jobs.length,0);assert.equal(sandbox.cards.has(job.id),false);assert.equal(sandbox.sessions.has(job.id),false);
  assert.equal(removed,1);assert.equal(closed,1);assert.equal(storage.has('public-jobs'),false);
  assert.equal(elements['jobs-section'].hidden,true);
  assert.equal(sandbox.galleryRefreshes-galleryBefore,state==='not_ready'?0:1);
  sandbox.applyState(job,{state:'queued'});
  assert.equal(sandbox.jobs.length,0,'A late reply cannot revive a retired job');
}
const uncertain=fixture('public-uncertain','queued');sandbox.jobs=[uncertain];
sandbox.applyState(uncertain,{state:'unknown'});
assert.equal(sandbox.jobs.length,1);assert.equal(JSON.parse(storage.get('public-jobs'))[0].state,'unknown');
sandbox.applyState(uncertain,{state:'disconnected'});
assert.equal(sandbox.jobs.length,1,'A connection failure must retain recovery identity');
sandbox.applyState(uncertain,{state:'queued'});
assert.equal(elements['queued-jobs-count'].textContent,'1 queued video');assert.equal(elements['queued-jobs-section'].hidden,false);
sandbox.applyState(uncertain,{state:'running'});
assert.equal(elements['queued-jobs-section'].hidden,true);assert.equal(elements['active-jobs-heading'].hidden,false);
assert.equal(sandbox.budgetRefreshes,3);
console.log('Active jobs only: terminal storage cleanup, card/socket retirement, completion gallery refresh, late-reply rejection, unknown/reconnect retention, queued-to-running UI.');

'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[2]||require('node:path').join(__dirname,'../app/app.js'),'utf8');
const code=source.slice(source.indexOf('class VideoReplayCache {'),source.indexOf('const videoReplayCache ='));
const sandbox={fetch,URL,Date,setTimeout,clearTimeout,AbortController,DOMException};
vm.createContext(sandbox);vm.runInContext(code+'\nthis.Cache = VideoReplayCache;',sandbox);
const ttl=24*60*60*1000,item={id:'public-video',url:'/api/videos/public-video'};
let time=0,fetches=0,created=0,revoked=[],released=[],timerId=0;
const timers=new Map();
const cache=new sandbox.Cache({
  fetcher:async()=>{fetches++;return new Response(new Blob(['public synthetic fixture'],{type:'video/mp4'}));},
  urlApi:{createObjectURL:()=>`blob:public/${++created}`,revokeObjectURL:url=>revoked.push(url)},now:()=>time,
  schedule:(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;},cancel:id=>timers.delete(id),
});
const release=reason=>released.push(reason);
(async()=>{
  cache.setInstance('public-gpu');
  assert.equal(fetches,0,'The gallery must not preload media');
  const first=cache.load('public-gpu',item,release),concurrent=cache.load('public-gpu',item,release);
  assert.equal(first,concurrent,'Concurrent opens share one transfer');
  const entry=await first;assert.equal(fetches,1);assert.equal(timers.get(entry.timer).delay,ttl);
  time=ttl-1;assert.equal(cache.peek('public-gpu',item.id),entry);
  assert.equal(await cache.load('public-gpu',item,release),entry);assert.equal(fetches,1,'Replay/seek use the same buffer');
  cache.setInstance('public-gpu');assert.equal(cache.peek('public-gpu',item.id),entry,'Reconnect/Stop same instance retain replay');
  time=ttl;cache.prune();assert.equal(cache.peek('public-gpu',item.id),null);assert.deepEqual(revoked,['blob:public/1']);assert.deepEqual(released,['expired']);
  await cache.load('public-gpu',item,release);assert.equal(fetches,2);
  cache.remove('public-gpu',item.id);assert.equal(cache.entries.size,0);assert.equal(released.at(-1),'deleted');assert.equal(revoked.length,2);
  await cache.load('public-gpu',item,release);cache.clear('destroyed');assert.equal(cache.entries.size,0);assert.equal(released.at(-1),'destroyed');assert.equal(revoked.length,3);assert.equal(timers.size,0);
  await cache.load('public-gpu',item,release);cache.setInstance('different-gpu');assert.equal(cache.entries.size,0);assert.equal(revoked.length,4);
  await assert.rejects(cache.load('public-gpu',item,release),error=>error.name==='AbortError');
  // Simulate an HTTP body completing after cancellation even if the transport ignores abort.
  let finishBody,signal;
  cache.fetcher=async(url,options)=>{signal=options.signal;return {status:200,blob:()=>new Promise(resolve=>{finishBody=resolve;})};};
  const late=cache.load('different-gpu',item,release);
  await new Promise(setImmediate);cache.clear('destroyed');assert.equal(signal.aborted,true);
  finishBody(new Blob(['late public fixture']));await assert.rejects(late,error=>error.name==='AbortError');
  assert.equal(cache.entries.size,0);assert.equal(created,4,'A late response cannot recreate a revoked URL');
  // An old failure must not remove a newer same-ID download.
  let finishOld;
  cache.fetcher=async()=>({status:200,blob:()=>new Promise(resolve=>{finishOld=resolve;})});
  const old=cache.load('different-gpu',item,release);await new Promise(setImmediate);cache.remove('different-gpu',item.id);
  cache.fetcher=async()=>new Response(new Blob(['fresh public fixture']));
  const replacement=await cache.load('different-gpu',item,release);
  finishOld(new Blob(['old public fixture']));await assert.rejects(old,error=>error.name==='AbortError');
  assert.equal(cache.peek('different-gpu',item.id),replacement);
  cache.clear();assert.equal(created,revoked.length);assert.equal(timers.size,0);
  cache.fetcher=async()=>new Response('public failure',{status:503});
  await assert.rejects(cache.load('different-gpu',item,release));assert.equal(cache.entries.size,0);
  console.log('Replay cache: lazy single-fetch replay, full 24h retention, expiry/delete/destroy/replacement revocation, same-instance retention, abort/late-response fencing, retry after failure. No persistent storage or cloud/media access.');
})().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
const $ = id => document.getElementById(id);
const promptInput = $('prompt'), button = $('generate');
const JOBS_KEY = 'leam-vid-jobs-v1';
const DEFAULT_MODEL = 'turbowan';
const MODEL_NAMES = {
  fasth3_v2:'FastH3 V2 NVFP4',
  fasth3_trim:'FastH3 Trim NVFP4', turbowan:'TurboWan 2.1 1.3B', h3:'MiniMax H3 Turbo',
};
let MODELS = {}, catalogSignature = '';
const TERMINAL = new Set(['complete', 'failed', 'not_ready', 'cancelled']);
let jobs = [], cloud = null, available = false, posting = false, cloudPosting = false;
let cloudFetch = null, cloudTimer = null, budgetTimer = null, budgetStamp = null;
let cloudError = '', mutationUncertain = false, initialCloud = true, lastInstance = undefined;
const sessions = new Map(), cards = new Map(), videoCards = new Map();
const modelRows = new Map();
let modelInstallation = new Map();
const pendingModelNotices = new Set();
let modelsFetch = null, modelsInstance = undefined, modelRemoving = null, modelsVersion = 0;
let galleryFetch = null, galleryFetchInstance = undefined, galleryInstance = undefined, galleryVersion = 0;
try { const saved = JSON.parse(localStorage.getItem(JOBS_KEY) || '[]'); if (Array.isArray(saved)) jobs = saved.filter(validJob); } catch (_) {}
function validJob(item) { return item && typeof item.id === 'string' && typeof item.client_id === 'string' && item.instance_id != null; }
function storedGet(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
function storedRemove(key) { try { localStorage.removeItem(key); } catch (_) {} }
function saveJobs() {
  // Keep recovery IDs and settings, never prompts, images or videos.
  if (!jobs.length) { storedRemove(JOBS_KEY); return; }
  try { localStorage.setItem(JOBS_KEY, JSON.stringify(jobs.map(({id, client_id, instance_id, state, config}) => ({id, client_id, instance_id, state, config})))); } catch (_) {}
}
const active = job => !TERMINAL.has(job.state);
const currentJob = job => jobs.includes(job) && cloud && cloud.instance && String(job.instance_id) === String(cloud.instance.id);
const identityConfirmed = () => cloud && !cloud.error && cloud.operation.state !== 'unknown' && (cloud.instance || cloud.operation.state === 'idle');
const operationBusy = () => cloudPosting || mutationUncertain || Boolean(cloud && cloud.operation && cloud.operation.state !== 'idle');
// Confirmed stop/destroy can cancel setup as soon as the rental ID is known.
const stopDestroyBusy = () => cloudPosting || mutationUncertain || Boolean(cloud && cloud.operation && ['stopping','destroying','unknown'].includes(cloud.operation.state));
const usd = value => new Intl.NumberFormat('en-US', {style:'currency', currency:'USD', maximumFractionDigits:2}).format(value);
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const value = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
  return `${value.slice(0,8)}-${value.slice(8,12)}-${value.slice(12,16)}-${value.slice(16,20)}-${value.slice(20)}`;
}
function modelName(id) {
  return MODELS[id] && MODELS[id].label || MODEL_NAMES[id] || 'Previous model';
}
function settings() {
  const [width, height] = $('size').value.split('x').map(Number), model = MODELS[$('model').value];
  return {model:$('model').value, duration:Number($('duration').value), width, height, audio:Boolean(model && model.audio && $('audio').value === 'on')};
}
function describe(config) {
  const name = modelName(config && config.model || DEFAULT_MODEL);
  if (!config) return `${name} · your rented GPU`;
  return `${name} · ~${Math.round((config.actual_duration || config.duration) * 10) / 10} seconds · ${config.width} × ${config.height} · ${config.audio ? 'With sound' : 'Silent'}`;
}
function syncModelCatalog() {
  const next = {};
  for (const item of cloud && Array.isArray(cloud.models) ? cloud.models : []) {
    if (!item || !Object.prototype.hasOwnProperty.call(MODEL_NAMES, item.id) || !Array.isArray(item.durations) || !Array.isArray(item.sizes) || typeof item.audio !== 'boolean') continue;
    const durations = [...new Set(item.durations.filter(value => Number.isFinite(value) && value > 0))].sort((left, right) => left - right);
    const sizes = item.sizes.filter(size => Array.isArray(size) && size.length === 2 && size.every(value => Number.isInteger(value) && value > 0));
    if (!durations.length || !sizes.length) continue;
    const defaultSize = sizes.find(([width, height]) => width === item.default_width && height === item.default_height) || sizes[0];
    next[item.id] = {label:item.label || item.name || MODEL_NAMES[item.id], durations, sizes,
      defaultSize:defaultSize.join('x'), audio:item.audio, sampler:String(item.sampler_node || ''),
      steps:Number.isFinite(item.steps) ? item.steps : null, note:typeof item.note === 'string' ? item.note : ''};
  }
  const signature = JSON.stringify(Object.keys(MODEL_NAMES).map(id => next[id] || null));
  if (signature === catalogSignature) return;
  catalogSignature = signature; MODELS = next;
  const previous = $('model').value;
  $('model').replaceChildren(...Object.entries(MODELS).map(([id, item]) => {
    const option = document.createElement('option'); option.value = id; option.textContent = item.label; return option;
  }));
  $('model').value = previous;
  if (!MODELS[previous]) $('model').value = MODELS[DEFAULT_MODEL] ? DEFAULT_MODEL : Object.keys(MODEL_NAMES).find(id => MODELS[id]) || '';
  modelSettings();
  if ($('model').value !== previous) { available = false; refreshReadiness(); }
  for (const job of jobs) if (cards.has(job.id)) renderJob(job);
}
function modelSettings() {
  if (!MODELS[$('model').value]) $('model').value = MODELS[DEFAULT_MODEL] ? DEFAULT_MODEL : Object.keys(MODEL_NAMES).find(id => MODELS[id]) || '';
  const model = MODELS[$('model').value];
  if (!model) {
    available = false; $('size').disabled = true; $('duration').disabled = true; $('audio').disabled = true;
    $('model-note').textContent = 'Waiting for supported model settings from your private studio.';
    return;
  }
  const previous = $('size').value, duration = Number($('duration').value);
  $('size').disabled = false; $('duration').disabled = false;
  $('size').replaceChildren(...model.sizes.map(([width, height]) => {
    const option = document.createElement('option'); option.value = `${width}x${height}`; option.textContent = `${width} × ${height}`; return option;
  }));
  $('size').value = model.sizes.some(size => size.join('x') === previous) ? previous : model.defaultSize;
  $('duration').replaceChildren(...model.durations.map(value => {
    const option = document.createElement('option'); option.value = String(value); option.textContent = `~${value} seconds`; return option;
  }));
  const nearest = model.durations.reduce((best, value) => Math.abs(value - duration) < Math.abs(best - duration) ? value : best, model.durations[0]);
  $('duration').value = String(nearest);
  $('audio').disabled = !model.audio;
  $('audio').querySelector('option[value="on"]').disabled = !model.audio;
  if (!model.audio) $('audio').value = 'off';
  $('model-note').textContent = [model.note, model.audio ? 'Longer clips need more memory and take longer to render.' : 'This model produces silent video.'].filter(Boolean).join(' ');
}
function updateButton() {
  // A running generation does not block another prompt or batch.
  button.disabled = posting || !available || !MODELS[$('model').value] || operationBusy();
  const count = Number($('batch').value);
  $('button-label').textContent = posting ? 'Adding to queue…' : available ? `Generate ${count === 1 ? 'video' : `${count} videos`}` : cloud && !cloud.instance ? 'Create a GPU above' : 'Preparing your GPU…';
  $('settings').disabled = posting;
  $('specs').textContent = MODELS[$('model').value] ? describe(settings()) : 'Choose a supported model to begin.';
  updateModelRemovalButtons();
}
function cardFor(job) {
  if (!jobs.includes(job)) return null;
  if (cards.has(job.id)) return cards.get(job.id);
  const card = $('job-template').content.firstElementChild.cloneNode(true);
  card.querySelector('.cancel-job').addEventListener('click', () => cancelJob(job));
  cards.set(job.id, card); placeJob(job, card);
  return card;
}
function placeJob(job, card) {
  const target = $(job.state === 'queued' ? 'queued-jobs' : TERMINAL.has(job.state) ? 'past-jobs' : 'jobs');
  const later = jobs.slice(jobs.indexOf(job) + 1).map(item => cards.get(item.id)).find(item => item && item.parentNode === target) || null;
  if (card.parentNode !== target || card.nextElementSibling !== later) target.insertBefore(card, later);
}
function queueSummary() {
  const current = jobs.filter(currentJob);
  const queued = current.filter(job => job.state === 'queued').length;
  const running = current.filter(job => active(job) && job.state !== 'queued').length;
  const past = current.filter(job => TERMINAL.has(job.state)).length;
  $('jobs-section').hidden = !running && !queued;
  $('active-jobs-heading').hidden = !running;
  $('queued-jobs-section').hidden = !queued;
  $('queue-summary').textContent = running ? `${running} active` : '';
  $('queued-jobs-count').textContent = `${queued} queued video${queued === 1 ? '' : 's'}`;
  $('past-jobs-section').hidden = !past;
  $('past-jobs-count').textContent = `${past} video${past === 1 ? '' : 's'}`;
  updateModelRemovalButtons();
}
function renderJob(job) {
  if (!jobs.includes(job)) return;
  const card = cardFor(job), position = jobs.indexOf(job) + 1;
  card.dataset.state = job.state; placeJob(job, card);
  card.querySelector('.job-title').textContent = `Video ${position}`;
  card.querySelector('.job-config').textContent = describe(job.config);
  const labels = {posting:'Sending', queued:'Waiting', running:'Running', unknown:'Confirming', disconnected:'Reconnecting', complete:'Ready', failed:'Stopped', not_ready:'Not submitted', cancelled:'Cancelled'};
  card.querySelector('.job-state').textContent = labels[job.state] || 'Waiting';
  const cancelling = Boolean(sessions.get(job.id) && sessions.get(job.id).cancelling);
  const cancel = card.querySelector('.cancel-job');
  cancel.hidden = job.state !== 'queued'; cancel.disabled = cancelling;
  cancel.textContent = cancelling ? 'Cancelling…' : 'Cancel queued video';
  if (TERMINAL.has(job.state)) card.querySelector('.cancel-status').textContent = '';
  queueSummary();
}
function stage(job, title, detail, step = null, max = null) {
  if (!jobs.includes(job)) return;
  const card = cardFor(job), progress = card.querySelector('progress');
  card.querySelector('.generation').hidden = false;
  card.querySelector('.stage').textContent = title; card.querySelector('.detail').textContent = detail;
  card.querySelector('.steps').textContent = step !== null ? `${step} / ${max} steps` : '';
  if (step !== null) { progress.max = max; progress.value = step; } else progress.removeAttribute('value');
  progress.hidden = TERMINAL.has(job.state);
}
function sessionFor(job) {
  if (!sessions.has(job.id)) sessions.set(job.id, {socket:null, timer:null, lastStep:null, sawNode:false, currentNode:null, refreshing:false, cancelling:false, budgetNotified:TERMINAL.has(job.state)});
  return sessions.get(job.id);
}
function finishConnection(job) {
  const session = sessionFor(job); clearTimeout(session.timer);
  if (session.socket) { session.socket.onclose = null; session.socket.close(); session.socket = null; }
}
function connectEvents(job) {
  if (!currentJob(job) || !active(job)) return;
  const session = sessionFor(job); finishConnection(job);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws?client_id=${encodeURIComponent(job.client_id)}`);
  session.socket = socket;
  socket.onmessage = event => {
    let message; try { message = JSON.parse(event.data); } catch (_) { return; }
    const data = message.data || {};
    if (!currentJob(job) || !active(job) || (data.prompt_id && data.prompt_id !== job.id)) return;
    if (job.state === 'queued' && data.prompt_id === job.id && ['execution_start','executing','progress'].includes(message.type) && !(message.type === 'executing' && data.node === null)) {
      job.state = 'running'; saveJobs(); renderJob(job);
    }
    const model = job.config && job.config.model || DEFAULT_MODEL;
    const sampler = MODELS[model] && MODELS[model].sampler || null;
    if (message.type === 'private_video_stage' && data.prompt_id === job.id) {
      const phases = {
        preparing:['Preparing your model', 'Setting up this model on your rented GPU.'],
        downloading:['Downloading your model', 'First use downloads the model files onto your GPU.'],
        loading:['Loading your model', 'Loading the model and preparing this video.'],
        sampling:['Making your video', 'Live steps appear when sampling starts.'],
        decoding:['Decoding your video', 'Turning the generated pictures and optional sound into video.'],
        saving:['Saving your video', 'Writing the finished video on your GPU.'],
      };
      if (phases[data.stage]) stage(job, ...phases[data.stage]);
    } else if (message.type === 'progress' && (String(data.node || session.currentNode) === sampler || !sampler && data.prompt_id === job.id) && Number.isFinite(data.value) && Number.isFinite(data.max) && data.max > 0) {
      session.lastStep = {value:data.value, max:data.max};
      stage(job, 'Making your video', 'Live sampling progress from your GPU.', data.value, data.max);
    } else if (message.type === 'executing' && data.node) {
      session.sawNode = true; session.currentNode = String(data.node);
      const stages = model === 'h3' ? {
        '10':['Loading H3', 'Loading the video model on your GPU.'],
        '11':['Preparing H3', 'Preparing the attention backend.'],
        '12':['Preparing H3 Turbo', 'Loading the four-step Turbo adapter.'],
        '13':['Loading the text model', 'The first video takes longer while the model loads.'],
        '14':['Loading the video decoder', 'Preparing the video decoder.'],
        '15':['Loading the audio decoder', 'Preparing sound for your video.'],
        '16':['Understanding your scene', 'Encoding your prompt on your rented GPU.'],
        '17':['Preparing to generate', 'Preparing this variation’s random seed.'],
        '18':['Preparing to generate', 'Preparing prompt conditioning.'],
        '19':['Preparing to generate', 'Preparing the four sampling steps.'],
        '20':['Preparing to generate', 'Preparing the sampler.'],
        '21':['Making your video', 'Waiting for the first of four sampling steps.'],
        '22':['Decoding your video', 'Turning the generated pictures into frames.'],
        '23':['Decoding your sound', 'Turning the generated audio into sound.'],
        '24':['Assembling your video', 'Combining the finished pictures and optional sound.'],
        '25':['Saving your video', 'Writing the finished MP4 on your GPU.'],
        '26':['Preparing H3 Turbo', 'Applying the matching Turbo settings.'],
      } : { [sampler || '30']:['Preparing your video', 'Model setup, loading and decoding may take time; live steps appear during sampling.'] };
      if (stages[session.currentNode]) stage(job, ...stages[session.currentNode]);
    } else if (['execution_success','execution_error','execution_interrupted'].includes(message.type) || (message.type === 'executing' && data.node === null)) refreshJob(job);
  };
  socket.onclose = () => { if (currentJob(job) && active(job)) session.timer = setTimeout(() => connectEvents(job), 2500); };
}
function applyState(job, data) {
  if (!jobs.includes(job)) return;
  // Confirmed queue deletion is terminal, even if an earlier status reply arrives late.
  if (job.state === 'cancelled' && data.state !== 'cancelled') return;
  // A reply sent before a confirmed halt must not revive its interrupted queue.
  if (job.state === 'failed' && currentJob(job) && cloud.instance.status === 'stopped' && cloud.operation.state === 'idle' && !TERMINAL.has(data.state)) return;
  const session = sessionFor(job);
  if (data.state === 'disconnected') {
    stage(job, 'Reconnecting to your GPU', 'Your video may still be running. It will not be submitted again.'); return;
  }
  if (typeof data.state !== 'string') throw new Error('Invalid job state');
  job.state = data.state;
  if (data.config) job.config = data.config;
  saveJobs(); renderJob(job);
  const card = cardFor(job);
  if (data.state === 'complete') {
    finishConnection(job); card.querySelector('.generation').hidden = true;
  } else if (data.state === 'failed') {
    finishConnection(job); stage(job, 'Video could not finish', data.message || 'Generation stopped. You can queue another prompt.');
  } else if (data.state === 'queued') stage(job, 'Waiting for your GPU', 'Your scene is queued. You can add another prompt while you wait.');
  else if (data.state === 'running' && data.preparation && !session.lastStep) {
    const prep = data.preparation;
    if (prep.stage === 'downloading') {
      const have = Number(prep.downloaded_bytes), total = Number(prep.size_bytes);
      const counts = Number.isFinite(have) && Number.isFinite(total) ? `${(have / 1e9).toFixed(1)} / ${(total / 1e9).toFixed(1)} GB on your GPU.` : 'Downloading model files onto your GPU.';
      stage(job, 'Downloading your model', counts + (prep.runtime_preparing ? ' Video software is also being prepared in parallel.' : ''));
    } else if (prep.stage === 'preparing') stage(job, 'Preparing video software', 'Installing the model’s software on your GPU. Sampling has not started.');
  }
  else if (data.state === 'running' && !session.lastStep && !session.sawNode) stage(job, 'Creating your video', 'Loading models or preparing your scene. Live steps appear when sampling starts.');
  else if (data.state === 'unknown') stage(job, 'Confirming this request', 'Checking the GPU. This request will not be sent again; you can queue a different video.');
  else if (data.state === 'not_ready') {
    finishConnection(job); stage(job, 'Video was not submitted', data.message || 'Please wait for the GPU and model download.');
  } else if (data.state === 'cancelled') {
    finishConnection(job); stage(job, 'Video cancelled', 'Removed this video from the GPU queue. Other videos keep running.');
  }
  if (['complete','failed','cancelled'].includes(job.state) && !session.budgetNotified) {
    session.budgetNotified = true; refreshCloud(true); refreshGallery(true); refreshModels(true);
  }
  updateButton();
}
async function cancelJob(job) {
  if (!currentJob(job) || job.state !== 'queued') return;
  const session = sessionFor(job);
  if (session.cancelling) return;
  session.cancelling = true; renderJob(job);
  const feedback = cardFor(job).querySelector('.cancel-status'); feedback.textContent = '';
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instance_id:job.instance_id}),
    });
    const data = await response.json();
    if (!currentJob(job)) return;
    if (response.ok) {
      applyState(job, data);
      if (!TERMINAL.has(job.state)) feedback.textContent = data.message || (['unknown','disconnected'].includes(data.state) ? 'Cancellation is not confirmed. Checking the GPU queue.' : 'This video is still in the GPU queue.');
    } else if (!TERMINAL.has(job.state)) feedback.textContent = data.message || 'Cancellation was not accepted. Checking whether the video is still queued.';
  } catch (_) {
    if (currentJob(job) && !TERMINAL.has(job.state)) feedback.textContent = 'The cancellation reply was interrupted. Checking the GPU; no cancellation has been confirmed.';
  } finally {
    session.cancelling = false;
    if (currentJob(job)) { renderJob(job); if (active(job)) refreshJob(job); }
  }
}
async function refreshJob(job) {
  if (!currentJob(job)) return;
  const session = sessionFor(job); if (session.refreshing || posting && job.state === 'posting') return;
  session.refreshing = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(job.id)}?model=${encodeURIComponent(job.config && job.config.model || DEFAULT_MODEL)}`, {cache:'no-store'});
    if (!response.ok) throw new Error('Connection unavailable');
    const data = await response.json(); if (!currentJob(job)) return;
    if (data.state === 'unknown' && TERMINAL.has(job.state)) {
      // Comfy history can reset while its output files remain in the gallery.
      if (job.state === 'complete') { applyState(job, {state:'complete'}); refreshGallery(); }
    } else applyState(job, data);
  } catch (_) { if (active(job)) stage(job, 'Reconnecting to your GPU', 'Your video may still be running. It will not be submitted again.'); }
  finally { session.refreshing = false; }
}
function reconcileInstance() {
  // Keep recovery records while a destroy result or cloud identity is uncertain.
  if (!identityConfirmed()) return;
  const id = cloud.instance ? String(cloud.instance.id) : null;
  if (initialCloud || id !== lastInstance) {
    available = false;
    jobs = jobs.filter(job => id !== null && String(job.instance_id) === id && job.state !== 'lost');
    const retained = new Set(jobs.map(job => job.id));
    for (const [jobId, session] of sessions) if (!retained.has(jobId)) {
      clearTimeout(session.timer);
      if (session.socket) { session.socket.onclose = null; session.socket.onmessage = null; session.socket.close(); }
      sessions.delete(jobId);
    }
    for (const [jobId, card] of cards) if (!retained.has(jobId)) { card.remove(); cards.delete(jobId); }
    queueSummary();
    clearGallery(); galleryInstance = id;
    for (const job of jobs) {
      renderJob(job);
      if (active(job) || job.state === 'complete') { refreshJob(job); if (active(job)) connectEvents(job); }
      else applyState(job, {state:job.state});
    }
    saveJobs(); $('destroy-confirm').hidden = true; $('stop-confirm').hidden = true;
    lastInstance = id; initialCloud = false;
    clearModels();
    refreshReadiness(); refreshGallery(); refreshModels();
  }
  if (cloud.instance && cloud.instance.status === 'stopped' && cloud.operation.state === 'idle') {
    for (const job of jobs) if (currentJob(job) && active(job)) {
      applyState(job, {state:'failed', message:'Interrupted when the GPU was stopped. Queue this video again after starting it.'});
    }
  }
}
function renderCloud() {
  const instance = cloud && cloud.instance, pricing = cloud && cloud.pricing;
  const locked = operationBusy(), stoppingLocked = stopDestroyBusy();
  syncModelCatalog();
  $('gpu-name').textContent = instance ? instance.gpu || 'GPU instance' : 'No GPU rented';
  $('instance-info').textContent = instance ? `Instance ${instance.id} · ${instance.status || 'connecting'}` : pricing ? 'Ready to create · model and workflow install automatically' : 'Looking for an available GPU offer';
  $('balance').textContent = cloud && Number.isFinite(cloud.balance) ? usd(cloud.balance) : '—';
  const rate = instance && instance.status === 'stopped' ? instance.storage_hourly_rate : instance ? instance.hourly_rate : pricing && pricing.hourly_rate;
  $('hourly-rate').textContent = Number.isFinite(rate) ? `${usd(rate)}/hr ${instance && instance.status === 'stopped' ? 'storage' : instance ? 'while running' : 'available offer'}` : 'USD · pricing unavailable';
  let message = 'Create a GPU when you want to make a video. Destroy it afterward to end all rental charges.';
  if (instance && instance.status === 'stopped') message = 'GPU stopped. Storage is still billed. Start it to keep creating, or destroy it to end storage charges.';
  else if (instance) message = cloud.setup && cloud.setup.message || 'Your GPU is connecting.';
  if (cloud && cloud.operation && cloud.operation.state !== 'idle') {
    const preparing = ['creating','starting'].includes(cloud.operation.state) && cloud.setup && ['connecting','installing'].includes(cloud.setup.state);
    message = preparing ? cloud.setup.message : cloud.operation.message || 'Updating your GPU…';
  }
  if (cloudPosting && (!cloud || !cloud.operation || cloud.operation.state === 'idle')) message = 'Sending your request once…';
  if (mutationUncertain) message = 'Confirming the cloud request before allowing another change. It will not be sent again.';
  $('cloud-message').textContent = message;
  $('create-gpu').hidden = Boolean(instance); $('create-gpu').disabled = !cloud || locked;
  $('start-gpu').hidden = !instance || instance.status !== 'stopped'; $('start-gpu').disabled = locked;
  $('stop-gpu').hidden = !instance || instance.status === 'stopped'; $('stop-gpu').disabled = stoppingLocked;
  $('destroy-gpu').hidden = !instance; $('destroy-gpu').disabled = stoppingLocked;
  $('confirm-stop').disabled = stoppingLocked; $('confirm-destroy').disabled = stoppingLocked;
  $('cloud-error').textContent = cloudError || cloud && cloud.error || ''; $('cloud-error').hidden = !$('cloud-error').textContent;
  $('budget-updated').textContent = budgetStamp ? `Budget checked ${budgetStamp.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})} · updates after each video or 5 min.` : 'Budget updates after each video or every 5 minutes.';
  updateButton();
  $('model-files').hidden = !instance;
  updateModelRemovalButtons();
}
async function refreshCloud(force = false) {
  // A completion must refresh credit even if a cached status call is in flight.
  if (cloudFetch) { await cloudFetch; if (force) return refreshCloud(true); return; }
  cloudFetch = (async () => {
    try {
      const response = await fetch(`/api/cloud${force ? '?refresh=1' : ''}`, {cache:'no-store'});
      if (!response.ok) throw new Error('Cloud status is unavailable.');
      const data = await response.json();
      if (!data.operation || !Object.prototype.hasOwnProperty.call(data, 'instance')) throw new Error('Cloud status is incomplete.');
      cloud = data;
      if (!data.error && (force || !budgetStamp)) budgetStamp = new Date();
      if (data.operation.state !== 'unknown') mutationUncertain = false;
      reconcileInstance(); renderCloud();
      if (force) { refreshGallery(); refreshModels(); }
      clearTimeout(cloudTimer);
      const tracking = data.operation.state !== 'idle' || data.instance && (data.instance.status === 'loading' || data.instance.status === 'running' && data.setup && data.setup.state !== 'ready');
      if (tracking) cloudTimer = setTimeout(() => refreshCloud(false), 3000);
      if (force || !budgetTimer) { clearTimeout(budgetTimer); budgetTimer = setTimeout(() => { budgetTimer = null; refreshCloud(true); }, 300000); }
    } catch (_) {
      available = false;
      $('cloud-error').hidden = false; $('cloud-error').textContent = 'The laptop studio or cloud connection is unavailable. No new cloud action has been sent.';
      updateButton(); clearTimeout(cloudTimer); cloudTimer = setTimeout(() => refreshCloud(false), 10000);
    }
  })();
  try { await cloudFetch; } finally { cloudFetch = null; }
}
async function cloudAction(action) {
  if (!cloud || (['stop','destroy'].includes(action) ? stopDestroyBusy() : operationBusy())) return;
  if (action !== 'create' && !cloud.instance) return;
  cloudPosting = true; cloudError = ''; renderCloud();
  const payload = {request_id:uuid()};
  if (action !== 'create') payload.instance_id = cloud.instance.id;
  if (action === 'stop' || action === 'destroy') payload.confirm = true;
  try {
    const response = await fetch(`/api/cloud/${action}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) {
      cloudError = data.message || 'The cloud did not accept this change.';
      if (data.state === 'unknown') mutationUncertain = true;
    } else if (data.state === 'unknown') { mutationUncertain = true; cloudError = data.message || 'The cloud reply was uncertain. Checking your rental.'; }
    $('destroy-confirm').hidden = true; $('stop-confirm').hidden = true;
  } catch (_) {
    mutationUncertain = true; cloudError = 'The cloud reply was interrupted. Checking the outcome before allowing another action.';
  } finally {
    cloudPosting = false; await refreshCloud(true); renderCloud(); refreshReadiness();
  }
}
function installationChanges(previous, items) {
  const next = new Map(), installed = [];
  for (const item of items) {
    const available = item.downloaded === true && item.runtime_ready === true;
    if (available && previous.get(item.id) === false) installed.push(item.id);
    next.set(item.id, available);
  }
  return {next, installed};
}
function updateNotificationControl() {
  $('enable-model-notifications').hidden = !window.isSecureContext || !('serviceWorker' in navigator) || !('Notification' in window) || Notification.permission !== 'default';
}
function flushModelNotices() {
  if (document.hidden || !pendingModelNotices.size) return;
  const names = [...pendingModelNotices].map(modelName);
  pendingModelNotices.clear();
  $('model-ready-message').textContent = `${names.join(', ')} ${names.length === 1 ? 'is' : 'are'} installed. You can queue a video.`;
  $('model-ready-notice').hidden = false;
}
async function notifyInstalledModels(ids, instance) {
  if (!ids.length || !window.isSecureContext || !('Notification' in window) || Notification.permission !== 'granted' || !('serviceWorker' in navigator)) return;
  const current = () => cloud && cloud.instance && String(cloud.instance.id) === instance && modelsInstance === instance && cloud.instance.status === 'running';
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    if (!registration || !registration.active || !current()) return;
    await registration.showNotification('Video model installed', {
      body:`${ids.map(modelName).join(', ')}. You can queue a video.`,
      tag:`leam-vid-models-${instance}-${ids.join('-')}`, icon:'/icon-192.png',
    });
  } catch (_) { /* The in-app notice remains available when browser alerts are unavailable. */ }
}
function recordModelNotices(ids, instance) {
  for (const id of ids) pendingModelNotices.add(id);
  notifyInstalledModels(ids, instance);
  flushModelNotices();
}
function clearModelNotices() {
  modelInstallation.clear(); pendingModelNotices.clear();
  $('model-ready-message').textContent = ''; $('model-ready-notice').hidden = true;
  if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistration().then(registration => registration && registration.getNotifications()).then(notices => {
    for (const notice of notices || []) if (notice.tag.startsWith('leam-vid-models-')) notice.close();
  }).catch(() => {});
}
function clearModels() {
  modelsVersion++;
  modelsInstance = undefined; modelRemoving = null; modelRows.clear(); clearModelNotices();
  $('model-files-list').replaceChildren(); $('model-free-space').textContent = '';
  $('model-files-status').textContent = 'Open this section to check model files on your GPU.';
  $('model-files').querySelector('summary').textContent = 'Model preparation and files';
}
function updateModelRemovalButtons() {
  const busy = !identityConfirmed() || !cloud.instance || cloud.instance.status !== 'running' || operationBusy() || posting || Boolean(modelRemoving) || jobs.some(job => currentJob(job) && active(job));
  for (const [id, row] of modelRows) {
    const pending = Boolean(modelRemoving && modelRemoving.id === id);
    for (const button of row.querySelectorAll('button')) button.disabled = pending || busy;
    row.querySelector('.remove-model').textContent = pending ? 'Removing…' : 'Remove files';
  }
}
function modelRow(item, instance) {
  const row = document.createElement('article'); row.className = 'model-file-row';
  const info = document.createElement('div'), name = document.createElement('h3'), size = document.createElement('p');
  name.textContent = MODEL_NAMES[item.id]; info.append(name, size);
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove-model quiet danger small'; remove.textContent = 'Remove files';
  const panel = document.createElement('section'); panel.className = 'model-remove-confirm'; panel.hidden = true;
  const warning = document.createElement('p'); warning.textContent = 'Remove this model’s downloaded files? Your videos and shared files stay. The next generation with this model downloads it again. The queue must be empty.';
  const actions = document.createElement('div'); actions.className = 'delete-actions';
  const keep = document.createElement('button'); keep.type = 'button'; keep.className = 'quiet small'; keep.textContent = 'Keep files';
  const confirm = document.createElement('button'); confirm.type = 'button'; confirm.className = 'destructive small'; confirm.textContent = 'Remove model files';
  actions.append(keep, confirm); panel.append(warning, actions);
  const status = document.createElement('p'); status.className = 'model-remove-status'; status.setAttribute('role', 'status');
  row.append(info, remove, panel, status);
  const alive = () => identityConfirmed() && cloud.instance && String(cloud.instance.id) === instance && modelsInstance === instance && modelRows.get(item.id) === row && row.isConnected;
  remove.addEventListener('click', () => { if (alive() && !remove.disabled) { panel.hidden = false; status.textContent = ''; keep.focus(); } });
  keep.addEventListener('click', () => { panel.hidden = true; remove.focus(); });
  confirm.addEventListener('click', async () => {
    if (!alive() || confirm.disabled || modelRemoving) return;
    const pending = {id:item.id, instance}; modelRemoving = pending;
    updateModelRemovalButtons(); status.textContent = 'Removing downloaded model files…';
    try {
      const response = await fetch(`/api/models/${encodeURIComponent(item.id)}/remove`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instance_id:instance, confirm:true}),
      });
      const data = await response.json();
      if (!alive()) return;
      if (response.ok && data.removed === true && data.model === item.id && String(data.instance_id) === instance) {
        status.textContent = `Model files removed. ${Number.isFinite(data.freed_bytes) ? `${(data.freed_bytes / 1073741824).toFixed(1)} GB freed. ` : ''}The next use downloads this model again.`;
        panel.hidden = true; remove.hidden = true; modelInstallation.set(item.id, false); pendingModelNotices.delete(item.id);
        $('model-ready-notice').hidden = true;
      } else status.textContent = data.message || 'Removal is not confirmed. Checking which files remain.';
    } catch (_) {
      if (alive()) status.textContent = 'The reply was interrupted. Removal is not confirmed; checking the GPU before you retry.';
    } finally {
      if (modelRemoving === pending) modelRemoving = null;
      if (alive()) { modelsVersion++; updateModelRemovalButtons(); await refreshModels(true); refreshReadiness(); }
    }
  });
  row.modelSize = size; row.modelRemove = remove;
  return row;
}
async function refreshModels(force = false) {
  if (!cloud || !cloud.instance || !identityConfirmed() || (!force && !$('model-files').open)) return;
  const instance = String(cloud.instance.id);
  if (modelsFetch) { await modelsFetch; if (force || modelsInstance !== instance) return refreshModels(force); return; }
  modelsInstance = instance;
  const version = modelsVersion;
  modelsFetch = (async () => {
    try {
      const response = await fetch('/api/models', {cache:'no-store'}), data = await response.json();
      if (version !== modelsVersion || !cloud.instance || String(cloud.instance.id) !== instance || modelsInstance !== instance) return;
      if (!response.ok || String(data.instance_id) !== instance || !Array.isArray(data.models)) throw new Error('Model list unavailable');
      // An offline inventory is unknown, not evidence that model files vanished.
      if (!data.models.length && (cloud.instance.status !== 'running' || data.message)) {
        $('model-files').querySelector('summary').textContent = 'Model preparation and files · unavailable';
        $('model-files-status').textContent = data.message || 'Model files cannot be read until the GPU connection is ready.';
        $('model-free-space').textContent = '';
        updateModelRemovalButtons();
        return;
      }
      const seen = new Set();
      let installedCount = 0, preparingCount = 0;
      for (const item of data.models) {
        if (!item || !Object.prototype.hasOwnProperty.call(MODEL_NAMES, item.id)) continue;
        seen.add(item.id);
        let row = modelRows.get(item.id);
        if (!row) { row = modelRow(item, instance); modelRows.set(item.id, row); $('model-files-list').append(row); }
        const bytes = Number.isFinite(item.downloaded_bytes) ? item.downloaded_bytes : 0;
        const total = Number.isFinite(item.size_bytes) ? item.size_bytes : 0;
        const counts = total > 0 ? `${(bytes / 1e9).toFixed(1)} / ${(total / 1e9).toFixed(1)} GB` : bytes > 0 ? `${(bytes / 1e9).toFixed(1)} GB` : '';
        const installed = item.downloaded && item.runtime_ready === true;
        const preparing = !installed && (item.preparing || ['downloading','preparing','queued'].includes(item.stage));
        if (installed) installedCount++;
        if (preparing) preparingCount++;
        const state = installed ? 'Files and runtime installed' : item.stage === 'downloading' ? 'Downloading' : !item.downloaded && (item.stage === 'queued' || item.preparing) ? 'Waiting to download' : item.downloaded && !item.runtime_ready ? (item.stage === 'preparing' || data.runtime_preparing ? 'Preparing video software' : 'Video software not ready') : bytes > 0 ? 'Partly downloaded' : 'Not downloaded';
        row.modelSize.textContent = [state, counts].filter(Boolean).join(' · ');
        row.modelRemove.hidden = !item.downloaded && bytes <= 0;
      }
      for (const [id, row] of modelRows) if (!seen.has(id)) { row.remove(); modelRows.delete(id); }
      const changes = installationChanges(modelInstallation, data.models.filter(item => item && seen.has(item.id)));
      modelInstallation = changes.next; recordModelNotices(changes.installed, instance);
      $('model-free-space').textContent = Number.isFinite(data.free_bytes) ? `${(data.free_bytes / 1073741824).toFixed(1)} GB available on your GPU` : '';
      $('model-files').querySelector('summary').textContent = `Model preparation and files · ${installedCount} installed${preparingCount ? ` · ${preparingCount} preparing` : ''}`;
      $('model-files-status').textContent = preparingCount ? 'Models prepare on your GPU. Installed means model files and video software are present; generation still needs a manual trial. This list updates every 10 seconds.' : 'Installed means files and video software are present; generation still needs a manual trial. Remove files only with an empty queue; shared files and videos are kept. Freeing space does not reduce the fixed disk rental charge.';
      updateModelRemovalButtons();
    } catch (_) {
      if (version === modelsVersion && cloud.instance && String(cloud.instance.id) === instance && modelsInstance === instance) $('model-files-status').textContent = cloud.instance.status === 'stopped' ? 'Start the GPU to check or remove its downloaded model files.' : 'Model files are unavailable. Refresh when the GPU connection is ready.';
    }
  })();
  try { await modelsFetch; } finally { modelsFetch = null; }
}
function clearGallery() {
  galleryVersion++;
  for (const video of $('gallery').querySelectorAll('video')) {
    video.pause(); video.removeAttribute('src'); video.removeAttribute('poster'); video.load();
  }
  for (const image of $('gallery').querySelectorAll('img')) { image.removeAttribute('src'); image.removeAttribute('srcset'); }
  for (const prompt of $('gallery').querySelectorAll('.video-prompt')) prompt.textContent = '';
  $('gallery').replaceChildren();
  videoCards.clear(); $('gallery-count').textContent = '';
}
function removeVideoCard(id, card) {
  const video = card.querySelector('video');
  video.pause(); video.removeAttribute('src'); video.removeAttribute('poster'); video.load();
  for (const image of card.querySelectorAll('img')) { image.removeAttribute('src'); image.removeAttribute('srcset'); }
  card.querySelector('.video-prompt').textContent = '';
  card.remove(); videoCards.delete(id);
}
function gallerySummary() {
  $('gallery-count').textContent = `${videoCards.size} video${videoCards.size === 1 ? '' : 's'}`;
  $('gallery-message').textContent = videoCards.size ? 'Newest first. Streamed from your GPU; no local archive is kept.' : 'Videos appear here when generation finishes. They stay on the GPU until you destroy it.';
}
function attachPlaybackControls(card, item, instance) {
  const video = card.querySelector('video');
  const retry = document.createElement('button');
  retry.type = 'button'; retry.className = 'text-button'; retry.textContent = 'Retry playback'; retry.hidden = true;
  card.querySelector('.video-actions').prepend(retry);
  const alive = () => cloud && cloud.instance && String(cloud.instance.id) === instance && galleryInstance === instance && videoCards.get(item.id) === card && card.isConnected;
  video.addEventListener('error', () => { if (alive()) retry.hidden = false; });
  video.addEventListener('loadeddata', () => { retry.hidden = true; });
  retry.addEventListener('click', () => {
    if (!alive()) return;
    // A user retry reconnects only this remote stream; it stores no media.
    video.load();
    const playing = video.play();
    if (playing) playing.catch(() => { if (alive()) retry.hidden = false; });
  });
}
function attachDeleteControls(card, item, instance) {
  const show = card.querySelector('.delete-video'), panel = card.querySelector('.delete-confirm');
  const confirm = card.querySelector('.confirm-delete'), cancel = card.querySelector('.cancel-delete'), status = card.querySelector('.delete-status');
  const alive = () => cloud && cloud.instance && String(cloud.instance.id) === instance && galleryInstance === instance && videoCards.get(item.id) === card && card.isConnected;
  let deleting = false;
  show.addEventListener('click', () => { if (alive() && !deleting) { panel.hidden = false; status.textContent = ''; cancel.focus(); } });
  cancel.addEventListener('click', () => { if (!deleting) { panel.hidden = true; show.focus(); } });
  confirm.addEventListener('click', async () => {
    if (!alive() || deleting) return;
    deleting = true; show.disabled = true; confirm.disabled = true; cancel.disabled = true;
    let recheck = false;
    status.textContent = 'Deleting this video from your GPU…';
    try {
      const response = await fetch(`/api/videos/${encodeURIComponent(item.id)}/delete`, {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({instance_id:instance, confirm:true}),
      });
      const data = await response.json();
      if (!alive()) return;
      if (response.ok && data.deleted === true && data.id === item.id && String(data.instance_id) === instance) {
        // Older in-flight lists must not put the confirmed deleted file back.
        galleryVersion++; removeVideoCard(item.id, card); gallerySummary();
        refreshGallery(true);
      } else {
        status.textContent = data.message || 'Deletion is not confirmed. The video has been kept in the list.';
        recheck = data.state === 'unknown';
      }
    } catch (_) {
      if (alive()) status.textContent = 'The reply was interrupted. Deletion is not confirmed; refresh the gallery to check.';
      recheck = true;
    } finally {
      if (recheck && alive()) await refreshGallery(true);
      deleting = false;
      if (alive()) { show.disabled = false; confirm.disabled = false; cancel.disabled = false; }
    }
  });
}
function attachPromptControls(card, item, instance) {
  const toggle = card.querySelector('.prompt-toggle'), panel = card.querySelector('.prompt-panel');
  const text = card.querySelector('.video-prompt'), copy = card.querySelector('.copy-prompt'), status = card.querySelector('.prompt-status');
  const alive = () => cloud && cloud.instance && String(cloud.instance.id) === instance && galleryInstance === instance && videoCards.get(item.id) === card && card.isConnected;
  let loaded = false, loading = false;
  panel.id = `prompt-${item.id}`; toggle.setAttribute('aria-controls', panel.id);
  toggle.addEventListener('click', async () => {
    if (!alive()) return;
    const show = panel.hidden;
    panel.hidden = !show; toggle.setAttribute('aria-expanded', String(show)); toggle.textContent = show ? 'Hide prompt' : 'Show prompt';
    if (!show || loaded || loading) return;
    loading = true; status.textContent = 'Reading the prompt from your GPU…';
    try {
      const response = await fetch(`/api/videos/${encodeURIComponent(item.id)}/prompt`, {cache:'no-store'});
      if (!alive()) return;
      if (!response.ok) throw new Error('Prompt unavailable');
      const data = await response.json();
      if (!alive()) return;
      loaded = true;
      if (typeof data.prompt === 'string') {
        // Preserve the original text exactly and render it as plain text.
        text.textContent = data.prompt; text.hidden = false; copy.hidden = false; status.textContent = '';
      } else status.textContent = data.message || 'This video does not have a saved generation prompt.';
    } catch (_) {
      if (alive()) status.textContent = 'The prompt is unavailable. Keep the GPU running, then hide and show this panel to try again.';
    } finally { loading = false; }
  });
  copy.addEventListener('click', async () => {
    if (!alive() || text.hidden) return;
    copy.disabled = true;
    try {
      if (!window.isSecureContext || !navigator.clipboard || !navigator.clipboard.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text.textContent);
      if (alive()) status.textContent = 'Prompt copied.';
    } catch (_) {
      if (!alive()) return;
      text.focus();
      const selection = window.getSelection();
      if (selection) {
        const range = document.createRange(); range.selectNodeContents(text); selection.removeAllRanges(); selection.addRange(range);
        status.textContent = 'The prompt is selected. Use your device’s Copy action to copy it.';
      } else status.textContent = 'Press and hold the prompt, or select its text, to copy it manually.';
    } finally { if (alive()) copy.disabled = false; }
  });
}
async function refreshGallery(force = false) {
  if (!identityConfirmed()) return;
  const instance = cloud && cloud.instance ? String(cloud.instance.id) : null;
  if (galleryInstance !== instance) { clearGallery(); galleryInstance = instance; }
  if (!instance) { $('gallery-message').textContent = 'Create a GPU to make videos. Destroying a rental deletes all videos on it.'; return; }
  if (cloud.instance.status === 'stopped') {
    for (const card of videoCards.values()) card.querySelector('video').pause();
    $('gallery-message').textContent = 'Start the GPU to view its saved videos. They remain on its disk.';
    return;
  }
  if (galleryFetch) {
    const fetching = galleryFetchInstance;
    await galleryFetch;
    if (force || fetching !== instance) return refreshGallery(force);
    return;
  }
  galleryFetchInstance = instance;
  const version = galleryVersion;
  galleryFetch = (async () => {
    try {
      const response = await fetch(`/api/videos${force ? '?refresh=1' : ''}`, {cache:'no-store'});
      if (!response.ok) throw new Error('Gallery unavailable');
      const data = await response.json();
      if (version !== galleryVersion || !cloud.instance || String(cloud.instance.id) !== instance || String(data.instance_id) !== instance) return;
      const videos = (Array.isArray(data.videos) ? data.videos : []).filter(item => typeof item.id === 'string' && typeof item.url === 'string' && item.url.startsWith('/api/videos/')).sort((left, right) => {
        const difference = (Number.isFinite(right.created_at) ? right.created_at : 0) - (Number.isFinite(left.created_at) ? left.created_at : 0);
        return difference || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
      });
      const found = new Set(), gallery = $('gallery');
      let position = 0;
      for (const item of videos) {
        if (found.has(item.id)) continue;
        found.add(item.id);
        let card = videoCards.get(item.id);
        if (!card) {
          card = $('video-template').content.firstElementChild.cloneNode(true);
          videoCards.set(item.id, card);
          // Connect template media at its final position before selecting a source.
          // Initializing then moving it can leave mobile players uninitialized.
          gallery.insertBefore(card, gallery.children[position] || null);
          const video = card.querySelector('video');
          attachPlaybackControls(card, item, instance);
          video.src = item.url;
          video.load(); // preload stays none: no media is fetched until playback.
          attachPromptControls(card, item, instance); attachDeleteControls(card, item, instance);
        } else {
          const video = card.querySelector('video');
          // A transient stream error must not persist until the whole page reloads.
          // Healthy and currently playing players keep their source and position.
          if (video.error) video.load();
        }
        card.querySelector('video').setAttribute('aria-label', item.filename || 'Generated video');
        card.querySelector('h3').textContent = item.filename || 'Generated video';
        const date = Number.isFinite(item.created_at) ? new Date(item.created_at * 1000).toLocaleString([], {dateStyle:'medium', timeStyle:'short'}) : '';
        const size = Number.isFinite(item.size_bytes) ? `${(item.size_bytes / 1048576).toFixed(1)} MB` : '';
        card.querySelector('p').textContent = [date, size, 'On GPU only'].filter(Boolean).join(' · ');
        const elapsed = Number.isFinite(item.generation_seconds) && item.generation_seconds > 0 && item.generation_seconds <= 604800 ? Math.round(item.generation_seconds) : null;
        card.querySelector('.generation-time').textContent = elapsed === null ? 'Generation time unavailable' : `Generation time: ${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;
        const before = gallery.children[position] || null;
        if (card !== before) {
          if (typeof gallery.moveBefore === 'function') gallery.moveBefore(card, before);
          else gallery.insertBefore(card, before);
        }
        position++;
      }
      for (const [id, card] of videoCards) if (!found.has(id)) removeVideoCard(id, card);
      gallerySummary();
    } catch (_) {
      if (version === galleryVersion && cloud.instance && String(cloud.instance.id) === instance) $('gallery-message').textContent = cloud.instance.status === 'stopped' ? 'Start the GPU to view its saved videos. They remain on its disk.' : 'Your video gallery will reconnect when the GPU is ready.';
    }
  })();
  try { await galleryFetch; } finally { galleryFetch = null; }
}
async function refreshReadiness() {
  const wasAvailable = available;
  if (!cloud || !cloud.instance || !MODELS[$('model').value] || cloud.instance.status === 'stopped' || operationBusy()) {
    available = false;
    $('availability').textContent = cloud && cloud.instance && cloud.instance.status === 'stopped' ? 'Start your GPU to generate videos.' : cloud && !cloud.instance ? 'Create a GPU above when you are ready.' : 'Preparing your GPU…';
  } else {
    const instance = cloud.instance.id, model = $('model').value;
    try {
      const response = await fetch(`/api/readiness?model=${encodeURIComponent(model)}`, {cache:'no-store'}); const data = await response.json();
      if (!cloud.instance || String(cloud.instance.id) !== String(instance) || $('model').value !== model) return;
      available = Boolean(data.ready);
      $('availability').textContent = data.message || (available ? 'Your GPU is ready. You can queue multiple videos.' : data.missing ? `${data.missing} required model file${data.missing === 1 ? '' : 's'} not available yet.` : 'Preparing your model…');
    } catch (_) {
      if (!cloud.instance || String(cloud.instance.id) !== String(instance) || $('model').value !== model) return;
      available = false; $('availability').textContent = 'Waiting for the private GPU connection…';
    }
  }
  $('connection-dot').classList.toggle('ready', available); updateButton();
  if (available && !wasAvailable) { refreshGallery(); refreshModels(); }
}
button.addEventListener('click', async () => {
  if (posting || !available || !MODELS[$('model').value] || operationBusy() || !cloud.instance) return;
  const prompt = promptInput.value; if (!prompt.trim()) { promptInput.focus(); return; }
  posting = true; updateButton();
  const config = settings(), count = Number($('batch').value), instance_id = cloud.instance.id;
  for (let index = 0; index < count; index++) {
    if (!cloud.instance || String(cloud.instance.id) !== String(instance_id) || operationBusy()) break;
    const job = {id:uuid(), client_id:uuid(), instance_id, state:'posting', config};
    jobs.push(job); saveJobs(); renderJob(job); sessionFor(job);
    stage(job, 'Sending your scene', 'Submitting this variation once to your rented GPU.'); connectEvents(job);
    try {
      const response = await fetch('/api/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({request_id:job.id, client_id:job.client_id, instance_id, prompt, ...config})});
      let data;
      if (response.status >= 400 && response.status < 500) {
        try { data = await response.json(); } catch (_) {}
        if (!data || !data.state) data = {state:'failed', message:'The request was not accepted. Please refresh and try another prompt.'};
      } else data = await response.json();
      if (!currentJob(job)) break;
      applyState(job, data);
      if (data.state === 'not_ready') { available = false; break; }
    } catch (_) {
      job.state = 'unknown'; saveJobs(); renderJob(job);
      stage(job, 'Confirming this request', 'The reply was interrupted. This request will not be sent again.');
    }
    if (active(job)) refreshJob(job);
  }
  posting = false; updateButton();
});
for (const id of ['duration','size','audio','batch']) $(id).addEventListener('change', updateButton);
$('model').addEventListener('change', () => { available = false; modelSettings(); updateButton(); refreshReadiness(); });
$('create-gpu').addEventListener('click', () => cloudAction('create'));
$('start-gpu').addEventListener('click', () => cloudAction('start'));
$('stop-gpu').addEventListener('click', () => { $('stop-confirm').hidden = false; $('destroy-confirm').hidden = true; $('confirm-stop').focus(); });
$('destroy-gpu').addEventListener('click', () => { $('destroy-confirm').hidden = false; $('stop-confirm').hidden = true; $('cancel-destroy').focus(); });
$('cancel-stop').addEventListener('click', () => { $('stop-confirm').hidden = true; $('stop-gpu').focus(); });
$('cancel-destroy').addEventListener('click', () => { $('destroy-confirm').hidden = true; $('destroy-gpu').focus(); });
$('confirm-stop').addEventListener('click', () => cloudAction('stop'));
$('confirm-destroy').addEventListener('click', () => cloudAction('destroy'));
$('refresh-cloud').addEventListener('click', () => { refreshCloud(true); refreshGallery(true); });
$('model-files').addEventListener('toggle', () => { if ($('model-files').open) refreshModels(); });
$('refresh-models').addEventListener('click', () => refreshModels(true));
$('dismiss-model-notice').addEventListener('click', () => { $('model-ready-notice').hidden = true; });
$('enable-model-notifications').addEventListener('click', async () => {
  if (!window.isSecureContext || !('Notification' in window) || Notification.permission !== 'default') return;
  try { await Notification.requestPermission(); } catch (_) {}
  updateNotificationControl();
});
updateNotificationControl();
let installPrompt = null;
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt = event; $('install').hidden = false; });
$('install').addEventListener('click', async () => { if (!installPrompt) return; const prompt = installPrompt; installPrompt = null; $('install').hidden = true; await prompt.prompt(); });
window.addEventListener('appinstalled', () => { $('install').hidden = true; $('install-help').hidden = true; });
if (window.matchMedia('(display-mode: standalone)').matches) $('install-help').hidden = true;
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => { $('offline-note').textContent = 'Use Chrome’s menu → Add to Home screen for a shortcut. Keep the laptop and Tailscale connected.'; });
window.addEventListener('online', () => { refreshCloud(true); refreshReadiness(); refreshGallery(); refreshModels(true).finally(flushModelNotices); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) { updateNotificationControl(); refreshModels(true).finally(flushModelNotices); refreshCloud(Boolean(budgetStamp && Date.now() - budgetStamp.getTime() >= 300000)); refreshReadiness(); refreshGallery(); for (const job of jobs) if (active(job)) refreshJob(job); } });
modelSettings();
refreshCloud();
setInterval(() => {
  refreshReadiness();
  if (!document.hidden && cloud && cloud.instance && cloud.instance.status === 'running') refreshModels(true);
}, 10000);
setInterval(() => { for (const job of jobs) if (active(job)) refreshJob(job); }, 2500);

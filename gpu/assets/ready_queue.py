"""Select the oldest runnable native item, retaining original IDs and numbers."""
import copy
import fcntl
import heapq
import os
from pathlib import Path
import subprocess
import sys
import time
from types import MethodType

from model_storage import MANIFEST, ROOT, ASSETS, complete, target

_children = {}
_failed = {}
PUBLIC = set(MANIFEST)


def model_ids(item):
    # Read only explicit model selectors, never text-conditioning fields.
    selected = set()
    for node in item[2].values():
        inputs = node.get('inputs', {})
        model = inputs.get('model')
        if node.get('class_type') in {'PrivateVideoGenerate', 'PrivateVideoModels'} and isinstance(model, str) and model in MANIFEST:
            selected.add(model)
        if node.get('class_type') == 'UNETLoader':
            filename = inputs.get('unet_name')
            for candidate, spec in MANIFEST.items():
                if spec['native'] and any(entry['destination'] == 'diffusion_models/' + str(filename) for entry in spec['files']):
                    selected.add(candidate)
    return selected


def available(model):
    spec = MANIFEST[model]
    if not all(complete(target(model, item), item['size']) for item in spec['files']): return False
    return spec['native'] or (ROOT / 'turbowan-runtime.installed').is_file()


def raise_if_preparation_failed(model):
    if model in _failed:
        raise RuntimeError('Model preparation failed before generation. Check access, disk and runtime installation before retrying.')


def background_running():
    with (ROOT / 'background-preparation.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return True
    return False


def prepare_pending(items):
    for model, process in list(_children.items()):
        outcome = process.poll()
        if outcome is not None:
            if outcome or not available(model): _failed[model] = time.time() * 1000
            del _children[model]
    if _children: return
    for item in items:
        created = item[3].get('create_time', 0)
        for model in model_ids(item):
            if model in _failed and isinstance(created, (int, float)) and created > _failed[model]:
                del _failed[model]  # A new explicit request can retry preparation.
    pending = {}
    for item in sorted(items, key=lambda value: value[0]):
        for model in model_ids(item):
            if not available(model) and model not in _failed: pending.setdefault(model, item[1])
    # The initial background process owns its sequential download plan.
    background = background_running()
    candidates = [model for model in pending if model not in PUBLIC or not background]
    if not candidates: return
    model = min(candidates, key=lambda value: sum({i['key']: i['size'] for i in MANIFEST[value]['files']}.values()))
    environment = dict(os.environ, VIDEO_PROMPT_ID=pending[model])
    environment.pop('VIDEO_EVENTS_FD', None)
    try:
        _children[model] = subprocess.Popen([sys.executable, str(ASSETS / 'prepare-model.py'), model],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
    except OSError:
        _failed[model] = time.time() * 1000


def install(server):
    queue = server.prompt_queue
    if getattr(queue, 'private_video_ready_fifo', False): return
    def get(self, timeout=None):
        deadline = time.monotonic() + timeout if timeout is not None else None
        with self.not_empty:
            while True:
                prepare_pending(self.queue)
                candidates = [index for index, item in enumerate(self.queue)
                              if all(available(model) or model in _failed for model in model_ids(item))]
                if candidates:
                    index = min(candidates, key=lambda value: self.queue[value][0])
                    item = self.queue.pop(index)
                    heapq.heapify(self.queue)
                    identifier = self.task_counter
                    self.currently_running[identifier] = copy.deepcopy(item)
                    self.task_counter += 1
                    self.server.queue_updated()
                    return item, identifier
                remaining = deadline - time.monotonic() if deadline is not None else None
                if remaining is not None and remaining <= 0: return None
                self.not_empty.wait(timeout=min(1, remaining) if remaining is not None else 1)
    queue.get = MethodType(get, queue)
    queue.private_video_ready_fifo = True

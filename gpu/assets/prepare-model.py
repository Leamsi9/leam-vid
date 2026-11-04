#!/usr/bin/env python3
"""Prepare one explicit model on the GPU; never consumes a prompt or runs inference."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import requests
from private_video_events import emit

ROOT = Path('/workspace/wan-cloud')
ASSETS = Path(__file__).resolve().parent
COMFY = ROOT / 'ComfyUI'
STORE = ROOT / 'model-artifacts'
PROMPT_ID = os.environ.get('VIDEO_PROMPT_ID', '')
TOKEN = ROOT / 'hf-tokens' / (PROMPT_ID if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', PROMPT_ID) else 'invalid')
MANIFEST = json.loads((ASSETS / 'runtime-models.json').read_text())

class PreparationError(Exception):
    def __init__(self, code): self.code = code

def destination(model, item):
    base = COMFY / 'models' if MANIFEST['models'][model]['native'] else ROOT / 'models' / model
    return base / item['destination']

def present(path, size):
    return path.is_file() and path.stat().st_size == size

def prepare(model):
    spec = MANIFEST['models'][model]
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'model-preparation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        STORE.mkdir(exist_ok=True)
        unique = {}
        for item in spec['files']:
            target, blob = destination(model, item), STORE / item['key']
            if not present(target, item['size']) and not present(blob, item['size']):
                unique[item['key']] = item
        # Reserve runtime scratch space before downloading any selected weights.
        reserve = (4 if spec['native'] else 20) * 1024**3
        needed = sum(max(0, item['size'] - ((STORE / (key + '.part')).stat().st_size if (STORE / (key + '.part')).exists() else 0)) for key, item in unique.items())
        if shutil.disk_usage(ROOT).free < needed + reserve:
            raise PreparationError('disk')
        token = TOKEN.read_text().strip() if spec.get('gated') and TOKEN.exists() else None
        if spec.get('gated') and unique and not token:
            raise PreparationError('access')
        if unique:
            emit('stage', stage='downloading')
        try:
            for item in spec['files']:
                target = destination(model, item)
                if not present(target, item['size']):
                    ensure_artifact(item, token)
                    link_artifact(model, item)
        finally:
            if spec.get('gated'):
                TOKEN.unlink(missing_ok=True)
        if not spec['native'] and not (ROOT / 'turbowan-runtime.installed').is_file():
            raise PreparationError('runtime')


def ensure_artifact(item, token):
    STORE.mkdir(exist_ok=True)
    locks = ROOT / 'artifact-locks'
    locks.mkdir(exist_ok=True)
    with (locks / (item['key'] + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        blob = STORE / item['key']
        if not present(blob, item['size']):
            download(item, blob, token)


def link_artifact(model, item):
    target, blob = destination(model, item), STORE / item['key']
    if present(target, item['size']): return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(blob)
    except FileExistsError:
        if not present(target, item['size']) or target.resolve() != blob.resolve():
            raise PreparationError('runtime')


def valid_hash(path, item):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest() == item['key']


def download(item, blob, token):
    partial = Path(str(blob) + '.part')
    url = f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/{item['name']}"
    for attempt in range(5):
        have = partial.stat().st_size if partial.exists() else 0
        if have == item['size']:
            if valid_hash(partial, item):
                partial.replace(blob)
                return
            partial.unlink()
            have = 0
        if have > item['size']:
            partial.unlink()
            have = 0
        headers = {'Range': f'bytes={have}-'} if have else {}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        try:
            # requests removes Authorization on redirects to other hosts. The
            # initial URL is always a pinned official huggingface.co artifact.
            with requests.get(url, headers=headers, stream=True, timeout=(15, 120)) as response:
                if response.status_code in {401, 403}:
                    raise PreparationError('access')
                response.raise_for_status()
                append = response.status_code == 206 and response.headers.get('Content-Range', '').startswith(f'bytes {have}-')
                with partial.open('ab' if append else 'wb') as handle:
                    for chunk in response.iter_content(4 * 1024**2):
                        if chunk: handle.write(chunk)
            if partial.stat().st_size != item['size']:
                raise PreparationError('runtime')
            if not valid_hash(partial, item):
                partial.unlink()
                raise PreparationError('runtime')
            partial.replace(blob)
            return
        except requests.RequestException:
            if attempt == 4: raise PreparationError('runtime')
            time.sleep(2)

if __name__ == '__main__':
    try:
        model = sys.argv[1]
        if model not in MANIFEST['models']: raise PreparationError('runtime')
        prepare(model)
    except PreparationError as error:
        emit('error', code=error.code)
        raise SystemExit(1)
    except Exception:
        emit('error', code='runtime')
        raise SystemExit(1)
    finally:
        TOKEN.unlink(missing_ok=True)

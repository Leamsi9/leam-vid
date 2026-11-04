#!/usr/bin/env python3
import fcntl,json
from model_storage import inventory,ROOT,MANIFEST
result=inventory()
with (ROOT/'background-preparation.lock').open('a') as lock:
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);background=False
 except BlockingIOError:background=True
active=set()
for path in (ROOT/'artifact-locks').glob('*.lock'):
 with path.open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:active.add(path.stem)
result['models'].sort(key=lambda item:item['size_bytes'])
current=next((item['id'] for item in result['models'] if not item['downloaded']),None) if background else None
for item in result['models']:
 downloading=any(entry['key'] in active for entry in MANIFEST[item['id']]['files'])
 if background and item['id']!=current:downloading=False
 preparing=(background or downloading) and not item['downloaded']
 item.update(runtime_ready=(ROOT/'turbowan-runtime.installed').is_file() if item['id']=='turbowan' else True,implementation='official_turbo' if item['id']=='turbowan' else 'comfy',preparing=preparing,stage=('downloading' if downloading else 'queued') if preparing else None)
result.update(background_preparing=background,runtime_preparing=False,runtime_profile='native-v1')
print(json.dumps(result))

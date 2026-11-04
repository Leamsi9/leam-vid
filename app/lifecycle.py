"""Private Vast controls. Vast, SSH processes and remote files own the state."""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import time
import uuid

from vastai.api.client import VastClient
from vastai.api import billing, instances, keys, offers
from model_catalog import MODELS, DEFAULT_MODEL

from settings import DATA, IDENTITY, VAST_KEY, IMAGE, ROOT
BASE = DATA
CONFIG = BASE / 'cloud.json'
ASSETS = ROOT / 'gpu/assets'
REMOTE = '/workspace/wan-cloud'
DISK_GB = int(os.environ.get('LEAM_VID_DISK_GB', '120'))
MODEL_SPECS = {key: value for key, value in json.loads((ASSETS/'runtime-models.json').read_text())['models'].items() if key in {model['id'] for model in MODELS}}
MODEL_BYTES = sum({item['key']: item['size'] for spec in MODEL_SPECS.values() for item in spec['files']}.values())
FIRST_MODEL_BYTES = sum({item['key']: item['size'] for item in MODEL_SPECS[DEFAULT_MODEL]['files']}.values())
OFFERED_MODELS = MODELS



class CloudError(Exception):
    def __init__(self, message, status=409, unknown=False, confirmation=False):
        super().__init__(message)
        self.message, self.status = message, status
        self.unknown, self.confirmation = unknown, confirmation


def request_id(value):
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise CloudError('A valid request identifier is required.', 400)


def number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def provider_boot_message(row):
    # Provider Docker lines describe individual layers, not a complete image.
    # Return only controlled phase text, never raw host/provider diagnostics.
    message = str(row.get('status_msg') or '').lower().splitlines()
    latest = message[-1] if message else ''
    if 'retrying' in latest:
        retry = re.search(r'\bretrying in ([0-9]{1,4}) seconds?\b', latest)
        if retry and 0 < int(retry.group(1)) <= 3600:
            return f'Retrying the GPU image download in {int(retry.group(1))} seconds.'
        return 'Retrying the GPU image download.'
    if 'extracting' in latest or 'unpacking' in latest:
        return 'Unpacking the GPU image layers.'
    if 'verifying checksum' in latest:
        return 'Verifying downloaded GPU image layers.'
    if 'download complete' in latest:
        return 'Downloading the GPU image; another layer finished.'
    if 'pull complete' in latest:
        return 'Assembling the GPU image layers and starting the container.'
    if 'downloading' in latest or 'pulling' in latest:
        return 'Downloading the prebuilt GPU image.'
    return 'Waiting for the GPU container to start and SSH to become available.'


class CloudManager:
    def __init__(self, app, get_queue, is_ready):
        self.app, self.get_queue, self.is_ready = app, get_queue, is_ready
        self.lock = asyncio.Lock()
        self.refresh_lock = asyncio.Lock()
        self.config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {'instance_id': None}
        self.cached = None
        self.cached_at = 0
        self.native = None
        self.selected_offer = None
        self.operation = {'state': 'idle', 'message': ''}
        self.setup = {'state': 'waiting', 'message': 'Waiting for a GPU.'}
        self.tunnel = None
        self.tunnel_instance = None
        self.preparation = None
        self.video_catalog = {}
        self.gallery_at = 0
        self.pending_action = None

    def save(self):
        CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = CONFIG.with_suffix('.tmp')
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(self.config, handle)
        os.replace(temporary, CONFIG)

    async def api(self, function, *args, **kwargs):
        def call():
            key = VAST_KEY.read_text().strip()
            client = VastClient(api_key=key, server_url='https://console.vast.ai', retry=1, timeout=30)
            return function(client, *args, **kwargs)
        return await asyncio.to_thread(call)

    async def all_instances(self):
        return await self.api(instances.show_instances)

    def price(self, offer):
        storage = number(offer.get('storage_cost')) * DISK_GB / 720
        compute = number(offer.get('dph_base'), number(offer.get('dph_total')))
        total = max(number(offer.get('dph_total')), compute + storage)
        return {'offer_id': int(offer['id']), 'hourly_rate': total,
                'compute_hourly_rate': compute, 'storage_hourly_rate': storage, 'disk_gb': DISK_GB,
                'model_download_estimate': (FIRST_MODEL_BYTES / 1e9) * number(offer.get('inet_down_cost')),
                'all_models_download_estimate': (MODEL_BYTES / 1e9) * number(offer.get('inet_down_cost'))}

    def offer_query(self):
        query = {'verified': {'eq': True}, 'rentable': {'eq': True}, 'rented': {'eq': False},
                 'external': {'eq': False}, 'gpu_name': {'eq': 'RTX 5090'}, 'num_gpus': {'eq': 1},
                 'gpu_ram': {'gte': 32000}, 'cpu_ram': {'gte': 64000},
                 'disk_space': {'gte': DISK_GB}, 'cuda_max_good': {'gte': 13.0},
                 'reliability': {'gte': .98}, 'inet_down': {'gte': 500}}
        query['disk_bw'] = {'gte': 500}
        return query

    async def find_offer(self, excluded=None):
        available = []
        for download_mbps in (1000, 500):
            query = self.offer_query()
            query['inet_down'] = {'gte': download_mbps}
            found = await self.api(offers.search_offers, query=query, offer_type='on-demand',
                                   order=[['dph_total', 'asc']], limit=50, storage=DISK_GB)
            available = [offer for offer in found if int(offer['id']) not in (excluded or set())]
            if available:
                break
        self.selected_offer = min(available, key=lambda item: self.price(item)['hourly_rate']) if available else None
        return self.price(self.selected_offer) if self.selected_offer else None

    async def refresh(self, force=False):
        async with self.refresh_lock:
            tracking = self.operation['state'] != 'idle' or (self.preparation and not self.preparation.done())
            if self.cached is not None and not force and time.monotonic() - self.cached_at < (3 if tracking else 300):
                return await self.view()
            try:
                rows, account = await asyncio.gather(self.all_instances(), self.api(billing.show_user))
                selected = self.config.get('instance_id')
                self.native = next((row for row in rows if int(row['id']) == selected), None)
                label = self.config.get('create_label')
                if not self.native and label and not self.config.get('create_confirmed', False):
                    matches = [row for row in rows if row.get('label') == label]
                    if len(matches) == 1:
                        self.native = matches[0]
                        self.config.update(instance_id=int(self.native['id']), create_confirmed=True)
                        self.save()
                        self.operation = {'state': 'idle', 'message': ''}
                        if not self.preparation or self.preparation.done():
                            self.preparation = asyncio.create_task(self.prepare(int(self.native['id']), True))
                    elif len(matches) > 1:
                        raise CloudError('More than one GPU matches the creation request. Resolve this in Vast.ai.', 409, True)
                if selected and self.native is None and not label:
                    self.config['instance_id'] = None
                    self.save()
                pricing = None
                if self.native is None:
                    pricing = await self.find_offer()
                balance = account.get('credit', account.get('balance'))
                self.cached = {'balance': number(balance) if balance is not None else None,
                               'pricing': pricing, 'other_instances': bool(rows and self.native is None)}
                if self.pending_action:
                    action, target = self.pending_action
                    row = next((item for item in rows if int(item['id']) == target), None)
                    if (action == 'destroy' and row is None) or (action == 'stop' and row and row.get('actual_status') in {'stopped', 'exited'}):
                        if action == 'destroy':
                            self.config['instance_id'] = None
                            self.save()
                            self.video_catalog = {}
                            self.gallery_at = 0
                        self.pending_action = None
                        self.operation = {'state': 'idle', 'message': ''}
                    elif action == 'start' and row and row.get('actual_status') == 'running' and row.get('intended_status') not in {'stopped', 'exited'}:
                        self.pending_action = None
                        if not self.preparation or self.preparation.done():
                            self.preparation = asyncio.create_task(self.prepare(target, False))
                self.cached_at = time.monotonic()
            except Exception:
                if self.cached is None:
                    self.cached = {'balance': None, 'pricing': None, 'other_instances': False}
                self.cached['error'] = 'Could not refresh Vast.ai. No account action was taken.'
            return await self.view()

    async def view(self):
        instance = None
        if self.native:
            row = self.native
            status = row.get('actual_status') or row.get('status') or 'unknown'
            if status in {'stopped', 'exited'} or row.get('intended_status') in {'stopped', 'exited'}:
                status = 'stopped'
            elif status not in {'running', 'loading'}:
                status = 'loading'
            storage = number(row.get('storage_cost')) * number(row.get('disk_space'), 70) / 720
            instance = {'id': int(row['id']), 'gpu': row.get('gpu_name', 'RTX 5090'), 'status': status,
                        'hourly_rate': number(row.get('dph_total')), 'storage_hourly_rate': storage}
            if status == 'running':
                if await self.is_ready():
                    self.setup = {'state': 'ready', 'message': 'Ready to make videos.'}
                elif not self.preparation or self.preparation.done():
                    self.setup = {'state': 'connecting', 'message': 'Connecting to your GPU.'}
                    await self.ensure_tunnel(row)
            elif status == 'stopped':
                self.setup = {'state': 'waiting', 'message': 'The GPU is stopped. Storage still costs money.'}
            elif status == 'loading':
                self.setup = {'state': 'connecting', 'message': provider_boot_message(row)}
            if self.operation['state'] == 'creating':
                started = number(row.get('start_date'))
                if 0 < started <= time.time(): instance['setup_started_at'] = started
        else:
            self.setup = {'state': 'waiting', 'message': 'Create a GPU when you want to make videos.'}
        result = {key: value for key, value in (self.cached or {}).items() if key != 'other_instances'}
        result.update(instance=instance, operation=self.operation, setup=self.setup,
                      models=OFFERED_MODELS)
        return result

    def require_instance(self, value):
        try:
            supplied = int(value)
        except (TypeError, ValueError):
            raise CloudError('Refresh the page to select the current GPU.', 409)
        if not self.native or int(self.native['id']) != supplied:
            raise CloudError('The selected GPU changed. Refresh before continuing.', 409)
        return supplied

    def ssh_args(self, row):
        host, port = row.get('ssh_host'), row.get('ssh_port')
        if not host or not re.fullmatch(r'[A-Za-z0-9.-]+', str(host)) or not 1 <= int(port or 0) <= 65535:
            raise CloudError('SSH access is not ready yet.', 503)
        return ['-i', str(IDENTITY), '-p', str(int(port)), '-o', 'BatchMode=yes',
                '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=accept-new',
                '-o', 'ConnectTimeout=10'], f'root@{host}'

    async def ssh(self, row, command, timeout=30):
        args, host = self.ssh_args(row)
        process = await asyncio.create_subprocess_exec('ssh', *args, host, command,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise CloudError('The GPU connection timed out.', 503)
        if process.returncode:
            raise CloudError('The GPU connection is not ready yet.', 503)
        # Vast's SSH proxy may print a fixed greeting before stdout.
        return output.decode(errors='replace')




    async def close_tunnel(self):
        if self.tunnel and self.tunnel.returncode is None:
            self.tunnel.terminate()
            try:
                await asyncio.wait_for(self.tunnel.wait(), 5)
            except asyncio.TimeoutError:
                self.tunnel.kill()
                await self.tunnel.wait()
        self.tunnel, self.tunnel_instance = None, None

    async def ensure_tunnel(self, row):
        identifier = int(row['id'])
        if self.tunnel and self.tunnel.returncode is None and self.tunnel_instance == identifier:
            return
        await self.close_tunnel()
        args, host = self.ssh_args(row)
        self.tunnel = await asyncio.create_subprocess_exec('ssh', *args, '-N', '-g',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
            '-L', '127.0.0.1:8188:127.0.0.1:8188', host,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self.tunnel_instance = identifier

    async def prepare(self, identifier, install):
        self.setup = {'state': 'connecting',
                      'message': 'Waiting for the GPU to boot and SSH access to become available.'}
        try:
            if install:
                # Authorize this known instance before waiting for SSH to work.
                try:
                    await self.api(keys.attach_ssh, instance_id=identifier, ssh_key=IDENTITY.with_suffix('.pub').read_text().strip())
                except Exception:
                    pass  # An already registered account key can still provide access.
            # This bounded task follows one explicit create/start request; no recurring watcher.
            deadline = time.monotonic() + 1800
            row = None
            while time.monotonic() < deadline:
                row = await self.api(instances.show_instance, id=identifier)
                if row and row.get('actual_status') == 'running' and row.get('intended_status') not in {'stopped', 'exited'} and row.get('ssh_host') and row.get('ssh_port'):
                    try:
                        self.setup = {'state': 'connecting', 'message': 'GPU running. Connecting securely through SSH.'}
                        await self.ssh(row, 'mkdir -p /workspace/wan-assets /workspace/wan-cloud')
                        self.setup = {'state': 'installing', 'message': 'SSH connected. Preparing the video software.'}
                        break
                    except CloudError:
                        pass
                await asyncio.sleep(10)
            else:
                raise CloudError('The GPU did not become available. Check Vast.ai before trying again.', 503)
            # Images are built and published ahead of time; no runtime package installation.
            image_profile = await self.ssh(row, 'if test -f /workspace/wan-cloud/native-image-v1.installed; then echo NATIVE_IMAGE; else echo BASE_IMAGE; fi')
            if image_profile.strip().splitlines()[-1] != 'NATIVE_IMAGE':
                raise CloudError('This GPU is missing the prebuilt leam-vid runtime. Configure LEAM_VID_IMAGE before creating a replacement.', 503)
            while time.monotonic() < deadline:
                # A running ComfyUI is preserved, including its model caches.
                self.setup = {'state': 'connecting', 'message': 'Starting ComfyUI on your GPU.'}
                await self.ssh(row, "if ! pgrep -f '^/opt/conda/bin/python main.py.*--port 8188' >/dev/null; then nohup /workspace/wan-cloud/start-comfy-video.sh > /workspace/wan-cloud/comfy.log 2>&1 < /dev/null & fi")
                self.setup = {'state': 'connecting', 'message': 'ComfyUI starting. Connecting the secure SSH tunnel.'}
                await self.ensure_tunnel(row)
                if await self.is_ready():
                    await self.ensure_background_preparation(row)
                    self.setup = {'state': 'ready', 'message': 'Ready to make videos.'}
                    break
                await asyncio.sleep(5)
            else:
                raise CloudError('Setup is taking longer than expected. Check Vast.ai; the GPU is still billed.', 503)
        except Exception:
            self.setup = {'state': 'waiting', 'message': 'Setup needs attention. The GPU may still be billed; check Vast.ai.'}
        finally:
            self.operation = {'state': 'idle', 'message': ''}
            self.cached_at = 0

    async def act(self, action, body):
        nonce = request_id(body.get('request_id'))
        async with self.lock:
            # The generation submission lock also excludes stop/destroy while posting.
            async with self.app['submission_lock']:
                await self.refresh(force=True)
                if self.preparation and not self.preparation.done():
                    if action not in {'stop', 'destroy'}:
                        raise CloudError('GPU setup is already running. Wait before changing it.')
                    if body.get('confirm') is not True:
                        raise CloudError('GPU setup will be interrupted. Confirm before continuing.', 409, confirmation=True)
                    self.preparation.cancel()
                    await asyncio.gather(self.preparation, return_exceptions=True)
                if action == 'create':
                    if not IMAGE:
                        raise CloudError('Set LEAM_VID_IMAGE to your published GPU image before creating a rental.', 409)
                    if self.native:
                        return await self.view()
                    label = 'leam-vid-' + nonce
                    previous = self.config.get('create_label')
                    if previous == label or (previous and not self.config.get('create_confirmed', False)):
                        raise CloudError('The previous creation request is being checked in Vast.ai. It will not be repeated.', 409, True)
                    if self.cached.get('error') or self.cached.get('other_instances'):
                        raise CloudError('Resolve the existing Vast.ai account state before creating another GPU.')
                    offer = self.selected_offer
                    if not offer:
                        raise CloudError('No matching GPU is available right now. Try Create GPU again shortly.', 409)
                    self.config.update(instance_id=None, create_label=label, create_confirmed=False)
                    self.save()
                    self.operation = {'state': 'creating', 'message': 'Allocating one GPU in Vast.ai.'}
                    excluded = set()
                    for attempt in range(3):
                        try:
                            result = await self.api(instances.create_instance, id=int(offer['id']), image=IMAGE,
                                disk=DISK_GB, label=label, ssh=True, direct=True,
                                onstart_cmd='mkdir -p /workspace/wan-assets /workspace/wan-cloud; if test -f /workspace/wan-cloud/native-image-v1.installed; then nohup /workspace/wan-cloud/start-comfy-video.sh > /workspace/wan-cloud/comfy.log 2>&1 < /dev/null & fi')
                            if result.get('success') is False:
                                self.config['create_confirmed'] = True
                                self.save()
                                self.operation = {'state': 'idle', 'message': ''}
                                raise CloudError('Vast.ai did not accept this GPU request. Create GPU will search again.', 409)
                            break
                        except CloudError:
                            raise
                        except Exception as error:
                            response = getattr(error, 'response', None)
                            status = response.status_code if response is not None else None
                            # Only explicit missing/conflicting/expired-offer rejections
                            # permit reselection. Lost replies are never retried.
                            if status in {404, 409, 410} and attempt < 2:
                                try:
                                    rows = await self.all_instances()
                                    accepted = [row for row in rows if row.get('label') == label]
                                    if len(accepted) == 1:
                                        result = {'new_contract': int(accepted[0]['id'])}
                                        break
                                    if rows:
                                        raise CloudError('An existing GPU must be resolved before creating another.', 409, True)
                                    excluded.add(int(offer['id']))
                                    await self.find_offer(excluded)
                                    if not self.selected_offer:
                                        raise CloudError('No matching GPU is currently available. Try Create GPU again shortly.', 409)
                                    offer = self.selected_offer
                                    continue
                                except CloudError as rejection:
                                    if not rejection.unknown:
                                        self.config['create_confirmed'] = True
                                        self.save()
                                        self.operation = {'state': 'idle', 'message': ''}
                                    else:
                                        self.operation = {'state': 'unknown', 'message': rejection.message}
                                    raise
                                except Exception:
                                    self.operation = {'state': 'unknown', 'message': 'Checking Vast.ai before repeating any creation request.'}
                                    raise CloudError(self.operation['message'], 503, True)
                            if status is not None and 400 <= status < 500:
                                self.config['create_confirmed'] = True
                                self.save()
                                self.operation = {'state': 'idle', 'message': ''}
                                raise CloudError('Vast.ai did not accept this GPU request. Create GPU will search again.', 409)
                            self.operation = {'state': 'unknown', 'message': 'Checking Vast.ai before repeating any creation request.'}
                            raise CloudError(self.operation['message'], 503, True)
                    identifier = result.get('new_contract') or result.get('instance_id')
                    if not identifier:
                        self.operation = {'state': 'unknown', 'message': 'Checking the accepted creation request in Vast.ai.'}
                        raise CloudError('Vast.ai did not confirm the GPU identifier. Refresh before doing anything else.', 503, True)
                    identifier = int(identifier)
                    self.config.update(instance_id=identifier, create_confirmed=True)
                    self.save()
                    self.preparation = asyncio.create_task(self.prepare(identifier, True))
                else:
                    identifier = self.require_instance(body.get('instance_id'))
                    if action == 'destroy' and body.get('confirm') is not True:
                        raise CloudError('Confirm deletion of this GPU and all its videos.', 409, confirmation=True)
                    if action in {'stop', 'destroy'}:
                        try:
                            queue = await self.get_queue()
                            active = bool(queue.get('queue_running') or queue.get('queue_pending'))
                        except Exception:
                            active = True
                        if active and body.get('confirm') is not True:
                            raise CloudError('Queued or running videos will be interrupted. Confirm before continuing.', 409, confirmation=True)
                        self.operation = {'state': 'stopping' if action == 'stop' else 'destroying', 'message': 'Stopping the GPU.' if action == 'stop' else 'Deleting the GPU and its videos.'}
                        try:
                            result = await self.api(instances.stop_instance if action == 'stop' else instances.destroy_instance, id=identifier)
                        except Exception:
                            self.operation = {'state': 'unknown', 'message': 'Checking the outcome in Vast.ai.'}
                            self.pending_action = (action, identifier)
                            raise CloudError('The action was not confirmed. Refresh Vast.ai before trying it again.', 503, True)
                        if result.get('success') is False:
                            self.operation = {'state': 'idle', 'message': ''}
                            raise CloudError('Vast.ai did not accept the action.', 503)
                        self.pending_action = (action, identifier)
                        await self.close_tunnel()
                        if action == 'destroy':
                            self.config['instance_id'] = None
                            self.save()
                            self.native = None
                            self.video_catalog = {}
                            self.gallery_at = 0
                    elif action == 'start':
                        self.operation = {'state': 'starting', 'message': 'Starting your existing GPU.'}
                        if self.native.get('actual_status') != 'running' or self.native.get('intended_status') in {'stopped', 'exited'}:
                            try:
                                result = await self.api(instances.start_instance, id=identifier)
                            except Exception:
                                self.operation = {'state': 'unknown', 'message': 'Checking the start request in Vast.ai.'}
                                self.pending_action = ('start', identifier)
                                raise CloudError('Starting was not confirmed. Refresh before trying again.', 503, True)
                            if result.get('success') is False:
                                self.operation = {'state': 'idle', 'message': ''}
                                self.pending_action = None
                                self.setup = {'state': 'waiting', 'message': 'Vast.ai rejected the start request. The GPU has not started.'}
                                self.cached_at = 0
                                raise CloudError('Vast.ai rejected the start request. The GPU has not started. Check its availability in Vast.ai before trying again.', 503)
                        self.preparation = asyncio.create_task(self.prepare(identifier, False))
                    else:
                        raise CloudError('Unknown GPU action.', 400)
                self.cached_at = 0
                return await self.refresh(force=True)

    async def ensure_background_preparation(self, row):
        # Called only on explicit create/start or laptop reconnect, never on status
        # reads. The native completion marker preserves intentional model removal.
        await self.ssh(row, "if test -f /workspace/wan-assets/prepare-background.py && ! test -f /workspace/wan-cloud/initial-prefetch-native-v1.complete; then nohup /opt/conda/bin/python /workspace/wan-assets/prepare-background.py >> /workspace/wan-cloud/background-preparation.log 2>&1 < /dev/null & fi")

    async def resume(self):
        """Reconnect only a native running rental; never rent or start a GPU."""
        await self.refresh(force=True)
        if not self.native or self.native.get('actual_status') != 'running' or self.native.get('intended_status') in {'stopped', 'exited'}:
            return
        await self.ensure_tunnel(self.native)
        if await self.is_ready():
            await self.ensure_background_preparation(self.native)
        if self.config.get('create_label') and (not self.preparation or self.preparation.done()) and not await self.is_ready():
            # A prior explicit create authorized installation. Recover its native files/process.
            try:
                output = await self.ssh(self.native, 'if test -x /workspace/wan-cloud/start-comfy-video.sh; then echo STAGED; else echo FRESH; fi')
                install = output.strip().splitlines()[-1] != 'STAGED'
            except CloudError:
                install = True
            self.preparation = asyncio.create_task(self.prepare(int(self.native['id']), install))

    async def gallery(self, force=False):
        async with self.lock:
            return await self._gallery(force)

    async def _gallery(self, force=False):
        await self.refresh()
        if not self.native or self.native.get('actual_status') != 'running':
            return {'instance_id': self.config.get('instance_id'), 'videos': []}
        identifier = int(self.native['id'])
        if force or time.monotonic() - self.gallery_at > 10:
            code = """import json,os,stat,math,urllib.request
from pathlib import Path
root=Path('/workspace/wan-cloud/ComfyUI/output').resolve()
timings={}
# Only execution timestamps and output locations are accessed. No prompts,
# inputs, exception text or workflow metadata are examined or returned.
try:
 with urllib.request.urlopen('http://127.0.0.1:8188/history',timeout=5) as response:
  histories=json.load(response)
 for history in histories.values():
  events=history.get('status',{}).get('messages',[])
  starts=[event[1].get('timestamp') for event in events if len(event)==2 and event[0]=='execution_start']
  ends=[event[1].get('timestamp') for event in events if len(event)==2 and event[0]=='execution_success']
  if not starts or not ends: continue
  seconds=(float(ends[-1])-float(starts[0]))/1000
  if not math.isfinite(seconds) or not 0<seconds<=604800: continue
  for output in history.get('outputs',{}).values():
   for kind in ('images','videos','gifs'):
    for item in output.get(kind,[]):
     if item.get('type')=='output':
      relative=(Path(item.get('subfolder',''))/item.get('filename','')).as_posix()
      timings[relative]=(seconds,float(starts[0])/1000,float(ends[-1])/1000)
except (OSError,ValueError,TypeError,KeyError):
 pass
try:
 import av
except ImportError:
 av=None
def has_writer(info):
 # Check only descriptor flags and inode metadata; never read video contents.
 for process in Path('/proc').iterdir():
  if not process.name.isdigit(): continue
  try: descriptors=list((process/'fd').iterdir())
  except OSError: continue
  for descriptor in descriptors:
   try: opened=descriptor.stat()
   except OSError: continue
   if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino): continue
   try:
    flags=next(line.split()[1] for line in (process/'fdinfo'/descriptor.name).read_text().splitlines() if line.startswith('flags:'))
    if int(flags,8)&os.O_ACCMODE in (os.O_WRONLY,os.O_RDWR): return True
   except FileNotFoundError: continue
   except (OSError,ValueError,StopIteration): return True
 return False
items=[]
if root.is_dir():
 for directory,dirs,files in os.walk(root,followlinks=False):
  dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
  for name in files:
   path=Path(directory)/name
   if path.suffix.lower() not in {'.mp4','.webm'} or path.is_symlink(): continue
   if not path.resolve().is_relative_to(root): continue
   try: info=path.stat()
   except FileNotFoundError: continue
   if not stat.S_ISREG(info.st_mode) or info.st_size<=0 or has_writer(info): continue
   try: after=path.stat()
   except FileNotFoundError: continue
   if (after.st_ino,after.st_size,after.st_mtime_ns)!=(info.st_ino,info.st_size,info.st_mtime_ns): continue
   relative=path.relative_to(root).as_posix()
   seconds=None
   try:
    value=float(os.getxattr(path,'user.private_video_generation_seconds'))
    if math.isfinite(value) and 0<value<=604800: seconds=value
   except (OSError,ValueError): pass
   matched=timings.get(relative)
   if seconds is None and matched is not None and matched[1]-2<=info.st_mtime<=matched[2]+2:
    seconds=matched[0]
    try: os.setxattr(path,'user.private_video_generation_seconds',repr(seconds).encode())
    except OSError: pass
   if seconds is None and av is not None:
    try:
     with av.open(str(path),mode='r') as container:
      value=container.metadata.get('generation_seconds')
     if value is not None:
      value=float(value)
      if math.isfinite(value) and 0<value<=604800: seconds=value
    except Exception: pass
   items.append({'path':relative,'size':info.st_size,'created':info.st_mtime,
                 'generation_seconds':seconds,
                 'identity':[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]})
print('VIDEO_CATALOG='+json.dumps(items))
"""
            output = await self.ssh(self.native, "python3 - <<'PY'\n" + code + '\nPY')
            line = next((line for line in output.splitlines() if line.startswith('VIDEO_CATALOG=')), None)
            if line is None:
                raise CloudError('Could not read the remote video list.', 503)
            catalog = {}
            for item in json.loads(line.split('=', 1)[1]):
                path = PurePosixPath(item['path'])
                if path.is_absolute() or '..' in path.parts or '\\' in str(path):
                    continue
                identity = item['identity']
                opaque = hashlib.sha256(f'{identifier}\0{path}\0{identity}'.encode()).hexdigest()
                catalog[opaque] = {'relative': str(path), 'filename': path.name,
                    'identity': identity,
                    'created_at': item['created'], 'size_bytes': item['size'], 'generation_seconds': item.get('generation_seconds'), 'id': opaque,
                    'url': '/api/videos/' + opaque}
            self.video_catalog = catalog
            self.gallery_at = time.monotonic()
        videos = [{key: value for key, value in item.items() if key not in {'relative', 'identity'}} for item in self.video_catalog.values()]
        return {'instance_id': identifier, 'videos': sorted(videos, key=lambda item: item['created_at'], reverse=True)}

    async def delete_video(self, identifier, body):
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise CloudError('The video is no longer available.', 404)
        if body.get('confirm') is not True:
            raise CloudError('Confirm deletion of this video.', 409, confirmation=True)
        async with self.lock:
            async with self.app['submission_lock']:
                await self.refresh()
                instance_id = self.require_instance(body.get('instance_id'))
                if self.operation['state'] != 'idle' or self.native.get('actual_status') != 'running':
                    raise CloudError('Start the GPU before deleting one of its videos.', 409)
                previous = self.video_catalog.get(identifier)
                await self._gallery(force=True)
                self.require_instance(instance_id)
                item = self.video_catalog.get(identifier)
                if not item:
                    if previous and any(video['relative'] == previous['relative'] for video in self.video_catalog.values()):
                        raise CloudError('This video is still changing. Wait until it finishes, then refresh.', 409)
                    # Already absent identifiers are harmless no-ops. No path
                    # supplied by the browser is ever used for deletion.
                    return {'deleted': True, 'id': identifier, 'instance_id': instance_id}
                code = """import json,os,stat
from pathlib import Path
root=Path('/workspace/wan-cloud/ComfyUI/output').resolve()
path=root/RELATIVE_PATH
expected=EXPECTED_IDENTITY
def identity(info):
 return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]
def delete_selected():
 try:
  info=path.lstat()
 except FileNotFoundError:
  return 'absent'
 if not stat.S_ISREG(info.st_mode) or path.suffix.lower() not in {'.mp4','.webm'} or not path.resolve().is_relative_to(root):
  return 'invalid'
 if identity(info)!=expected:
  return 'changed'
 # Inspect only file descriptors and access flags, never file contents,
 # process arguments, generation history or embedded metadata.
 try:
  processes=list(Path('/proc').iterdir())
  for process in processes:
   if not process.name.isdigit(): continue
   try: descriptors=list((process/'fd').iterdir())
   except FileNotFoundError: continue
   for descriptor in descriptors:
    try:
     opened=descriptor.stat()
     if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino): continue
     flags=(process/'fdinfo'/descriptor.name).read_text()
     access=next(int(line.split()[1],8) for line in flags.splitlines() if line.startswith('flags:'))
     if (access & os.O_ACCMODE) in {os.O_WRONLY,os.O_RDWR}: return 'busy'
    except FileNotFoundError: continue
 except (OSError,ValueError,StopIteration):
  return 'unverified'
 try:
  if identity(path.lstat())!=expected or not path.resolve().is_relative_to(root): return 'changed'
  path.unlink()
 except FileNotFoundError:
  return 'absent'
 return 'deleted'
print('VIDEO_DELETE='+json.dumps({'state':delete_selected()}))
""".replace('RELATIVE_PATH', repr(item['relative']), 1).replace('EXPECTED_IDENTITY', repr(item['identity']), 1)
                try:
                    output = await self.ssh(self.native, "python3 - <<'PY'\n" + code + '\nPY')
                except CloudError:
                    raise CloudError('The delete reply was interrupted. Refresh the video list to check the result before trying again.', 503, unknown=True)
                finally:
                    # Even a lost SSH reply must cause a fresh filesystem read.
                    self.gallery_at = 0
                line = next((line for line in output.splitlines() if line.startswith('VIDEO_DELETE=')), None)
                try:
                    state = json.loads(line.split('=', 1)[1]).get('state') if line else None
                except (ValueError, AttributeError):
                    state = None
                if state not in {'deleted', 'absent', 'busy', 'changed', 'invalid', 'unverified'}:
                    raise CloudError('The deletion result is unconfirmed. Refresh the video list before trying again.', 503, unknown=True)
                if state in {'busy', 'changed'}:
                    raise CloudError('This video is still being written or has changed. Wait until it finishes, then refresh.', 409)
                if state not in {'deleted', 'absent'}:
                    raise CloudError('Could not safely delete this video. No other files were changed.', 409)
                self.video_catalog.pop(identifier, None)
                return {'deleted': True, 'id': identifier, 'instance_id': instance_id}

    async def video_prompt(self, identifier):
        if not re.fullmatch(r'[0-9a-f]{64}', identifier):
            raise CloudError('The video is no longer available.', 404)
        async with self.lock:
            listing = await self._gallery(force=True)
            if not self.native or identifier not in {item['id'] for item in listing['videos']}:
                raise CloudError('The video is no longer available.', 404)
            relative = self.video_catalog[identifier]['relative']
            # Read only the container header on the GPU; return only the original
            # positive prompt. No video bytes or complete metadata leave the GPU.
            code = """import json
from pathlib import Path
root=Path('/workspace/wan-cloud/ComfyUI/output').resolve()
relative=RELATIVE_PATH
path=root/relative
result={'found':False,'prompt':None}
if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root) and path.suffix.lower() in {'.mp4','.webm'}:
 result['found']=True
 try:
  import av
  with av.open(str(path),mode='r') as container:
   tags={key.lower():value for key,value in container.metadata.items()}
   raw=tags.get('prompt') or tags.get('comment')
  if isinstance(raw,str) and len(raw)<=8388608:
   graph=json.loads(raw)
   if isinstance(graph,str): graph=json.loads(graph)
   if isinstance(graph,dict) and isinstance(graph.get('prompt'),dict): graph=graph['prompt']
   if isinstance(graph,dict):
    for node in graph.values():
     if not isinstance(node,dict) or node.get('class_type') not in {'PrivateVideoGenerate','MiniMaxH3ImageToVideo'}: continue
     prompt=node.get('inputs',{}).get('prompt')
     if isinstance(prompt,str) and result['prompt'] is None:
      result['prompt']=prompt
      break
 except Exception:
  pass
print('VIDEO_PROMPT='+json.dumps(result))
""".replace('RELATIVE_PATH', repr(relative), 1)
            output = await self.ssh(self.native, "/opt/conda/bin/python - <<'PY'\n" + code + '\nPY')
            line = next((line for line in output.splitlines() if line.startswith('VIDEO_PROMPT=')), None)
            if line is None:
                raise CloudError('Could not read this video’s saved prompt.', 503)
            result = json.loads(line.split('=', 1)[1])
            if not result.get('found'):
                raise CloudError('The video is no longer available.', 404)
            prompt = result.get('prompt')
            if isinstance(prompt, str):
                return {'prompt': prompt}
            return {'prompt': None, 'message': 'This video does not contain a readable saved prompt.'}

    async def close(self):
        if self.preparation and not self.preparation.done():
            self.preparation.cancel()
            await asyncio.gather(self.preparation, return_exceptions=True)
        await self.close_tunnel()

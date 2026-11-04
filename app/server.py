#!/usr/bin/env python3
"""A private, local interface to the SSH-tunnelled ComfyUI instance."""
import asyncio
import json
import math
import secrets
import time
import uuid
from pathlib import Path, PurePosixPath

import aiohttp
from aiohttp import web
from lifecycle import CloudManager, CloudError
from model_catalog import DEFAULT_MODEL, MODEL_BY_ID
from native_image_graphs import graph as native_image_graph

ROOT = Path(__file__).resolve().parent
from settings import COMFY, HOSTS, ORIGINS, BIND, PORT
APP_SHELL_ROUTES = ('/', '/app.js', '/style.css', '/manifest.webmanifest', '/sw.js',
                    '/icon.svg', '/icon-192.png', '/icon-512.png')
def model_id(value=DEFAULT_MODEL):
    if not isinstance(value, str) or value not in MODEL_BY_ID:
        raise web.HTTPBadRequest(text='Choose one of the supported video models.')
    return value


def expand_prompt_choices(prompt):
    """Resolve balanced {a|b} groups without interpreting any other language."""
    pairs, openings = {}, []
    for position, character in enumerate(prompt):
        if character == '{':
            openings.append(position)
        elif character == '}' and openings:
            pairs[openings.pop()] = position
    # Ranges keep nested choices iterative; malformed text and literal braces survive.
    pending, output = [(0, len(prompt))], []
    while pending:
        part = pending.pop()
        if isinstance(part, str):
            output.append(part)
            continue
        cursor, end = part
        pieces = []
        while cursor < end:
            opening = prompt.find('{', cursor, end)
            if opening < 0:
                pieces.append(prompt[cursor:end])
                break
            pieces.append(prompt[cursor:opening])
            closing = pairs.get(opening)
            if closing is None or closing >= end:
                pieces.append(prompt[opening:end])
                break
            alternatives, start, position = [], opening + 1, opening + 1
            while position < closing:
                if prompt[position] == '{':
                    position = pairs[position] + 1
                    continue
                if prompt[position] == '|':
                    alternatives.append((start, position))
                    start = position + 1
                position += 1
            if alternatives:
                alternatives.append((start, closing))
                pieces.append(secrets.choice(alternatives))
            else:
                pieces.extend(('{', (opening + 1, closing), '}'))
            cursor = closing + 1
        pending.extend(reversed(pieces))
    return ''.join(output)


def valid_id(value):
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise web.HTTPBadRequest(text='Invalid request identifier.')


def generation_config(body):
    model = model_id(body.get('model', DEFAULT_MODEL))
    spec = MODEL_BY_ID[model]
    try:
        duration = float(body.get('duration', 5))
        width = int(body.get('width', spec['default_width']))
        height = int(body.get('height', spec['default_height']))
    except (ValueError, TypeError):
        raise web.HTTPBadRequest(text='Choose a valid duration and video size.')
    audio = body.get('audio', spec['audio'])
    if not math.isfinite(duration) or duration not in spec['durations']:
        raise web.HTTPBadRequest(text='Choose one of the supported durations for this model.')
    if [width, height] not in spec['sizes'] or not isinstance(audio, bool) or (audio and not spec['audio']):
        raise web.HTTPBadRequest(text='Choose a supported video size and sound setting.')
    fps = spec['fps']
    if model in {'h3', 'fasth3_v2', 'fasth3_trim'}:
        frames = math.ceil((duration * fps - 5) / 17) * 17 + 5
    elif model == 'turbowan':
        frames = 81
    else:
        frames = math.ceil(duration * fps / 8) * 8 + 1
    return {'model': model, 'duration': duration, 'actual_duration': frames / fps,
            'width': width, 'height': height, 'audio': audio, 'frames': frames,
            'fps': fps, 'seed': secrets.randbelow(2**53)}


def graph(prompt, config):
    if config['model'] in {'h3', 'fasth3_v2', 'fasth3_trim'}:
        return native_image_graph(prompt, config)
    return {
        '30': {'class_type': 'PrivateVideoGenerate', 'inputs': {
            'model': config['model'], 'prompt': prompt, 'width': config['width'],
            'height': config['height'], 'frames': config['frames'],
            'seed': config['seed'], 'audio': config['audio']}},
        '31': {'class_type': 'SaveVideo', 'inputs': {
            'video': ['30', 0], 'filename_prefix': 'video/' + config['model'],
            'format': 'mp4', 'format.codec': 'h264', 'format.codec.encoding': 'auto'}},
    }


async def upstream(app, path):
    async with app['http'].get(COMFY + path, timeout=aiohttp.ClientTimeout(total=15)) as response:
        response.raise_for_status()
        return await response.json()




def output_files(history):
    files = []
    for output in history.get('outputs', {}).values():
        for key in ('images', 'videos', 'gifs'):
            for item in output.get(key, []):
                name, folder = item.get('filename', ''), item.get('subfolder', '')
                if (item.get('type') == 'output' and name == PurePosixPath(name).name
                        and PurePosixPath(name).suffix.lower() in {'.mp4', '.webm'}
                        and not PurePosixPath(folder).is_absolute()
                        and '..' not in PurePosixPath(folder).parts
                        and '\\' not in folder and '\\' not in name):
                    files.append({'filename': name, 'subfolder': folder, 'type': 'output'})
    return files


async def job_state(app, prompt_id):
    deletion_key = (app['cloud'].config.get('instance_id'), prompt_id)
    deletion = app['queue_deletions'].get(deletion_key)
    # Native task_done moves running jobs into history under the queue mutex.
    # Read queue first so completion between these reads cannot look missing.
    queue = await upstream(app, '/queue')
    # Recovered requests retain their UUID. A native queued/running copy is
    # authoritative over an earlier failed preparation history for that UUID.
    for kind, state in [('queue_running', 'running'), ('queue_pending', 'queued')]:
        if any(len(item) > 1 and item[1] == prompt_id for item in queue.get(kind, [])):
            if deletion is not None and app['queue_deletions'].get(deletion_key) is deletion:
                app['queue_deletions'].pop(deletion_key, None)
            return {'state': state}, None
    history = (await upstream(app, '/history/' + prompt_id)).get(prompt_id)
    if history:
        if deletion is not None and app['queue_deletions'].get(deletion_key) is deletion:
            app['queue_deletions'].pop(deletion_key, None)
        if prompt_id not in app['budget_refreshed']:
            app['budget_refreshed'].add(prompt_id)
            app['cloud'].gallery_at = 0
            asyncio.create_task(app['cloud'].refresh(force=True))
        status = history.get('status', {})
        videos = output_files(history)
        if status.get('status_str') == 'error':
            return {'state': 'failed', 'message': 'Generation stopped. You can try a new prompt.'}, history
        if videos:
            return {'state': 'complete', 'videos': [f'/video/{prompt_id}/{i}' for i in range(len(videos))]}, history
        return {'state': 'failed', 'message': 'Generation ended without a playable video.'}, history
    if deletion is not None and app['queue_deletions'].get(deletion_key) is deletion:
        deletion['state'] = 'cancelled'
        return {'state': 'cancelled', 'message': 'Removed from the queue.'}, None
    return {'state': 'unknown'}, None


async def preparation_status(app):
    """Briefly cache native file statistics; never read prompts or media."""
    cloud = app['cloud']
    row = cloud.native
    if not row or row.get('actual_status') != 'running': return None
    instance_id = int(row['id'])
    async with app['preparation_status_lock']:
        cache = app['preparation_status_cache']
        if cache and cache['instance_id'] == instance_id and time.monotonic() - cache['at'] < 5:
            return cache['data']
        try:
            output = await cloud.ssh(row, '/opt/conda/bin/python /workspace/wan-assets/preparation-status.py', timeout=12)
            data = json.loads(output[output.index('{'):])
            if not isinstance(data.get('models'), list): return None
        except (CloudError, ValueError, KeyError):
            return None
        app['preparation_status_cache'] = {'instance_id': instance_id, 'at': time.monotonic(), 'data': data}
        return data


async def model_readiness(app, model):
    data = await preparation_status(app) or await upstream(app, '/private_video/models')
    item = next((entry for entry in data['models'] if entry['id'] == model), None)
    if not item:
        return {'ready': False, 'model': model, 'message': 'The selected runtime is not installed on this GPU.'}
    if model in {'fasth3_v2', 'fasth3_trim'} and item.get('implementation') != 'comfy':
        return {'ready': False, 'model': model, 'prepared': False, 'message': 'Checking the native Comfy model download.'}
    prepared = item['downloaded'] and (model == 'h3' or item.get('runtime_ready') is True)
    if not item['can_prepare']:
        return {'ready': False, 'model': model, 'prepared': prepared,
                'message': 'Not enough free GPU disk for this model. Remove unused models or use a larger rental.'}
    if model in {'fasth3_v2', 'fasth3_trim'} and not item['downloaded'] and data.get('runtime_profile') != 'native-v1':
        return {'ready': False, 'model': model, 'prepared': False,
                'message': f"Downloading the native Comfy model: {item['downloaded_bytes'] / 1e9:.1f} / {item['size_bytes'] / 1e9:.1f} GB. Other installed models remain available."}
    message = 'Files and runtime installed. Ready for your manual trial.' if prepared else 'Ready to queue. First use downloads and prepares this model.'
    if item.get('stage') == 'downloading':
        message = f"Downloading in the background: {item['downloaded_bytes'] / 1e9:.1f} / {item['size_bytes'] / 1e9:.1f} GB. You can queue a video now."
    elif item.get('stage') == 'preparing':
        message = 'Preparing video software in the background. You can queue a video now.'
    return {'ready': True, 'model': model, 'prepared': prepared, 'message': message, 'runtime_profile': data.get('runtime_profile')}


@web.middleware
async def private_boundary(request, handler):
    if request.host not in HOSTS:
        raise web.HTTPForbidden(text='Use the private studio address.')
    origin = request.headers.get('Origin')
    if origin and origin not in ORIGINS:
        raise web.HTTPForbidden(text='Origin not allowed.')
    # The public shell contains no user data. PWA launches and service-worker
    # refreshes may omit navigation metadata; privileged routes never qualify.
    public_shell = request.method in {'GET', 'HEAD'} and request.path in APP_SHELL_ROUTES
    if request.headers.get('Sec-Fetch-Site') == 'cross-site' and not public_shell:
        raise web.HTTPForbidden(text='Cross-site access is disabled.')
    if request.method == 'POST' and (origin not in ORIGINS or request.content_type != 'application/json'):
        raise web.HTTPForbidden(text='A same-origin JSON request is required.')
    try:
        response = await handler(request)
    except CloudError as error:
        response = web.json_response({'state': 'unknown' if error.unknown else 'failed',
            'message': error.message, 'requires_confirmation': error.confirmation}, status=error.status)
    if not isinstance(response, web.WebSocketResponse):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Cross-Origin-Resource-Policy': 'same-origin', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        })
    return response


async def readiness(request):
    model = model_id(request.query.get('model', DEFAULT_MODEL))
    try:
        return web.json_response(await model_readiness(request.app, model))
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError):
        return web.json_response({'ready': False, 'model': model, 'message': 'Connecting to your GPU launcher…'})


async def model_inventory(request):
    cloud = request.app['cloud']
    async with cloud.lock:
        await cloud.refresh()
        if not cloud.native or cloud.native.get('actual_status') != 'running':
            return web.json_response({'instance_id': cloud.config.get('instance_id'), 'models': [],
                                      'message': 'Start the GPU to view its downloaded models.'})
        try:
            data = await preparation_status(request.app) or await upstream(request.app, '/private_video/models')
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise CloudError('The GPU model inventory is unavailable.', 503)
        return web.json_response(dict(data, instance_id=int(cloud.native['id'])))


async def remove_model(request):
    model = model_id(request.match_info['model'])
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise CloudError('A JSON request is required.', 400)
    if not isinstance(body, dict):
        raise CloudError('A JSON object is required.', 400)
    if body.get('confirm') is not True:
        raise CloudError('Confirm removal of this downloaded model. Videos are kept.', 409, confirmation=True)
    app, cloud = request.app, request.app['cloud']
    async with cloud.lock:
        async with app['submission_lock']:
            await cloud.refresh()
            instance_id = cloud.require_instance(body.get('instance_id'))
            if cloud.operation['state'] != 'idle' or cloud.native.get('actual_status') != 'running':
                raise CloudError('Start the GPU before removing downloaded model files.', 409)
            try:
                preparation = await preparation_status(app)
                if preparation and preparation.get('background_preparing'):
                    raise CloudError('Wait until background model preparation finishes before removing files.', 409)
                if model in {'fasth3_v2', 'fasth3_trim'}:
                    native_inventory = await upstream(app, '/private_video/models')
                    old_item = next((item for item in native_inventory.get('models', []) if item.get('id') == model), None)
                    current_item = next((item for item in (preparation or {}).get('models', []) if item.get('id') == model), None)
                    if not old_item or not current_item or old_item.get('size_bytes') != current_item.get('size_bytes'):
                        raise CloudError('Native model removal will be available after the GPU runtime is updated while idle. No files were removed.', 409)
                queue = await upstream(app, '/queue')
                if queue.get('queue_running') or queue.get('queue_pending'):
                    raise CloudError('Wait until all queued and running videos finish.', 409)
                async with app['http'].post(COMFY + '/private_video/models/' + model + '/remove',
                        json={}, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    result = await response.json()
                    if response.status >= 400:
                        raise CloudError(result.get('message', 'The model could not be removed.'), response.status)
                    if result.get('removed') is not True or result.get('model') != model:
                        raise CloudError('Removal is unconfirmed. Refresh the downloaded model list.', 503, unknown=True)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                raise CloudError('Removal is unconfirmed. Refresh the downloaded model list before trying again.', 503, unknown=True)
            app['preparation_status_cache'] = None
            return web.json_response(dict(result, instance_id=instance_id))


async def cloud_status(request):
    return web.json_response(await request.app['cloud'].refresh(force=request.query.get('refresh') == '1'))


async def cloud_action(request):
    try:
        body = await request.json()
    except ValueError:
        raise CloudError('A JSON request is required.', 400)
    if not isinstance(body, dict):
        raise CloudError('A JSON object is required.', 400)
    return web.json_response(await request.app['cloud'].act(request.match_info['action'], body))


async def gallery(request):
    try:
        return web.json_response(await request.app['cloud'].gallery(force=request.query.get('refresh') == '1'))
    except (CloudError, ValueError, StopIteration):
        raise CloudError('The remote videos are unavailable. Start or reconnect your GPU to view them.', 503)


async def gallery_prompt(request):
    return web.json_response(await request.app['cloud'].video_prompt(request.match_info['identifier']))


async def gallery_delete(request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise CloudError('A JSON request is required.', 400)
    if not isinstance(body, dict):
        raise CloudError('A JSON object is required.', 400)
    return web.json_response(await request.app['cloud'].delete_video(request.match_info['identifier'], body))


async def gallery_video(request):
    identifier = request.match_info['identifier']
    if len(identifier) != 64 or any(character not in '0123456789abcdef' for character in identifier):
        raise web.HTTPNotFound()
    cloud = request.app['cloud']
    await cloud.gallery(force=True)
    item = cloud.video_catalog.get(identifier)
    if not item:
        raise web.HTTPNotFound()
    path = PurePosixPath(item['relative'])
    return await stream_output(request, {'filename': path.name, 'subfolder': str(path.parent) if str(path.parent) != '.' else '', 'type': 'output'})


async def submit(request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text='Invalid JSON.')
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text='A JSON object is required.')
    prompt_id = valid_id(body.get('request_id'))
    client_id = valid_id(body.get('client_id'))
    prompt = body.get('prompt', '')
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 12000:
        raise web.HTTPBadRequest(text='Write a prompt of up to 12,000 characters.')
    config = generation_config(body)
    cloud = request.app['cloud']
    async with cloud.lock:
        async with request.app['submission_lock']:
            await cloud.refresh()
            cloud.require_instance(body.get('instance_id'))
            if cloud.operation['state'] != 'idle' or cloud.native.get('actual_status') != 'running':
                return web.json_response({'state': 'not_ready', 'message': 'Wait until your GPU is running and ready.'}, status=503)
            try:
                state, _ = await job_state(request.app, prompt_id)
                if state['state'] != 'unknown':
                    return web.json_response({'prompt_id': prompt_id, **state})
                if prompt_id in request.app['attempts']:
                    return web.json_response({'prompt_id': prompt_id, 'state': 'unknown', 'message': 'Checking the previous request. It has not been sent again.'}, status=202)
                readiness = await model_readiness(request.app, config['model'])
                if not readiness['ready']:
                    return web.json_response({'state': 'not_ready', 'message': readiness['message']}, status=503)
                if readiness.get('runtime_profile'):
                    config['runtime_profile'] = readiness['runtime_profile']
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return web.json_response({'state': 'not_ready', 'message': 'The GPU connection is unavailable. Nothing was sent.'}, status=503)
            # Resolve each new job once, after native UUID reconciliation, never on retry.
            resolved_prompt = expand_prompt_choices(prompt)
            request.app['attempts'].add(prompt_id)
            payload = {'prompt': graph(resolved_prompt, config), 'client_id': client_id, 'prompt_id': prompt_id}
            try:
                async with request.app['http'].post(COMFY + '/prompt', json=payload, timeout=aiohttp.ClientTimeout(total=60)) as response:
                    result = await response.json()
                    if response.status >= 400:
                        return web.json_response({'prompt_id': prompt_id, 'state': 'failed', 'message': 'The GPU could not accept this workflow. Setup may still be finishing.'}, status=422)
                    if result.get('prompt_id') != prompt_id:
                        return web.json_response({'prompt_id': prompt_id, 'state': 'unknown'}, status=202)
                    return web.json_response({'prompt_id': prompt_id, 'state': 'queued', 'config': config})
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return web.json_response({'prompt_id': prompt_id, 'state': 'unknown', 'message': 'The reply was interrupted. Checking the GPU before doing anything else.'}, status=202)


async def status(request):
    prompt_id = valid_id(request.match_info['prompt_id'])
    try:
        state, _ = await job_state(request.app, prompt_id)
        selected = request.query.get('model')
        if state['state'] == 'running' and selected in MODEL_BY_ID:
            data = await preparation_status(request.app)
            item = next((item for item in (data or {}).get('models', []) if item['id'] == selected), None)
            if item and item.get('stage'):
                state['preparation'] = {key: item[key] for key in ('stage', 'downloaded_bytes', 'size_bytes')}
                state['preparation']['runtime_preparing'] = bool(data.get('runtime_preparing'))
        return web.json_response(state)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return web.json_response({'state': 'disconnected'})


async def cancel_queued(request):
    prompt_id = valid_id(request.match_info['prompt_id'])
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text='Invalid JSON.')
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text='A JSON object is required.')
    app, cloud = request.app, request.app['cloud']
    async with cloud.lock:
        async with app['submission_lock']:
            await cloud.refresh()
            instance_id = cloud.require_instance(body.get('instance_id'))
            if cloud.operation['state'] != 'idle' or cloud.native.get('actual_status') != 'running':
                raise CloudError('Wait until the current GPU is running before cancelling a queued video.', 409)
            try:
                state, _ = await job_state(app, prompt_id)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return web.json_response({'state': 'disconnected', 'message': 'Could not check the queue. Nothing was cancelled.'})
            if state['state'] != 'queued':
                if state['state'] == 'running':
                    state['message'] = 'This video has started. Only queued videos can be cancelled.'
                elif state['state'] == 'unknown':
                    state['message'] = 'This video is no longer in the queue; its outcome is unknown.'
                return web.json_response(state)
            try:
                # Native deletion touches pending entries only. If this UUID
                # starts in the meantime, it is never interrupted.
                async with app['http'].post(COMFY + '/queue', json={'delete': [prompt_id]},
                        timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status == 200:
                        # Keep this acknowledged UUID if the following read
                        # disconnects; a later status read can reconcile it.
                        app['queue_deletions'][(instance_id, prompt_id)] = {'state': 'acknowledged'}
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass  # Reconcile native state; never repeat an uncertain POST.
            try:
                state, _ = await job_state(app, prompt_id)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return web.json_response({'state': 'disconnected', 'message': 'Cancellation is unconfirmed. Reconnecting to check the queue.'})
            if state['state'] == 'running':
                state['message'] = 'This video started before it could be removed. It is still running.'
            elif state['state'] == 'queued':
                state['message'] = 'The video is still queued. Cancellation was not confirmed.'
            elif state['state'] == 'unknown':
                state['message'] = 'The video is no longer queued, but cancellation could not be confirmed.'
            return web.json_response(state)


async def socket(request):
    if request.headers.get('Origin') not in ORIGINS:
        raise web.HTTPForbidden()
    client_id = valid_id(request.query.get('client_id'))
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    try:
        async with request.app['http'].ws_connect(COMFY + '/ws', params={'clientId': client_id}, heartbeat=25) as remote:
            async def relay():
                async for message in remote:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(message.data)
                        if data.get('type') in {'executing', 'progress', 'execution_start', 'execution_success', 'execution_error', 'execution_interrupted', 'execution_cached', 'private_video_stage'}:
                            detail = data.get('data', {})
                            # Errors can include full prompt traces; expose only safe event fields.
                            await ws.send_json({'type': data['type'], 'data': {key: detail[key] for key in ('prompt_id', 'node', 'value', 'max', 'nodes', 'stage') if key in detail}})
            async def until_browser_closes():
                async for _ in ws:
                    pass
            tasks = {asyncio.create_task(relay()), asyncio.create_task(until_browser_closes())}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, ConnectionError):
        if not ws.closed:
            await ws.send_json({'type': 'disconnected'})
    finally:
        await ws.close()
    return ws


async def stream_output(request, item):
    headers = {'Range': request.headers['Range']} if 'Range' in request.headers else {}
    try:
        async with request.app['http'].get(COMFY + '/view', params=item, headers=headers, timeout=aiohttp.ClientTimeout(total=None, sock_read=120)) as remote:
            if remote.status not in {200, 206, 416}:
                raise web.HTTPBadGateway(text='The remote video is unavailable.')
            outgoing = {name: remote.headers[name] for name in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges') if name in remote.headers}
            outgoing.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin'})
            response = web.StreamResponse(status=remote.status, headers=outgoing)
            await response.prepare(request)
            async for chunk in remote.content.iter_chunked(256 * 1024):
                await response.write(chunk)
            return response
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(text='The GPU connection is unavailable.')


async def video(request):
    prompt_id = valid_id(request.match_info['prompt_id'])
    try:
        index = int(request.match_info['index'])
        history = (await upstream(request.app, '/history/' + prompt_id)).get(prompt_id, {})
        videos = output_files(history)
        if index < 0 or index >= len(videos):
            raise web.HTTPNotFound()
        return await stream_output(request, videos[index])
    except (ValueError, aiohttp.ClientError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(text='The GPU connection is unavailable.')


async def lifecycle(app):
    app['http'] = aiohttp.ClientSession()
    async def ready():
        try:
            return bool((await upstream(app, '/private_video/models')).get('models'))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return False
    app['cloud'] = CloudManager(app, lambda: upstream(app, '/queue'), ready)
    recovery = asyncio.create_task(app['cloud'].resume())
    yield
    recovery.cancel()
    await asyncio.gather(recovery, return_exceptions=True)
    await app['cloud'].close()
    await app['http'].close()


async def main():
    app = web.Application(client_max_size=128 * 1024, middlewares=[private_boundary])
    app['preparation_status_cache'] = None
    app['preparation_status_lock'] = asyncio.Lock()
    app['attempts'] = set()
    # Native ComfyUI keeps no history for deleted queue entries. These transient
    # UUID acknowledgments/outcomes contain no prompts and are never persisted.
    app['queue_deletions'] = {}
    app['submission_lock'] = asyncio.Lock()
    app['budget_refreshed'] = set()
    app.cleanup_ctx.append(lifecycle)
    async def static_file(request):
        filename = 'index.html' if request.path == '/' else request.path.lstrip('/')
        response = web.FileResponse(ROOT / filename)
        if filename == 'manifest.webmanifest':
            response.content_type = 'application/manifest+json'
        elif filename == 'sw.js':
            response.content_type = 'application/javascript'
            response.headers['Service-Worker-Allowed'] = '/'
        return response
    for route in APP_SHELL_ROUTES:
        app.router.add_get(route, static_file)
    app.router.add_get('/api/readiness', readiness)
    app.router.add_get('/api/cloud', cloud_status)
    app.router.add_get('/api/models', model_inventory)
    app.router.add_post('/api/models/{model}/remove', remove_model)
    app.router.add_post('/api/cloud/{action:create|start|stop|destroy}', cloud_action)
    app.router.add_get('/api/videos', gallery)
    app.router.add_get('/api/videos/{identifier}/prompt', gallery_prompt)
    app.router.add_post('/api/videos/{identifier}/delete', gallery_delete)
    app.router.add_get('/api/videos/{identifier}', gallery_video)
    app.router.add_post('/api/generate', submit)
    app.router.add_get('/api/jobs/{prompt_id}', status)
    app.router.add_post('/api/jobs/{prompt_id}/cancel', cancel_queued)
    app.router.add_get('/ws', socket)
    app.router.add_get('/video/{prompt_id}/{index}', video)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    for host in BIND:
        await web.TCPSite(runner, host, PORT).start()
    print('leam-vid listening on configured private addresses.', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    asyncio.run(main())

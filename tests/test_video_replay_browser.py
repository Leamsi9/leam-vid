#!/usr/bin/env python3
"""Public synthetic browser boundary check; never connects to installed Studio/GPU."""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import websocket
except ImportError:
    websocket = None

APP = Path(__file__).resolve().parents[1] / 'app'
FIRST, SECOND = '1' * 64, '2' * 64


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, video):
        super().__init__(('127.0.0.1', 0), FixtureHandler)
        self.video = video
        self.instance = 42
        self.status = 'running'
        self.video_ids = [FIRST]
        self.media_requests = 0
        self.hold = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.next_shell = False
        self.reject_destroy = False
        self.unexpected = []

    def cloud(self):
        return {'instance': None if self.instance is None else {'id': self.instance, 'status': self.status, 'gpu': 'Public fixture GPU', 'hourly_rate': 0.5},
                'operation': {'state': 'idle'}, 'setup': {'state': 'ready'}, 'balance': 10,
                'models': [{'id': 'turbowan', 'label': 'Public Turbo fixture', 'durations': [5], 'sizes': [[832, 480]],
                            'audio': False, 'default_width': 832, 'default_height': 480, 'sampler_node': '30', 'steps': 4}]}


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, kind='application/json'):
        body = json.dumps(value).encode() if kind == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/cloud':
            return self.reply(self.server.cloud())
        if path == '/api/readiness':
            return self.reply({'ready': self.server.status == 'running', 'message': 'Public browser fixture'})
        if path == '/api/models':
            return self.reply({'instance_id': self.server.instance, 'models': [], 'message': 'Public inventory fixture'})
        if path == '/api/videos':
            return self.reply({'instance_id': self.server.instance, 'videos': [
                {'id': ident, 'url': '/api/videos/' + ident, 'filename': 'public-' + ident[0] + '.mp4',
                 'created_at': int(ident[0]), 'size_bytes': len(self.server.video), 'generation_seconds': 1}
                for ident in self.server.video_ids]})
        if path in {'/api/videos/' + FIRST, '/api/videos/' + SECOND}:
            self.server.media_requests += 1
            self.server.started.set()
            if self.server.hold:
                self.server.release.wait(15)
            return self.reply(self.server.video, kind='video/mp4')
        name = 'index.html' if path == '/' else path.lstrip('/')
        if name not in {'index.html', 'app.js', 'style.css', 'sw.js', 'manifest.webmanifest', 'icon.svg', 'icon-192.png', 'icon-512.png'}:
            return self.reply({}, 404)
        body = (APP / name).read_bytes()
        if name == 'sw.js' and self.server.next_shell:
            version = int(re.search(rb'SHELL_VERSION = (\d+)', body).group(1))
            body = body.replace(f'SHELL_VERSION = {version}'.encode(), f'SHELL_VERSION = {version + 1}'.encode()).replace(f'shell-v{version}'.encode(), f'shell-v{version + 1}'.encode()).replace(f'v={version}'.encode(), f'v={version + 1}'.encode())
        kinds = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css', 'svg': 'image/svg+xml', 'png': 'image/png', 'webmanifest': 'application/manifest+json'}
        return self.reply(body, kind=kinds[name.rsplit('.', 1)[-1]])

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if path == '/api/cloud/stop':
            self.server.status = 'stopped'
        elif path == '/api/cloud/start':
            self.server.status = 'running'
        elif path == '/api/cloud/destroy':
            if self.server.reject_destroy:
                return self.reply({'state':'failed','message':'Public rejected destroy fixture'},400)
            self.server.instance = None
            self.server.video_ids = []
        elif path == '/api/videos/' + FIRST + '/delete':
            self.server.video_ids.remove(FIRST)
            return self.reply({'deleted': True, 'id': FIRST, 'instance_id': self.server.instance})
        else:
            self.server.unexpected.append(path)
            return self.reply({}, 400)
        return self.reply(self.server.cloud())


@unittest.skipUnless(websocket and shutil.which('google-chrome') and shutil.which('ffmpeg'), 'Requires Chrome, ffmpeg and websocket-client')
class ReplayBrowser(unittest.TestCase):
    def test_public_replay_and_lifecycle(self):
        with tempfile.TemporaryDirectory(prefix='leam-vid-public-browser-') as temporary:
            root = Path(temporary)
            video = root / 'public.mp4'
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=12',
                            '-t', '1', '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video)], check=True)
            server = FixtureServer(video.read_bytes())
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = subprocess.Popen(['google-chrome', '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-gpu', '--mute-audio', '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                '--disable-component-update', '--disable-sync', '--disable-extensions', '--remote-debugging-port=0',
                '--user-data-dir=' + str(root / 'profile'), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            connection = None
            try:
                port_file = root / 'profile' / 'DevToolsActivePort'
                self.wait(lambda: port_file.exists())
                port = port_file.read_text().splitlines()[0]
                with urllib.request.urlopen('http://127.0.0.1:' + port + '/json/list') as response:
                    target = next(item for item in json.load(response) if item['type'] == 'page')
                connection = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=20, suppress_origin=True)
                self.connection, self.sequence = connection, 0
                self.rpc('Page.enable')
                self.rpc('Page.addScriptToEvaluateOnNewDocument', {'source': '''
                  window.__created = []; window.__revoked = [];
                  const make = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
                  URL.createObjectURL = blob => { const url = make(blob); __created.push(url); return url; };
                  URL.revokeObjectURL = url => { __revoked.push(url); revoke(url); };
                  localStorage.setItem('leam-vid-jobs-v1', JSON.stringify([
                    {id:'public-complete',client_id:'public-client',instance_id:42,state:'complete'},
                    {id:'public-failed',client_id:'public-client',instance_id:42,state:'failed'}]));
                '''})
                self.rpc('Page.navigate', {'url': 'http://127.0.0.1:' + str(server.server_port) + '/'})
                self.wait_js("typeof videoCards !== 'undefined' && videoCards.size === 1 && cloud && cloud.instance && available")
                self.assertEqual(server.media_requests, 0, 'Gallery must not preload videos')
                self.assertIsNone(self.js("document.getElementById('past-jobs-section')"))
                self.assertEqual(self.js('jobs.length'), 0)
                self.assertIsNone(self.js("localStorage.getItem(JOBS_KEY)"))
                self.js("window.__first = [...videoCards.values()][0]; window.__video = __first.querySelector('video'); const original = __video.play.bind(__video); let denyOnce = true; __video.play = () => {if (denyOnce) {denyOnce=false; return Promise.reject(new DOMException('Public denied gesture fixture','NotAllowedError'));} return original();}")
                self.js("__first.querySelector('.play-video').click()")
                self.wait_js("__first.querySelector('.playback-status').textContent.includes('Tap Play video') || __first.querySelector('.playback-status').textContent.includes('could not')")
                self.assertIn('Tap Play video',self.js("__first.querySelector('.playback-status').textContent"),self.js("JSON.stringify({status:__first.querySelector('.playback-status').textContent,error:__video.error&&__video.error.code,entries:videoReplayCache.entries.size,created:__created.length})"))
                self.assertEqual(server.media_requests, 1)
                self.js("__first.querySelector('.play-video').click()")
                self.wait_js('__video.currentTime > 0.05')
                self.js('__video.currentTime = 0; __video.play()')
                self.wait_js('__video.currentTime > 0.05')
                self.js('__video.currentTime = 0.7; __video.pause()')
                self.assertEqual(server.media_requests, 1, 'Replay and seek reuse the Blob')
                server.video_ids.append(SECOND)
                self.js('refreshGallery(true)')
                self.wait_js("document.querySelector('#gallery h3').textContent === 'public-2.mp4'")
                self.assertEqual(self.js("__video.getAttribute('src') === __created[0]"), True)
                self.js("cloudAction('stop')")
                self.wait_js("cloud.instance.status === 'stopped'")
                self.js("__video.currentTime = 0; __first.querySelector('.play-video').click()")
                self.wait_js('!__video.paused')
                self.assertEqual(server.media_requests, 1, 'Stop retains loaded playback')
                self.js("[...videoCards.values()].find(card=>card!==__first).querySelector('.play-video').click()")
                self.assertEqual(server.media_requests, 1, 'Uncached playback while stopped must ask to start GPU')
                self.js('videoReplayCache.now = () => Date.now() + 24*60*60*1000 + 1; videoReplayCache.prune(); videoReplayCache.now = Date.now')
                self.assertIsNone(self.js("__video.getAttribute('src')"))
                self.assertEqual(self.js('__revoked.length'), 1)
                self.js("cloudAction('start')")
                self.wait_js("cloud.instance.status === 'running' && !cloudPosting")
                self.js("__first.querySelector('.play-video').click()")
                self.wait_js('videoReplayCache.entries.size === 1 && __created.length === 2')
                self.assertEqual(server.media_requests, 2)
                self.js("promptInput.value = 'Public draft before destroy'; $('batch').value = '3'; $('queued-jobs-section').open = true; $('destroy-gpu').click()")
                self.assertEqual(self.js('promptInput.value'), 'Public draft before destroy', 'Opening confirmation retains draft')
                server.reject_destroy = True
                self.js("cloudAction('destroy')")
                self.assertEqual(self.js('videoReplayCache.entries.size'), 1, 'A rejected destroy retains replay')
                self.assertIsNotNone(self.js("__video.getAttribute('src')"))
                self.assertEqual(self.js('promptInput.value'), 'Public draft before destroy', 'Rejected destruction retains draft')
                self.assertEqual(self.js("$('batch').value"), '3')
                server.reject_destroy = False
                self.js("__first.querySelector('.delete-video').click(); __first.querySelector('.confirm-delete').click()")
                self.wait_js('videoReplayCache.entries.size === 0 && videoCards.size === 1')
                self.assertIsNone(self.js("__video.getAttribute('src')"))
                self.assertEqual(self.js('__revoked.length'), 2)
                server.hold = True; server.started.clear()
                self.js("[...videoCards.values()][0].querySelector('.play-video').click()")
                self.assertTrue(server.started.wait(5))
                self.js("window.__realFetch = fetch; fetch = (url, options) => url === '/api/generate' ? new Promise((resolve, reject) => window.__lateSubmitReject = reject) : __realFetch(url, options); $('generate').click()")
                self.wait_js('posting && jobs.length === 1')
                self.js("cloudAction('destroy')")
                self.wait_js('cloud.instance === null && videoCards.size === 0 && videoReplayCache.entries.size === 0')
                self.assertEqual(self.js('promptInput.value'), '')
                self.assertEqual(self.js("$('batch').value"), '1')
                self.assertEqual(self.js("$('model').value"), 'turbowan')
                self.assertEqual(self.js("$('duration').value"), '5')
                self.assertEqual(self.js("$('audio').value"), 'off')
                self.assertFalse(self.js("$('queued-jobs-section').open || $('model-files').open || !$('destroy-confirm').hidden"))
                self.assertEqual(self.js('jobs.length + sessions.size + cards.size + modelRows.size + pendingModelNotices.size'), 0)
                self.assertIsNone(self.js('localStorage.getItem(JOBS_KEY)'))
                self.js("promptInput.value = 'Public fresh draft after destroy'; __lateSubmitReject(new Error('Public interrupted submit')); fetch = __realFetch; refreshCloud(true)")
                self.assertEqual(self.js('promptInput.value'), 'Public fresh draft after destroy')
                self.assertEqual(self.js('jobs.length'), 0, 'Late submission failure cannot restore recovery/card')
                server.release.set()
                time.sleep(0.2)
                self.assertEqual(self.js('__created.length'), 2, 'Late destroyed download must not create a Blob URL')
                server.instance, server.status, server.video_ids = 43, 'running', [SECOND]
                self.js('refreshCloud(true)')
                self.wait_js('galleryInstance === "43" && videoCards.size === 1')
                self.assertEqual(self.js('videoReplayCache.entries.size'), 0)
                self.js("promptInput.value = 'Public external destroy draft'")
                server.instance = None
                self.js('refreshCloud(true)')
                self.wait_js('cloud.instance === null')
                self.assertEqual(self.js('promptInput.value'), '', 'Confirmed external destruction clears draft')
                self.js("promptInput.value = 'Public fresh absent draft'; refreshCloud(true)")
                self.assertEqual(self.js('promptInput.value'), 'Public fresh absent draft', 'Repeated absent polls preserve next draft')
                self.js("initialCloud = true; lastInstance = undefined; promptInput.value = 'Public bootstrap draft'; refreshCloud(true)")
                self.assertEqual(self.js('promptInput.value'), 'Public bootstrap draft', 'Initial absent bootstrap preserves draft')
                self.js("promptInput.value = 'Public draft retained during update'; navigator.serviceWorker.ready")
                server.next_shell = True
                self.js('navigator.serviceWorker.getRegistration().then(registration => registration.update())')
                self.wait_js("!document.getElementById('studio-update').hidden", timeout=15)
                self.assertEqual(self.js('promptInput.value'), 'Public draft retained during update')
                persistent = self.js("Promise.all((await caches.keys()).map(async key => (await (await caches.open(key)).keys()).map(request=>request.url))).then(groups=>groups.flat())")
                self.assertFalse(any('/api/' in url or url.startswith('blob:') for url in persistent))
                self.assertEqual(self.js('sessionStorage.length'), 0)
                self.assertEqual(self.js('JSON.stringify(localStorage).includes("blob:")'), False)
                self.assertEqual(server.unexpected, [])
                print('Public isolated Chrome passed: lazy one-fetch replay/seek, denied-play retry without fetch, newest-first DOM reuse, Stop replay, expiry/delete/destroy buffers revoked, late response fenced, accepted/external Destroy resets draft/settings/jobs and rejects late submit, rejected Destroy and new draft preserved, terminal jobs absent, explicit SW update keeps draft, no persistent media.')
            finally:
                server.release.set()
                if connection:
                    connection.close()
                browser.terminate()
                try:
                    browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    browser.kill(); browser.wait()
                server.shutdown(); server.server_close()

    @staticmethod
    def wait(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('Public browser fixture timed out')

    def rpc(self, method, params=None):
        self.sequence += 1
        self.connection.send(json.dumps({'id': self.sequence, 'method': method, 'params': params or {}}))
        while True:
            message = json.loads(self.connection.recv())
            if message.get('id') == self.sequence:
                if 'error' in message:
                    raise AssertionError(message['error'])
                return message.get('result', {})

    def js(self, expression):
        result = self.rpc('Runtime.evaluate', {'expression': expression, 'replMode': True,
                                              'awaitPromise': True, 'returnByValue': True, 'userGesture': True})
        if 'exceptionDetails' in result:
            raise AssertionError(result['exceptionDetails'].get('exception', {}).get('description', 'Public fixture JavaScript error'))
        return result.get('result', {}).get('value')

    def wait_js(self, expression, timeout=15):
        self.wait(lambda: self.js('(()=>{try{return Boolean(' + expression + ')}catch{return false}})()'), timeout)


if __name__ == '__main__':
    unittest.main()

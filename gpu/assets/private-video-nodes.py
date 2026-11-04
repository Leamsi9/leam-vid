"""Two local ComfyUI nodes: native model preparation and isolated GPU pipelines."""
import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import comfy.model_management
import comfy.utils
import folder_paths
import nodes
from comfy_api.latest import InputImpl
from server import PromptServer

ASSETS = Path('/workspace/wan-assets')
ROOT = Path('/workspace/wan-cloud')
MANIFEST = json.loads((ASSETS / 'runtime-models.json').read_text())
ERRORS = {
    'access': 'This model requires Hugging Face access. Accept its license and configure your local read token.',
    'disk': 'This GPU does not have enough free disk for the selected model and runtime. Use a larger rental.',
    'runtime': 'Model preparation could not finish. Check model access, disk space and runtime installation.',
    'generation': 'The selected runtime could not finish this video. Its weights and software may need attention.',
}

def stage(value):
    server = PromptServer.instance
    server.send_sync('private_video_stage', {'prompt_id': server.last_prompt_id, 'stage': value})

def command(arguments, payload=None, progress=None):
    read_fd, write_fd = os.pipe()
    environment = dict(os.environ, VIDEO_EVENTS_FD=str(write_fd),
        PYTHONPATH=str(ASSETS), VIDEO_PROMPT_ID=PromptServer.instance.last_prompt_id, HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1', WANDB_DISABLED='true')
    error = None
    process = None
    try:
        process = subprocess.Popen(arguments, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(write_fd,), env=environment,
            start_new_session=True)
        os.close(write_fd); write_fd = None
        if payload is not None:
            try:
                process.stdin.write(json.dumps(payload).encode())
                process.stdin.close()
            except BrokenPipeError:
                pass
        with os.fdopen(read_fd) as events:
            read_fd = None
            for line in events:
                event = json.loads(line)
                if event.get('kind') == 'stage':
                    stage(event['stage'])
                elif event.get('kind') == 'progress' and progress is not None:
                    stage('sampling')
                    progress.update_absolute(event['value'], event['max'])
                elif event.get('kind') == 'error':
                    error = event.get('code')
        if process.wait():
            raise RuntimeError(ERRORS.get(error, ERRORS['runtime']))
    finally:
        if write_fd is not None: os.close(write_fd)
        if read_fd is not None: os.close(read_fd)
        if process is not None and process.poll() is None:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()

def prepare(model):
    from ready_queue import raise_if_preparation_failed
    raise_if_preparation_failed(model)
    stage('preparing')
    command([sys.executable, str(ASSETS / 'prepare-model.py'), model])

def model_file(model, prefix):
    return next(item['destination'].split('/', 1)[1] for item in MANIFEST['models'][model]['files']
                if item['destination'].startswith(prefix + '/'))

class PrivateVideoModels:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'model': ([key for key, value in MANIFEST['models'].items() if value['native']],)}}
    RETURN_TYPES = ('MODEL', 'CLIP', 'VAE', 'VAE')
    RETURN_NAMES = ('model', 'clip', 'video_vae', 'audio_vae')
    FUNCTION = 'load'
    CATEGORY = 'private video'
    def load(self, model):
        prepare(model)
        stage('loading')
        unet = nodes.UNETLoader().load_unet(model_file(model, 'diffusion_models'), 'default')[0]
        clip = nodes.CLIPLoader().load_clip(model_file(model, 'text_encoders'),
                                           type='minimax', device='default')[0]
        vaes = [item['destination'].split('/', 1)[1] for item in MANIFEST['models'][model]['files']
                if item['destination'].startswith('vae/')]
        video_name = next(name for name in vaes if 'video' in name)
        audio_name = next(name for name in vaes if 'audio' in name)
        return unet, clip, nodes.VAELoader().load_vae(video_name)[0], nodes.VAELoader().load_vae(audio_name)[0]

class PrivateH3Lora:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'model': ('MODEL',)}}
    RETURN_TYPES = ('MODEL',)
    FUNCTION = 'apply'
    CATEGORY = 'private video'
    def apply(self, model):
        return nodes.LoraLoaderModelOnly().load_lora_model_only(
            model, 'minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors', 1.0)

class ConsumedVideo(InputImpl.VideoFromFile):
    """Remove only our scratch copy after native SaveVideo succeeds."""
    def __init__(self, path):
        super().__init__(str(path))
        self.owned_path = path

    def save_to(self, *args, **kwargs):
        result = super().save_to(*args, **kwargs)
        self.owned_path.unlink(missing_ok=True)
        return result

class PrivateVideoGenerate:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'model': ([key for key, value in MANIFEST['models'].items() if not value['native']],),
            'prompt': ('STRING', {'multiline': True}),
            'width': ('INT', {'default': 832, 'min': 32, 'max': 1344}),
            'height': ('INT', {'default': 480, 'min': 32, 'max': 768}),
            'frames': ('INT', {'default': 81, 'min': 1, 'max': 400}),
            'seed': ('INT', {'default': 0, 'min': 0, 'max': 2**53 - 1}),
            'audio': ('BOOLEAN', {'default': False}),
        }}
    RETURN_TYPES = ('VIDEO',)
    FUNCTION = 'generate'
    CATEGORY = 'private video'
    def generate(self, model, prompt, width, height, frames, seed, audio):
        if model != 'turbowan': raise ValueError('Unsupported external model.')
        prepare(model)
        # A cache-free Comfy graph releases prior node outputs; also clear GPU
        # residency before another Python/CUDA runtime touches this single GPU.
        comfy.model_management.unload_all_models()
        gc.collect()
        comfy.model_management.soft_empty_cache()
        progress = comfy.utils.ProgressBar(4)
        progress.update_absolute(0)
        scratch = Path(folder_paths.get_temp_directory()) / 'private-video'
        scratch.mkdir(parents=True, exist_ok=True)
        output = scratch / (str(uuid.uuid4()) + '.mp4')
        request = {'model': model, 'prompt': prompt, 'width': width, 'height': height,
                   'frames': frames, 'seed': seed, 'audio': audio,
                   'output_path': str(output), 'model_root': str(ROOT / 'models' / model)}
        script = 'runner-turbowan.py'
        command([sys.executable, str(ASSETS / script)], request, progress)
        if not output.is_file(): raise RuntimeError(ERRORS['generation'])
        stage('saving')
        return (ConsumedVideo(output),)

NODE_CLASS_MAPPINGS = {'PrivateVideoModels': PrivateVideoModels, 'PrivateVideoGenerate': PrivateVideoGenerate, 'PrivateH3Lora': PrivateH3Lora}
NODE_DISPLAY_NAME_MAPPINGS = {'PrivateVideoModels': 'Private video models', 'PrivateH3Lora': 'H3 Turbo LoRA'}

# These routes exist only on the loopback Comfy server. The laptop checks its
# exact private Origin/current instance before forwarding any mutation.
sys.path.insert(0, str(ASSETS))
from aiohttp import web
from model_storage import inventory, remove_model
from ready_queue import install as install_ready_queue
install_ready_queue(PromptServer.instance)

@PromptServer.instance.routes.get('/private_video/models')
async def private_models(request):
    data = inventory()
    data['queue_policy'] = 'oldest_ready_fifo'
    data['runtime_profile'] = 'native-v1'
    return web.json_response(data)

@PromptServer.instance.routes.post('/private_video/models/{model}/remove')
async def private_remove(request):
    model = request.match_info['model']
    if model not in MANIFEST['models']:
        raise web.HTTPBadRequest(text='Unknown model.')
    queue = PromptServer.instance.prompt_queue
    # Native queue mutex makes this atomic against direct Comfy submissions,
    # not only the laptop's own serialized submission API.
    with queue.mutex:
        running, pending = queue.get_current_queue()
        if running or pending:
            return web.json_response({'message': 'Wait until all queued and running videos finish.'}, status=409)
        try:
            return web.json_response(remove_model(model))
        except (OSError, RuntimeError, ValueError):
            return web.json_response({'message': 'Could not safely remove this model. Its files may be in use or changed.'}, status=409)

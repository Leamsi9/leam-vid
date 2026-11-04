"""Manifest-owned GPU model files only; never reads prompts or generated media."""
import fcntl
import json
from pathlib import Path
import shutil
import stat

ROOT = Path('/workspace/wan-cloud')
ASSETS = Path(__file__).resolve().parent
STORE = ROOT / 'model-artifacts'
MANIFEST = json.loads((ASSETS / 'runtime-models.json').read_text())['models']

def target(model, item):
    base = ROOT / 'ComfyUI/models' if MANIFEST[model]['native'] else ROOT / 'models' / model
    return base / item['destination']

def complete(path, size):
    return path.is_file() and path.stat().st_size == size

def inventory():
    free = shutil.disk_usage(ROOT).free
    models = []
    for model, spec in MANIFEST.items():
        cached = {}
        all_present = True
        missing = {}
        for item in spec['files']:
            blob, path = STORE / item['key'], target(model, item)
            if complete(path, item['size']):
                cached[item['key']] = item['size']
            else:
                all_present = False
            if complete(blob, item['size']):
                cached[item['key']] = item['size']
            elif not complete(path, item['size']):
                partial = Path(str(blob) + '.part')
                have = partial.stat().st_size if partial.is_file() else 0
                cached[item['key']] = min(have, item['size'])
                missing[item['key']] = max(0, item['size'] - have)
        reserve = 4 * 1024**3
        size = sum({item['key']: item['size'] for item in spec['files']}.values())
        models.append({'id': model, 'downloaded': all_present,
            'downloaded_bytes': sum(cached.values()), 'size_bytes': size,
            'required_bytes': sum(missing.values()) + reserve,
            'can_prepare': free >= sum(missing.values()) + reserve})
    return {'free_bytes': free, 'models': models}

def remove_model(model):
    """Caller holds native prompt_queue.mutex and has checked it is empty."""
    if model not in MANIFEST: raise ValueError('Unknown model.')
    ROOT.mkdir(parents=True, exist_ok=True)
    # Background preparation also downloads disjoint files beside a legacy job.
    # Keep its native ownership lock until this removal finishes.
    background = (ROOT / 'background-preparation.lock').open('a')
    try: fcntl.flock(background, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        background.close()
        raise RuntimeError('Background model preparation is still running.')
    with background, (ROOT / 'model-preparation.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Model preparation is still running.')
        # Native models share one logical encoder/VAE path, not only one blob.
        # Keep that path while another model owns its distinct checkpoint data.
        selected_keys = {item['key'] for item in MANIFEST[model]['files']}
        protected_paths = set()
        for other, spec in MANIFEST.items():
            if other == model:
                continue
            owned = any(candidate['key'] not in selected_keys and (
                complete(target(other, candidate), candidate['size']) or
                complete(STORE / candidate['key'], candidate['size']) or
                Path(str(STORE / candidate['key']) + '.part').is_file())
                for candidate in spec['files'])
            if owned:
                protected_paths.update(target(other, candidate) for candidate in spec['files'])
        files = []
        for item in MANIFEST[model]['files']:
            path = target(model, item)
            if not path.exists() and not path.is_symlink(): continue
            if path.is_symlink():
                if path.resolve() != (STORE / item['key']).resolve(): raise RuntimeError('Unexpected model link.')
            elif not stat.S_ISREG(path.lstat().st_mode) or path.stat().st_size != item['size']:
                raise RuntimeError('Unexpected model file.')
            # No parent may escape the exact model root through a symlink.
            base = ROOT / 'ComfyUI/models' if MANIFEST[model]['native'] else ROOT / 'models' / model
            if not path.parent.resolve().is_relative_to(base.resolve()): raise RuntimeError('Unexpected model directory.')
            if path not in protected_paths:
                files.append(path)
        freed = 0
        for path in files:
            if not path.is_symlink(): freed += path.stat().st_size
            path.unlink()
        # Shared artifacts survive while any other model links to them.
        for item in MANIFEST[model]['files']:
            blob = STORE / item['key']
            referenced = any(
                target(other, candidate).is_symlink() and target(other, candidate).resolve() == blob.resolve()
                for other, spec in MANIFEST.items() if other != model
                for candidate in spec['files'] if candidate['key'] == item['key'])
            if referenced: continue
            for owned in (blob, Path(str(blob) + '.part')):
                if owned.is_file() and not owned.is_symlink():
                    freed += owned.stat().st_size
                    owned.unlink()
        return {'removed': True, 'model': model, 'freed_bytes': freed}

#!/usr/bin/env python3
"""Build a portable GPU image; never publish, rent a GPU or fetch model weights."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
def selected_manifest(manifest, selected):
    known = manifest['models']
    if not selected or len(set(selected)) != len(selected) or any(key not in known for key in selected):
        raise ValueError('Choose a nonempty, unique subset of the supported model IDs.')
    return {**manifest, 'models': {key: known[key] for key in selected}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Your Docker image reference, e.g. registry.example/user/leam-vid:v1')
    parser.add_argument('--models', default='turbowan,h3')
    parser.add_argument('--base-image', help='Optional compatible, already-built leam-vid image reference for a small selection-only derivative.')
    parser.add_argument('--print-commands', action='store_true')
    args = parser.parse_args()
    if any(character.isspace() for character in args.image) or args.image.startswith('-') or '@' in args.image:
        parser.error('Use a valid writable image name and tag; pin its digest after publication.')
    selected = [value.strip() for value in args.models.split(',') if value.strip()]
    manifest = selected_manifest(json.loads((ROOT/'gpu/assets/runtime-models.json').read_text()), selected)
    core, turbo = args.image+'-core', args.image+'-kernels'
    commands = [
        ['docker','build','-f','turbo/Dockerfile','-t',turbo,'.'],
        ['docker','build','-f','Dockerfile.core','-t',core,'.'],
        ['docker','build','--build-arg','CORE_IMAGE='+core,'--build-arg','TURBO_IMAGE='+turbo,'-t',args.image,'.'],
    ]
    if args.base_image:
        commands = [['docker','build','-f','Dockerfile.select','--build-arg','BASE_IMAGE='+args.base_image,'-t',args.image,'.']]
    unique = {item['key']: item['size'] for model in manifest['models'].values() for item in model['files']}
    print(json.dumps({'models': selected, 'unique_weight_bytes':sum(unique.values()), 'image':args.image, 'commands':commands},indent=2),flush=True)
    if args.print_commands: return
    # Frozen source-only context. Never includes launcher configuration, credentials,
    # private state or a model/media directory from an installed application.
    with tempfile.TemporaryDirectory(prefix='leam-vid-build-') as temporary:
        context = Path(temporary)/'gpu'
        shutil.copytree(ROOT/'gpu', context, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        (context/'assets/runtime-models.json').write_text(json.dumps(manifest,indent=2)+'\n')
        profile = json.loads((context/'assets/image-profile.json').read_text())
        profile['models'] = selected
        profile['unique_weight_bytes'] = sum(unique.values())
        profile['first_model_default'] = min(selected, key=lambda key: sum({item['key']: item['size'] for item in manifest['models'][key]['files']}.values()))
        (context/'assets/image-profile.json').write_text(json.dumps(profile,indent=2)+'\n')
        # Only include public examples supported by this model selection.
        for fixture in (context/'workflows').glob('*.json'):
            if not any(fixture.name.startswith(key+'_') for key in selected): fixture.unlink()
        for command in commands: subprocess.run(command,cwd=context,check=True)

if __name__=='__main__': main()

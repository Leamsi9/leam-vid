#!/usr/bin/env python3
"""Run pinned Comfy's pure API graph validation on public CPU fixtures.

No HTTP submission, server listener, prompt worker, or inference is started.
Run in the native image with --network none and without --gpus.
"""
import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sys


async def validate(paths):
    import execution
    import nodes
    import torch
    from app.assets.manager import default_asset_manager
    from server import PromptServer

    server = PromptServer(asyncio.get_running_loop(), default_asset_manager())
    await nodes.init_extra_nodes(init_custom_nodes=True, init_api_nodes=False)
    errors = []
    passed = 0
    for path in paths:
        graph = json.loads(path.read_text())
        result = await execution.validate_prompt('public-validation-only', graph, None)
        ok, error, outputs, node_errors = result
        if ok and outputs and not node_errors:
            passed += 1
        else:
            errors.append({'fixture': path.name,
                           'type': (error or {}).get('type'),
                           'nodes': {node_id: {'class_type': data.get('class_type'),
                                              'types': [item.get('type') for item in data.get('errors', [])]}
                                     for node_id, data in node_errors.items()}})
    running, queued = server.prompt_queue.get_current_queue()
    if running or queued:
        raise RuntimeError('Pure validation unexpectedly populated a prompt queue')
    if torch.cuda.is_initialized():
        raise RuntimeError('CPU validation unexpectedly initialized CUDA')
    return {'native_validator': 'execution.validate_prompt', 'fixtures_checked': len(paths),
            'fixtures_passed': passed, 'errors': errors, 'queued_jobs': 0,
            'cpu': True, 'gpu_inference_verified': False, 'generation_run': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', action='store_true', required=True)
    parser.add_argument('--comfy-dir', type=Path, default=Path('/workspace/wan-cloud/ComfyUI'))
    parser.add_argument('fixtures', nargs='*', type=Path)
    args = parser.parse_args()
    examples = Path('/opt/private-video/examples')
    if not examples.is_dir(): examples = Path(__file__).parent / 'workflows'
    paths = args.fixtures or sorted(examples.glob('*.json'))
    if not paths:
        parser.error('No public API graph fixtures found')
    os.environ.update(CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1')
    sys.path.insert(0, str(args.comfy_dir))
    sys.argv = [sys.argv[0], '--cpu', '--offline', '--disable-auto-launch', '--disable-assets',
                '--disable-all-custom-nodes', '--whitelist-custom-nodes', 'PrivateVideoRuntime']
    import comfy.options
    comfy.options.enable_args_parsing()
    from comfy.cli_args import args as comfy_args
    if not comfy_args.cpu:
        raise RuntimeError('Comfy CPU mode was not enabled')
    logging.disable(logging.CRITICAL)
    # Core/custom import diagnostics cannot dump fixtures or obscure the result.
    # Failure reporting below exposes error categories, never prompt contents.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = asyncio.run(validate(paths))
    print(json.dumps(result, indent=2))
    return int(bool(result['errors']))


if __name__ == '__main__':
    sys.exit(main())

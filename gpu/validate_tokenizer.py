#!/usr/bin/env python3
"""Offline CPU validation of the pinned Comfy H3 tokenizer, without model weights.

Run inside the native image with Docker --network none and without --gpus.
Only the bundled tokenizer encodes a fixed public placeholder; no neural model,
server, prompt queue, inference pipeline, or user data is loaded.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy-dir', type=Path, default=Path('/workspace/wan-cloud/ComfyUI'))
    parser.add_argument('--cpu', action='store_true', required=True)
    args = parser.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1', TOKENIZERS_PARALLELISM='false')
    sys.path.insert(0, str(args.comfy_dir))
    # Configure core imports before model_management is imported indirectly.
    sys.argv = [sys.argv[0], '--cpu', '--offline', '--disable-auto-launch']
    import comfy.options
    comfy.options.enable_args_parsing()
    from comfy.cli_args import args as comfy_args
    if not comfy_args.cpu:
        raise RuntimeError('Comfy CPU mode was not enabled')
    from comfy.text_encoders.minimax import MiniMaxH3Tokenizer, MINIMAX_EXTRA_TOKENS
    import torch

    tokenizer = MiniMaxH3Tokenizer(embedding_directory=None, tokenizer_data={})
    public_text = 'A small red ball on a plain table.'
    weighted = tokenizer.tokenize_with_weights(public_text)
    if set(weighted) != {'qwen3vl_32b'} or len(weighted['qwen3vl_32b']) != 1:
        raise RuntimeError('Unexpected H3 tokenizer output structure')
    entries = weighted['qwen3vl_32b'][0]
    if not entries or any(type(token) is not int or not math.isfinite(weight) for token, weight in entries):
        raise RuntimeError('Tokenizer did not return finite text token weights')
    inner = tokenizer.qwen3vl_32b.tokenizer
    raw_ids = inner.encode(public_text, add_special_tokens=False)
    if [token for token, _ in entries] != raw_ids:
        raise RuntimeError('H3 text tokens differ from the raw untemplated prompt')
    if inner.decode(raw_ids) != public_text:
        raise RuntimeError('Bundled tokenizer text did not round-trip')
    for text, expected in MINIMAX_EXTRA_TOKENS.items():
        if inner.convert_tokens_to_ids(text) != expected:
            raise RuntimeError('H3 additional-token ID differs from the pinned contract')
    if torch.cuda.is_initialized():
        raise RuntimeError('CUDA initialized during CPU-only tokenizer validation')
    print(json.dumps({'tokenizer': 'MiniMaxH3Tokenizer', 'cpu': True,
                      'offline': True, 'public_placeholder_tokens': len(raw_ids),
                      'extra_token_ids_checked': len(MINIMAX_EXTRA_TOKENS),
                      'model_weights_loaded': False, 'generation_run': False}))


if __name__ == '__main__':
    main()

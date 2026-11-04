#!/usr/bin/env python3
"""One private TurboWan request using the pinned official four-step sampler.

Sampling is adapted from TurboDiffusion's wan2.1_t2v_infer.py at
e3d61360b0eeab60c5cd09ff7d6c1020cccc80b2 (Apache-2.0, TurboDiffusion team).
The parent supplies JSON on stdin and suppresses upstream stdout/stderr.
Only non-content stage/progress events use the dedicated inherited event FD.
"""
import gc
import json
import math
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from private_video_events import emit, record_error

SOURCE = Path('/workspace/wan-cloud/runtime-source/TurboDiffusion/turbodiffusion')


def run(request):
    # This checkpoint's supported initial recipe is one silent 480p clip.
    if (request.get('width'), request.get('height'), request.get('frames')) != (832, 480, 81):
        raise ValueError('Unsupported TurboWan dimensions or duration.')
    if request.get('audio') is not False:
        raise ValueError('TurboWan generates silent video.')
    if not isinstance(request.get('prompt'), str) or not request['prompt'].strip():
        raise ValueError('A prompt is required.')
    seed = request.get('seed')
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError('Invalid seed.')
    model_root = Path(request['model_root'])
    output_path = Path(request['output_path'])
    if output_path.suffix.lower() != '.mp4':
        raise ValueError('An MP4 output is required.')
    os.environ.update({
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1',
        'WANDB_DISABLED': 'true', 'TOKENIZERS_PARALLELISM': 'false',
    })
    sys.path[:0] = [str(SOURCE), str(SOURCE / 'inference')]
    emit('stage', stage='loading')
    import torch
    import turbo_diffusion_ops

    from modify_model import create_model, tensor_kwargs
    from rcm.utils.umt5 import UMT5EncoderModel
    from rcm.tokenizers.wan2pt1 import Wan2pt1VAEInterface
    from imaginaire.utils.io import save_image_or_video
    from private_video_rotary import apply_rotary_emb
    import rcm.networks.wan2pt1 as wan21
    wan21.flash_apply_rotary_emb = apply_rotary_emb

    torch._dynamo.config.suppress_errors = True
    # Use the official local tokenizer parameter, avoiding its hub-name default.
    # Preserve the entire supplied text; only the upstream UMT5 tokenization
    # performs the checkpoint's standard whitespace cleaning and 512-token cap.
    encoder = UMT5EncoderModel(
        text_len=512, device='cuda',
        checkpoint_path=str(model_root / 'models_t5_umt5-xxl-enc-bf16.pth'),
        tokenizer_path=str(model_root / 'tokenizer'))
    with torch.no_grad():
        text_embedding = encoder([request['prompt']], device='cuda').to(**tensor_kwargs)
    del encoder
    gc.collect()
    torch.cuda.empty_cache()

    args = SimpleNamespace(model='Wan2.1-1.3B', attention_type='sla',
                           sla_topk=0.1, quant_linear=True, default_norm=False)
    net = create_model(
        dit_path=str(model_root / 'TurboWan2.1-T2V-1.3B-480P-quant.pth'), args=args)
    # Official Wan2.1 latent geometry: 16 channels, temporal compression 4,
    # spatial compression 8. 81 frames therefore use 21 latent frames.
    shape = (1, 16, 1 + (request['frames'] - 1) // 4,
             request['height'] // 8, request['width'] // 8)
    generator = torch.Generator(device='cuda').manual_seed(seed)
    initial_noise = torch.randn(*shape, dtype=torch.float32, device='cuda', generator=generator)
    times = torch.tensor([math.atan(80), 1.5, 1.4, 1.0, 0],
                         dtype=torch.float64, device=initial_noise.device)
    times = torch.sin(times) / (torch.cos(times) + torch.sin(times))
    x = initial_noise.to(torch.float64) * times[0]
    del initial_noise
    ones = torch.ones(x.size(0), 1, device=x.device, dtype=x.dtype)
    emit('stage', stage='sampling')
    emit('progress', value=0, max=4)
    with torch.no_grad():
        for index, (current, following) in enumerate(zip(times[:-1], times[1:])):
            prediction = net(
                x_B_C_T_H_W=x.to(**tensor_kwargs),
                timesteps_B_T=(current.float() * ones * 1000).to(**tensor_kwargs),
                crossattn_emb=text_embedding).to(torch.float64)
            x = ((1 - following) * (x - current * prediction)
                 + following * torch.randn(*x.shape, dtype=torch.float32,
                                           device='cuda', generator=generator))
            torch.cuda.synchronize()
            emit('progress', value=index + 1, max=4)
    samples = x.float()
    # Release DiT before loading the video VAE; no second large model is needed
    # on the GPU during decode. This does not alter the official sampler math.
    del prediction, x, text_embedding
    net.cpu()
    del net
    gc.collect()
    torch.cuda.empty_cache()
    emit('stage', stage='decoding')
    tokenizer = Wan2pt1VAEInterface(vae_pth=str(model_root / 'Wan2.1_VAE.pth'))
    with torch.no_grad():
        video = tokenizer.decode(samples).float().cpu()
    frames = (1 + video[0].clamp(-1, 1)) / 2
    emit('stage', stage='saving')
    save_image_or_video(frames, str(output_path), fps=16)
    if not output_path.is_file() or not output_path.stat().st_size:
        raise RuntimeError('Video output was not saved.')


if __name__ == '__main__':
    request = {}
    try:
        request = json.load(sys.stdin)
        run(request)
    except Exception as error:
        record_error(error, request.get('model') if isinstance(request, dict) else None)
        # Do not copy upstream exceptions, input text, or private paths to logs.
        emit('error', code='generation')
        raise SystemExit(1)

"""Validate the exact TurboWan Python imports without models or CUDA calls."""
import inspect
import json
import os
import sys
import torch
assert not torch.cuda.is_initialized(), "CUDA initialized before CPU import check"

source = os.environ.get("TURBO_SOURCE", "/workspace/wan-cloud/runtime-source/TurboDiffusion/turbodiffusion")
sys.path[:0] = [source, source + "/inference"]
import turbo_diffusion_ops
from modify_model import create_model, tensor_kwargs
from rcm.utils.umt5 import UMT5EncoderModel
from rcm.tokenizers.wan2pt1 import Wan2pt1VAEInterface
from imaginaire.utils.io import save_image_or_video
from private_video_rotary import apply_rotary_emb, apply_rotary_emb_torch
import rcm.networks.wan2pt1 as wan21
wan21.flash_apply_rotary_emb = apply_rotary_emb
assert wan21.flash_apply_rotary_emb is apply_rotary_emb
assert inspect.signature(UMT5EncoderModel).parameters["device"].default is None
assert all(callable(fn) for fn in [create_model, UMT5EncoderModel, Wan2pt1VAEInterface, save_image_or_video])
sig = inspect.signature(wan21.flash_apply_rotary_emb)
sig.bind(object(), object(), object(), interleaved=True, inplace=False)
# Synthetic CPU rotary reference verifies axis/interleaving semantics only; no model.
x = torch.arange(24, dtype=torch.float32).reshape(1, 2, 3, 4)
cos = torch.zeros(2, 2)
sin = torch.ones(2, 2)
expected = torch.stack([-x[..., 1::2], x[..., ::2]], dim=-1).flatten(-2)
result = apply_rotary_emb_torch(x, cos, sin, interleaved=True)
torch.testing.assert_close(result, expected)
assert "fastvideo" not in sys.modules and "flashinfer" not in sys.modules
assert not torch.cuda.is_initialized(), "Import eagerly initialized CUDA"
print(json.dumps({"torch":torch.__version__, "cuda":torch.version.cuda,
    "gpu_visible":torch.cuda.is_available(), "upstream_imports":"passed",
    "rotary_source":inspect.getfile(apply_rotary_emb), "rotary_signature":str(sig),
    "synthetic_cpu_rotary_reference":"passed", "cuda_initialized":torch.cuda.is_initialized(), "gpu_kernels_executed":False}, indent=2))

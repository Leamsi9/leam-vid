# Third-party notices

Project-authored source currently has no additional license grant. Third-party
files retain their original copyrights and licenses; public repository access
does not change their terms.

- `gpu/runtime-source/TurboDiffusion/`: thu-ml/TurboDiffusion commit
  `e3d61360b0eeab60c5cd09ff7d6c1020cccc80b2`, Apache2.0. Its LICENSE is included.
  `gpu/apply-public-patches.py` applies one exact, idempotent lazy CUDA-device
  default patch, allowing CPU import without changing an explicitly supplied
  GPU device. CUDA source is compiled unchanged for SM120a.
- `gpu/turbo/rotary-only/`: Dao-AILab/flash-attention v2.8.3 commit
  `060c9188beec3a8b62b33a3bfa6d5d2d44975fab`, BSD3-Clause. LICENSE, AUTHORS and
  `PROVENANCE.json` retain the exact original and adapted source hashes. The
  adaptation changes only the package-local rotary import path.
- NVIDIA CUTLASS commit `e67e63c331d6e4b729047c95cf6b92c8454cba89` is fetched at
  build time under its BSD license; its license is included in the built kernel
  wheel. NVIDIA CUDA packages and the official PyTorch base retain their terms.
- ComfyUI commit `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24` is fetched at build
  time from Comfy-Org/ComfyUI; its GPL3.0 license remains in the image's checkout.
  Review its redistribution obligations before publishing an image.
- Hugging Face model artifacts are downloaded at runtime from their pinned
  official/model-author repositories. Model weights and their separate licenses
  are not redistributed in this source tree or baked into the default image.

The Python lockfiles list the remaining packages; their installed distribution
metadata and licenses remain in the image.

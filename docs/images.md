# Build your own GPU image

Builds require Linux amd64 Docker, roughly 20GB free RAM, generous disk/cache
space and access to the pinned public upstream packages. No NVIDIA GPU is
required to cross-compile SM120a kernels. The image targets RTX 5090, Python3.11,
Torch2.9.1 and CUDA13. Changing the GPU architecture requires rebuilding and
validating the kernels, not merely changing the rental search string.

```sh
python3 scripts/build-image.py \
  --image docker.io/YOUR_USER/leam-vid:v1 \
  --models turbowan,h3
```

The helper makes a temporary source-only build context and:

1. Compiles pinned official TurboDiffusion/CUTLASS kernels and packages the
   pinned official rotary implementation from source; no local wheel is needed.
2. Builds the Comfy core from its pinned official PyTorch base digest, exact
   Comfy commit and Python constraints.
3. Assembles the selected manifest, software and public workflow examples.

All native models share the same Comfy environment. Turbo uses the same Torch
ABI and four official CUDA bindings, not an additional FastVideo environment.
Only the small CUDA build toolchain is in the builder. The final image contains
a host C/C++ compiler for runtime GPU helper compilation but no credentials,
pre-generated SSH private host keys, prompts, media or model weights. SSH host
keys are generated uniquely at startup. Do not mount an empty volume over
`/workspace`, which would hide the built software.

`--models fasth3_trim,h3`, for example, limits the image's accepted/preloaded
models, readiness inventory and public examples to those supported IDs. Set the
same subset in `LEAM_VID_MODELS`. Downloads remain smallest-first among enabled
models; identical content hashes share one stored blob. The selected set's
unique weight bytes are printed before building. Allow additional capacity for
the software, partial downloads, scratch space and outputs. The default120GB
allocation fits all four sets; the default selection is TurboWan plus H3, smaller subsets can use a lower explicitly
configured allocation after accounting for that overhead.

To change a selection without recompiling the same software, use a compatible
published leam-vid base, preferably pinned by registry digest:

```sh
python3 scripts/build-image.py --image registry.example/YOUR_USER/leam-vid:v2 \
  --base-image registry.example/YOUR_USER/leam-vid@sha256:BASE_DIGEST \
  --models turbowan,h3
```

This replaces only model manifests, public examples and the manifest-derived
model selector. It adds no weights and changes no installed dependency versions.
It is for this runtime family, not arbitrary third-party base images. Revalidate
the resulting image and do not enable models absent from the underlying runtime.

## Publish to your registry

Any Docker-compatible registry may be used, for example Docker Hub, GHCR or your
own TLS registry. The helper builds locally and never publishes automatically.
After validation, log in as that registry's owner and push:

```sh
docker login docker.io
docker push docker.io/YOUR_USER/leam-vid:v1
docker buildx imagetools inspect docker.io/YOUR_USER/leam-vid:v1
```

For GHCR substitute `ghcr.io/YOUR_USER/leam-vid:v1`; for another registry use
its hostname and repository. Configure `LEAM_VID_IMAGE` with the resulting real
registry digest. A local Docker image ID is not a registry manifest digest.
The launcher currently supports images Vast hosts can pull without registry
credentials. For a private registry, configure authorized pull credentials in
Vast's template/account; do not embed tokens in this source or the image name.
Confirm your Vast configuration supports those credentials before creating.
A laptop-only or tailnet-only registry is unreachable to the host before its
container starts; a publicly reachable TLS endpoint is required.

## Adding a different model

Selecting these supported IDs is different from adding a new architecture.
An added model needs pinned official artifacts with byte sizes/SHA256 hashes in
`gpu/assets/runtime-models.json`, compatible loader/inference code, validated
API graph and sampler/frame/audio defaults, a catalog entry in
`app/model_catalog.py`, public validation fixtures and appropriate dependencies.
Update `app/native_image_graphs.py`, `gpu/native_graphs.py` and the model picker
as needed. Keep licenses, queue-readiness checks and model-file ownership in
sync. A new URL alone does not make an unsupported checkpoint functional.

Rebuild after changing runtime code/manifests. The previously validated laptop
image remains an unchanged historical artifact; it is not the newly branded,
selected-model build described here.

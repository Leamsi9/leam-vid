# CPU validation

Source syntax and inexpensive local download/queue/configuration tests:

```sh
python3 -m unittest discover -s tests -v
python3 gpu/validate_downloader.py
python3 gpu/validate_prefetch.py
python3 gpu/validate_queue.py
node tests/test_offline_inventory.js
node tests/test_setup_feedback.js
node tests/test_active_jobs.js
node tests/test_video_replay.js
python3 tests/test_video_replay_browser.py
node --check app/app.js
node --check app/sw.js
```

Use the installed launcher virtual environment for tests requiring aiohttp and
Vast's SDK (`.venv/bin/python` after `scripts/install`). The tests use public
placeholder input, tiny temporary local HTTP files and in-memory boundaries.
They do not rent a GPU or submit inference. The replay browser check uses an
isolated temporary Chrome profile and a tiny synthetic video served by a local
fixture HTTP server; it never connects to the installed Studio or reads user
media. It checks on-demand fetch/replay, stopped playback, expiry/delete/destroy
cleanup and the explicit app-update notice.

After building an image, run its real Comfy input validator without GPU access
or networking. The validator uses the selected public workflow examples baked into the image:

```sh
docker run --rm --network none --entrypoint /opt/conda/bin/python \
  -v "$PWD/gpu:/validation:ro" YOUR_IMAGE:TAG \
  /validation/validate_native.py --cpu

docker run --rm --network none --entrypoint /opt/conda/bin/python \
  -v "$PWD/gpu:/validation:ro" YOUR_IMAGE:TAG \
  /validation/validate_tokenizer.py --cpu

docker run --rm --network none --entrypoint /opt/conda/bin/python \
  -v "$PWD/gpu:/validation:ro" YOUR_IMAGE:TAG \
  /validation/validate_media_io.py
```

The selected model subset is detected from the image examples; explicit fixture
paths can also be passed to `validate_native.py`.
Additional scripts check actual Turbo imports/bindings and SSH/compiler access.
`validate_ssh_bootstrap.py` reproduces the observed pre-onstart missing-key
failure, then checks first-invocation key generation, real key authentication,
onstart idempotence and CPU HTTP readiness in a disposable network-disabled
container. No owner keys are mounted and no model inference runs.
`validate_turbowan_tokenizer.py` requires the small public tokenizer files at
its documented path; it does not load model weights. CPU startup can be checked
with `VIDEO_PREPARE_MODELS=0` and `start-comfy-video.sh --cpu`; production startup
defaults to downloading enabled models independently of Comfy HTTP readiness.

Record the immutable image ID alongside results. CPU schema/import/codec checks
cannot prove CUDA numerical execution, generation quality, speed or VRAM use.
Only the user's manual GPU trials establish those outcomes.

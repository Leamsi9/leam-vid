# leam-vid

A private video studio for an on-demand RTX 5090 on Vast.ai. The laptop runs a
small Python web app; the rented GPU runs ComfyUI. A phone can use the same app
through a private HTTPS connection such as Tailscale Serve.

- H3 Turbo: the four-step 768p comparison workflow with optional sound.
- TurboWan 2.1 1.3B: the default, four-step silent 480p drafts with pinned official kernels.
- FastH3 V2 and Trim remain explicit source-build options, excluded from defaults.
- Queue and batch jobs, view real progress, reuse a video's prompt, and manage
  videos, downloaded models, GPU lifecycle and Vast credit.

Prompts and saved videos stay on the rented machine. Opening a video loads it
once into a temporary replay buffer in that browser tab. Replay and seeking reuse
that buffer for up to 24 hours; GPU destruction, video deletion or changing
instances clears it sooner. Stop retains the replay buffer and remote files,
while storage remains billed. Closing/reloading the page or device memory
pressure can lose the buffer earlier; physical RAM residency is browser managed.
The app creates no persistent video cache or local archive. Its localStorage holds
only unfinished job identifiers and settings for reconnecting, never prompts or
video bytes. Finished job cards and their recovery records are removed; generated
videos remain in the gallery. Accepted Destroy deletes the rental and its files, and clears the prompt, composer
settings, jobs, gallery and temporary replay buffers. App preferences and account
budget remain. Rejected requests leave the draft and videos intact.

## Install

Linux, Python 3.11+, OpenSSH client and a Vast.ai account are required. Docker is
needed only to build/validate the GPU image.

```sh
git clone https://github.com/Leamsi9/leam-vid.git
cd leam-vid
./scripts/install
ssh-keygen -t ed25519 -f "$HOME/.ssh/leam-vid"
```

Store your Vast API key in `~/.config/vastai/vast_api_key` with permissions `600`.
Never put the key or SSH private key into this repository or a chat. Use an SSH
agent if the private key has a passphrase.

[Build and publish your GPU image](docs/images.md), then copy `.env.example` to
`~/.config/leam-vid/environment`, edit its image reference and selected models,
and start:

```sh
. "$HOME/.config/leam-vid/environment"
./scripts/start
```

Open `http://127.0.0.1:8189`. No rental starts until you press **Create GPU**.
Create uses the cheapest currently matching verified single RTX 5090 offer,
64GB system RAM, CUDA 13 support and your configured disk allocation. It prefers
advertised 1Gb/s downloads, falls back to 500Mb/s and requires 500MB/s disk
bandwidth. These advertised values do not guarantee actual throughput. The app
shows actual pricing and credit; it does not impose a rental price cap.

See [private access and configuration](docs/configuration.md) for phone access.
Keep runtime state and credentials outside the checkout. The public source tree
contains no account configuration, model weights, private prompts or media.

## Startup and model selection

The GPU image contains all software; there is no `apt` or `pip` installation at
rental startup. Comfy starts independently of model downloads. The default models
prepare sequentially: TurboWan first, then H3 Turbo. Explicitly enabled V2/Trim
join the same smallest-first sequence.
Three artifacts within one model may download concurrently. The queue skips
models still preparing and runs the oldest runnable request without changing
skipped jobs' original order. Installed-model notices appear while the app is
open; browser notifications use existing permission only.

The default two model sets occupy **50,382,663,429 unique bytes**, about 34%
less than all four sets (75,889,749,265 bytes). TurboWan alone needs
13,339,261,258 bytes. The
default disk allocation is 120GB. Weights download on first boot, with resumable
transfers, pinned revisions and SHA256 verification. Later boots reuse them;
models explicitly removed after initial setup download again only if requested.
Software image pull, network speed and first model load also take time. Keeping
only TurboWan does not guarantee five-minute cold readiness. A first video
still needs model loading and may compile GPU kernels; no fixed
GPU timing is promised. “Installed” is not a generation benchmark.

## Validation and limits

The earlier optimized image passed laptop CPU startup, 31 native Comfy graph
validations, tokenizer, MP4/H264/AAC, Turbo import/binding, SSH and downloader/
queue checks. Native H3 and V2 also completed prior manual GPU requests. This
portable source extraction and its build/configuration changes receive the
checks in [validation](docs/validation.md); they are not a newly built or
published image. Trim and Turbo require manual GPU trials. CPU checks cannot
validate CUDA numerical results, speed or VRAM use.

Third-party licenses and pinned source provenance are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No license grant is asserted for
new project-authored files until the owner chooses one. Model licenses remain
with their respective authors and are separate from this repository.

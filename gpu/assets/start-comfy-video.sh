#!/usr/bin/env bash
set -euo pipefail
mkdir -p /run/sshd
if [[ ! -f /etc/ssh/ssh_host_ed25519_key ]]; then ssh-keygen -A >/dev/null; fi
cd /workspace/wan-cloud/ComfyUI
export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
if [[ ${VIDEO_PREPARE_MODELS:-1} == 1 ]]; then
  nohup /opt/conda/bin/python /workspace/wan-assets/prepare-background.py >> /workspace/wan-cloud/background-preparation.log 2>&1 < /dev/null &
fi
exec /opt/conda/bin/python main.py --listen 127.0.0.1 --port "${VIDEO_PORT:-8188}" --preview-method none --disable-auto-launch --disable-all-custom-nodes --whitelist-custom-nodes PrivateVideoRuntime --offline "$@"

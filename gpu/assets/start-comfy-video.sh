#!/usr/bin/env bash
set -euo pipefail
mkdir -p /run/sshd
if [[ ! -f /etc/ssh/ssh_host_ed25519_key ]]; then ssh-keygen -A >/dev/null; fi
# Vast may attempt SSH before onstart runs. Recover that first-start failure
# after generating unique host keys; keep its injected authorized_keys intact.
/usr/sbin/sshd -t
if ! pgrep -x 'sshd(\.real)?' >/dev/null; then /usr/sbin/sshd; fi
cd /workspace/wan-cloud/ComfyUI
export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
if [[ ${VIDEO_PREPARE_MODELS:-1} == 1 ]]; then
  nohup /opt/conda/bin/python /workspace/wan-assets/prepare-background.py >> /workspace/wan-cloud/background-preparation.log 2>&1 < /dev/null &
fi
exec /opt/conda/bin/python main.py --listen 127.0.0.1 --port "${VIDEO_PORT:-8188}" --preview-method none --disable-auto-launch --disable-all-custom-nodes --whitelist-custom-nodes PrivateVideoRuntime --offline "$@"

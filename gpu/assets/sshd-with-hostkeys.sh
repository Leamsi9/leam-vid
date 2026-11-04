#!/usr/bin/env bash
set -euo pipefail
mkdir -p /run/sshd
ssh-keygen -A >/dev/null
exec /usr/sbin/sshd.real "$@"

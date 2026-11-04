# Configuration and private access

The launcher reads environment variables. Keep the environment file outside Git
and source it before `scripts/start`. An existing installation can keep its
private data directory by setting `LEAM_VID_DATA_DIR` explicitly; source and
runtime data are separate.

| Variable | Default | Purpose |
|---|---|---|
| `LEAM_VID_IMAGE` | unset | Your published image, preferably `registry/repo@sha256:digest`; Create refuses when unset. |
| `LEAM_VID_MODELS` | all four supported IDs | Comma-separated supported subset; must match your image selection. |
| `LEAM_VID_DISK_GB` | `120` | Rental disk allocation, included in displayed storage pricing. |
| `LEAM_VID_DATA_DIR` | `~/.local/share/leam-vid` | Private rental identity/configuration; not a video archive. |
| `LEAM_VID_SSH_IDENTITY` | `~/.ssh/leam-vid` | Private SSH key; matching `.pub` is attached to an explicit rental. |
| `LEAM_VID_VAST_KEY_FILE` | `~/.config/vastai/vast_api_key` | API credential file; not a literal key. |
| `LEAM_VID_BIND` | `127.0.0.1` | Comma-separated exact private listen addresses. Wildcard binds are rejected. |
| `LEAM_VID_PORT` | `8189` | Local app port. |
| `LEAM_VID_ORIGINS` | localhost HTTP origins on that port | Exact comma-separated allowed origins, no trailing slash. Hosts are derived from these values. |

The app forwards Comfy through SSH to `127.0.0.1:8188`. It never binds the tunnel
to the public network. Each app process owns only its own SSH child process.

For phone access, use a private Tailscale Serve HTTPS endpoint forwarding to
`http://127.0.0.1:8189`. Add its exact origin, for example
`https://YOUR_DEVICE.YOUR_TAILNET.ts.net:8447`, to `LEAM_VID_ORIGINS`, alongside
any localhost origins you still use. Leave the backend bound to localhost when
using this proxy. Use an available Serve port; do not overwrite another app's
route. Do not use Funnel for the app itself. HTTPS permits Android PWA install
and copy/notification features. Notifications are not server push: the page
must be running and polling.

Origin checks and exact Host allowlisting protect API writes and websocket
connections. Public shell resources tolerate cross-site PWA launches; API and
media requests retain their stricter boundary. This is a single-user private
network app, not a public multi-user service.

A user systemd service can run `scripts/start` with its working directory set to
your checkout and `EnvironmentFile=` pointing to an external configuration file.
Systemd environment files use `NAME=value` without shell `export` or `$HOME`
expansion: use absolute paths. Restarting the laptop app reconnects to an already
running owned rental; it never starts a stopped rental or creates a new one.

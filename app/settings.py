"""Local configuration; credential contents never enter application responses."""
import os
from pathlib import Path
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('LEAM_VID_DATA_DIR', str(Path.home()/'.local/share/leam-vid'))).expanduser()
IDENTITY = Path(os.environ.get('LEAM_VID_SSH_IDENTITY', str(Path.home()/'.ssh/leam-vid'))).expanduser()
VAST_KEY = Path(os.environ.get('LEAM_VID_VAST_KEY_FILE', str(Path.home()/'.config/vastai/vast_api_key'))).expanduser()
IMAGE = os.environ.get('LEAM_VID_IMAGE', '').strip()
BIND = tuple(value.strip() for value in os.environ.get('LEAM_VID_BIND', '127.0.0.1').split(',') if value.strip())
PORT = int(os.environ.get('LEAM_VID_PORT', '8189'))
if not BIND or any(value in {'0.0.0.0','::',''} for value in BIND):
    raise ValueError('Bind only localhost or your explicit private network address.')
ORIGINS = set(value.strip() for value in os.environ.get('LEAM_VID_ORIGINS', f'http://127.0.0.1:{PORT},http://localhost:{PORT}').split(',') if value.strip())
if not ORIGINS or any(urlsplit(value).scheme not in {'http','https'} or not urlsplit(value).netloc or urlsplit(value).path not in {'','/'} or value.endswith('/') for value in ORIGINS):
    raise ValueError('LEAM_VID_ORIGINS must contain exact origins without a trailing slash.')
HOSTS = {urlsplit(value).netloc for value in ORIGINS}
COMFY = 'http://127.0.0.1:8188'

"""Apply the pinned public import-time CUDA initialization fix; never mock CUDA."""
from pathlib import Path
import hashlib
import sys

source = Path(sys.argv[1]) / "turbodiffusion/rcm/utils/umt5.py"
raw = source.read_text()
digest = hashlib.sha256(raw.encode()).hexdigest()
ORIGINAL_HASH = 'cf914a3dae2f9b4fe4b37a2f7d5fe9430daeed91f32aa11dc14e607bd780e422'
PATCHED_HASH = '1c62564da90ed16ceeffb9fc0a0cbe2a1c8ae69f252be0f0ca08ae363c2ba418'
if digest == PATCHED_HASH:
    print("UMT5 lazy device patch already applied; pinned hash verified")
elif digest == ORIGINAL_HASH:
    old_default = '        device=torch.cuda.current_device(),\n'
    new_default = '        device=None,\n'
    old_body = '    ):\n        self.text_len = text_len\n        self.dtype = dtype\n'
    new_body = '    ):\n        if device is None:\n            device = torch.cuda.current_device()\n        self.text_len = text_len\n        self.dtype = dtype\n'
    assert raw.count(old_default) == 1
    assert raw.count(old_body) == 1
    patched = raw.replace(old_default, new_default).replace(old_body, new_body)
    assert hashlib.sha256(patched.encode()).hexdigest() == PATCHED_HASH
    source.write_text(patched)
    print("Applied pinned UMT5 lazy device patch")
else:
    raise SystemExit("Refusing to patch an unrecognized TurboDiffusion UMT5 source hash")

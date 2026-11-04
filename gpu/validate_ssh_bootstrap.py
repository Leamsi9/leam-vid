#!/usr/bin/env python3
"""Exercise failed pre-onstart SSH, then actual startup repair in a disposable CPU container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request


def main():
    assert not list(Path('/etc/ssh').glob('ssh_host_*_key')), 'Image must not bake SSH host private keys'
    Path('/run/sshd').mkdir(parents=True,exist_ok=True)
    sshdir=Path('/root/.ssh');sshdir.mkdir(mode=0o700,exist_ok=True);sshdir.chmod(0o700)
    authorized=sshdir/'authorized_keys';assert not authorized.exists(), 'No owner keys may be present in this disposable image'
    process=None;daemon=None
    with tempfile.TemporaryDirectory(prefix='public-ssh-ordering-') as temporary:
        key=Path(temporary)/'key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True)
        injected=key.with_suffix('.pub').read_bytes();authorized.write_bytes(injected);authorized.chmod(0o600)
        # Match the observed Vast ordering: its first daemon attempt happens
        # before onstart has generated host keys, so this must fail first.
        early=subprocess.run(['/usr/sbin/sshd.real','-D','-e'],capture_output=True,timeout=10)
        assert early.returncode!=0 and b'no hostkeys available' in early.stderr
        started=time.monotonic()
        try:
            # Simulate the actual wrapper's first SSH invocation, before onstart.
            daemon=subprocess.Popen(['/usr/sbin/sshd','-D','-e'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for _ in range(50):
                if list(Path('/etc/ssh').glob('ssh_host_*_key')): break
                assert daemon.poll() is None
                time.sleep(.1)
            hostkeys={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in Path('/etc/ssh').glob('ssh_host_*_key')}
            assert hostkeys and daemon.poll() is None

            process=subprocess.Popen(['/workspace/wan-assets/start-comfy-video.sh','--cpu'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                env=dict(os.environ,VIDEO_PREPARE_MODELS='0',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1'))
            deadline=time.monotonic()+45;handshake=False;http=False
            while time.monotonic()<deadline:
                assert process.poll() is None,'Actual startup exited before HTTP readiness'
                if not handshake:
                    result=subprocess.run(['ssh','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                        '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(Path(temporary)/'known_hosts'),
                        '-o','ConnectTimeout=1','root@127.0.0.1','true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=4)
                    handshake=result.returncode==0
                if handshake:
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:8188/system_stats',timeout=1) as response:
                            http=response.status==200
                    except OSError:pass
                if handshake and http:break
                time.sleep(.2)
            assert handshake and http,'SSH repair and CPU HTTP must both become ready'
            assert daemon.poll() is None,'Onstart must preserve the already-running SSH master'
            repeat=subprocess.run(['/workspace/wan-assets/start-comfy-video.sh','--cpu','--help'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,
                env=dict(os.environ,VIDEO_PREPARE_MODELS='0'))
            assert repeat.returncode==0 and daemon.poll() is None
            assert hostkeys=={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in Path('/etc/ssh').glob('ssh_host_*_key')}
            assert authorized.read_bytes()==injected,'Startup must preserve wrapper-injected public keys'
            assert list(Path('/etc/ssh').glob('ssh_host_*_key'))
            print(json.dumps({'pre_onstart_sshd':'failed_without_hostkeys_as_expected',
                'first_wrapped_sshd_initialized_keys':True,'onstart_preserved_running_sshd':True,'ssh_bootstrap_idempotent':True,'real_public_key_handshake':True,
                'wrapper_authorized_keys_preserved':True,'cpu_http_ready':True,
                'cpu_boot_seconds':round(time.monotonic()-started,3),'model_downloads':False,'inference':False}))
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            if daemon and daemon.poll() is None:
                daemon.terminate()
                try:daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:daemon.kill();daemon.wait()
            authorized.unlink(missing_ok=True)

if __name__=='__main__':main()

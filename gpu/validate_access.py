#!/usr/bin/env python3
"""Disposable offline SSH/compiler checks; no owner keys or credentials."""
import ctypes,json
from pathlib import Path
import shutil,socket,subprocess,tempfile,time
subprocess.run(['/usr/sbin/sshd','-t'],check=True,capture_output=True)
with tempfile.TemporaryDirectory(prefix='public-ssh-compiler-') as temp:
 root=Path(temp);key=root/'key'
 subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],check=True,capture_output=True)
 authorized=root/'authorized';authorized.write_text(key.with_suffix('.pub').read_text())
 public=Path('/etc/ssh/ssh_host_ed25519_key.pub').read_text().split()
 known=root/'known';known.write_text('[127.0.0.1]:2222 '+public[0]+' '+public[1]+'\n')
 daemon=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-p','2222','-o','ListenAddress=127.0.0.1','-o','AuthorizedKeysFile='+str(authorized),'-o','StrictModes=no','-o','PermitRootLogin=prohibit-password','-o','PasswordAuthentication=no','-o','PidFile='+str(root/'sshd.pid')],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 try:
  for _ in range(40):
   with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1',2222))==0:break
   if daemon.poll() is not None:raise RuntimeError('Disposable SSH daemon did not start')
   time.sleep(.1)
  subprocess.run(['ssh','-p','2222','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(known),'root@127.0.0.1','/bin/true'],check=True,capture_output=True,timeout=10)
 finally:
  daemon.terminate();daemon.communicate(timeout=5)
 with tempfile.TemporaryDirectory(prefix='public-compiler-',dir='/workspace/wan-cloud') as executable_temp:
  source=Path(executable_temp)/'probe.c';library=Path(executable_temp)/'probe.so';source.write_text('int public_probe(void){return 42;}\n')
  subprocess.run(['cc','-shared','-fPIC',str(source),'-o',str(library)],check=True,capture_output=True)
  assert ctypes.CDLL(str(library)).public_probe()==42
 assert shutil.which('c++')
 ptxas=Path('/opt/conda/lib/python3.11/site-packages/triton/backends/nvidia/bin/ptxas')
 assert ptxas.is_file()
 subprocess.run([str(ptxas),'--version'],check=True,capture_output=True)
print(json.dumps({'sshd_configuration':'passed','ephemeral_public_key_ssh_handshake':'passed','host_c_compiler':'passed','host_cpp_compiler':'present','triton_ptxas':'present','ephemeral_keys_removed':True,'gpu_kernels_executed':False}))

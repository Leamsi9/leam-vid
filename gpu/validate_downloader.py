#!/usr/bin/env python3
"""Public CPU HTTP-boundary checks. Does not contact model hosts or run inference."""
import concurrent.futures
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ASSETS=Path(__file__).resolve().parent/'assets'
sys.path.insert(0,str(ASSETS))
spec=importlib.util.spec_from_file_location('public_preparation',ASSETS/'prepare-model.py')
prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep)
import model_storage
PAYLOAD=b'public model download boundary fixture\n'*8192

class Handler(BaseHTTPRequestHandler):
 calls=[]
 mode='normal'
 def log_message(self,*args):pass
 def do_GET(self):
  type(self).calls.append(self.headers.get('Range'))
  if self.mode=='denied':self.send_error(401);return
  if self.mode=='missing':self.send_error(404);return
  start=int(self.headers.get('Range','bytes=0-').split('=')[1].split('-')[0])
  if self.mode=='ignore':start=0
  self.send_response(206 if start else 200)
  if start:self.send_header('Content-Range',f'bytes {start}-{len(PAYLOAD)-1}/{len(PAYLOAD)}')
  self.send_header('Content-Length',str(len(PAYLOAD)-start));self.end_headers()
  if self.mode=='disconnect' and len(self.calls)==1:
   self.wfile.write(PAYLOAD[:len(PAYLOAD)//2]);self.wfile.flush();self.close_connection=True;return
  self.wfile.write((b'x'*len(PAYLOAD) if self.mode=='corrupt' else PAYLOAD)[start:])

class DownloadBoundary(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  prep.ROOT=self.root;prep.STORE=self.root/'artifacts';prep.STORE.mkdir()
  self.item={'key':hashlib.sha256(PAYLOAD).hexdigest(),'size':len(PAYLOAD),'repo':'public/fixture','revision':'fixed','name':'fixture.bin','destination':'diffusion_models/fixture.bin'}
  Handler.calls=[];Handler.mode='normal'
  self.original_get=prep.requests.get
  def local_get(url,**kwargs):
   self.assertTrue(url.startswith('https://huggingface.co/public/fixture/resolve/fixed/'))
   return self.original_get(f'http://127.0.0.1:{self.server.server_port}/fixture',**kwargs)
  self.get_patch=patch.object(prep.requests,'get',side_effect=local_get);self.get_patch.start()
  self.sleep_patch=patch.object(prep.time,'sleep');self.sleep_patch.start()
 def tearDown(self):self.sleep_patch.stop();self.get_patch.stop();self.tmp.cleanup()
 def blob(self):return prep.STORE/self.item['key']
 def partial(self):return Path(str(self.blob())+'.part')
 def assert_complete(self):self.assertEqual(self.blob().read_bytes(),PAYLOAD);self.assertFalse(self.partial().exists())
 def configure_failover(self,gated=False,token=None,corrupt=False):
  self.get_patch.stop();self.urls=[]
  prep.MANIFEST={'models':{'public':{'gated':gated,'files':[self.item]}}}
  def get(url,**kwargs):
   self.urls.append(url)
   self.assertIsNot(kwargs.get('verify',True),False)
   if url.startswith('https://huggingface.co/'):
    raise prep.requests.ConnectionError('public network failure fixture')
   self.assertEqual(url,'https://hf-mirror.com/public/fixture/resolve/fixed/fixture.bin')
   self.assertNotIn('Authorization',kwargs.get('headers',{}))
   if corrupt:Handler.mode='corrupt'
   return self.original_get(f'http://127.0.0.1:{self.server.server_port}/fixture',**kwargs)
  self.get_patch=patch.object(prep.requests,'get',side_effect=get);self.get_patch.start()
 def test_public_connection_failure_uses_verified_mirror(self):
  self.configure_failover();prep.ensure_artifact(self.item,None);self.assert_complete()
  self.assertEqual(len(self.urls),2);self.assertTrue(self.urls[-1].startswith('https://hf-mirror.com/'))
 def test_mirror_resume_preserves_range_and_hash(self):
  self.configure_failover();self.partial().write_bytes(PAYLOAD[:131072])
  prep.ensure_artifact(self.item,None);self.assert_complete();self.assertEqual(Handler.calls,['bytes=131072-'])
 def test_late_connection_failure_does_not_exceed_retry_budget_or_report_success(self):
  self.configure_failover();self.get_patch.stop();Handler.mode='missing';count=0
  def get(url,**kwargs):
   nonlocal count
   count+=1
   if count==5:raise prep.requests.ConnectionError('public late network failure')
   return self.original_get(f'http://127.0.0.1:{self.server.server_port}/fixture',**kwargs)
  self.get_patch=patch.object(prep.requests,'get',side_effect=get);self.get_patch.start()
  with self.assertRaises(prep.PreparationError):prep.ensure_artifact(self.item,None)
  self.assertEqual(count,5);self.assertFalse(self.blob().exists())
 def test_authenticated_artifact_never_uses_mirror(self):
  self.configure_failover()
  with self.assertRaises(prep.PreparationError):prep.ensure_artifact(self.item,'public-test-token-placeholder')
  self.assertEqual(len(self.urls),5);self.assertTrue(all(url.startswith('https://huggingface.co/') for url in self.urls))
 def test_gated_artifact_never_uses_mirror_even_without_token(self):
  self.configure_failover(gated=True)
  with self.assertRaises(prep.PreparationError):prep.ensure_artifact(self.item,None)
  self.assertEqual(len(self.urls),5);self.assertTrue(all(url.startswith('https://huggingface.co/') for url in self.urls))
 def test_mirror_hash_mismatch_is_never_published(self):
  self.configure_failover(corrupt=True)
  with self.assertRaises(prep.PreparationError):prep.ensure_artifact(self.item,None)
  self.assertFalse(self.blob().exists());self.assertFalse(self.partial().exists())
 def test_initial_download_and_verified_reuse(self):
  prep.ensure_artifact(self.item,None);self.assert_complete();prep.ensure_artifact(self.item,None);self.assertEqual(len(Handler.calls),1)
 def test_resume(self):
  self.partial().write_bytes(PAYLOAD[:131072]);prep.ensure_artifact(self.item,None);self.assert_complete();self.assertEqual(Handler.calls,['bytes=131072-'])
 def test_server_ignoring_range_restarts_safely(self):
  self.partial().write_bytes(PAYLOAD[:131072]);Handler.mode='ignore';prep.ensure_artifact(self.item,None);self.assert_complete()
 def test_complete_partial_is_promoted_without_http(self):
  self.partial().write_bytes(PAYLOAD);prep.ensure_artifact(self.item,None);self.assert_complete();self.assertEqual(Handler.calls,[])
 def test_corrupt_complete_partial_is_not_trusted(self):
  self.partial().write_bytes(b'x'*len(PAYLOAD));prep.ensure_artifact(self.item,None);self.assert_complete();self.assertEqual(len(Handler.calls),1)
 def test_truncated_transfer_recovers(self):
  Handler.mode='disconnect';prep.ensure_artifact(self.item,None);self.assert_complete();self.assertEqual(len(Handler.calls),2)
 def test_downloaded_hash_mismatch_is_never_published(self):
  Handler.mode='corrupt'
  with self.assertRaises(prep.PreparationError):prep.ensure_artifact(self.item,None)
  self.assertFalse(self.blob().exists());self.assertFalse(self.partial().exists())
 def test_shared_lock_prevents_duplicate_download(self):
  with concurrent.futures.ThreadPoolExecutor(max_workers=3) as workers:
   list(workers.map(lambda _:prep.ensure_artifact(self.item,None),range(3)))
  self.assert_complete();self.assertEqual(len(Handler.calls),1)
 def test_access_failure_is_explicit(self):
  Handler.mode='denied'
  with self.assertRaises(prep.PreparationError) as error:prep.ensure_artifact(self.item,None)
  self.assertEqual(error.exception.code,'access');self.assertFalse(self.blob().exists())
 def test_missing_artifact_has_bounded_retries(self):
  Handler.mode='missing'
  with self.assertRaises(prep.PreparationError):prep.ensure_artifact(self.item,None)
  self.assertEqual(len(Handler.calls),5);self.assertFalse(self.blob().exists())
 def test_partial_is_not_advertised_as_installed(self):
  model_storage.ROOT=self.root;model_storage.STORE=prep.STORE
  model_storage.MANIFEST={'public_model':{'native':True,'files':[self.item]}}
  self.partial().write_bytes(PAYLOAD[:100]);row=model_storage.inventory()['models'][0]
  self.assertFalse(row['downloaded']);self.assertEqual(row['downloaded_bytes'],100)

if __name__=='__main__':unittest.main(verbosity=2)

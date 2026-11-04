#!/usr/bin/env python3
"""Public tiny-file HTTP checks for initial preparation and deliberate removal."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib,importlib.util,io,json
from pathlib import Path
import sys,tempfile,threading,time,unittest
from unittest.mock import patch
ASSETS=Path(__file__).resolve().parent/'assets';sys.path.insert(0,str(ASSETS))
import model_storage

def load_background():
 spec=importlib.util.spec_from_file_location('public_background',ASSETS/'prepare-background.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class Handler(BaseHTTPRequestHandler):
 payloads={};owners={};events=[];active=0;maximum=0;families=set();overlap=False;lock=threading.Lock();failure=None;pause=None;entered=None;pause_owner=None
 def log_message(self,*args):pass
 def do_GET(self):
  name=self.path.rsplit('/',1)[-1];owner=self.owners[name]
  with self.lock:
   type(self).events.append(owner);type(self).active+=1;type(self).maximum=max(self.maximum,self.active);self.families.add(owner)
   if len(self.families)>1:type(self).overlap=True
  try:
   if self.pause and (self.pause_owner is None or owner==self.pause_owner):
    if self.entered:self.entered.set()
    self.pause.wait(timeout=5)
   if owner==self.failure:self.send_error(401);return
   time.sleep(.01)
   body=self.payloads[name];self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  finally:
   with self.lock:
    type(self).active-=1
    if self.active==0:self.families.clear()

class Prefetch(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=cls.http.serve_forever,daemon=True).start()
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.bg=load_background()
  Handler.payloads={};Handler.owners={};Handler.events=[];Handler.active=0;Handler.maximum=0;Handler.families=set();Handler.overlap=False;Handler.failure=None;Handler.pause=None;Handler.entered=None;Handler.pause_owner=None
  def entry(owner,name,size):
   body=(('public:'+name).encode()*size)[:size];Handler.payloads[name]=body;Handler.owners[name]=owner
   return {'name':name,'repo':'public/prefetch','revision':'fixture-v1','key':hashlib.sha256(body).hexdigest(),'size':len(body),'destination':'diffusion_models/'+name}
  shared=entry('fasth3_trim','shared.bin',100)
  self.manifest={'models':{
   'h3':{'native':True,'files':[shared,entry('h3','h3.bin',800),entry('h3','h3-lora.bin',100)]},
   'fasth3_v2':{'native':True,'files':[shared,entry('fasth3_v2','v2.bin',700)]},
   'turbowan':{'native':False,'files':[entry('turbowan','turbo-a.bin',100),entry('turbowan','turbo-b.bin',100),entry('turbowan','turbo-c.bin',100)]},
   'fasth3_trim':{'native':True,'files':[shared,entry('fasth3_trim','trim.bin',600)]}}}
  self.configure(self.bg)
  model_storage.ROOT=self.root;model_storage.STORE=self.root/'artifacts';model_storage.MANIFEST=self.manifest['models']
  self.real_get=self.bg.prep.requests.get
  self.get_patch=patch.object(self.bg.prep.requests,'get',side_effect=lambda url,**kwargs:self.real_get(f'http://127.0.0.1:{self.http.server_port}/'+url.rsplit('/',1)[-1],**kwargs));self.get_patch.start()
  self.output=self.root/'ComfyUI/output/keep.fixture';self.output.parent.mkdir(parents=True);self.output.write_bytes(b'public preserved output fixture')
 def configure(self,bg):
  bg.prep.ROOT=self.root;bg.prep.STORE=self.root/'artifacts';bg.prep.COMFY=self.root/'ComfyUI';bg.prep.MANIFEST=self.manifest
 def tearDown(self):self.get_patch.stop();self.tmp.cleanup()
 def run_initial(self,bg=None):
  with redirect_stdout(io.StringIO()):(bg or self.bg).main()
 def marker(self):return self.root/'initial-prefetch-native-v1.complete'
 def test_two_model_default_is_sequential(self):
  self.manifest['models']={key:self.manifest['models'][key] for key in ['h3','turbowan']}
  self.configure(self.bg)
  Handler.owners['shared.bin']='h3'
  self.run_initial()
  self.assertEqual(list(dict.fromkeys(Handler.events)),['turbowan','h3'])
  self.assertFalse(Handler.overlap)
  self.assertTrue((self.root/'initial-prefetch-native-v1.complete').exists())

 def test_actual_http_models_are_sequential_smallest_first(self):
  self.run_initial();groups=[]
  for owner in Handler.events:
   if not groups or groups[-1]!=owner:groups.append(owner)
  self.assertEqual(groups,['turbowan','fasth3_trim','fasth3_v2','h3']);self.assertFalse(Handler.overlap);self.assertLessEqual(Handler.maximum,3);self.assertTrue(self.marker().is_file())
 def test_interrupted_initial_download_resumes_without_redownloading_done_model(self):
  Handler.failure='fasth3_trim'
  with self.assertRaises(self.bg.prep.PreparationError):self.run_initial()
  self.assertFalse(self.marker().exists());turbo_calls=Handler.events.count('turbowan');Handler.failure=None
  fresh=load_background();self.configure(fresh);self.run_initial(fresh)
  self.assertTrue(self.marker().exists());self.assertEqual(Handler.events.count('turbowan'),turbo_calls)
 def test_removal_survives_fresh_background_process_and_preserves_shared_files(self):
  self.run_initial();before=len(Handler.events);model_storage.remove_model('fasth3_v2')
  shared=self.manifest['models']['h3']['files'][0];self.assertTrue((self.root/'artifacts'/shared['key']).exists());self.assertTrue((self.root/'ComfyUI/models'/shared['destination']).exists())
  fresh=load_background();self.configure(fresh);self.run_initial(fresh)
  self.assertEqual(len(Handler.events),before);self.assertFalse((self.root/'ComfyUI/models/diffusion_models/v2.bin').exists());self.assertTrue(self.output.exists())
 def test_last_native_model_removal_reclaims_shared_blob(self):
  self.run_initial()
  for model in ('fasth3_v2','fasth3_trim','h3'):model_storage.remove_model(model)
  shared=self.manifest['models']['h3']['files'][0];self.assertFalse((self.root/'artifacts'/shared['key']).exists());self.assertTrue(self.output.exists())
 def test_ready_model_can_dequeue_while_later_models_download(self):
  import heapq,ready_queue
  ready_queue.ROOT=self.root;ready_queue.MANIFEST=self.manifest['models'];ready_queue.target=model_storage.target;ready_queue._failed.clear()
  (self.root/'turbowan-runtime.installed').touch()
  Handler.entered=threading.Event();Handler.pause=threading.Event();Handler.pause_owner='fasth3_trim'
  class Queue:
   def __init__(self):
    self.queue=[(1,'public-waiting',{'10':{'class_type':'PrivateVideoModels','inputs':{'model':'fasth3_v2'}}},{},[]),(2,'public-runnable',{'30':{'class_type':'PrivateVideoGenerate','inputs':{'model':'turbowan'}}},{},[])]
    heapq.heapify(self.queue);self.mutex=threading.RLock();self.not_empty=threading.Condition(self.mutex);self.currently_running={};self.task_counter=0
  class Server:
   def __init__(self):self.prompt_queue=Queue();self.prompt_queue.server=self
   def queue_updated(self):pass
  with ThreadPoolExecutor(max_workers=1) as pool:
   job=pool.submit(self.run_initial);self.assertTrue(Handler.entered.wait(timeout=5))
   try:
    self.assertTrue(ready_queue.available('turbowan'));self.assertFalse(ready_queue.available('fasth3_v2'))
    server=Server();ready_queue.install(server);selected,_=server.prompt_queue.get(timeout=.2)
    self.assertEqual(selected[1],'public-runnable');self.assertEqual(server.prompt_queue.queue[0][1],'public-waiting');self.assertGreater(Handler.active,0)
   finally:Handler.pause.set()
   job.result(timeout=10)
 def test_remove_refused_during_initial_prefetch(self):
  Handler.entered=threading.Event();Handler.pause=threading.Event()
  with ThreadPoolExecutor(max_workers=1) as pool:
   job=pool.submit(self.run_initial);self.assertTrue(Handler.entered.wait(timeout=5))
   try:
    with self.assertRaises(RuntimeError):model_storage.remove_model('fasth3_v2')
   finally:Handler.pause.set()
   job.result(timeout=10)
  self.assertTrue(self.marker().is_file())

if __name__=='__main__':unittest.main(verbosity=2)

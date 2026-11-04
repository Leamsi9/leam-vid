#!/usr/bin/env python3
"""Exercise the native queue-selection boundary with public CPU-only items."""
from pathlib import Path
import heapq,sys,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent/'assets'))
import ready_queue

class Queue:
 def __init__(self,items):
  self.queue=items;heapq.heapify(self.queue)
  self.mutex=threading.RLock();self.not_empty=threading.Condition(self.mutex)
  self.currently_running={};self.task_counter=0
class Server:
 def __init__(self,items):self.prompt_queue=Queue(items);self.prompt_queue.server=self;self.updates=0
 def queue_updated(self):self.updates+=1

def item(number,model):return(number,str(number),{'10':{'class_type':'PrivateVideoModels','inputs':{'model':model}}},{'create_time':0},[])

class Selection(unittest.TestCase):
 def setUp(self):
  self.ready={'h3'}
  self.available=patch.object(ready_queue,'available',side_effect=lambda model:model in self.ready);self.available.start()
  self.prepare=patch.object(ready_queue,'prepare_pending');self.prepare.start()
 def tearDown(self):self.prepare.stop();self.available.stop()
 def test_oldest_runnable_preserves_skipped_order_and_identity(self):
  original=[item(1,'fasth3_v2'),item(2,'fasth3_trim'),item(3,'h3'),item(4,'h3')]
  server=Server(original.copy());ready_queue.install(server)
  selected,_=server.prompt_queue.get(timeout=0.01);self.assertEqual(selected[1],'3')
  self.ready.add('fasth3_trim')
  selected,_=server.prompt_queue.get(timeout=0.01);self.assertEqual(selected[1],'2')
  self.ready.add('fasth3_v2')
  selected,_=server.prompt_queue.get(timeout=0.01);self.assertEqual(selected[1],'1')
  self.assertEqual([entry[1] for entry in server.prompt_queue.queue],['4'])
 def test_unavailable_job_is_retained_on_timeout(self):
  server=Server([item(1,'fasth3_v2')]);ready_queue.install(server)
  self.assertIsNone(server.prompt_queue.get(timeout=0.01));self.assertEqual(server.prompt_queue.queue[0][1],'1')
 def test_stock_loader_manifest_identity_is_detected(self):
  name=next(entry['destination'].split('/',1)[1] for entry in ready_queue.MANIFEST['fasth3_v2']['files'] if entry['destination'].startswith('diffusion_models/'))
  graph=(1,'public-id',{'10':{'class_type':'UNETLoader','inputs':{'unet_name':name}}},{},[])
  self.assertEqual(ready_queue.model_ids(graph),{'fasth3_v2'})
if __name__=='__main__':unittest.main(verbosity=2)

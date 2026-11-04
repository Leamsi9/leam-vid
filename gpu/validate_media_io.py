#!/usr/bin/env python3
"""Exercise actual SaveVideo dynamic inputs/codecs on tiny public CPU tensors."""
import json,os
from pathlib import Path
import subprocess,sys,tempfile
from fractions import Fraction
from types import SimpleNamespace
os.environ.update(CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
sys.path.insert(0,'/workspace/wan-cloud/ComfyUI');sys.argv=[sys.argv[0],'--cpu','--offline']
import comfy.options
comfy.options.enable_args_parsing()
import torch
import folder_paths
from comfy_api.latest import InputImpl,Types,_io
from comfy_extras.nodes_video import SaveVideo

results=[]
with tempfile.TemporaryDirectory(prefix='public-video-codec-') as temp:
 folder_paths.set_output_directory(temp)
 for enabled in (False,True):
  images=torch.zeros((4,32,32,3),dtype=torch.float32);images[...,0]=1
  audio={'waveform':torch.zeros((1,2,8000)), 'sample_rate':48000} if enabled else None
  video=InputImpl.VideoFromComponents(Types.VideoComponents(images=images,audio=audio,frame_rate=Fraction(24)))
  flat={'video':video,'filename_prefix':'public-validation/audio-'+str(enabled).lower(),'format':'mp4','format.codec':'h264','format.codec.encoding':'auto'}
  _,_,v3_data=_io.get_finalized_class_inputs(SaveVideo.INPUT_TYPES(),flat)
  nested=_io.build_nested_inputs(flat,v3_data)
  assert nested['format']['format']=='mp4' and nested['format']['codec']['codec']=='h264'
  SaveVideo.hidden=SimpleNamespace(prompt=None,extra_pnginfo=None)
  SaveVideo.execute(**nested)
  path=next(Path(temp).rglob('audio-'+str(enabled).lower()+'_*.mp4'))
  probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(path)]))
  streams=probe['streams'];videos=[s for s in streams if s['codec_type']=='video'];audios=[s for s in streams if s['codec_type']=='audio']
  assert len(videos)==1 and videos[0]['codec_name']=='h264' and videos[0]['width']==32 and videos[0]['height']==32
  assert bool(audios)==enabled
  # Decode the public file too; no model or GPU is involved.
  subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  results.append({'audio':enabled,'container':'mp4','video_codec':videos[0]['codec_name'],'audio_codec':audios[0]['codec_name'] if audios else None,'decoded':True})
assert not torch.cuda.is_initialized()
print(json.dumps({'cases':results,'temporary_files_removed':True,'gpu_used':False,'model_generation_run':False}))

#!/usr/bin/env python3
"""Offline public UMT5 tokenizer only; no text-encoder weights or CUDA use."""
import json,os,sys
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',CUDA_VISIBLE_DEVICES='')
source='/workspace/wan-cloud/runtime-source/TurboDiffusion/turbodiffusion'
sys.path[:0]=[source,source+'/inference']
import torch
from rcm.utils.umt5 import HuggingfaceTokenizer
folder=sys.argv[1]
tokenizer=HuggingfaceTokenizer(folder,seq_len=512,clean='whitespace',local_files_only=True)
ids,mask=tokenizer(['A small red ball on a plain table.'],return_mask=True)
assert tuple(ids.shape)==(1,512) and tuple(mask.shape)==(1,512)
assert int(mask.sum())>0 and not torch.cuda.is_initialized()
print(json.dumps({'tokenizer':'official local UMT5','offline':True,'shape':list(ids.shape),'active_tokens':int(mask.sum()),'cuda_initialized':False,'model_weights_loaded':False,'generation_run':False}))

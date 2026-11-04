#!/usr/bin/env python3
"""Sequential smallest-first model families; three bounded shared downloads."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import importlib.util
from pathlib import Path
import shutil
ASSETS=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('preparation',ASSETS/'prepare-model.py')
prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep)

def main():
 prep.ROOT.mkdir(parents=True,exist_ok=True);prep.STORE.mkdir(exist_ok=True)
 with (prep.ROOT/'background-preparation.lock').open('a') as owner,(prep.ROOT/'model-preparation.lock').open('a') as shared:
  try:fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  fcntl.flock(shared,fcntl.LOCK_SH)
  completed=prep.ROOT/'initial-prefetch-native-v1.complete'
  if completed.is_file():return
  models=sorted(prep.MANIFEST['models'],key=lambda key:sum({x['key']:x['size'] for x in prep.MANIFEST['models'][key]['files']}.values()))
  unique={item['key']:item for model in models for item in prep.MANIFEST['models'][model]['files']}
  remaining=sum(max(0,item['size']-min(item['size'],Path(str(prep.STORE/item['key'])+'.part').stat().st_size if Path(str(prep.STORE/item['key'])+'.part').exists() else 0)) for item in unique.values() if not prep.present(prep.STORE/item['key'],item['size']))
  if shutil.disk_usage(prep.ROOT).free<remaining+4*1024**3:raise RuntimeError('Insufficient disk for the selected native model set.')
  with ThreadPoolExecutor(max_workers=3) as pool:
   for model in models:
    files=prep.MANIFEST['models'][model]['files']
    futures=[pool.submit(prep.ensure_artifact,item,None) for item in files if not prep.present(prep.STORE/item['key'],item['size'])]
    for future in futures:future.result()
    for item in files:prep.link_artifact(model,item)
    print('MODEL_INSTALLED='+model,flush=True)
  # Removal is allowed only after the background owner lock releases. Keep this
  # one native completion marker so later boots do not restore removed models.
  completed.touch()
if __name__=='__main__':main()

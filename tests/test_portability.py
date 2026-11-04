"""Portable configuration/graphs and actual HTTP privacy boundary, no cloud calls."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import server
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

spec=importlib.util.spec_from_file_location('build_image',ROOT/'scripts/build-image.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)

class Portable(unittest.TestCase):
    def test_default_catalog_only_uses_requested_models(self):
        self.assertEqual(list(server.MODEL_BY_ID),['turbowan','h3'])
        self.assertEqual(server.DEFAULT_MODEL,'turbowan')

    def test_supported_subset_deduplicates_shared_files(self):
        manifest=json.loads((ROOT/'gpu/assets/runtime-models.json').read_text())
        selected=build.selected_manifest(manifest,['fasth3_trim','h3'])
        self.assertEqual(list(selected['models']),['fasth3_trim','h3'])
        total=sum(item['size'] for model in selected['models'].values() for item in model['files'])
        unique=sum({item['key']:item['size'] for model in selected['models'].values() for item in model['files']}.values())
        self.assertLess(unique,total)
        for invalid in [[],['unsupported'],['h3','h3']]:
            with self.assertRaises(ValueError):build.selected_manifest(manifest,invalid)

    def test_subset_catalog_has_working_default(self):
        env={**os.environ,'PYTHONPATH':str(ROOT/'app'),'LEAM_VID_MODELS':'h3'}
        data=json.loads(subprocess.check_output([sys.executable,'-c','import json,model_catalog as m; print(json.dumps([m.DEFAULT_MODEL,list(m.MODEL_BY_ID)]))'],env=env,text=True))
        self.assertEqual(data,['h3',['h3']])

    def test_public_graphs_preserve_required_save_format(self):
        for model in server.MODEL_BY_ID:
            config=server.generation_config({'model':model})
            graph=server.graph('A small red ball on a plain table.',config)
            saves=[node['inputs'] for node in graph.values() if node['class_type']=='SaveVideo']
            self.assertEqual(len(saves),1)
            self.assertEqual(saves[0]['format'],'mp4')
            self.assertEqual(saves[0]['format.codec'],'h264')
            if model!='turbowan':self.assertEqual(graph['10']['class_type'],'PrivateVideoModels')

    def test_choices_resolve_without_saving_or_rewriting_plain_text(self):
        original='  A small red ball.\n'
        self.assertEqual(server.expand_prompt_choices(original),original)
        self.assertIn(server.expand_prompt_choices('{red|blue} ball'),{'red ball','blue ball'})

class Boundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app=web.Application(middlewares=[server.private_boundary])
        async def public(request):return web.json_response({'ok':True})
        app.router.add_get('/',public)
        app.router.add_get('/api/example',public)
        app.router.add_post('/api/example',public)
        self.client=TestClient(TestServer(app));await self.client.start_server()
    async def asyncTearDown(self):await self.client.close()
    async def test_pwa_shell_works_with_cross_site_launcher(self):
        response=await self.client.get('/',headers={'Host':'localhost:8189','Sec-Fetch-Site':'cross-site'})
        self.assertEqual(response.status,200)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
    async def test_cross_site_data_and_foreign_host_blocked(self):
        for path,headers in [('/api/example',{'Host':'localhost:8189','Sec-Fetch-Site':'cross-site'}),('/',{'Host':'foreign.example'}),('/',{'Host':'localhost:8189','Origin':'https://foreign.example'})]:
            response=await self.client.get(path,headers=headers);self.assertEqual(response.status,403)
    async def test_write_requires_allowed_origin_and_json(self):
        for headers in [{'Host':'localhost:8189'},{'Host':'localhost:8189','Origin':'https://foreign.example'}]:
            response=await self.client.post('/api/example',json={},headers=headers);self.assertEqual(response.status,403)
        response=await self.client.post('/api/example',json={},headers={'Host':'localhost:8189','Origin':'http://localhost:8189'})
        self.assertEqual(response.status,200)

if __name__=='__main__':unittest.main()

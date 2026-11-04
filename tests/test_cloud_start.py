"""The HTTP action boundary handles a provider rejection without starting setup."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
import lifecycle
import server
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer

class Start(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        with patch.object(lifecycle,'CONFIG',Path(self.directory.name)/'cloud.json'):
            self.manager=lifecycle.CloudManager({'submission_lock':asyncio.Lock()},AsyncMock(),AsyncMock())
        self.manager.native={'id':42,'actual_status':'exited','intended_status':'stopped'}
        self.manager.config={'instance_id':42}
        self.manager.refresh=AsyncMock(return_value={})
        self.manager.prepare=AsyncMock()
        self.manager.api=AsyncMock(return_value={'success':False,'msg':'provider-private-detail'})
        app=web.Application(middlewares=[server.private_boundary]);app['cloud']=self.manager
        app.router.add_post('/api/cloud/{action:start}',server.cloud_action)
        self.client=TestClient(TestServer(app));await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close();self.directory.cleanup()
    async def request(self):
        return await self.client.post('/api/cloud/start',headers={'Host':'localhost:8189','Origin':'http://localhost:8189'},json={'instance_id':42,'request_id':'60f8c45e-523f-47bb-a9e2-9736bc63bc6d'})
    async def test_explicit_provider_rejection_is_failed_not_starting(self):
        response=await self.request();data=await response.json()
        self.assertEqual(response.status,503);self.assertEqual(data['state'],'failed')
        self.assertIn('rejected',data['message']);self.assertNotIn('provider-private-detail',data['message'])
        self.assertEqual(self.manager.operation['state'],'idle');self.assertIsNone(self.manager.pending_action)
        self.assertIsNone(self.manager.preparation);self.manager.prepare.assert_not_called()
        self.manager.api.assert_awaited_once()
    async def test_lost_provider_response_stays_unknown_without_retry(self):
        self.manager.api.side_effect=TimeoutError()
        response=await self.request();data=await response.json()
        self.assertEqual(response.status,503);self.assertEqual(data['state'],'unknown')
        self.assertEqual(self.manager.operation['state'],'unknown');self.assertEqual(self.manager.pending_action,('start',42))
        self.manager.prepare.assert_not_called();self.manager.api.assert_awaited_once()

if __name__=='__main__':unittest.main()

"""Cloud entrypoint checks with an in-memory SDK double and no browser/network."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from apify import Event
from src.environment import ROOT
from src import main as entry
import test_extract


class FakeActor:
    configuration = SimpleNamespace(input_key='INPUT')

    def __init__(self, data):
        self.input = data
        self.records, self.values, self.listeners, self.exits = [], {}, {}, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get_input(self):
        return self.input

    async def set_value(self, key, value):
        self.values[key] = deepcopy(value)

    async def push_data(self, value):
        self.records.append(deepcopy(value))

    def on(self, event, callback):
        self.listeners[event] = callback

    async def exit(self, *, exit_code=0, **_):
        self.exits.append(exit_code)


class CloudLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cloud-offline-', dir=ROOT / '.tmp')
        self.root = Path(self.temp.name).resolve()
        assert self.root.is_relative_to(ROOT)
        (self.root / '.actor').mkdir()
        shutil.copyfile(ROOT / '.actor/dataset_schema.json', self.root / '.actor/dataset_schema.json')
        (self.root / 'reports').mkdir()
        (self.root / 'reports/access-blocked.json').write_text('{"status":"blocked"}', encoding='utf-8')
        case = test_extract.ExtractTests()
        case.setUp()
        self.case = case
        self.actor = FakeActor({'url': case.input['url'], 'outputFormat': 'reference1688'})

    def tearDown(self):
        self.temp.cleanup()

    def context(self, acquire):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(entry, 'ROOT', self.root))
        stack.enter_context(patch.object(entry, 'Actor', self.actor))
        stack.enter_context(patch.object(entry, 'event'))
        stack.enter_context(patch.object(entry, 'advice'))
        stack.enter_context(patch('src.browser.acquire', acquire))
        stack.enter_context(patch.dict('os.environ', {'APIFY_IS_AT_HOME': '1', 'BACKMARKET_F5': '0'}))
        return stack

    async def test_cloud_ignores_local_marker_and_pushes_one_aggregate(self):
        html = '<html><script type="application/json" id="captured-commercial-data">' + json.dumps(
            {'productId': self.case.input['productId'], 'data': self.case.data}) + '</script></html>'
        acquire = AsyncMock(return_value=(html, {}, {'status': 'received'}))
        with self.context(acquire):
            await entry.run()
        acquire.assert_awaited_once()
        self.assertEqual(len(self.actor.records), 1)
        self.assertEqual(len(self.actor.records[0]), 11)
        self.assertEqual(self.actor.records[0]['price'], '12.30')
        self.assertEqual(self.actor.values['RUN_STATE']['status'], 'success')
        self.assertEqual(self.actor.values['RUN_STATE']['savedResults'], 1)
        self.assertNotIn('INPUT', self.actor.values)
        self.assertEqual(self.actor.values['RUN_STATE']['proxy']['status'], 'disabled')
        self.assertEqual(acquire.await_args.kwargs, {})

    async def test_challenge_fails_without_a_product_or_stale_output(self):
        (self.root / 'output').mkdir()
        (self.root / 'output/product.json').write_text('{"old":true}', encoding='utf-8')
        acquire = AsyncMock(return_value=('', {}, {'status': 'blocked', 'failureStage': 'redirect',
            'error': {'code': 'ACCESS_CHALLENGE', 'message': 'Synthetic offline challenge'}}))
        with self.context(acquire):
            await entry.run()
        self.assertEqual(self.actor.records, [])
        self.assertEqual(self.actor.exits, [1])
        self.assertEqual(self.actor.values['RUN_STATE']['status'], 'blocked')
        self.assertIsNone(self.actor.values['RUN_STATE']['outputFile'])
        self.assertFalse((self.root / 'output/product.json').exists())

    async def test_proxy_passes_once_to_acquisition_and_saves_one_aggregate(self):
        self.actor.input.update(proxyConfiguration={'useApifyProxy': True, 'apifyProxyGroups': ['TEST_GROUP']},
                                proxyAccessAuthorized=True)
        sdk_proxy = SimpleNamespace(new_url=AsyncMock(
            return_value='http://test-user:synthetic-password@proxy.invalid:8000'))
        self.actor.create_proxy_configuration = AsyncMock(return_value=sdk_proxy)
        html = '<html><script type="application/json">' + json.dumps(
            {'productId': self.case.input['productId'], 'data': self.case.data}) + '</script></html>'
        acquire = AsyncMock(return_value=(html, {}, {'status': 'received',
            'requests': [{'category': 'document', 'httpStatus': 200}]}))
        with self.context(acquire):
            await entry.run()
        acquire.assert_awaited_once()
        self.assertEqual(acquire.await_args.kwargs['proxy_config'].server, 'http://proxy.invalid:8000')
        self.assertEqual(len(self.actor.records), 1)
        self.assertEqual(len(self.actor.records[0]), 11)
        state = self.actor.values['RUN_STATE']
        self.assertEqual(state['status'], 'success')
        self.assertEqual(state['proxy']['status'], 'configured')
        self.assertTrue(state['proxy']['connected'])
        self.assertTrue(state['proxy']['documentRequestSucceeded'])
        sdk_proxy.new_url.assert_awaited_once_with(session_id=state['runId'])
        self.assertNotIn('synthetic-password', json.dumps(self.actor.values))

    async def test_proxy_setup_error_does_not_start_acquisition(self):
        self.actor.input.update(proxyConfiguration={'useApifyProxy': True, 'apifyProxyGroups': ['TEST_GROUP']},
                                proxyAccessAuthorized=True)
        self.actor.create_proxy_configuration = AsyncMock(side_effect=RuntimeError('synthetic-password'))
        acquire = AsyncMock()
        with self.context(acquire):
            await entry.run()
        acquire.assert_not_awaited()
        self.assertEqual(self.actor.records, [])
        self.assertEqual(self.actor.exits, [1])
        state = self.actor.values['RUN_STATE']
        self.assertEqual(state['stage'], 'proxy-setup')
        self.assertEqual(state['error']['code'], 'PROXY_CONFIGURATION_ERROR')
        self.assertEqual(state['proxy']['status'], 'error')
        self.assertNotIn('synthetic-password', json.dumps(self.actor.values))

    async def test_unblocker_omits_session_and_saves_one_aggregate(self):
        self.actor.input.update(proxyConfiguration={'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER']},
                                proxyAccessAuthorized=True)
        sdk_proxy = SimpleNamespace(new_url=AsyncMock(
            return_value='http://groups-UNBLOCKER:synthetic-password@proxy.invalid:8000'))
        self.actor.create_proxy_configuration = AsyncMock(return_value=sdk_proxy)
        html = '<html><script type="application/json">' + json.dumps(
            {'productId': self.case.input['productId'], 'data': self.case.data}) + '</script></html>'
        acquire = AsyncMock(return_value=(html, {}, {'status': 'received',
            'requests': [{'category': 'document', 'httpStatus': 200}]}))
        with self.context(acquire):
            await entry.run()
        sdk_proxy.new_url.assert_awaited_once_with()
        acquire.assert_awaited_once()
        self.assertEqual(acquire.await_args.kwargs['proxy_config'].username, 'groups-UNBLOCKER')
        self.assertEqual(acquire.await_args.kwargs['proxy_mode'], 'unblocker')
        self.assertEqual(len(self.actor.records), 1)
        self.assertEqual(len(self.actor.records[0]), 11)
        state = self.actor.values['RUN_STATE']
        self.assertEqual(state['proxy']['mode'], 'unblocker')
        self.assertEqual(state['proxy']['sessionMode'], 'not-supported')
        self.assertEqual(state['status'], 'success')
        self.assertNotIn('synthetic-password', json.dumps(self.actor.values))

    async def test_abort_finishes_work_before_sdk_exit(self):
        entered = asyncio.Event()
        cleaned_up = False

        async def acquire(_):
            nonlocal cleaned_up
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up = True

        with self.context(acquire):
            task = asyncio.create_task(entry.run())
            await asyncio.wait_for(entered.wait(), timeout=3)
            abort = asyncio.create_task(self.actor.listeners[Event.ABORTING]())
            await asyncio.wait_for(asyncio.gather(task, abort), timeout=3)
        self.assertTrue(cleaned_up)
        self.assertEqual(self.actor.records, [])
        self.assertEqual(self.actor.values['RUN_STATE']['status'], 'aborted')
        self.assertEqual(self.actor.values['RUN_STATE']['logicalExitCode'], 143)
        self.assertTrue(self.actor.exits and all(code == 143 for code in self.actor.exits))

    async def test_empty_document_does_not_create_a_product(self):
        acquire = AsyncMock(return_value=('<html><body>No product data</body></html>', {}, {'status': 'received'}))
        with self.context(acquire):
            await entry.run()
        self.assertEqual(self.actor.records, [])
        self.assertEqual(self.actor.exits, [1])
        self.assertEqual(self.actor.values['RUN_STATE']['error']['code'], 'NO_PRODUCT_DATA')

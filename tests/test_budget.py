"""Offline deadline and DOM handoff checks; no real browser or network."""
import asyncio
import json
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import budget
from src.browser import acquire
from src.extract import parse_input

URL = 'https://www.backmarket.com/en-us/p/offline-test/11111111-1111-4111-8111-111111111111'


class BudgetTests(unittest.TestCase):
    def test_deadlines_share_elapsed_startup_and_never_exceed_55(self):
        with patch('src.budget.time.monotonic', return_value=120):
            clock = budget.RunBudget(100)
            self.assertEqual(clock.remaining(48), 28)
            self.assertEqual(clock.remaining(52), 32)
            self.assertEqual(clock.remaining(), 35)
        self.assertEqual((budget.TOTAL_SECONDS, budget.ACQUISITION_SECONDS), (55, 48))
        default_input = parse_input({'url': URL})
        self.assertEqual(default_input['timeoutSeconds'], 48)
        self.assertFalse(default_input['diagnosticMode'])
        diagnostic = parse_input({'url': URL, 'diagnosticMode': True})
        self.assertEqual(diagnostic['timeoutSeconds'], 160)
        self.assertEqual(diagnostic['diagnosticTimeoutSeconds'], 180)
        shorter = parse_input({'url': URL, 'diagnosticMode': True, 'diagnosticTimeoutSeconds': 90,
                               'timeoutSeconds': 48})
        self.assertEqual(shorter['timeoutSeconds'], 70)
        with self.assertRaises(ValueError):
            parse_input({'url': URL, 'diagnosticMode': True, 'diagnosticTimeoutSeconds': 181})
        with self.assertRaises(ValueError):
            parse_input({'url': URL, 'timeoutSeconds': 49})

    def test_watchdog_counts_os_process_age_and_exits_on_deadline(self):
        with patch('src.budget._process_start', None), \
                patch('src.budget.time.time', return_value=100), \
                patch('src.budget.time.monotonic', return_value=50), \
                patch.dict('os.environ', {'APIFY_IS_AT_HOME': '0'}), \
                patch('psutil.Process', return_value=SimpleNamespace(create_time=lambda: 95)), \
                patch('src.budget.threading.Timer') as timer, \
                patch('src.budget.os.write'), patch('src.budget.os._exit') as terminate:
            budget.arm_process_watchdog()
            self.assertEqual(timer.call_args.args[0], 175)
            timer.call_args.args[1]()
            terminate.assert_called_once_with(1)
            self.assertTrue(timer.return_value.daemon)

    def test_cloud_platform_start_reduces_time_left_before_python(self):
        with patch('src.budget._process_start', None), \
                patch('src.budget.time.time', return_value=100), \
                patch('src.budget.time.monotonic', return_value=50), \
                patch.dict('os.environ', {'APIFY_IS_AT_HOME': '1',
                                         'ACTOR_STARTED_AT': '1970-01-01T00:01:28Z'}), \
                patch('psutil.Process', return_value=SimpleNamespace(create_time=lambda: 95)), \
                patch('src.budget.threading.Timer') as timer:
            budget.arm_process_watchdog()
            self.assertEqual(timer.call_args.args[0], 168)

    def test_watchdog_can_be_configured_to_diagnostic_deadline(self):
        previous = SimpleNamespace(cancel=unittest.mock.Mock())
        with patch('src.budget._process_start', 40), \
                patch('src.budget.time.monotonic', return_value=50), \
                patch('src.budget._watchdog_timer', previous), \
                patch('src.budget.threading.Timer') as timer:
            budget.configure_process_watchdog(180)
        previous.cancel.assert_called_once_with()
        self.assertEqual(timer.call_args.args[0], 170)


class AcquisitionBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_css_reads_response_body_and_hands_off_html_without_candidate_probe(self):
        from unittest.mock import patch
        data = parse_input({'url': URL, 'extractionMethod': 'crawl4ai_css'})
        html = ('<html><head><title>Test item</title></head><body>'
                '<main data-product-id="11111111-1111-4111-8111-111111111111">'
                '<h1>Test item</h1><span data-testid="product-price">USD 12.00</span></main></body></html>')
        hooks, handlers, frame = {}, {}, object()
        page = SimpleNamespace(main_frame=frame, route=AsyncMock(),
            evaluate=AsyncMock(return_value={'webdriver': True, 'userAgent': 'native'}),
            on=lambda name, fn: handlers.update({name: fn}))
        response = SimpleNamespace(url=URL, status=200, frame=frame,
            headers={'content-type': 'text/html', 'content-length': str(len(html.encode()))},
            body=AsyncMock(return_value=html.encode()),
            request=SimpleNamespace(is_navigation_request=lambda: True))
        async def arun(*_, **__):
            await hooks['before_goto'](page)
            handlers['response'](response)
            await asyncio.Event().wait()
        crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda name, fn: hooks.update({name: fn})),
            arun=AsyncMock(side_effect=arun))
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'), \
                patch('src.dom_crawl4ai.candidate', side_effect=AssertionError('CSS candidate probe must not run in response callback')):
            document, _, report = await asyncio.wait_for(acquire(data), timeout=1)
        self.assertEqual(document, html)
        self.assertEqual(report['status'], 'received')
        self.assertEqual(report['requests'][0]['bodyRead']['status'], 'read')
        self.assertEqual(report['requests'][0]['bodyRead']['bytes'], len(html.encode()))
        self.assertTrue(report['requests'][0]['bodyReadSucceeded'])
        self.assertTrue(report['requests'][0]['htmlInspection']['looksLikeRequestedProduct'])

    async def test_content_length_is_not_reported_as_read_body_when_playwright_body_fails(self):
        data = parse_input({'url': URL, 'extractionMethod': 'crawl4ai_css'})
        html = '<html><head><title>Product</title></head><body><h1>Product</h1></body></html>'
        hooks, handlers, frame = {}, {}, object()
        page = SimpleNamespace(main_frame=frame, route=AsyncMock(),
            evaluate=AsyncMock(return_value={'webdriver': True, 'userAgent': 'native'}),
            on=lambda name, fn: handlers.update({name: fn}))
        response = SimpleNamespace(url=URL, status=200, frame=frame,
            headers={'content-type': 'text/html', 'content-length': '1367851'},
            body=AsyncMock(side_effect=AttributeError('simulated Playwright response API error')),
            request=SimpleNamespace(is_navigation_request=lambda: True))
        async def arun(*_, **__):
            await hooks['before_goto'](page)
            handlers['response'](response)
            return SimpleNamespace(success=False, status_code=None, html=html,
                                   error_message='AttributeError: simulated Crawl4AI wrapper failure')
        crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda name, fn: hooks.update({name: fn})),
            arun=AsyncMock(side_effect=arun))
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'):
            document, _, report = await asyncio.wait_for(acquire(data), timeout=1)
        self.assertEqual(document, html)
        body = report['requests'][0]['bodyRead']
        self.assertEqual(body['contentLengthBytes'], 1367851)
        self.assertIsNone(body['bytes'])
        self.assertEqual(body['status'], 'failed')
        self.assertEqual(body['failedAt'], 'response-body-read')
        self.assertEqual(body['errorCode'], 'BODY_READ_ATTRIBUTE_ERROR')
        self.assertEqual(report['status'], 'received')
        self.assertEqual(report['requests'][0]['crawl4aiHtmlFallback'],
                         {'available': True, 'bytes': len(html.encode())})
        self.assertIsNotNone(report['navigationError'])

    async def test_server_commerce_html_does_not_wait_for_dom_resources_and_still_checks_challenges(self):
        data = parse_input({'url': URL})
        for title, expected in [('Test product', 'received'), ('Just a moment', 'blocked')]:
            hooks, handlers = {}, {}
            frame = object()
            html = '<html><head><title>' + title + '</title></head><body><script type="application/json">' + json.dumps({
                'productId': data['productId'], 'selectedOffer': {}, 'pickerGroups': [],
                'specifications': []}) + '</script></body></html>'
            page = SimpleNamespace(main_frame=frame, route=AsyncMock(),
                evaluate=AsyncMock(return_value={'webdriver': True, 'userAgent': 'native'}),
                on=lambda name, fn: handlers.update({name: fn}))
            async def arun(*_, **__):
                await hooks['before_goto'](page)
                handlers['response'](SimpleNamespace(url=URL, status=200, frame=frame,
                    headers={'content-type': 'text/html'}, body=AsyncMock(return_value=html.encode()),
                    request=SimpleNamespace(is_navigation_request=lambda: True)))
                await asyncio.Event().wait()  # DOMContentLoaded is delayed by external scripts.
            async def close():
                handlers['requestfailed'](SimpleNamespace(is_navigation_request=lambda: True,
                    frame=frame, failure='net::ERR_ABORTED'))
            crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(side_effect=close),
                crawler_strategy=SimpleNamespace(set_hook=lambda name, fn: hooks.update({name: fn})),
                arun=AsyncMock(side_effect=arun))
            with patch('src.browser.make_crawler', return_value=crawler), \
                    patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'):
                document, _, report = await asyncio.wait_for(acquire(data), timeout=1)
            self.assertEqual(report['status'], expected)
            if expected == 'received':
                self.assertEqual(document, html)
                self.assertEqual(report['htmlSource'], 'navigation-response')
                self.assertEqual(report['navigationFailures'], [])
            else:
                self.assertEqual(document, '')
                self.assertEqual(report['error']['code'], 'ACCESS_CHALLENGE')

    async def test_ready_dom_bypasses_hanging_crawl4ai_postprocessing(self):
        data = parse_input({'url': URL})
        html = '<html><script type="application/json">' + json.dumps({
            'productId': data['productId'], 'selectedOffer': {}, 'pickerGroups': [],
            'specifications': []}) + '</script></html>'
        hooks, handlers = {}, {}
        frame = object()
        page = SimpleNamespace(main_frame=frame, url=URL, title=AsyncMock(return_value='Test product'),
            content=AsyncMock(return_value=html), route=AsyncMock(),
            evaluate=AsyncMock(side_effect=[{'webdriver': True, 'userAgent': 'native'}, 'Product']),
            on=lambda name, fn: handlers.update({name: fn}))
        cancelled = asyncio.Event()

        async def arun(*_, config):
            self.assertEqual(config.wait_until, 'domcontentloaded')
            self.assertEqual(config.delay_before_return_html, 0)
            self.assertFalse(config.wait_for_images)
            self.assertEqual(config.max_retries, 0)
            await hooks['before_goto'](page)
            handlers['response'](SimpleNamespace(url=URL, status=200, frame=frame,
                headers={'content-type': 'text/html'},
                request=SimpleNamespace(is_navigation_request=lambda: True)))
            try:
                await hooks['after_goto'](page)
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda name, fn: hooks.update({name: fn})),
            arun=AsyncMock(side_effect=arun))
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'):
            document, _, report = await asyncio.wait_for(acquire(data), timeout=1)
        self.assertEqual(document, html)
        self.assertEqual(report['status'], 'received')
        self.assertEqual(report['htmlSource'], 'browser-dom-hook')
        self.assertTrue(cancelled.is_set())
        crawler.close.assert_awaited_once()
        for field in ('firstHttpSeconds', 'documentHttpSeconds', 'htmlReadySeconds', 'closeSeconds'):
            self.assertGreaterEqual(report[field], 0)

    async def test_exhausted_shared_deadline_does_not_start_another_48s(self):
        data = parse_input({'url': URL}) | {'_acquisitionDeadline': time.monotonic() + 0.02}
        async def pending(*_, **__):
            await asyncio.Event().wait()
        crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda *_: None),
            arun=AsyncMock(side_effect=pending))
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'):
            _, _, report = await asyncio.wait_for(acquire(data), timeout=0.5)
        self.assertEqual(report['error']['code'], 'TIMEOUT')
        self.assertTrue(report['browserClosed'])

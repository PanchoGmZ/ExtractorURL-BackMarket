"""Only simulated failures and inert browser objects; no sockets or browser launch."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from crawl4ai import BrowserConfig, CrawlerRunConfig, ProxyConfig
from src.browser import NativeBrowserManager, acquire
from src.diagnostics import navigation_failure
from src.extract import parse_input
from src.proxy import private_proxy_logger


class NormalizationTests(unittest.TestCase):
    def test_known_causes_are_separate_without_http(self):
        cases = {
            'Browser.new_context: proxy.server: expected string, got object': 'PROXY_CONFIGURATION_ERROR',
            'Page.goto: net::ERR_PROXY_AUTH_REQUESTED': 'PROXY_AUTH_ERROR',
            'Proxy authentication required HTTP 407': 'PROXY_AUTH_ERROR',
            'Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED': 'PROXY_TUNNEL_ERROR',
            'Page.goto: net::ERR_PROXY_CONNECTION_FAILED': 'PROXY_CONNECTION_ERROR',
            'Page.goto: net::ERR_CERT_AUTHORITY_INVALID': 'TLS_ERROR',
            'Page.goto: net::ERR_PROXY_CERTIFICATE_INVALID': 'TLS_ERROR',
            'Page.goto: net::ERR_QUIC_PROTOCOL_ERROR': 'CHROMIUM_ERR_QUIC_PROTOCOL_ERROR',
            'Browser.new_context: Browser does not support socks5 proxy authentication': 'PLAYWRIGHT_PROXY_UNSUPPORTED',
            "Browser.new_context: TypeError: got an unexpected keyword argument 'proxy'": 'PLAYWRIGHT_API_ERROR',
            'Page.goto: an unrecognized failure': 'PLAYWRIGHT_ERROR',
            'Unexpected error in _crawl_web at line 10 in crawl (module.py): Error: internal failure': 'CRAWL4AI_ERROR',
            'Page.goto: net::ERR_NAME_NOT_RESOLVED': 'NETWORK_ERROR',
            'TargetClosedError: Target page, context or browser has been closed': 'BROWSER_CLOSED',
            'Page.goto: Timeout 30000ms exceeded': 'TIMEOUT',
        }
        for message, code in cases.items():
            with self.subTest(code=code):
                result = navigation_failure(message)
                self.assertEqual(result['code'], code)
                self.assertIsNone(result['httpStatus'])
                self.assertTrue(result['rawMessageOmitted'])

    def test_missing_and_unrecognized_details_do_not_invent_causes(self):
        for message in (None, '', '  ', {'unexpected': 'private'}):
            self.assertEqual(navigation_failure(message)['code'], 'NAVIGATION_ERROR_NO_DETAILS')
        result = navigation_failure('synthetic-unknown-detail')
        self.assertEqual(result['code'], 'NAVIGATION_ERROR_UNCLASSIFIED')
        self.assertTrue(result['errorMessagePresent'])
        self.assertNotIn('synthetic-unknown-detail', json.dumps(result))
        proxy_http = navigation_failure('upstream error', http_status=593)
        self.assertEqual(proxy_http['code'], 'APIFY_PROXY_HTTP_593')
        self.assertEqual(proxy_http['failureType'], 'http')
        self.assertEqual(proxy_http['httpStatus'], 593)

    def test_credentials_paths_and_code_context_never_reach_output(self):
        message = (
            'Unexpected error in _crawl_web at line 10 in crawl (C:\\Users\\PRIVATE_USER\\module.py):\n'
            'Error: Page.goto: net::ERR_CERT_AUTHORITY_INVALID\n'
            'http://PRIVATE_USERNAME:PRIVATE_PASSWORD@proxy.invalid:8000/path?token=PRIVATE_TOKEN\n'
            'password="PRIVATE_PASSWORD" token=PRIVATE_TOKEN Cookie: PRIVATE_COOKIE\n'
            'Code context:\n SECRET_SOURCE_CODE ERR_PROXY_AUTH_REQUESTED'
        )
        result = navigation_failure(message)
        self.assertEqual(result['code'], 'TLS_ERROR')
        self.assertEqual(result['signals'], ['ERR_CERT_AUTHORITY_INVALID'])
        self.assertTrue(result['crawl4aiWrapped'])
        for private in ('PRIVATE_USER', 'PRIVATE_USERNAME', 'PRIVATE_PASSWORD', 'PRIVATE_TOKEN',
                        'PRIVATE_COOKIE', 'proxy.invalid', 'SECRET_SOURCE_CODE', 'module.py'):
            self.assertNotIn(private, json.dumps(result))
        hidden_signal = navigation_failure('password="ERR_CERT_AUTHORITY_INVALID" https://user:ERR_TUNNEL_CONNECTION_FAILED@proxy.invalid')
        self.assertEqual(hidden_signal['signals'], [])

    def test_public_logger_preserves_only_bounded_safe_signals(self):
        logger = private_proxy_logger()
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            for _ in range(7):
                logger.error_status('https://private.invalid', 'Page.goto: net::ERR_CERT_AUTHORITY_INVALID token=PRIVATE_TOKEN')
            logger.error('Internal failure: {error}', params={'error': 'ERR_TUNNEL_CONNECTION_FAILED password=PRIVATE_PASSWORD'})
        self.assertEqual(output.getvalue(), '')
        self.assertLessEqual(len(logger.safe_errors), 4)
        self.assertEqual(logger.safe_errors[-1]['code'], 'PROXY_TUNNEL_ERROR')
        self.assertNotIn('PRIVATE_', json.dumps(logger.safe_errors))
        self.assertIsNone(logger.log_file)


class AcquisitionDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def data(self):
        return parse_input({'url': 'https://www.backmarket.com/en-us/p/offline-test/11111111-1111-4111-8111-111111111111'})

    async def simulated(self, message, proxy, *, status=None, logger_message=None, raised=None):
        logger = private_proxy_logger()

        async def arun(*_, **__):
            if logger_message:
                logger.error_status('https://private.invalid', logger_message)
            if raised:
                raise raised
            return SimpleNamespace(success=False, status_code=status, error_message=message, html='')

        crawler = SimpleNamespace(logger=logger, start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda *_: None), arun=AsyncMock(side_effect=arun))
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event') as log:
            _, _, report = await acquire(self.data(), proxy_config=proxy)
        crawler.arun.assert_awaited_once()
        crawler.close.assert_awaited_once()
        self.assertTrue(report['browserClosed'])
        for private in ('PRIVATE_PASSWORD', 'PRIVATE_TOKEN', 'PRIVATE_COOKIE'):
            self.assertNotIn(private, json.dumps(report) + str(log.call_args_list))
        return report

    async def test_failed_result_without_http_is_classified_for_all_transports(self):
        for group in (None, 'RESIDENTIAL', 'UNBLOCKER'):
            proxy = ProxyConfig.from_string(f'http://groups-{group}:PRIVATE_PASSWORD@proxy.invalid:8000') if group else None
            for message, code in (
                (None, 'NAVIGATION_ERROR_NO_DETAILS'), ('PRIVATE_TOKEN', 'NAVIGATION_ERROR_UNCLASSIFIED'),
                ('Page.goto: ERR_CERT_AUTHORITY_INVALID token=PRIVATE_TOKEN', 'TLS_ERROR'),
                ('Page.goto: ERR_TUNNEL_CONNECTION_FAILED password=PRIVATE_PASSWORD', 'PROXY_TUNNEL_ERROR'),
                ('Browser.new_context: proxy.server: expected string', 'PROXY_CONFIGURATION_ERROR'),
            ):
                report = await self.simulated(message, proxy)
                self.assertEqual(report['error']['code'], code)
                self.assertEqual(report['failureStage'], 'crawl-call')
                self.assertEqual(report['requests'], [])
                self.assertIsNone(report['navigationError']['httpStatus'])

    async def test_safe_logger_evidence_survives_empty_result_error(self):
        proxy = ProxyConfig.from_string('http://groups-UNBLOCKER:PRIVATE_PASSWORD@proxy.invalid:8000')
        report = await self.simulated(None, proxy,
            logger_message='Page.goto: ERR_PROXY_CERTIFICATE_INVALID password=PRIVATE_PASSWORD')
        self.assertEqual(report['error']['code'], 'TLS_ERROR')
        self.assertEqual(report['navigationError']['source'], 'crawl4ai-logger')
        self.assertFalse(report['navigationError']['resultErrorMessagePresent'])

    async def test_specific_logger_error_replaces_generic_playwright_result(self):
        proxy = ProxyConfig.from_string('http://groups-UNBLOCKER:PRIVATE_PASSWORD@proxy.invalid:8000')
        report = await self.simulated('Page.goto: an unrecognized failure', proxy,
            logger_message='Page.goto: net::ERR_QUIC_PROTOCOL_ERROR password=PRIVATE_PASSWORD')
        self.assertEqual(report['navigationError']['code'], 'CHROMIUM_ERR_QUIC_PROTOCOL_ERROR')
        self.assertEqual(report['navigationError']['source'], 'crawl4ai-logger')
        self.assertEqual(len(report['navigationError']['loggerErrors']), 1)
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(report))

    async def test_main_document_requestfailed_code_is_normalized_and_kept(self):
        hooks = {}
        handlers = {}
        page = SimpleNamespace(main_frame=object(),
            evaluate=AsyncMock(return_value={'webdriver': True, 'userAgent': 'test-browser'}),
            route=AsyncMock(), on=lambda name, callback: handlers.update({name: callback}))

        async def arun(*_, **__):
            await hooks['before_goto'](page)
            request = SimpleNamespace(is_navigation_request=lambda: True, frame=page.main_frame,
                failure='net::ERR_QUIC_PROTOCOL_ERROR token=PRIVATE_TOKEN https://private:password@proxy.invalid')
            handlers['requestfailed'](request)
            return SimpleNamespace(success=False, status_code=None,
                error_message='Page.goto: an unrecognized failure', html='')

        crawler = SimpleNamespace(logger=private_proxy_logger(), start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda name, callback: hooks.update({name: callback})),
            arun=AsyncMock(side_effect=arun))
        proxy = ProxyConfig.from_string('http://groups-UNBLOCKER:PRIVATE_PASSWORD@proxy.invalid:8000')
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'):
            _, _, report = await acquire(self.data(), proxy_config=proxy, proxy_mode='unblocker')
        self.assertEqual(report['navigationFailures'], [{
            'failureType': 'navigation', 'source': 'playwright-requestfailed',
            'chromiumErrorCode': 'ERR_QUIC_PROTOCOL_ERROR'}])
        self.assertEqual(report['navigationError']['code'], 'CHROMIUM_ERR_QUIC_PROTOCOL_ERROR')
        self.assertEqual(report['navigationError']['failureType'], 'navigation')
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(report))

    async def test_apify_proxy_http_statuses_remain_http_diagnostics(self):
        for status in range(590, 600):
            report = await self.simulated('proxy upstream response', None, status=status)
            self.assertEqual(report['navigationError']['code'], f'APIFY_PROXY_HTTP_{status}')
            self.assertEqual(report['navigationError']['failureType'], 'http')
            self.assertEqual(report['navigationError']['httpStatus'], status)

    async def test_actual_http_403_remains_blocked(self):
        report = await self.simulated('PRIVATE_TOKEN', None, status=403)
        self.assertEqual(report['status'], 'blocked')
        self.assertEqual(report['error']['code'], 'HTTP_403')

    async def test_main_document_http_502_is_acquisition_error_not_secondary_resource(self):
        hooks, handlers, frame = {}, {}, object()
        page = SimpleNamespace(main_frame=frame,
            evaluate=AsyncMock(return_value={'webdriver': True, 'userAgent': 'test-browser'}),
            route=AsyncMock(), on=lambda name, callback: handlers.update({name: callback}))

        async def arun(*_, **__):
            await hooks['before_goto'](page)
            handlers['response'](SimpleNamespace(url=self.data()['url'], status=502, frame=frame,
                headers={'content-type': 'text/html'},
                request=SimpleNamespace(is_navigation_request=lambda: True)))
            return SimpleNamespace(success=False, status_code=502, error_message='HTTP 502', html='')

        crawler = SimpleNamespace(logger=private_proxy_logger(), start=AsyncMock(), close=AsyncMock(),
            crawler_strategy=SimpleNamespace(set_hook=lambda name, callback: hooks.update({name: callback})),
            arun=AsyncMock(side_effect=arun))
        with patch('src.browser.make_crawler', return_value=crawler), \
                patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event') as log:
            _, _, report = await acquire(self.data(), proxy_config=ProxyConfig.from_string(
                'http://groups-UNBLOCKER:PRIVATE_PASSWORD@proxy.invalid:8000'), proxy_mode='unblocker')
        self.assertEqual(report['status'], 'error')
        self.assertEqual(report['error']['code'], 'HTTP_502')
        self.assertEqual(report['documentError'], {'httpStatus': 502, 'code': 'HTTP_502', 'stage': 'navigation'})
        self.assertEqual(report['resourceErrors'], [])
        self.assertNotIn('RECURSO_SECUNDARIO', [call.args[0] for call in log.call_args_list])

    async def test_raised_exception_uses_same_safe_classification(self):
        report = await self.simulated(None, None,
            raised=RuntimeError('Page.goto: ERR_CERT_AUTHORITY_INVALID password=PRIVATE_PASSWORD'))
        self.assertEqual(report['error']['code'], 'TLS_ERROR')
        self.assertEqual(report['navigationError']['source'], 'exception')

    async def test_installed_crawl4ai_forwards_proxy_to_inert_playwright_context(self):
        # Exercise the real context-builder against an AsyncMock, never Chromium.
        for group in (None, 'RESIDENTIAL', 'UNBLOCKER'):
            proxy = ProxyConfig.from_string(f'http://groups-{group}:PRIVATE_PASSWORD@proxy.invalid:8000') if group else None
            ignore_https_errors = group == 'UNBLOCKER'
            manager = NativeBrowserManager(BrowserConfig(headless=True, ignore_https_errors=ignore_https_errors), logger=private_proxy_logger())
            manager.browser = SimpleNamespace(new_context=AsyncMock(return_value=SimpleNamespace(route=AsyncMock())))
            await manager.create_browser_context(CrawlerRunConfig(proxy_config=proxy, max_retries=0))
            kwargs = manager.browser.new_context.await_args.kwargs
            self.assertEqual(kwargs['ignore_https_errors'], ignore_https_errors)
            if proxy:
                self.assertEqual(kwargs['proxy'], {'server': proxy.server, 'username': proxy.username, 'password': proxy.password})
            else:
                self.assertIsNone(kwargs['proxy'])


if __name__ == '__main__':
    unittest.main()

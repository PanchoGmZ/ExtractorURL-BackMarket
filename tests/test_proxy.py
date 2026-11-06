"""Small proxy configuration checks with SDK doubles; never open a socket/browser."""
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from crawl4ai import CrawlerRunConfig, ProxyConfig
from src.proxy import (ProxyConfigurationError, configure_proxy, private_proxy_logger,
                       proxy_failure_code, proxy_observation, split_proxy_input)


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    def settings(self):
        return split_proxy_input({'url': 'not-navigated', 'proxyConfiguration': {
            'useApifyProxy': True, 'apifyProxyGroups': ['TEST_GROUP'], 'apifyProxyCountry': 'US'
        }, 'proxyAccessAuthorized': True})[1]

    async def test_default_and_explicit_disabled_never_call_sdk(self):
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock())
        for raw in ({'url': 'not-navigated'}, {'url': 'not-navigated', 'proxyConfiguration': {'useApifyProxy': False}}):
            product, settings = split_proxy_input(raw)
            self.assertEqual(product, {'url': 'not-navigated'})
            self.assertEqual(settings.mode, 'direct')
            self.assertIsNone(await configure_proxy(actor, settings, session_id='test', timeout_seconds=1))
        actor.create_proxy_configuration.assert_not_awaited()

    async def test_explicit_authorization_and_group_required(self):
        for raw in (
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['TEST_GROUP']}},
            {'proxyConfiguration': {'useApifyProxy': True}, 'proxyAccessAuthorized': True},
            {'proxyConfiguration': {'useApifyProxy': 'true'}},
            {'proxyAccessAuthorized': 'true'},
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER']}},
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER', 'RESIDENTIAL']}, 'proxyAccessAuthorized': True},
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER', 'TEST_GROUP']}, 'proxyAccessAuthorized': True},
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['unblocker']}, 'proxyAccessAuthorized': True},
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['GOOGLE_SERP']}, 'proxyAccessAuthorized': True},
            {'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL', 'TEST_GROUP']}, 'proxyAccessAuthorized': True},
        ):
            with self.assertRaises(ValueError):
                split_proxy_input(raw)

    async def test_custom_urls_and_invalid_settings_rejected(self):
        for config in (
            {'proxyUrls': ['http://invalid.test:8080']},
            {'apifyProxyGroups': ['TEST_GROUP', 'TEST_GROUP']},
            {'apifyProxyGroups': ['unsafe\nvalue']},
            {'apifyProxyGroups': [[]]},
            {'apifyProxyCountry': 'usa'},
            {'password': 'synthetic-password'},
            {'proxy_rotation_strategy': 'rotate'},
        ):
            with self.assertRaises(ValueError):
                split_proxy_input({'proxyConfiguration': config})

    async def test_one_sdk_configuration_and_one_session_url(self):
        settings = self.settings()
        sdk_config = SimpleNamespace(new_url=AsyncMock(
            return_value='http://test-user:synthetic-password@proxy.invalid:8000'))
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock(return_value=sdk_config))
        proxy = await configure_proxy(actor, settings, session_id='one-run', timeout_seconds=1)
        actor.create_proxy_configuration.assert_awaited_once_with(actor_proxy_input={
            'useApifyProxy': True, 'apifyProxyGroups': ['TEST_GROUP'], 'apifyProxyCountry': 'US'})
        sdk_config.new_url.assert_awaited_once_with(session_id='one-run')
        config = CrawlerRunConfig(proxy_config=proxy, max_retries=0)
        self.assertEqual(config.proxy_config.server, 'http://proxy.invalid:8000')
        self.assertEqual(config.proxy_config.username, 'test-user')
        self.assertEqual(config.proxy_config.password, 'synthetic-password')
        self.assertIsNone(config.proxy_rotation_strategy)
        self.assertNotIn('synthetic-password', json.dumps(settings.diagnostic('configured')))

    async def test_sdk_failure_is_safe_and_does_not_retry(self):
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock(side_effect=RuntimeError('synthetic-password')))
        with self.assertRaises(ProxyConfigurationError) as caught:
            await configure_proxy(actor, self.settings(), session_id='one-run', timeout_seconds=1)
        self.assertNotIn('synthetic-password', str(caught.exception))
        actor.create_proxy_configuration.assert_awaited_once()

    async def test_timeout_cancels_setup_without_retry(self):
        pending = asyncio.Event()
        async def wait(**_):
            await pending.wait()
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock())
        actor.create_proxy_configuration.side_effect = wait
        with self.assertRaises(ProxyConfigurationError):
            await configure_proxy(actor, self.settings(), session_id='one-run', timeout_seconds=0.01)
        actor.create_proxy_configuration.assert_awaited_once()

    async def test_missing_sdk_result_does_not_fall_back(self):
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock(return_value=None))
        with self.assertRaises(ProxyConfigurationError):
            await configure_proxy(actor, self.settings(), session_id='one-run', timeout_seconds=1)
        actor.create_proxy_configuration.assert_awaited_once()

    async def test_third_party_logger_has_no_console_or_file_sink(self):
        from crawl4ai.async_logger import AsyncLoggerBase
        logger = private_proxy_logger()
        self.assertIsInstance(logger, AsyncLoggerBase)
        self.assertIsNone(logger.log_file)
        self.assertFalse(logger.verbose)
        output = io.StringIO()
        private = 'password=synthetic-password token=synthetic-token cookie=synthetic-cookie'
        with redirect_stdout(output), redirect_stderr(output):
            for method in ('debug', 'info', 'success', 'warning', 'error'):
                getattr(logger, method)(private, params={'private': private}, force_verbose=True)
            logger.url_status(private, False, 0)
            logger.error_status(private, private)
        self.assertEqual(output.getvalue(), '')

    async def test_residential_is_explicit_and_sdk_compatible(self):
        from apify import Actor, ProxyConfiguration
        from inspect import signature
        self.assertIn('actor_proxy_input', signature(Actor.create_proxy_configuration).parameters)
        _, settings = split_proxy_input({'proxyConfiguration': {
            'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL']}, 'proxyAccessAuthorized': True})
        self.assertEqual(settings.groups, ('RESIDENTIAL',))
        self.assertEqual(settings.diagnostic('configured')['connected'], None)
        # Constructor + URL generation are local operations. Never call initialize(),
        # which performs the real account/proxy access check.
        sdk_config = ProxyConfiguration(password='synthetic-password', groups=['RESIDENTIAL'], country_code='US')
        config = ProxyConfig.from_string(await sdk_config.new_url(session_id='a' * 32))
        self.assertIn('groups-RESIDENTIAL', config.username)
        self.assertIn('session-' + 'a' * 32, config.username)
        self.assertIn('country-US', config.username)
        self.assertIsInstance(CrawlerRunConfig(proxy_config=config).proxy_config, ProxyConfig)

    async def test_unblocker_has_no_session_and_uses_official_sdk_configuration(self):
        _, settings = split_proxy_input({'proxyConfiguration': {
            'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER']}, 'proxyAccessAuthorized': True})
        sdk_config = SimpleNamespace(new_url=AsyncMock(
            return_value='http://groups-UNBLOCKER:synthetic-password@proxy.invalid:8000'))
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock(return_value=sdk_config))
        config = await configure_proxy(actor, settings, session_id='internal_run', timeout_seconds=1)
        actor.create_proxy_configuration.assert_awaited_once_with(actor_proxy_input={
            'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER'], 'apifyProxyCountry': None})
        sdk_config.new_url.assert_awaited_once_with()
        self.assertNotIn('session-', config.username)
        self.assertEqual(settings.diagnostic('configured')['sessionMode'], 'not-supported')
        self.assertEqual(settings.mode, 'unblocker')
        self.assertEqual(config.password, 'synthetic-password')
        self.assertIsNone(CrawlerRunConfig(proxy_config=config, max_retries=0).proxy_rotation_strategy)
        self.assertNotIn('synthetic-password', json.dumps(settings.diagnostic('configured')))

    async def test_unblocker_url_generation_and_linux_browser_configuration_offline(self):
        from apify import ProxyConfiguration
        from crawl4ai import AsyncWebCrawler
        from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
        from src.browser import make_crawler
        # SDK URL generation only: initialize() would contact a real service.
        sdk_config = ProxyConfiguration(password='synthetic-password', groups=['UNBLOCKER'])
        config = ProxyConfig.from_string(await sdk_config.new_url())
        self.assertEqual(config.username, 'groups-UNBLOCKER')
        run_config = CrawlerRunConfig(proxy_config=config, max_retries=0)
        self.assertIs(run_config.proxy_config, config)
        # Construct the real objects, never start Chromium or call arun().
        with patch('src.browser.os', SimpleNamespace(name='posix')), \
                patch('src.browser.AsyncWebCrawler.start', new_callable=AsyncMock) as start:
            crawler = make_crawler(proxy_enabled=True)
        start.assert_not_awaited()
        self.assertIsInstance(crawler, AsyncWebCrawler)
        self.assertIsInstance(crawler.crawler_strategy, AsyncPlaywrightCrawlerStrategy)
        self.assertTrue(crawler.browser_config.headless)
        self.assertFalse(crawler.browser_config.use_persistent_context)
        self.assertFalse(crawler.browser_config.ignore_https_errors)
        self.assertFalse(crawler.browser_config.enable_stealth)

    async def test_unblocker_setup_failure_keeps_credentials_private(self):
        _, settings = split_proxy_input({'proxyConfiguration': {
            'useApifyProxy': True, 'apifyProxyGroups': ['UNBLOCKER']}, 'proxyAccessAuthorized': True})
        sdk_config = SimpleNamespace(new_url=AsyncMock(side_effect=RuntimeError('synthetic-password synthetic-token')))
        actor = SimpleNamespace(create_proxy_configuration=AsyncMock(return_value=sdk_config))
        with self.assertRaises(ProxyConfigurationError) as caught:
            await configure_proxy(actor, settings, session_id='internal_run', timeout_seconds=1)
        sdk_config.new_url.assert_awaited_once_with()
        for private in ('synthetic-password', 'synthetic-token'):
            self.assertNotIn(private, str(caught.exception))

    async def test_configuration_connection_and_request_are_separate(self):
        self.assertEqual(proxy_observation({})['connected'], None)
        for status, code, connected, succeeded in (
            (403, 'HTTP_403', True, False), (200, None, True, True),
            (200, 'ACCESS_CHALLENGE', True, False), (307, 'ACCESS_CHALLENGE', True, False),
            (407, 'PROXY_AUTH_ERROR', False, False),
        ):
            result = proxy_observation({'requests': [{'category': 'document', 'httpStatus': status}],
                                        'error': {'code': code}})
            self.assertEqual(result, {'connected': connected, 'documentHttpStatus': status,
                                       'documentRequestSucceeded': succeeded})
        self.assertEqual(proxy_failure_code('net::ERR_PROXY_CONNECTION_FAILED password=synthetic'), 'PROXY_CONNECTION_ERROR')
        self.assertEqual(proxy_failure_code('net::ERR_PROXY_AUTH_REQUESTED token=synthetic'), 'PROXY_AUTH_ERROR')
        self.assertIsNone(proxy_failure_code('HTTP 403'))

    async def test_acquisition_handles_private_wrapped_errors_once(self):
        from src.browser import acquire
        from src.extract import parse_input
        data = parse_input({'url': 'https://www.backmarket.com/en-us/p/offline-test/11111111-1111-4111-8111-111111111111'})
        proxy = ProxyConfig.from_string('http://test-user:synthetic-password@proxy.invalid:8000')
        for status, message, expected in (
            (None, 'net::ERR_PROXY_AUTH_REQUESTED password=synthetic-password', 'PROXY_AUTH_ERROR'),
            (None, 'net::ERR_TUNNEL_CONNECTION_FAILED token=synthetic-token', 'PROXY_TUNNEL_ERROR'),
            (407, 'cookie=synthetic-cookie', 'PROXY_AUTH_ERROR'),
            (403, '', 'HTTP_403'),
        ):
            crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
                crawler_strategy=SimpleNamespace(set_hook=lambda *_: None),
                arun=AsyncMock(return_value=SimpleNamespace(success=False, status_code=status,
                    error_message=message, html='')))
            with patch('src.browser.make_crawler', return_value=crawler) as factory, \
                    patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event') as log:
                _, _, report = await acquire(data, proxy_config=proxy, proxy_mode='datacenter')
            factory.assert_called_once_with(proxy_enabled=True, ignore_https_errors=False)
            crawler.arun.assert_awaited_once()
            self.assertIs(crawler.arun.await_args.kwargs['config'].proxy_config, proxy)
            self.assertEqual(report['error']['code'], expected)
            self.assertTrue(report['browserClosed'])
            for secret in ('synthetic-password', 'synthetic-token', 'synthetic-cookie'):
                self.assertNotIn(secret, json.dumps(report) + str(log.call_args_list))

    def test_browser_tls_policy_is_false_by_default_and_true_only_when_requested(self):
        from src.browser import make_crawler
        with patch('src.browser.event'):
            direct = make_crawler()
            residential = make_crawler(proxy_enabled=True, ignore_https_errors=False)
            unblocker = make_crawler(proxy_enabled=True, ignore_https_errors=True)
        self.assertFalse(direct.browser_config.ignore_https_errors)
        self.assertFalse(residential.browser_config.ignore_https_errors)
        self.assertTrue(unblocker.browser_config.ignore_https_errors)


if __name__ == '__main__':
    unittest.main()

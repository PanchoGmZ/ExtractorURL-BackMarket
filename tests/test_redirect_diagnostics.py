"""URL/redirect and stale-output checks, entirely offline."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.environment import ROOT
from src.extract import parse_input
from src.main import archive_previous_output
from src.urls import is_challenge_url, redirect_details, safe_url, selection_parameters, selection_state

URL = 'https://www.backmarket.com/en-us/p/test/11111111-1111-4111-8111-111111111111'


class RedirectTests(unittest.TestCase):
    def test_selection_parameters_reach_navigation_url_unchanged(self):
        url = URL + '?l=12&offerType=best_price'
        self.assertEqual(parse_input({'url': url})['url'], url)
        self.assertEqual(selection_parameters(url), {'l': '12', 'offerType': 'best_price'})
        for query in ('l=', 'l=12&l=13', 'offerType=x&offerType=y', 'token=private', 'l=12&grade=1'):
            with self.assertRaises(ValueError):
                parse_input({'url': URL + '?' + query})

    def test_normal_redirect_and_challenge_redirect_are_distinct(self):
        normal = redirect_details(URL, URL + '?l=12', 301, 'document', 'offline-ray')
        challenge = redirect_details(URL + '?l=12', '/testchallengepage?token=private', 307, 'document', None)
        self.assertEqual(normal['type'], 'normal')
        self.assertEqual(normal['httpStatus'], 301)
        self.assertEqual(challenge['type'], 'verification')
        self.assertEqual(challenge['sourceUrl'], URL + '?l=12')
        self.assertTrue(is_challenge_url(challenge['targetUrl']))
        self.assertNotIn('private', json.dumps(challenge))
        self.assertTrue(is_challenge_url('https://www.backmarket.com/%74estchallengepage'))

    def test_private_url_components_never_reach_diagnostics(self):
        value = URL.replace('https://', 'https://user:secret@') + '?l=12&token=private#credential'
        safe = safe_url(value)
        for secret in ('secret', 'user', 'private', 'credential'):
            self.assertNotIn(secret, safe)
        self.assertIn('l=12', safe)
        protected = safe_url('https://www.backmarket.com/cdn-cgi/challenge-platform/opaque-secret?token=private')
        self.assertNotIn('opaque-secret', protected)
        self.assertNotIn('private', protected)

    def test_selection_removed_changed_or_ambiguous_is_not_verified(self):
        selected = URL + '?l=12&offerType=best_price'
        self.assertEqual(selection_state(selected, selected)['status'], 'preserved')
        self.assertEqual(selection_state(selected, URL)['status'], 'changed_or_removed')
        self.assertEqual(selection_state(URL + '?l=12', URL + '?l=13')['status'], 'changed_or_removed')
        self.assertEqual(selection_state(URL + '?l=12', URL + '?l=13&l=12')['status'], 'changed_or_removed')
        self.assertEqual(selection_state(URL, URL)['status'], 'not_requested')

    def test_previous_output_is_preserved_but_no_longer_current(self):
        root = Path(tempfile.mkdtemp(prefix='redirect-output-offline-', dir=ROOT / '.tmp'))
        self.assertTrue(root.resolve().is_relative_to(ROOT))
        (root / 'output').mkdir()
        source = root / 'output/product.json'
        source.write_text('{"title":"previous run"}', encoding='utf-8')
        archived = archive_previous_output(root, 'offline-test')
        self.assertFalse(source.exists())
        self.assertEqual(json.loads((root / archived).read_text(encoding='utf-8'))['title'], 'previous run')
        self.assertIsNone(archive_previous_output(root, 'next-run'))
        with self.assertRaises(ValueError):
            archive_previous_output(root, '../outside')


class AcquisitionRedirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_301_then_307_challenge_keeps_previous_ray_and_stops(self):
        # Exercise hooks with inert Python objects, never a browser or HTTP server.
        from src.browser import acquire

        class Page:
            main_frame = object()
            url = URL + '?l=12'

            async def evaluate(self, _):
                return {'webdriver': True, 'userAgent': 'offline-test'}

            async def route(self, *_):
                pass

            def on(self, _, callback):
                self.callback = callback

        class Crawler:
            def __init__(self):
                self.hooks = {}
                self.crawler_strategy = SimpleNamespace(set_hook=lambda name, callback: self.hooks.__setitem__(name, callback))

            async def start(self):
                pass

            async def close(self):
                pass

            async def arun(self, url, **_):
                self.requested_url = url
                page = Page()
                await self.hooks['before_goto'](page)
                request = SimpleNamespace(is_navigation_request=lambda: True)
                page.callback(SimpleNamespace(url=URL + '?l=12', status=301, frame=page.main_frame,
                    request=request, headers={'location': URL + '/?l=12', 'cf-ray': 'offline-ray'}))
                page.callback(SimpleNamespace(url=URL + '/?l=12', status=307, frame=page.main_frame,
                    request=request, headers={'location': '/testchallengepage?token=private'}))
                return SimpleNamespace(success=False, status_code=307, html='')

        crawler = Crawler()
        with patch('src.browser.make_crawler', return_value=crawler), patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'):
            _, _, report = await acquire(parse_input({'url': URL + '?l=12'}))
        self.assertEqual(crawler.requested_url, URL + '?l=12')
        self.assertEqual(report['error']['code'], 'ACCESS_CHALLENGE')
        self.assertEqual(report['failureStage'], 'redirect')
        self.assertEqual([item['httpStatus'] for item in report['redirects']], [301, 307])
        self.assertEqual(report['accessChallenge']['cfRay'], 'offline-ray')
        self.assertEqual(report['accessChallenge']['cfRaySource'], 'previous-response')
        self.assertTrue(report['browserClosed'])
        self.assertNotIn('private', json.dumps(report))


if __name__ == '__main__':
    unittest.main()

"""Synthetic semantic HTML; not a captured/verified Back Market selector profile."""
import json
import os
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from jsonschema import Draft7Validator
from src.dom_crawl4ai import extract_html
from src.extract import parse_input
from src.environment import ROOT

ID = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
URL = 'https://www.backmarket.com/en-us/p/semantic-test/' + ID
HTML = f'''<!doctype html><html><head><link rel="canonical" href="{URL}"></head><body>
<main itemtype="https://schema.org/Product" data-product-id="{ID}">
<h1>iPhone 14 Pro 128 GB</h1><h1 hidden>Hidden headline</h1><span itemprop="model">iPhone 14 Pro</span>
<div itemprop="offers"><meta itemprop="priceCurrency" content="USD">
<del><span itemprop="price">USD 900.00</span></del>
<span itemprop="price" content="429.00">USD 429.00</span>
<span itemprop="price" hidden>USD 1.00</span></div>
<div data-testid="product-gallery"><img src="https://cdn.example.invalid/product-a.jpg">
<img src="https://cdn.example.invalid/product-a.jpg"><img data-src="https://cdn.example.invalid/product-b.jpg"></div>
<table><tr><th>Brand</th><td>Apple</td></tr><tr><th>Display</th><td>OLED</td></tr>
<tr><th>Weight</th><td>206 g</td></tr><tr><th>Dimensions</th><td>147.5 × 71.5 mm</td></tr></table>
<dl><dt>Storage</dt><dd>128 GB</dd></dl>
<fieldset><legend>Color</legend>
<button aria-checked="true" data-option-label="Yellow" data-product-id="{ID}" data-price="429.00" data-currency="USD">Yellow</button>
<button aria-checked="false" disabled data-option-label="Black" data-product-id="{OTHER}" data-price="440.00" data-currency="USD">Black</button></fieldset>
<fieldset><legend>Storage</legend>
<button aria-pressed="true" data-product-id="{ID}" data-price="429.00" data-currency="USD">128 GB</button>
<button aria-pressed="false" data-product-id="{OTHER}" data-price="480.00" data-currency="USD">256 GB</button></fieldset>
<fieldset><legend>Condition</legend><button aria-checked="true" data-product-id="{ID}" data-price="429.00" data-currency="USD">Excellent</button></fieldset>
<fieldset><legend>Battery</legend><button aria-checked="true" data-product-id="{ID}" data-price="429.00" data-currency="USD">Standard</button></fieldset>
<fieldset><legend>Payment method</legend><button>Credit card</button></fieldset>
<section data-testid="reviews"><table><tr><th>Customer</th><td>PRIVATE_CUSTOMER</td></tr></table></section>
<aside data-testid="recommendations"><article itemtype="https://schema.org/Product">
<h2>Another phone</h2><div itemprop="offers"><span itemprop="price">USD 1.00</span></div>
<img itemprop="image" src="https://cdn.example.invalid/recommendation.jpg"></article></aside>
<article itemtype="https://schema.org/Product"><meta itemprop="productID" content="{OTHER}">
<h2>Unscoped recommendation card</h2><img itemprop="image" src="https://cdn.example.invalid/card.jpg"></article>
<script type="application/json">{{"selectedOffer":{{"price":{{"amount":"999.00"}}}}}}</script>
</main></body></html>'''


class CssMethodTests(unittest.TestCase):
    def setUp(self):
        self.data = parse_input({'url': URL, 'extractionMethod': 'crawl4ai_css'})

    def test_css_semantic_product_selected_price_scope_and_contract(self):
        output, meta, missing, _ = extract_html(HTML, self.data)
        self.assertEqual(output['title'], 'iPhone 14 Pro 128 GB')
        self.assertEqual(output['price'], '429.00')
        self.assertEqual(meta['currency'], 'USD')
        self.assertEqual(len(output['images']), 2)
        self.assertFalse(any('recommendation' in url for url in output['images']))
        self.assertNotIn('PRIVATE_CUSTOMER', json.dumps(output))
        self.assertNotIn('Credit card', json.dumps(output))
        self.assertIn({'attrName': 'Storage', 'attrValue': '128 GB'}, output['characteristics'])
        self.assertIn({'attrName': 'Display', 'attrValue': 'OLED'}, output['characteristics'])
        self.assertEqual(output['weight'], {'value': 0.206, 'unit': 'kg'})
        self.assertIsNone(output['dimensions'])
        self.assertEqual(meta['domDiagnostics']['dimensionsDeclared'], '147.5 × 71.5 mm')
        self.assertEqual(len(output['variations']), 4)
        self.assertEqual(sum(len(group['variants']) for group in output['variations']), 6)
        self.assertFalse(meta['variantGroups'][0]['options'][1]['available'])

        self.assertEqual(meta['variantGroups'][0]['options'][1]['productId'], OTHER)
        self.assertEqual(missing, [])
        self.assertEqual(meta['sourceDiagnostics']['nuxtScripts'], 0)
        schema = json.loads((ROOT / '.actor/dataset_schema.json').read_text(encoding='utf-8'))['fields']
        Draft7Validator(schema).validate(output)
        meta['status'] = 'success'
        schema = json.loads((ROOT / '.actor/key_value_store_schema.json').read_text(encoding='utf-8'))['collections']['metadata']['jsonSchema']
        Draft7Validator(schema).validate(meta)

    def test_price_ambiguity_reference_only_and_currency_absence_do_not_fabricate_price(self):
        for changed in (
            HTML.replace('</del>', '</del><span data-current-price="430.00">USD 430.00</span>', 1),
            HTML.replace('content="429.00"', 'content="900.00"', 1),
            f'<main itemtype="https://schema.org/Product" data-product-id="{ID}"><h1>Phone</h1><span data-current-price="429.00">$429.00</span></main>',
            HTML.replace('<meta itemprop="priceCurrency" content="USD">', '').replace('USD 429.00</span>', '$429.00</span>'),
        ):
            output, meta, _, _ = extract_html(changed, self.data)
            self.assertIsNone(output['price'])
            self.assertEqual(meta['priceVerification']['status'], 'unverified')
        nested = HTML.replace('USD 429.00</span>', '<span>USD</span><span>429.00</span></span>')
        self.assertEqual(extract_html(nested, self.data)[0]['price'], '429.00')

    def test_mismatch_and_json_only_captures_cannot_become_dom_products(self):
        for changed in (HTML.replace('rel="canonical" href="' + URL, 'rel="canonical" href="' + URL.replace(ID, OTHER)),
                        '<script type="application/json">' + json.dumps({'productId': ID, 'title': 'Not DOM', 'price': '1'}) + '</script>'):
            output, meta, _, _ = extract_html(changed, self.data)
            self.assertIsNone(output['title'])
            self.assertFalse(meta['domDiagnostics']['identityVerified'])
            self.assertTrue(meta['domDiagnostics']['errorCode'].startswith('CSS_'))

    def test_second_synthetic_product_and_missing_fields(self):
        changed = HTML.replace(ID, OTHER).replace('iPhone 14 Pro', 'iPhone 13').replace('429.00', '320.00')
        output, _, _, _ = extract_html(changed, parse_input({'url': URL.replace(ID, OTHER)}))
        self.assertEqual((output['productId'], output['price'], output['title']), (OTHER, '320.00', 'iPhone 13 128 GB'))
        output, meta, missing, _ = extract_html(f'<main itemtype="https://schema.org/Product" data-product-id="{ID}"><h1>Product</h1></main>', self.data)
        self.assertIsNone(output['price'])
        self.assertIsNone(output['weight'])
        self.assertIsNone(output['variations'])
        self.assertIn('images', missing)
        self.assertTrue(meta['domDiagnostics']['identityVerified'])
        empty_model = f'<main itemtype="https://schema.org/Product" data-product-id="{ID}"><h1>Product</h1><span itemprop="model"> </span></main>'
        self.assertIsNone(extract_html(empty_model, self.data)[0]['characteristics'])

    def test_declared_apple_weight_only_no_manufacturer_fallback_or_oz_reinterpretation(self):
        for mass in ('201 oz', ''):
            changed = HTML.replace('iPhone 14 Pro', 'iPhone 15 Plus').replace('206 g', mass)
            output, meta, _, _ = extract_html(changed, self.data)
            self.assertIsNone(output['weight'])
            self.assertNotEqual(meta['weightNormalization']['source'], 'manufacturer')

    def test_native_select_and_label_association_are_not_cartesian_variants(self):
        markup = f'''<main itemtype="https://schema.org/Product" data-product-id="{ID}">
            <h1>Product</h1><span data-current-price="50.00">USD 50.00</span>
            <label for="storage">Storage</label><select id="storage">
            <option value="{ID}" selected data-price="50.00" data-currency="USD">128 GB</option>
            <option value="{OTHER}" disabled data-price="60.00" data-currency="USD">256 GB</option></select></main>'''
        output, meta, _, _ = extract_html(markup, self.data)
        self.assertEqual(output['variations'][0]['name'], 'Storage')
        self.assertEqual(len(output['variations'][0]['variants']), 2)
        self.assertEqual(meta['domDiagnostics']['selection']['Storage'], ['128 GB'])
        self.assertFalse(meta['variantGroups'][0]['options'][1]['available'])


class AcquisitionMethodTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_json_denial_is_optional_for_dom_css_and_required_for_nuxt(self):
        from src.browser import acquire
        for method in ('playwright_dom', 'crawl4ai_css', 'nuxt'):
            data = parse_input({'url': URL, 'extractionMethod': method})
            hooks, handlers = {}, {}
            frame = object()
            page = SimpleNamespace(main_frame=frame, url=URL, title=AsyncMock(return_value='Product'),
                content=AsyncMock(return_value=HTML), route=AsyncMock(),
                evaluate=AsyncMock(side_effect=[{'webdriver': True, 'userAgent': 'native'}, 'Product']),
                on=lambda name, callback: handlers.update({name: callback}))
            async def arun(*_, **__):
                await hooks['before_goto'](page)
                handlers['response'](SimpleNamespace(url=URL, status=200, frame=frame,
                    headers={'content-type': 'text/html'}, body=AsyncMock(return_value=HTML.encode()),
                    request=SimpleNamespace(is_navigation_request=lambda: True)))
                handlers['response'](SimpleNamespace(url='https://www.backmarket.com/product-page/products/' + ID + '/pickers',
                    status=403, frame=frame, headers={'content-type': 'application/json'},
                    request=SimpleNamespace(is_navigation_request=lambda: False)))
                await hooks['after_goto'](page)
                await asyncio.Event().wait()
            crawler = SimpleNamespace(start=AsyncMock(), close=AsyncMock(),
                crawler_strategy=SimpleNamespace(set_hook=lambda name, callback: hooks.update({name: callback})),
                arun=AsyncMock(side_effect=arun))
            with patch('src.browser.make_crawler', return_value=crawler), \
                    patch('src.browser.memory_mb', return_value=(10, 20)), patch('src.browser.event'), \
                    patch('src.dom_playwright.collect', new_callable=AsyncMock, return_value={'identity': {}}) as collect:
                _, responses, report = await asyncio.wait_for(acquire(data), timeout=1)
            if method == 'nuxt':
                self.assertEqual(report['status'], 'blocked')
                self.assertEqual(report['error']['code'], 'HTTP_403')
            else:
                self.assertEqual(report['status'], 'received')
                self.assertEqual(report['resourceErrors'][0]['category'], 'secondary')
                self.assertNotIn('pickers', responses)
            self.assertEqual(collect.await_count, 1 if method == 'playwright_dom' else 0)
            self.assertTrue(report['browserClosed'])


@unittest.skipUnless(os.environ.get('BACKMARKET_LOCAL_DOM_TEST') == '1', 'Opt-in local rendered-fixture check; all network is blocked')
class PlaywrightFixtureTests(unittest.IsolatedAsyncioTestCase):
    async def test_playwright_reads_rendered_fields_and_matches_css_contract(self):
        from playwright.async_api import async_playwright
        from src.dom_playwright import collect, extract_raw
        data = parse_input({'url': URL, 'extractionMethod': 'playwright_dom'})
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, channel='chrome' if os.name == 'nt' else 'chromium')
            try:
                context = await browser.new_context()
                await context.route('**/*', lambda route: route.abort())
                page = await context.new_page()
                await page.set_content(HTML, wait_until='domcontentloaded')
                output, meta, missing, _ = extract_raw(await collect(page), data)
                css, _, _, _ = extract_html(HTML, data)
                self.assertEqual(output, css)
                self.assertEqual(missing, [])
                self.assertEqual(meta['extractionMethod'], 'playwright_dom')
                # A property mutation must be read from the current rendered DOM.
                await page.locator('button[data-option-label="Yellow"]').evaluate('(el) => el.setAttribute("aria-checked","false")')
                await page.locator('button[data-option-label="Black"]').evaluate('(el) => el.setAttribute("aria-checked","true")')
                _, changed, _, _ = extract_raw(await collect(page), data)
                self.assertEqual(changed['domDiagnostics']['selection']['Color'], ['Black'])
                changed_output, _, _, _ = extract_raw(await collect(page), data)
                self.assertIsNone(changed_output['price'])
                await context.close()
            finally:
                await browser.close()

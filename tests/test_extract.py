"""Small, explicitly synthetic unit cases; not evidence of Back Market access."""
import json
import unittest
from pathlib import Path

from jsonschema import Draft7Validator
from src.extract import extract_html, parse_input, transform
from src.nuxt import decode, find_commerce

UID = '11111111-1111-4111-8111-111111111111'
URL = 'https://www.backmarket.com/en-us/p/test/' + UID


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.input = parse_input({'url': URL})
        self.data = {'product': {'productId': UID, 'titles': {'raw': 'Synthetic unit product'},
            'brand': 'Apple', 'model': 'iPhone 13',
            'priceWhenNew': {'amount': '999.00'}, 'paymentConfig': {'private': 'do not export'},
            'images': [{'url': 'https://example.com/a.jpg'}, {'url': 'https://example.com/a.jpg'}]},
            'pickers': {'selectedOffer': {'price': {'amount': '12.30', 'currency': 'USD'}},
                'pickerGroups': [{'id': 'color', 'items': [{'label': 'A', 'productId': UID,
                    'available': False, 'selected': True, 'price': {'amount': '12.30', 'currency': 'USD'}}]}]},
            'specifications': {'specifications': [{'display': 'Weight', 'field': 'weight', 'values': [{'label': '201 oz'}]}]}}

    def test_selected_price_and_contract(self):
        output, metadata, missing = transform(self.input, self.data)
        schema = json.loads(Path('.actor/dataset_schema.json').read_text(encoding='utf-8'))['fields']
        Draft7Validator(schema).validate(output)
        self.assertEqual(len(output), 11)
        self.assertEqual(output['price'], '12.30')
        self.assertEqual(metadata['currency'], 'USD')
        self.assertFalse(missing)
        self.assertIsNone(output['weight'])
        self.assertEqual(len(output['images']), 1)
        self.assertNotIn('paymentConfig', json.dumps(output))

    def test_actual_options_only(self):
        output, metadata, _ = transform(self.input, self.data)
        self.assertEqual(len(output['variations']), 1)
        self.assertEqual(len(output['variations'][0]['variants']), 1)
        self.assertIs(metadata['variantGroups'][0]['options'][0]['available'], False)

    def test_missing_selected_price_not_reference(self):
        self.data['pickers']['selectedOffer'] = {}
        output, metadata, missing = transform(self.input, self.data, {'currency': 'EUR'})
        self.assertIsNone(output['price'])
        self.assertIsNone(metadata['currency'])
        self.assertIn('price', missing)

    def test_selected_offer_must_match_selection(self):
        self.data['pickers']['pickerGroups'][0]['items'][0]['price']['amount'] = '11.00'
        output, metadata, missing = transform(self.input, self.data)
        self.assertIsNone(output['price'])
        self.assertIn('price', missing)
        self.assertEqual(metadata['priceVerification']['reason'], 'SELECTED_OPTION_PRICE_MISMATCH')

    def test_unassociated_variant_has_no_price(self):
        self.data['pickers']['pickerGroups'][0]['items'][0]['productId'] = None
        output, _, missing = transform(self.input, self.data)
        self.assertIsNone(output['variations'][0]['variants'][0]['price'])
        self.assertTrue(missing)

    def test_jsonld_offer_is_not_a_verified_selected_price(self):
        document = '<html><script type="application/ld+json">' + json.dumps(
            {'@type': 'Product', 'name': 'Test', 'offers': {'@type': 'Offer', 'price': '19.00', 'priceCurrency': 'USD'}}) + '</script></html>'
        output, metadata, missing, _ = extract_html(document, self.input)
        self.assertIsNone(output['price'])
        self.assertTrue(metadata['sourceDiagnostics']['jsonLdOfferPriceFound'])
        self.assertEqual(metadata['priceVerification']['status'], 'unverified')
        self.assertIn('price', missing)

    def test_reject_urls(self):
        for url in ('http://www.backmarket.com/en-us/p/x/' + UID,
                    URL + '?grade=1', 'https://evil.test/en-us/p/x/' + UID, 'https://www.backmarket.com/x'):
            with self.assertRaises(ValueError):
                parse_input({'url': url})

    def test_nuxt_data_only(self):
        table = [{'data': 1}, {'products/' + UID: 2}, {'titles': 3, 'images': 4, 'productId': 5},
                 {'raw': 6}, [], UID, 'Synthetic unit product']
        parsed = decode(table)
        found = find_commerce(parsed, UID)
        self.assertEqual(found['product']['titles']['raw'], 'Synthetic unit product')
        self.assertFalse(find_commerce(parsed, '22222222-2222-4222-8222-222222222222'))

    def test_bound_snapshot(self):
        document = '<html><script type="application/json" id="captured-commercial-data">' + json.dumps(
            {'productId': UID, 'data': self.data}) + '</script></html>'
        output, metadata, missing, subset = extract_html(document, self.input)
        self.assertEqual(output['price'], '12.30')
        self.assertFalse(missing)
        self.assertNotIn('paymentConfig', json.dumps(subset))
        self.assertTrue(metadata['sourceDiagnostics']['selectedOfferPriceFound'])
        self.assertTrue(metadata['sourceDiagnostics']['originalPricePresent'])
        self.assertFalse(metadata['sourceDiagnostics']['originalPriceUsed'])
        schema = json.loads(Path('.actor/key_value_store_schema.json').read_text(encoding='utf-8'))
        Draft7Validator(schema['collections']['metadata']['jsonSchema']).validate(
            metadata | {'status': 'partial', 'executionMode': 'offline', 'extractionPerformed': False})

    def test_jsonld_aggregate_not_sale_price(self):
        document = '<html><script type="application/ld+json">' + json.dumps(
            {'@type': 'Product', 'name': 'Test', 'offers': {'@type': 'AggregateOffer', 'lowPrice': '1.00'}}) + '</script></html>'
        output, _, missing, _ = extract_html(document, self.input)
        self.assertIsNone(output['price'])
        self.assertIn('selectedOffer.price.amount', missing)

    def test_foreign_jsonld(self):
        document = '<html><script type="application/ld+json">' + json.dumps(
            {'@type': 'Product', 'url': URL.replace(UID, '22222222-2222-4222-8222-222222222222'),
             'name': 'Foreign'}) + '</script></html>'
        output, _, _, _ = extract_html(document, self.input)
        self.assertIsNone(output['title'])


if __name__ == '__main__':
    unittest.main()

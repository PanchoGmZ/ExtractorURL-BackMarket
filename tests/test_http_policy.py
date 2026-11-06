"""Focused response-policy regression checks; no browser or network requests."""
import unittest

from src.browser import blocking_reason, classify_resource, diagnostic_path


class HttpPolicyTests(unittest.TestCase):
    def test_review_403_is_optional_even_if_its_own_response_is_a_challenge(self):
        category = classify_resource('/reviews/v1/products/11111111-1111-4111-8111-111111111111/reviews',
            is_document=False, commercial_kind=None, first_party=True)
        self.assertEqual(category, 'optional')
        self.assertIsNone(blocking_reason(category, 403, False))
        self.assertIsNone(blocking_reason(category, 403, True))

    def test_product_and_commercial_denials_stop(self):
        for is_document, kind in [(True, None), (False, 'pickers'), (False, 'product'), (False, 'specifications')]:
            category = classify_resource('/product', is_document=is_document,
                commercial_kind=kind, first_party=True)
            self.assertEqual(blocking_reason(category, 403, False), 'HTTP_403')
            self.assertEqual(blocking_reason(category, 200, True), 'ACCESS_CHALLENGE')
            self.assertEqual(blocking_reason(category, 403, True), 'ACCESS_CHALLENGE')

    def test_jsd_observation_does_not_block_but_explicit_challenge_does(self):
        path = '/cdn-cgi/challenge-platform/synthetic-token/main.js'
        category = classify_resource(path, is_document=False, commercial_kind=None, first_party=True)
        self.assertEqual(category, 'protection')
        for status in (302, 200):
            self.assertIsNone(blocking_reason(category, status, False))
        self.assertEqual(blocking_reason(category, 200, True), 'ACCESS_CHALLENGE')
        self.assertNotIn('synthetic-token', diagnostic_path(path, category))

    def test_other_asset_403_does_not_discard_product(self):
        category = classify_resource('/some-asset', is_document=False,
            commercial_kind=None, first_party=True)
        self.assertEqual(category, 'secondary')
        self.assertIsNone(blocking_reason(category, 403, False))


if __name__ == '__main__':
    unittest.main()

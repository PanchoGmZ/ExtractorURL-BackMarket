"""Small deterministic mass checks; no HTTP, browser or shipping estimates."""
import unittest

from src.weight import normalize_weight, product_weight


def specification(value, field='weight', display='Weight'):
    return {'field': field, 'display': display, 'values': [{'label': value}]}


class WeightTests(unittest.TestCase):
    def test_explicit_units_to_kg(self):
        for raw, expected in [('201 g', 0.201), ('0,201 kg', 0.201),
                              ('1 oz', 0.028349523125), ('1 lb', 0.45359237)]:
            result = normalize_weight(raw)
            self.assertEqual(result['status'], 'normalized')
            self.assertAlmostEqual(result['candidateKg'], expected, places=12)

    def test_201_oz_is_not_201_g(self):
        result = normalize_weight('201 oz', 'smartphone')
        self.assertEqual(result['status'], 'suspicious')
        self.assertGreater(result['candidateKg'], 5)
        self.assertNotEqual(result['candidateKg'], 0.201)

    def test_exact_official_model_only(self):
        specs = [specification('201 oz')]
        weight, metadata = product_weight({'brand': 'Apple', 'model': 'iPhone 15 Plus'}, specs)
        self.assertEqual(weight, {'value': 0.201, 'unit': 'kg'})
        self.assertEqual(metadata['sourceUrl'], 'https://support.apple.com/es-es/111830')
        self.assertEqual(metadata['candidates'][0]['status'], 'suspicious')
        self.assertIsNone(metadata['shippingWeightKg'])
        for model in ('iPhone 15', 'iPhone 15 Pro', 'iPhone 15 Pro Max', 'iPhone 13'):
            other, _ = product_weight({'brand': 'Apple', 'model': model}, specs)
            self.assertIsNone(other)
        ambiguous = specs + [specification('iPhone 15 Pro', 'model', 'Model')]
        other, _ = product_weight({'brand': 'Apple', 'model': 'iPhone 15 Plus'}, ambiguous)
        self.assertIsNone(other)

    def test_shipping_is_not_physical_weight(self):
        product = {'brand': 'Apple', 'model': 'iPhone 13'}
        weight, metadata = product_weight(product, [specification('1 kg', 'shipping_weight', 'Shipping weight')])
        self.assertIsNone(weight)
        self.assertEqual(metadata['excludedShippingValues'], ['1 kg'])
        physical, _ = product_weight(product, [specification('174 g')])
        self.assertEqual(physical, {'value': 0.174, 'unit': 'kg'})

    def test_unknown_missing_ambiguous_and_conflicting_stay_null(self):
        for raw in ('201', '0 kg', '-1 kg', '1,234 kg', '201 bananas'):
            self.assertNotEqual(normalize_weight(raw)['status'], 'normalized')
        unknown, _ = product_weight({}, [specification('201 g')])
        self.assertIsNone(unknown)
        conflicting, _ = product_weight({'model': 'iPhone 13'}, [specification('174 g'), specification('200 g')])
        self.assertIsNone(conflicting)


if __name__ == '__main__':
    unittest.main()

"""Explicit mass conversion and product-weight provenance; never shipping estimates."""
from decimal import Decimal
import re
import unicodedata


KG_PER_UNIT = {'g': Decimal('0.001'), 'kg': Decimal('1'),
               'oz': Decimal('0.028349523125'), 'lb': Decimal('0.45359237')}
# Broad validation bounds, not estimates of a product's actual mass.
PRODUCT_BOUNDS_KG = {'smartphone': (Decimal('0.02'), Decimal('2')),
                     'tablet': (Decimal('0.05'), Decimal('5')),
                     'laptop': (Decimal('0.1'), Decimal('20'))}
OFFICIAL_WEIGHTS = {
    ('apple', 'iphone 15 plus'): {
        'raw': '201 g', 'kg': 0.201, 'sourceUrl': 'https://support.apple.com/es-es/111830',
        'verifiedOn': '2026-11-05',
    },
}


def key(value):
    if not isinstance(value, str):
        return ''
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return ' '.join(value.casefold().split())


def normalize_weight(raw, product_type=None):
    result = {'raw': raw if isinstance(raw, str) else None, 'unit': None,
              'candidateKg': None, 'status': 'unverified', 'reason': 'EXPLICIT_UNIT_REQUIRED'}
    if not isinstance(raw, str) or len(raw) > 100:
        return result
    match = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(kg|g|oz|lbs?)\s*', raw, re.I)
    if not match:
        return result
    number, unit = match.groups()
    unit = 'lb' if unit.lower() == 'lbs' else unit.lower()
    result['unit'] = unit
    # Do not guess whether 1,234 means a decimal or a thousands separator.
    if ',' in number and len(number.split(',')[1]) == 3 and not number.startswith('0,'):
        result['reason'] = 'AMBIGUOUS_NUMBER_FORMAT'
        return result
    if len(number) > 30:
        result['reason'] = 'EXCESSIVE_VALUE'
        return result
    kg = Decimal(number.replace(',', '.')) * KG_PER_UNIT[unit]
    result['candidateKg'] = float(kg)
    if kg <= 0:
        result['reason'] = 'NON_POSITIVE_WEIGHT'
    elif product_type in PRODUCT_BOUNDS_KG and not PRODUCT_BOUNDS_KG[product_type][0] <= kg <= PRODUCT_BOUNDS_KG[product_type][1]:
        result.update(status='suspicious', reason='OUTSIDE_PRODUCT_TYPE_RANGE')
    else:
        result.update(status='normalized', reason='EXPLICIT_UNIT_CONVERSION')
    return result


def product_identity(product, specs):
    brands, models = set(), set()
    for name, target in (('brand', brands), ('model', models)):
        if key(product.get(name)):
            target.add(key(product[name]))
    for spec in specs:
        names = {key(spec.get('display')), key(spec.get('field'))}
        target = brands if names & {'brand', 'marca', 'marque'} else models if names & {'model', 'modelo', 'modele'} else None
        if target is not None:
            target.update(key(value.get('label')) for value in spec.get('values', [])
                          if isinstance(value, dict) and key(value.get('label')))
    brand = next(iter(brands)) if len(brands) == 1 else None
    model = next(iter(models)) if len(models) == 1 else None
    product_type = None
    if model:
        if model.startswith(('ipad ', 'galaxy tab ', 'surface pro ')):
            product_type = 'tablet'
        elif model.startswith(('macbook ', 'thinkpad ', 'latitude ', 'chromebook ')):
            product_type = 'laptop'
        elif re.match(r'^(iphone\s+\d|galaxy\s+(s\d|a\d|note|z\s)|pixel\s+\d)', model):
            product_type = 'smartphone'
    return brand, model, product_type


def product_weight(product, specifications):
    brand, model, product_type = product_identity(product, specifications)
    candidates, excluded_shipping = [], []
    for spec in specifications:
        names = ' '.join((key(spec.get('display')), key(spec.get('field'))))
        if not re.search(r'weight|peso|poids', names):
            continue
        shipping = bool(re.search(r'shipping|shipment|package|packaging|gross|envio|embalaje|bruto|expedition', names))
        for value in spec.get('values', []):
            if not isinstance(value, dict):
                continue
            raw = value.get('label')
            if shipping:
                if isinstance(raw, str):
                    excluded_shipping.append(raw)
                continue
            normalized = normalize_weight(raw, product_type)
            normalized['field'] = spec.get('field') or spec.get('display')
            candidates.append(normalized)
    metadata = {'kind': 'physical_product', 'shippingWeightKg': None, 'brand': brand, 'model': model,
        'productType': product_type, 'candidates': candidates, 'excludedShippingValues': excluded_shipping,
        'status': 'unverified', 'reason': 'NO_RELIABLE_PHYSICAL_WEIGHT',
        'source': None, 'sourceUrl': None, 'verifiedOn': None, 'sourceValue': None, 'valueKg': None}
    official = OFFICIAL_WEIGHTS.get((brand, model))
    if official:
        metadata.update(status='official', reason='EXACT_MODEL_MANUFACTURER_SPECIFICATION',
            source='manufacturer', sourceUrl=official['sourceUrl'], verifiedOn=official['verifiedOn'],
            sourceValue=official['raw'], valueKg=official['kg'])
    elif candidates:
        valid = [candidate for candidate in candidates if candidate['status'] == 'normalized']
        if not product_type:
            metadata['reason'] = 'PRODUCT_TYPE_UNVERIFIED'
        elif len(valid) == len(candidates) and len({candidate['candidateKg'] for candidate in valid}) == 1:
            metadata.update(status='published_normalized', reason='EXPLICIT_PRODUCT_SPECIFICATION',
                source='backmarket.technical_specifications', sourceValue=valid[0]['raw'], valueKg=valid[0]['candidateKg'])
        else:
            metadata['reason'] = 'CONFLICTING_OR_SUSPICIOUS_PUBLISHED_WEIGHT'
    weight = {'value': metadata['valueKg'], 'unit': 'kg'} if metadata['valueKg'] is not None else None
    return weight, metadata

"""HTML/JSON determinista -> contrato TU CUMPA 1688 (once campos)."""
import hashlib
import json
import re
import os
from decimal import Decimal
from urllib.parse import urlsplit

from lxml import html as lhtml

from .nuxt import UUID_RE, decode, find_commerce
from .weight import product_weight
from .urls import selection_parameters

PARAMETERS = {'grade', 'battery', 'premium_grade', 'battery_confidence_experiment',
              'cheapest_for_first_picker', 'storage', 'color'}


def text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def decimal(value):
    return value if isinstance(value, str) and re.fullmatch(r'(?:0|[1-9]\d*)(?:\.\d+)?', value) else None


def image_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        return value if url.scheme == 'https' and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def parse_input(raw):
    if not isinstance(raw, dict):
        raise ValueError('INPUT debe ser un objeto JSON')
    defaults = {'mode': 'product', 'source': 'backmarket', 'outputFormat': 'reference1688',
                'executionMode': os.environ.get('BACKMARKET_EXECUTION_MODE', 'live'),
                'timeoutSeconds': int(os.environ.get('BACKMARKET_TIMEOUT_SECONDS', '48')),
                'maxHtmlBytes': int(os.environ.get('BACKMARKET_MAX_HTML_BYTES', '4194304')),
                'maxMemoryMb': int(os.environ.get('BACKMARKET_MAX_MEMORY_MB', '1024')),
                'htmlFile': os.environ.get('BACKMARKET_HTML_FILE', '')}
    if set(raw) - (set(defaults) | {'url'}):
        raise ValueError('INPUT contiene campos desconocidos; usa el esquema Python actual')
    value = defaults | raw
    if (value['mode'], value['source'], value['outputFormat']) != ('product', 'backmarket', 'reference1688'):
        raise ValueError('Solo product/backmarket/reference1688')
    if value['executionMode'] not in {'live', 'offline'}:
        raise ValueError('executionMode debe ser live u offline')
    url = urlsplit(value.get('url', ''))
    match = re.fullmatch(r'/en-us/p/[^/]+/(' + UUID_RE.pattern + r')/?', url.path, re.I)
    if (url.scheme != 'https' or url.netloc != 'www.backmarket.com' or url.username
            or url.password or url.fragment or not match):
        raise ValueError('URL compatible: https://www.backmarket.com/en-us/p/slug/UUID, con l/offerType opcionales y sin fragmento')
    selection_parameters(value['url'])
    for key, low, high in [('timeoutSeconds', 5, 48), ('maxHtmlBytes', 1024, 8388608), ('maxMemoryMb', 256, 2048)]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f'{key} fuera de rango {low}..{high}')
    if not isinstance(value['htmlFile'], str):
        raise ValueError('htmlFile debe ser una ruta relativa')
    return value | {'productId': match[1].lower()}


def commercial_subset(data):
    """Do not persist full framework state, payment configurations or personal data."""
    output = {}
    p = data.get('product')
    if isinstance(p, dict):
        titles = p.get('titles') if isinstance(p.get('titles'), dict) else {}
        output['product'] = {
            'productId': text(p.get('productId')),
            'brand': text(p.get('brand')), 'model': text(p.get('model')),
            'titles': {k: text(titles.get(k)) for k in ('raw', 'default', 'subtitle')},
            'images': [{'url': image_url(item.get('url'))} for item in p.get('images', []) if isinstance(item, dict)],
        }
    p = data.get('pickers')
    if isinstance(p, dict):
        offer = p.get('selectedOffer') if isinstance(p.get('selectedOffer'), dict) else {}
        price = offer.get('price') if isinstance(offer.get('price'), dict) else {}
        groups = []
        for group in p.get('pickerGroups', []):
            if not isinstance(group, dict) or not isinstance(group.get('items'), list):
                continue
            items = []
            for item in group['items']:
                if not isinstance(item, dict):
                    continue
                ip = item.get('price') if isinstance(item.get('price'), dict) else {}
                params = item.get('parameters') if isinstance(item.get('parameters'), dict) else {}
                items.append({
                    **{k: text(item.get(k)) for k in ('label', 'productId', 'slug')},
                    'price': {'amount': decimal(ip.get('amount')), 'currency': text(ip.get('currency'))},
                    **{k: item.get(k) if type(item.get(k)) is bool else None for k in ('available', 'selected')},
                    'parameters': {k: v for k, v in params.items() if k in PARAMETERS and
                                   (v is None or type(v) in (str, int, float, bool))},
                })
            groups.append({'id': text(group.get('id')), 'items': items})
        output['pickers'] = {'pickerGroups': groups,
            'selectedOffer': {'price': {'amount': decimal(price.get('amount')), 'currency': text(price.get('currency'))},
                              'offerId': text(offer.get('offerId')), 'sku': text(offer.get('sku')),
                              'productId': text(offer.get('productId'))},
            'isOutOfStock': p.get('isOutOfStock') if type(p.get('isOutOfStock')) is bool else None}
    p = data.get('specifications')
    if isinstance(p, dict):
        output['specifications'] = {'title': text(p.get('title')), 'specifications': [
            {'display': text(s.get('display')), 'field': text(s.get('field')),
             'values': [{'label': text(v.get('label'))} for v in s.get('values', []) if isinstance(v, dict)]}
            for s in p.get('specifications', []) if isinstance(s, dict)]}
    return output


def selected_price(input_data, pickers):
    offer = pickers.get('selectedOffer', {})
    price = offer.get('price', {})
    amount, currency = decimal(price.get('amount')), text(price.get('currency'))
    verification = {'status': 'unverified', 'reason': None, 'selectedOptionChecks': 0,
        'requestedProductId': input_data['productId'], 'selectedOfferProductId': offer.get('productId'),
        'reportedAmount': amount, 'reportedCurrency': currency}
    if amount is None:
        verification['reason'] = 'SELECTED_OFFER_AMOUNT_MISSING_OR_INVALID'
    elif not currency or not re.fullmatch(r'[A-Z]{3}', currency):
        verification['reason'] = 'SELECTED_OFFER_CURRENCY_MISSING_OR_INVALID'
    elif offer.get('productId') and offer['productId'].lower() != input_data['productId']:
        verification['reason'] = 'SELECTED_OFFER_PRODUCT_MISMATCH'
    else:
        for group in pickers.get('pickerGroups', []):
            selected = [item for item in group['items'] if item.get('selected') is True]
            if len(selected) > 1:
                verification['reason'] = 'AMBIGUOUS_PICKER_SELECTION'
                break
            for item in selected:
                verification['selectedOptionChecks'] += 1
                if item.get('productId') and item['productId'].lower() != input_data['productId']:
                    verification['reason'] = 'SELECTED_OPTION_PRODUCT_MISMATCH'
                elif item['price']['amount'] is not None and Decimal(item['price']['amount']) != Decimal(amount):
                    verification['reason'] = 'SELECTED_OPTION_PRICE_MISMATCH'
                elif item['price']['currency'] and item['price']['currency'] != currency:
                    verification['reason'] = 'SELECTED_OPTION_CURRENCY_MISMATCH'
            if verification['reason']:
                break
        if verification['reason'] is None:
            verification.update(status='verified', reason='SELECTED_OFFER_FOR_REQUESTED_PRODUCT')
    return amount if verification['status'] == 'verified' else None, currency, verification


def transform(input_data, data, fallback=None):
    data = commercial_subset(data)
    fallback = fallback or {}
    product = data.get('product', {})
    pickers = data.get('pickers', {})
    specs = data.get('specifications', {})
    if product.get('productId') and product['productId'].lower() != input_data['productId']:
        raise ValueError('UUID de captura no corresponde a INPUT')
    offer = pickers.get('selectedOffer', {})
    price = offer.get('price', {})
    main_price, currency, price_verification = selected_price(input_data, pickers)
    weight, weight_metadata = product_weight(product, specs.get('specifications', []))
    images = list(dict.fromkeys(filter(None, [i['url'] for i in product.get('images', [])])))
    attrs = []
    for spec in specs.get('specifications', []):
        name = spec.get('display') or spec.get('field')
        labels = [v['label'] for v in spec.get('values', []) if v.get('label')]
        if name and labels:
            attrs.append({'attrName': name, 'attrValue': ', '.join(labels)})
    groups, metadata_groups = [], []
    for group in pickers.get('pickerGroups', []):
        if not group.get('id'):
            continue
        variants, options = [], []
        for index, item in enumerate(group['items']):
            associated = (item['label'] is not None and item.get('productId') is not None
                and UUID_RE.fullmatch(item['productId']) is not None
                and item['price']['currency'] is not None and re.fullmatch(r'[A-Z]{3}', item['price']['currency']) is not None)
            variants.append({'name': item['label'], 'price': item['price']['amount'] if associated else None, 'image': None})
            options.append({'variantIndex': index, **{key: item.get(key) for key in
                ('productId', 'available', 'selected', 'slug', 'parameters')}, 'currency': item['price']['currency']})
        metadata_groups.append({'groupIndex': len(groups), 'id': group['id'], 'options': options})
        groups.append({'name': group['id'], 'variants': variants})
    titles = product.get('titles', {})
    output = {'url': input_data['url'], 'title': titles.get('raw') or titles.get('default') or fallback.get('title'),
        'price': main_price,
        'productLadderPrices': [], 'moq': None, 'weight': weight, 'dimensions': None,
        'images': images or fallback.get('images') or None, 'characteristics': attrs or None,
        'variations': groups if 'pickers' in data else None, 'productId': input_data['productId']}
    missing = [k for k in ('title', 'price', 'images', 'characteristics', 'variations') if not output[k]]
    if not price.get('currency'):
        missing.append('selectedOffer.price.currency')
    if not price.get('amount'):
        missing.append('selectedOffer.price.amount')
    for group_index, group in enumerate(groups):
        for index, variant in enumerate(group['variants']):
            for key in ('name', 'price'):
                if variant[key] is None:
                    missing.append(f'variations[{group_index}].variants[{index}].{key}')
            option = metadata_groups[group_index]['options'][index]
            for key in ('productId', 'available', 'currency'):
                if option[key] is None:
                    missing.append(f'variantMetadata[{group_index}].options[{index}].{key}')
    metadata = {'productId': input_data['productId'], 'url': input_data['url'],
        'currency': currency, 'priceSource': 'selectedOffer.price' if price.get('amount') is not None else None,
        'offerId': offer.get('offerId'), 'sku': offer.get('sku'), 'isOutOfStock': pickers.get('isOutOfStock'),
        'variantGroups': metadata_groups, 'priceVerification': price_verification,
        'weightVerification': weight_metadata['status'] + '; physical product only; shipping weight unavailable',
        'weightNormalization': weight_metadata}
    return output, metadata, missing


def extract_html(html, input_data, responses=None):
    if len(html.encode('utf-8')) > input_data['maxHtmlBytes']:
        raise ValueError('HTML excede maxHtmlBytes')
    doc = lhtml.fromstring(html, parser=lhtml.HTMLParser(no_network=True))
    data, fallback, inventory, sources = {}, {}, [], {}
    original_price_present = False
    jsonld_product_found = False

    def remember(kind, value, source, replace=False):
        nonlocal original_price_present
        if kind == 'product' and value.get('priceWhenNew') is not None:
            original_price_present = True
        if replace or kind not in data:
            data[kind] = value
            sources[kind] = source
    for node in doc.xpath('//script'):
        raw = node.text or ''
        script_id, typ = node.get('id', ''), node.get('type', '')
        if typ not in ('application/json', 'application/ld+json') and script_id != '__NUXT_DATA__':
            continue
        source = 'nuxt' if script_id == '__NUXT_DATA__' else 'json-ld' if typ == 'application/ld+json' else 'embedded-json'
        inventory.append({'id': script_id, 'type': typ, 'format': source, 'length': len(raw)})
        try:
            parsed = json.loads(raw)
            if script_id == '__NUXT_DATA__':
                parsed = decode(parsed)
            if script_id == 'captured-commercial-data':
                if parsed.get('productId') == input_data['productId']:
                    snapshot = parsed.get('data', {})
                    if isinstance(snapshot, dict):
                        for kind in ('product', 'pickers', 'specifications'):
                            if isinstance(snapshot.get(kind), dict):
                                remember(kind, snapshot[kind], 'local-commercial-capture', replace=True)
            elif typ == 'application/ld+json':
                nodes = parsed if isinstance(parsed, list) else [parsed]
                nodes += [v for n in nodes if isinstance(n, dict) for v in n.get('@graph', [])]
                for item in nodes:
                    if not isinstance(item, dict) or 'Product' not in ([item.get('@type')] if isinstance(item.get('@type'), str) else item.get('@type', [])):
                        continue
                    declared = ' '.join(str(item.get(k, '')) for k in ('url', '@id', 'productID'))
                    ids = UUID_RE.findall(declared)
                    if ids and input_data['productId'] not in [i.lower() for i in ids]:
                        continue
                    jsonld_product_found = True
                    fallback['title'] = text(item.get('name'))
                    imgs = item.get('image', [])
                    imgs = [imgs] if isinstance(imgs, str) else imgs
                    fallback['images'] = list(dict.fromkeys(filter(None, map(image_url, imgs)))) if isinstance(imgs, list) else []
                    offers = item.get('offers', {})
                    if isinstance(offers, list):
                        offers = offers[0] if len(offers) == 1 else {}
                    if isinstance(offers, dict) and offers.get('@type') == 'Offer':
                        fallback.update(price=decimal(str(offers.get('price', ''))), currency=text(offers.get('priceCurrency')),
                                        priceSource='JSON-LD Offer (partial)')
            else:
                for kind, value in find_commerce(parsed, input_data['productId']).items():
                    remember(kind, value, source)
        except (ValueError, TypeError, RecursionError):
            inventory[-1]['parseError'] = True
    # Page-generated JSON is more current than server-rendered payload. No direct endpoint fetches.
    for kind, value in (responses or {}).items():
        if kind in ('product', 'pickers', 'specifications'):
            remember(kind, value, 'page-generated-json', replace=True)
    data = commercial_subset(data)
    output, metadata, missing = transform(input_data, data, fallback)
    metadata['htmlInventory'] = inventory
    metadata['htmlSha256'] = hashlib.sha256(html.encode()).hexdigest()
    metadata['sourceDiagnostics'] = {
        'nuxtScripts': sum(item['format'] == 'nuxt' for item in inventory),
        'jsonLdScripts': sum(item['format'] == 'json-ld' for item in inventory),
        'embeddedJsonScripts': sum(item['format'] == 'embedded-json' for item in inventory),
        'scriptErrors': sum(item.get('parseError', False) for item in inventory),
        'jsonLdProductFound': jsonld_product_found,
        'jsonLdOfferPriceFound': fallback.get('price') is not None,
        'commercialSources': sources,
        'selectedOfferPriceFound': data.get('pickers', {}).get('selectedOffer', {}).get('price', {}).get('amount') is not None,
        'selectedOfferCurrencyFound': bool(data.get('pickers', {}).get('selectedOffer', {}).get('price', {}).get('currency')),
        'originalPricePresent': original_price_present,
        'originalPriceUsed': False,
        'foundFields': [key for key in ('title', 'price', 'images', 'characteristics', 'variations') if output[key]],
        'missingFields': missing,
    }
    return output, metadata, missing, data

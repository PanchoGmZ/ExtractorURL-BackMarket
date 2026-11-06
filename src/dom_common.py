"""Conservative semantic selector profile and shared DOM/CSS normalization.

Selectors are validated against synthetic semantic HTML, not a claimed captured
Back Market DOM. Never read script JSON, generate combinations or infer currency
from a dollar sign alone. All selectors run inside one cleaned product scope.
"""
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from .reference1688 import product_record
from .weight import product_weight

ROOT_SELECTOR = 'main, [role="main"], [itemtype$="/Product"], [data-testid="product-page"], [data-product-id]'
EXCLUDE_SELECTOR = ('script, style, noscript, aside, nav, footer, del, s, [hidden], [aria-hidden="true"], '
                    '[data-testid*="recommend" i], [data-testid*="related" i], '
                    '[aria-label*="recommend" i], [aria-label*="related" i], '
                    '[data-testid*="review" i], [id*="reviews" i], [aria-label*="reviews" i], '
                    '[data-testid*="payment" i], [data-testid*="seller" i], [data-testid*="customer" i], '
                    '[data-price-type="original"], [data-price-type="old"], '
                    '[data-testid*="original-price" i], [data-testid*="previous-price" i]')
PRICE_SELECTOR = ('[itemprop="offers"] [itemprop="price"]:not(meta), [data-testid="product-price"], '
                  '[data-current-price], [data-price-type="current"]')
UUID_TEXT = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
UUID = re.compile(r'\b' + UUID_TEXT + r'\b', re.I)


def field(name, selector=None, attribute=None, source=None):
    value = {'name': name, 'type': 'attribute' if attribute else 'text'}
    if selector is not None:
        value['selector'] = selector
    if attribute:
        value['attribute'] = attribute
    if source:
        value['source'] = source
    return value


def many(name, selector, fields):
    return {'name': name, 'type': 'nested_list', 'selector': selector, 'fields': fields}


OPTION_FIELDS = [field('text'), field('label', attribute='data-extractor-label'),
    field('labelText', '[data-option-label], [data-testid="option-label"]'),
    *[field(key, attribute=attr) for key, attr in (
        ('id', 'data-product-id'), ('href', 'href'), ('price', 'data-price'),
        ('currency', 'data-currency'), ('selected', 'aria-checked'), ('pressed', 'aria-pressed'),
        ('checked', 'checked'), ('optionSelected', 'selected'), ('disabled', 'disabled'),
        ('ariaDisabled', 'aria-disabled'), ('tag', 'data-extractor-tag'), ('value', 'value'))],
    field('nestedPrice', '[data-price]', 'data-price'), field('nestedCurrency', '[data-currency]', 'data-currency')]
SCHEMA = {'name': 'Product semantic DOM v1', 'baseSelector': '[data-extraction-root]', 'fields': [
    many('titles', 'h1', [field('text')]),
    many('models', '[itemprop="model"], [data-testid="product-model"]', [field('text'), field('content', attribute='content')]),
    many('brands', '[itemprop="brand"]', [field('text'), field('content', attribute='content')]),
    many('prices', PRICE_SELECTOR, [field('text'), field('content', attribute='content'),
        field('value', attribute='data-current-price'), field('currency', attribute='data-currency')]),
    many('currencies', '[itemprop="priceCurrency"]',
         [field('text'), field('content', attribute='content'), field('currency', attribute='data-currency')]),
    many('images', '[data-testid="product-gallery"] img, [data-test="product-gallery"] img, [itemprop="image"]',
         [field('src', attribute='src'), field('lazy', attribute='data-src'), field('href', attribute='href')]),
    many('specs', 'tr:has(th), [data-spec-row]', [field('label', 'th, [data-spec-label]'), field('value', 'td, [data-spec-value]')]),
    many('cellSpecs', 'tr:not(:has(th))', [field('label', 'td:first-child'), field('value', 'td:nth-child(2)')]),
    many('definitions', 'dt', [field('label'), field('value', source='+ dd')]),
    many('groups', 'fieldset, [role="radiogroup"], [data-variant-group], select', [
        field('name', attribute='data-extractor-label'), field('legend', 'legend'),
        many('options', 'button, [role="radio"], input[type="radio"], option, a[data-product-id], a[href*="/p/"]', OPTION_FIELDS)])]}


def clean(value):
    return (' '.join(value.split())[:1000] or None) if isinstance(value, str) else None


def product_id(value):
    if not isinstance(value, str):
        return None
    match = UUID.search(value)
    return match.group(0).lower() if match else None


def product_url_id(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.hostname != 'www.backmarket.com' or parsed.username or parsed.password:
            return None
        match = re.fullmatch(r'/en-us/p/[^/]+/(' + UUID_TEXT + r')/?', parsed.path, re.I)
        return match.group(1).lower() if match else None
    except ValueError:
        return None


def currency(value):
    value = clean(value) or ''
    codes = set(re.findall(r'(?<![A-Z])(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY)(?![A-Z])', value.upper()))
    codes.update(code for token, code in (('US$', 'USD'), ('CA$', 'CAD'), ('A$', 'AUD'), ('€', 'EUR'), ('£', 'GBP')) if token in value)
    return next(iter(codes)) if len(codes) == 1 else None


def amount(value):
    value = clean(value)
    if not value or '+' in value or '%' in value:
        return None
    value = re.sub(r'(?<![A-Z])(?:USD|EUR|GBP|CAD|AUD|JPY|CHF|CNY)(?![A-Z])|US\$|CA\$|A\$|[$€£¥]', '', value, flags=re.I).strip()
    if not re.fullmatch(r'\d[\d.,\s]*', value):
        return None
    value = value.replace(' ', '')
    if re.fullmatch(r'\d{1,3}(?:,\d{3})+\.\d{2}', value):
        value = value.replace(',', '')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+,\d{2}', value):
        value = value.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d+,\d{2}', value):
        value = value.replace(',', '.')
    elif not re.fullmatch(r'\d+(?:\.\d{1,2})?', value):
        return None
    try:
        return format(Decimal(value).quantize(Decimal('0.01')), 'f') if Decimal(value) >= 0 else None
    except InvalidOperation:
        return None


def normalize(raw, data, method):
    prefix = 'DOM' if method == 'playwright_dom' else 'CSS'
    identity = raw.get('identity', {})
    ids = {product_id(value) for value in identity.get('ids', []) if product_id(value)}
    explicit_mismatch = any(value != data['productId'] for value in ids)
    titles = {clean(item.get('text')) for item in raw.get('titles', []) if clean(item.get('text'))}
    title = next(iter(titles)) if len(titles) == 1 else None
    product_scope = identity.get('semantic') or bool(raw.get('prices'))
    page_match = product_url_id(identity.get('documentUrl')) == data['productId']
    verified = bool(title and product_scope and not identity.get('verificationPage') and not explicit_mismatch and (data['productId'] in ids or page_match))
    reason = ('VERIFICATION_DOCUMENT' if identity.get('verificationPage') else 'PRODUCT_ID_MISMATCH' if explicit_mismatch else 'PRODUCT_ROOT_NOT_FOUND' if not identity.get('rootFound')
              else 'PRODUCT_IDENTITY_UNVERIFIED' if not verified else None)
    diagnostics = {'profile': 'semantic-v1', 'identityVerified': verified,
                   'verificationPage': bool(identity.get('verificationPage')),
                   'errorCode': prefix + '_' + reason if reason else None,
                   'priceCandidateCount': len(raw.get('prices', [])), 'selection': {},
                   'availabilitySource': 'declared-control-enabled-state',
                   'dimensionsDeclared': None, 'evidence': identity.get('evidence', [])}
    raw = raw if verified else {}
    known_currencies = {currency(item.get('content') or item.get('currency') or item.get('text'))
                        for item in raw.get('currencies', [])}
    known_currencies.discard(None)
    global_currency = next(iter(known_currencies)) if len(known_currencies) == 1 else None
    prices, inconsistent_price = set(), False
    for item in raw.get('prices', []):
        value = amount(item.get('text'))
        declared = amount(item.get('content') or item.get('value'))
        own_currencies = {currency(item.get('currency')), currency(item.get('text'))} - {None}
        if ((declared is not None and value is not None and declared != value) or len(own_currencies) > 1
                or (global_currency and own_currencies and any(unit != global_currency for unit in own_currencies))):
            inconsistent_price = True
            continue
        unit = next(iter(own_currencies)) if own_currencies else global_currency
        if value is not None and unit:
            prices.add((value, unit))
    main_price, main_currency = next(iter(prices)) if len(prices) == 1 and not inconsistent_price else (None, global_currency)
    price_reason = ('VISIBLE_CURRENT_PRICE_IN_PRODUCT_SCOPE' if main_price is not None else
                    'AMBIGUOUS_CURRENT_PRICE' if len(prices) > 1 or inconsistent_price else 'CURRENT_PRICE_OR_CURRENCY_MISSING')
    attrs = []
    seen_attrs = set()
    for item in raw.get('specs', []) + raw.get('cellSpecs', []) + raw.get('definitions', []):
        name, value = clean(item.get('label')), clean(item.get('value'))
        if name and re.fullmatch(r'seller(?: name)?|vendedor|customer(?: name)?|email|e-mail|reviewer|account', name, re.I):
            continue
        if name and value and (name, value) not in seen_attrs:
            attrs.append({'attrName': name, 'attrValue': value})
            seen_attrs.add((name, value))
    for field_name, label in (('models', 'Model'), ('brands', 'Brand')):
        values = {clean(item.get('content') or item.get('text')) for item in raw.get(field_name, [])}
        values.discard(None)
        if len(values) == 1:
            value = next(iter(values))
            if (label, value) not in seen_attrs:
                attrs.append({'attrName': label, 'attrValue': value})
                seen_attrs.add((label, value))
    groups, metadata_groups, seen_groups = [], [], set()
    for group in raw.get('groups', []):
        name = clean(group.get('name') or group.get('legend'))
        if not name or re.search(r'payment|payement|pago|financi|installment|shipping|delivery|livraison|env[ií]o|billing|address|customer|seller|vendedor|insurance|warranty|garanti|account|contact|newsletter|checkout', name, re.I):
            continue
        variants, options, seen_options = [], [], set()
        for option in group.get('options', []):
            label = clean(option.get('labelText') or option.get('label') or option.get('text'))
            href = option.get('href')
            linked_id = product_id(option.get('id'))
            if not linked_id and option.get('tag') == 'option' and UUID.fullmatch(option.get('value') or ''):
                linked_id = product_id(option['value'])
            if not linked_id and href:
                linked_id = product_url_id(urljoin(data['url'], href))
            if not label or (label, linked_id) in seen_options:
                continue
            seen_options.add((label, linked_id))
            selected = (option.get('selected') == 'true' or option.get('pressed') == 'true'
                        or option.get('checked') is not None or option.get('optionSelected') is not None)
            has_selected = any(option.get(k) is not None for k in ('selected', 'pressed', 'checked', 'optionSelected'))
            available = (False if option.get('disabled') is not None or option.get('ariaDisabled') == 'true' else
                         True if option.get('tag') in ('button', 'input', 'option') or option.get('ariaDisabled') == 'false' else None)
            unit = currency(option.get('currency') or option.get('nestedCurrency'))
            value = amount(option.get('price') or option.get('nestedPrice')) if linked_id and unit else None
            variants.append({'name': label, 'price': value, 'image': None})
            options.append({'variantIndex': len(variants) - 1, 'productId': linked_id,
                'available': available, 'selected': selected if has_selected else None,
                'slug': None, 'parameters': None, 'currency': unit})
            if selected:
                diagnostics['selection'].setdefault(name, []).append(label)
                if linked_id and linked_id != data['productId']:
                    main_price, price_reason = None, 'SELECTED_OPTION_PRODUCT_MISMATCH'
                elif value is not None and main_price is not None and Decimal(value) != Decimal(main_price):
                    main_price, price_reason = None, 'SELECTED_OPTION_PRICE_MISMATCH'
        signature = (name, tuple((v['name'], o['productId']) for v, o in zip(variants, options)))
        if not variants or signature in seen_groups:
            continue
        seen_groups.add(signature)
        metadata_groups.append({'groupIndex': len(groups), 'id': name, 'options': options})
        groups.append({'name': name, 'variants': variants})
    for name, labels in diagnostics['selection'].items():
        unique = list(dict.fromkeys(labels))
        if len(unique) == 1:
            attrs.append({'attrName': name, 'attrValue': unique[0]})
        else:
            main_price = None
            price_reason = 'AMBIGUOUS_SELECTED_OPTIONS'
    image_urls = []
    for item in raw.get('images', []):
        for value in (item.get('src'), item.get('lazy'), item.get('href')):
            if not isinstance(value, str):
                continue
            parsed = urlsplit(urljoin(data['url'], value))
            if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
                resolved = urljoin(data['url'], value)
                if resolved not in image_urls:
                    image_urls.append(resolved)
                break
    specs = [{'display': item['attrName'], 'field': item['attrName'], 'values': [{'label': item['attrValue']}]} for item in attrs]
    weight, weight_meta = product_weight({}, specs)
    if weight_meta['status'] == 'official':
        candidates = weight_meta['candidates']
        values = {item['candidateKg'] for item in candidates if item['status'] == 'normalized'}
        reliable = bool(candidates and len(values) == 1 and all(item['status'] == 'normalized' for item in candidates))
        value = next(iter(values)) if reliable else None
        weight = {'value': value, 'unit': 'kg'} if reliable else None
        weight_meta.update(status='published_normalized' if reliable else 'unverified',
            source=None, sourceUrl=None, verifiedOn=None, sourceValue=candidates[0]['raw'] if reliable else None,
            valueKg=value, reason='DOM_EXPLICIT_SPECIFICATION' if reliable else 'DOM_EXPLICIT_RELIABLE_WEIGHT_REQUIRED')
    if weight_meta['status'] == 'published_normalized':
        weight_meta['source'] = method + ':declared-specification'
    for item in attrs:
        if re.search(r'dimensions|dimensiones|medidas', item['attrName'], re.I):
            diagnostics['dimensionsDeclared'] = item['attrValue']
    output = product_record(data, title=title if verified else None, price=main_price,
        weight=weight, images=image_urls, characteristics=attrs, variations=groups or None)
    missing = [key for key in ('title', 'price', 'images', 'characteristics', 'variations') if not output[key]]
    if not verified:
        missing.append('productIdentity')
    if main_currency is None:
        missing.append('visiblePrice.currency')
    for group in metadata_groups:
        for option in group['options']:
            for key in ('productId', 'available', 'currency'):
                if option[key] is None:
                    missing.append(f"variantMetadata[{group['groupIndex']}].options[{option['variantIndex']}].{key}")
            if groups[group['groupIndex']]['variants'][option['variantIndex']]['price'] is None:
                missing.append(f"variations[{group['groupIndex']}].variants[{option['variantIndex']}].price")
    metadata = {'productId': data['productId'], 'url': data['url'], 'currency': main_currency,
        'extractionMethod': method, 'priceSource': method + ':visible-current-price' if main_price is not None else None,
        'offerId': None, 'sku': None, 'isOutOfStock': None, 'variantGroups': metadata_groups,
        'priceVerification': {'status': 'verified' if main_price is not None else 'unverified',
            'reason': price_reason, 'selectedOptionChecks': len(diagnostics['selection']),
            'requestedProductId': data['productId'], 'selectedOfferProductId': None,
            'reportedAmount': main_price, 'reportedCurrency': main_currency},
        'weightVerification': weight_meta['status'] + '; declared physical product only',
        'weightNormalization': weight_meta, 'domDiagnostics': diagnostics, 'htmlInventory': [],
        'sourceDiagnostics': {'nuxtScripts': 0, 'jsonLdScripts': 0, 'embeddedJsonScripts': 0, 'scriptErrors': 0,
            'jsonLdProductFound': False, 'jsonLdOfferPriceFound': False, 'commercialSources': {},
            'selectedOfferPriceFound': False, 'selectedOfferCurrencyFound': False,
            'originalPricePresent': False, 'originalPriceUsed': False,
            'foundFields': [key for key in ('title', 'price', 'images', 'characteristics', 'variations') if output[key]],
            'missingFields': missing}}
    return output, metadata, missing, {'product': {'productId': data['productId'] if verified else None}}

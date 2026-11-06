"""CSS extraction from a single product HTML scope, never Nuxt/script data."""
import re

from bs4 import BeautifulSoup

from .dom_common import EXCLUDE_SELECTOR, ROOT_SELECTOR, SCHEMA, normalize, product_url_id


def prepare(html, document_url=None):
    soup = BeautifulSoup(html, 'lxml')
    canonical = soup.select_one('link[rel="canonical"]')
    canonical_url = product_url_id(canonical.get('href', '')) if canonical else None
    identity = {'rootFound': False, 'semantic': False, 'ids': [canonical_url],
                'documentUrl': document_url, 'evidence': []}
    if soup.title and re.search(r'just a moment|verify.*human|security verification|captcha', soup.title.get_text(), re.I):
        identity['verificationPage'] = True
        return '', identity
    for node in list(soup.select(EXCLUDE_SELECTOR)):
        if node.parent is not None:
            node.decompose()
    for node in list(soup.select('[style]')):
        if re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)', node.get('style', ''), re.I):
            node.decompose()
    candidates = [node for node in soup.select(ROOT_SELECTOR) if len(node.select('h1')) == 1]
    # BeautifulSoup returns None for a valueless itemtype attribute; normalize
    # it before suffix checks so one malformed/empty semantic attribute cannot
    # abort inspection of an otherwise readable product page.
    specific = [node for node in candidates if (node.get('itemtype') or '').endswith('/Product')
                or node.get('data-testid') == 'product-page' or node.get('data-product-id')]
    roots = specific or [node for node in candidates if node.name == 'main' or node.get('role') == 'main']
    roots = [node for node in roots if not any(other != node and node in other.parents for other in roots)]
    if len(roots) != 1:
        return '', identity
    root = roots[0]
    identity.update(rootFound=True, semantic=bool(specific), evidence=['single-product-scope', 'single-h1'])
    identity['ids'].extend([root.get('data-product-id', '')])
    # A second Product scope without the main h1 is a card/recommendation.
    for node in list(root.select('[itemtype$="/Product"]')):
        if node != root and not node.select('h1'):
            node.decompose()
    for node in root.select('[itemprop="productID"]'):
        identity['ids'].append(node.get('content') or node.get_text(' ', strip=True))
    for group in root.select('fieldset, [role="radiogroup"], [data-variant-group], select'):
        label = group.get('aria-label') or ''
        if not label and group.get('aria-labelledby'):
            label = ' '.join(node.get_text(' ', strip=True) for key in group['aria-labelledby'].split()
                             if (node := root.find(id=key)))
        legend = group.find('legend')
        associated = root.find('label', attrs={'for': group.get('id')}) if group.get('id') else None
        group['data-extractor-label'] = label or (legend.get_text(' ', strip=True) if legend else '') or (associated.get_text(' ', strip=True) if associated else '')
    for option in root.select('button, [role="radio"], input[type="radio"], option, a'):
        if option.name in ('button', 'input', 'option') and option.find_parent(
                lambda node: node.name in ('fieldset', 'select', 'optgroup') and node.has_attr('disabled')):
            option['disabled'] = ''
        label = option.get('data-option-label') or option.get('aria-label') or ''
        if not label and option.get('id'):
            associated = root.find('label', attrs={'for': option['id']})
            label = associated.get_text(' ', strip=True) if associated else ''
        option['data-extractor-label'] = label
        option['data-extractor-tag'] = option.name
    wrapper = soup.new_tag('section', attrs={'data-extraction-root': 'true'})
    wrapper.append(root.extract())
    return str(wrapper), identity


def candidate(html):
    """Only a DOM/CSS presence check, not commercial validation."""
    snippet, identity = prepare(html)
    if not identity['rootFound']:
        return False
    doc = BeautifulSoup(snippet, 'lxml')
    selectors = {field['name']: field['selector'] for field in SCHEMA['fields']}
    return (all(doc.select(selectors[name]) for name in ('prices', 'images', 'groups'))
            and any(doc.select(selectors[name]) for name in ('specs', 'cellSpecs', 'definitions')))


def extract_html(html, data, responses=None):
    from crawl4ai import JsonCssExtractionStrategy
    if len(html.encode('utf-8')) > data['maxHtmlBytes']:
        raise ValueError('CSS_HTML_TOO_LARGE')
    snippet, identity = prepare(html, data.get('_documentUrl'))
    raw = {}
    if snippet:
        results = JsonCssExtractionStrategy(SCHEMA, verbose=False).extract(data['url'], snippet)
        raw = results[0] if len(results) == 1 else {}
    raw['identity'] = identity
    return normalize(raw, data, 'crawl4ai_css')

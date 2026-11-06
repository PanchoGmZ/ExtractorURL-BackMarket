"""Read only semantic product fields directly from the rendered Playwright DOM."""
from .dom_common import EXCLUDE_SELECTOR, ROOT_SELECTOR, SCHEMA, normalize

# This is fixed application code. No page script, JSON payload or selector value
# is evaluated as code. The schema is passed as data to querySelector(All).
READ_DOM = r'''(bodies, profile) => {
    const body = bodies[0];
    const identity = {rootFound:false, semantic:false, ids:[], documentUrl:location.href, evidence:[]};
    if (!body) return {identity};
    if (/just a moment|verify.*human|security verification|captcha/i.test(document.title)) { identity.verificationPage=true; return {identity}; }
    const canonical = document.querySelector('link[rel="canonical"]')?.getAttribute('href') || '';
    try {
        const url = new URL(canonical), match = url.pathname.match(/^\/en-us\/p\/[^/]+\/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\/?$/i);
        if (url.protocol === 'https:' && url.hostname === 'www.backmarket.com' && match) identity.ids.push(match[1]);
    } catch {}
    const excluded = el => el.closest(profile.exclude);
    const candidates = [...body.querySelectorAll(profile.roots)].filter(el => !excluded(el) && [...el.querySelectorAll('h1')].filter(h => !excluded(h) && getComputedStyle(h).display !== 'none' && getComputedStyle(h).visibility !== 'hidden').length === 1);
    const specific = candidates.filter(el => (el.getAttribute('itemtype') || '').endsWith('/Product') || el.dataset.testid === 'product-page' || el.hasAttribute('data-product-id'));
    const possible = specific.length ? specific : candidates.filter(el => el.tagName === 'MAIN' || el.getAttribute('role') === 'main');
    const roots = possible.filter(el => !possible.some(other => other !== el && el.contains(other)));
    if (roots.length !== 1) return {identity};
    const original = roots[0];
    identity.rootFound = true;
    identity.semantic = specific.length > 0;
    identity.evidence = ['single-product-scope','single-h1','rendered-visibility'];
    identity.ids.push(original.getAttribute('data-product-id') || '');
    const root = original.cloneNode(true);
    const originals = [original, ...original.querySelectorAll('*')];
    const clones = [root, ...root.querySelectorAll('*')];
    for (let i=clones.length-1;i>0;i--) {
        const el = originals[i], clone = clones[i], style = getComputedStyle(el);
        const metadata = ['META','LINK'].includes(el.tagName);
        if (el.matches(profile.exclude) || (!metadata && (style.display === 'none' || style.visibility === 'hidden'))) clone.remove();
    }
    for (const el of root.querySelectorAll('[itemtype$="/Product"]')) if (!el.querySelector('h1')) el.remove();
    for (const el of root.querySelectorAll('[itemprop="productID"]')) identity.ids.push(el.getAttribute('content') || el.textContent);
    for (const group of root.querySelectorAll('fieldset,[role="radiogroup"],[data-variant-group],select')) {
        const ids = (group.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
        const label = group.getAttribute('aria-label') || ids.map(id => [...root.querySelectorAll('[id]')].find(el => el.id === id)?.textContent || '').join(' ') || group.querySelector('legend')?.textContent || [...root.querySelectorAll('label[for]')].find(el => el.htmlFor === group.id)?.textContent || '';
        group.setAttribute('data-extractor-label',label);
    }
    for (const el of root.querySelectorAll('button,[role="radio"],input[type="radio"],option,a')) {
        if (['BUTTON','INPUT','OPTION'].includes(el.tagName) && (el.matches(':disabled') || el.closest('fieldset[disabled],select[disabled],optgroup[disabled]'))) el.setAttribute('disabled','');
        const label = el.getAttribute('data-option-label') || el.getAttribute('aria-label') || [...root.querySelectorAll('label[for]')].find(label => label.htmlFor === el.id)?.textContent || '';
        el.setAttribute('data-extractor-label',label);
        el.setAttribute('data-extractor-tag',el.tagName.toLowerCase());
        // Live input properties may differ from serialized attributes after hydration.
        if (el.tagName === 'INPUT') { if (el.checked) el.setAttribute('checked',''); else el.removeAttribute('checked'); }
        if (el.tagName === 'OPTION') { if (el.selected) el.setAttribute('selected',''); else el.removeAttribute('selected'); }
    }
    const wrapper = document.createElement('section'); wrapper.append(root);
    function fields(element, schema) {
        const result = {};
        for (const field of schema) {
            let base = element;
            if (field.source) { base = element.nextElementSibling; if (!base || !base.matches(field.source.slice(1).trim())) continue; }
            if (field.type === 'list' || field.type === 'nested_list') result[field.name] = [...base.querySelectorAll(field.selector)].slice(0,200).map(el => fields(el,field.fields));
            else {
                const node = field.selector === undefined ? base : base.querySelector(field.selector);
                if (node) result[field.name] = field.type === 'attribute' ? node.getAttribute(field.attribute) : node.textContent.trim();
            }
        }
        return result;
    }
    return {...fields(wrapper,profile.schema.fields),identity};
}'''


async def collect(page):
    return await page.locator('body').evaluate_all(READ_DOM, {
        'roots': ROOT_SELECTOR, 'exclude': EXCLUDE_SELECTOR, 'schema': SCHEMA})


def extract_raw(raw, data):
    return normalize(raw, data, 'playwright_dom')

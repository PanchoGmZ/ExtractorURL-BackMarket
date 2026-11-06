"""Product selection and redirection diagnostics without private URL components."""
import re
from urllib.parse import parse_qsl, urlencode, unquote, urljoin, urlsplit, urlunsplit

SELECTION_KEYS = {'l', 'offerType'}
SELECTION_VALUE = re.compile(r'[A-Za-z0-9_.-]{1,100}')


def selection_parameters(url):
    try:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
    except ValueError as error:
        raise ValueError('Query inválida: solo l y offerType, una vez cada uno.') from error
    result = {}
    for name, value in pairs:
        if name not in SELECTION_KEYS or name in result or not SELECTION_VALUE.fullmatch(value):
            raise ValueError('Solo se admiten l y offerType, sin duplicados, vacíos ni valores no compatibles.')
        result[name] = value
    return result


def is_challenge_url(url):
    try:
        return bool(re.match(r'^/(testchallengepage|captcha)(/|$)', unquote(urlsplit(url).path), re.I))
    except ValueError:
        return False


def safe_url(value):
    """Keep commercial l/offerType; remove credentials, fragments and other queries."""
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname:
            return '[URL no HTTP]'
        host = url.hostname
        if ':' in host:
            host = '[' + host + ']'
        if url.port:
            host += ':' + str(url.port)
        path = url.path
        if path.startswith('/cdn-cgi/challenge-platform/'):
            path = '/cdn-cgi/challenge-platform/[recurso]'
        elif is_challenge_url(value):
            path = '/testchallengepage' if 'testchallengepage' in unquote(path).lower() else '/captcha'
        elif not (re.fullmatch(r'/[a-z]{2}-[a-z]{2}/p/[^/]+/[a-f0-9-]{36}/?', path, re.I)
                  or re.fullmatch(r'/(?:product-page/products|reviews/v1/products)/[a-f0-9-]{36}(?:/pickers|/technical_specifications|/reviews)?/?', path, re.I)
                  or re.fullmatch(r'/(?:[a-z]{2}-[a-z]{2}/?)?', path, re.I)):
            path = '/[ruta-secundaria]'
        query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                 if k in SELECTION_KEYS and SELECTION_VALUE.fullmatch(v)]
        return urlunsplit((url.scheme, host, path, urlencode(query), ''))
    except (ValueError, TypeError):
        return '[URL inválida]'


def redirect_details(source, location, status, category, cf_ray=None, stage='navigation'):
    destination = urljoin(source, location)
    return {'sourceUrl': safe_url(source), 'targetUrl': safe_url(destination),
            'httpStatus': status, 'category': category, 'stage': stage,
            'type': 'verification' if is_challenge_url(source) or is_challenge_url(destination) else 'normal', 'cfRay': cf_ray}


def selection_state(requested_url, final_url):
    requested = selection_parameters(requested_url)
    final, ambiguous = {}, False
    # Other server-generated parameters are private and irrelevant to this comparison.
    for name, value in parse_qsl(urlsplit(final_url).query, keep_blank_values=True):
        if name in SELECTION_KEYS:
            if name in final or not SELECTION_VALUE.fullmatch(value):
                ambiguous = True
            else:
                final[name] = value
    preserved = not ambiguous and all(final.get(k) == v for k, v in requested.items())
    return {'requested': requested, 'final': final,
            'status': 'preserved' if requested and preserved else 'not_requested' if not requested else 'changed_or_removed'}

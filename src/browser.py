"""One ordinary Crawl4AI/Playwright navigation. No stealth, API fetches or retries."""
import asyncio
import json
import os
import re
import time
from urllib.parse import urlsplit, urljoin
from lxml import html as lhtml

from .environment import ROOT
from .budget import BROWSER_CLOSE_SECONDS
import psutil
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.browser_manager import BrowserManager
from .diagnostics import chromium_error_code, event, navigation_failure
from .urls import is_challenge_url, redirect_details, safe_url, selection_state


def classify_resource(path, *, is_document, commercial_kind, first_party):
    if is_document:
        return 'document'
    if first_party and commercial_kind:
        return 'commercial'
    if first_party and re.fullmatch(r'/reviews/v1/products/[a-f0-9-]+/reviews/?', path, re.I):
        return 'optional'
    if first_party and path.startswith('/cdn-cgi/challenge-platform/'):
        return 'protection'
    return 'secondary'


def blocking_reason(category, status, explicit_challenge):
    if explicit_challenge and category in ('document', 'commercial', 'protection'):
        return 'ACCESS_CHALLENGE'
    if category in ('document', 'commercial') and status in (401, 403):
        return f'HTTP_{status}'
    return None


def diagnostic_path(path, category):
    # JSD paths may contain opaque challenge tokens. Query strings are never logged.
    if category == 'protection':
        return '/cdn-cgi/challenge-platform/[recurso]'
    if category == 'secondary':
        return '/[recurso-secundario]'
    return path


def inspect_product_html(document, product_id):
    """Return small HTML diagnostics only; never retain/log page text or markup."""
    doc = lhtml.fromstring(document, parser=lhtml.HTMLParser(no_network=True))
    title = doc.xpath('string(//title)')
    body_text = ' '.join(doc.xpath('//body//text()[not(ancestor::script or ancestor::style or ancestor::noscript)]'))
    h1_present = bool(doc.xpath('//h1[normalize-space()]'))
    markers = {
        'price': bool(doc.xpath('//*[@itemprop="price" or @data-testid="product-price" or @data-current-price]')),
        'images': bool(doc.xpath('//*[@itemprop="image"] | //*[@data-testid="product-gallery"]//img | //*[@data-test="product-gallery"]//img')),
        'specifications': bool(doc.xpath('//*[@itemprop="model"] | //*[@data-testid="specifications"] | //table//tr[th]')),
        'variants': bool(doc.xpath('//fieldset | //*[@role="radiogroup"] | //*[@data-variant-group] | //select')),
    }
    return {'titlePresent': bool(title.strip()), 'h1Present': h1_present,
        'requestedProductIdPresent': product_id in document.lower(), 'markers': markers,
        'looksLikeRequestedProduct': bool(h1_present and product_id in document.lower() and markers['price'])}, \
        title, body_text


class NativeBrowserManager(BrowserManager):
    def _build_browser_args(self):
        options = super()._build_browser_args()
        # Crawl4AI includes this even with stealth=False. Keep automation visible.
        options['args'] = [arg for arg in options['args'] if 'AutomationControlled' not in arg]
        return options


def make_crawler(*, proxy_enabled=False, ignore_https_errors=False):
    config = BrowserConfig(headless=os.name != 'nt', browser_type='chromium',
        chrome_channel='chrome' if os.name == 'nt' else 'chromium', ignore_https_errors=ignore_https_errors,
        enable_stealth=False, use_persistent_context=False, verbose=False)
    event('TLS', 'Verificación de certificados HTTPS=%s', 'desactivada' if ignore_https_errors else 'activada')
    # Constructor defaults advertise a different OS/version. Retain native identity instead.
    config.user_agent = None
    config.browser_hint = None
    config.headers = {}
    logger = None
    if proxy_enabled:
        from .proxy import private_proxy_logger
        logger = private_proxy_logger()
    strategy = AsyncPlaywrightCrawlerStrategy(browser_config=config, logger=logger)
    strategy.browser_manager = NativeBrowserManager(config, logger=strategy.logger)
    return AsyncWebCrawler(crawler_strategy=strategy, config=config, logger=logger,
                           verbose=False, base_directory=str(ROOT / '.cache'))


def memory_mb():
    process = psutil.Process()
    rss = private = 0
    for child in [process, *process.children(recursive=True)]:
        try:
            info = child.memory_full_info()
            rss += info.rss
            private += getattr(info, 'uss', getattr(info, 'private', info.rss))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return round(private / 1048576, 1), round(rss / 1048576, 1)


async def acquire(data, *, proxy_config=None, proxy_mode=None):
    method = data.get('extractionMethod', 'nuxt')
    started = time.perf_counter()
    acquisition_deadline = min(time.monotonic() + data['timeoutSeconds'],
                               data.get('_acquisitionDeadline', float('inf')))
    navigation_deadline = acquisition_deadline - BROWSER_CLOSE_SECONDS
    report = {'status': 'running', 'url': safe_url(data['url']), 'navigationCount': 0,
        'extractionMethod': method,
        'directApiRequests': 0, 'requests': [], 'peakPrivateMb': 0, 'peakRssMb': 0,
        'nativeAutomationVisible': None, 'browserClosed': False, 'stage': 'browser-start',
        'resourceErrors': [], 'optionalRequestsSuppressed': 0, 'redirects': [], 'navigationFailures': []}
    responses, tasks = {}, set()
    html_ready = asyncio.Event()
    html_snapshot = ''
    denied_optional = set()
    stop = asyncio.Event()
    if proxy_config is not None:
        crawler = make_crawler(proxy_enabled=True, ignore_https_errors=proxy_mode == 'unblocker')
    else:
        crawler = make_crawler()
    origin = urlsplit(data['url']).hostname
    endpoint = '/product-page/products/' + data['productId']
    paths = {endpoint: 'product', endpoint + '/pickers': 'pickers',
             endpoint + '/technical_specifications': 'specifications'}

    def embedded_commerce_candidate(html):
        if method == 'crawl4ai_css':
            from .dom_crawl4ai import candidate
            return candidate(html)
        if method != 'nuxt':
            return False
        return (data['productId'] in html.lower() and
                all(marker in html for marker in ('selectedOffer', 'pickerGroups', 'specifications')))

    def fail(status, code, message):
        if not stop.is_set():
            report.update(status=status, failureStage=report['stage'], error={'code': code, 'message': message})
            event('PARADA', 'etapa=%s | estado=%s | código=%s | %s',
                  report['stage'], status, code, message, level='error' if status == 'error' else 'warning')
            stop.set()

    def diagnose_failure(message, http_status=None, source='crawl-result'):
        diagnosis = navigation_failure(message, http_status=http_status, source=source)
        result_message_present = diagnosis['errorMessagePresent'] if source == 'crawl-result' else None
        logger_errors = getattr(getattr(crawler, 'logger', None), 'safe_errors', [])
        document_statuses = [item['httpStatus'] for item in report['requests']
                             if item.get('category') == 'document' and type(item.get('httpStatus')) is int]
        effective_http_status = http_status if type(http_status) is int else (document_statuses[-1] if document_statuses else None)
        if effective_http_status != diagnosis['httpStatus']:
            diagnosis = navigation_failure(message, http_status=effective_http_status, source=source)
        generic_codes = {'PLAYWRIGHT_ERROR', 'CRAWL4AI_ERROR', 'NAVIGATION_ERROR_UNCLASSIFIED',
                         'NAVIGATION_ERROR_NO_DETAILS'}
        specific_logger = next((item for item in reversed(logger_errors)
                                if item['code'] not in generic_codes and
                                (item['signals'] or item['httpStatus'] is not None)), None)
        request_failure = next((item for item in reversed(report['navigationFailures'])
                                if item.get('chromiumErrorCode')), None)
        if diagnosis['code'] in generic_codes:
            if specific_logger:
                diagnosis = dict(specific_logger) | {'httpStatus': effective_http_status}
            elif request_failure:
                diagnosis = navigation_failure('Page.goto: net::' + request_failure['chromiumErrorCode'],
                                               http_status=effective_http_status,
                                               source='playwright-requestfailed')
        logger_summary = [{'code': item['code'], 'category': item['category'],
                           'operation': item['operation'], 'signals': item['signals'],
                           'exceptionType': item['exceptionType']} for item in logger_errors]
        diagnosis.update(stage=report['stage'], loggerErrorCount=len(logger_errors),
                         resultErrorMessagePresent=result_message_present,
                         loggerErrors=logger_summary,
                         navigationFailures=report['navigationFailures'][-4:])
        report['navigationError'] = diagnosis
        event('CAUSA_NAVEGACION', 'tipo=%s | código=%s | categoría=%s | operación=%s | señales=%s | excepción=%s | fuente=%s | mensaje presente=%s | errores logger=%s',
              diagnosis['failureType'],
              diagnosis['code'], diagnosis['category'], diagnosis['operation'],
              ','.join(diagnosis['signals']) or '(ninguna)', diagnosis['exceptionType'],
              diagnosis['source'], diagnosis['errorMessagePresent'], diagnosis['loggerErrorCount'], level='warning')
        if logger_summary:
            event('LOGGER_CAUSES', 'errores=%s | diagnósticos=%s', len(logger_summary),
                  ';'.join(item['code'] + ':' + (','.join(item['signals']) or '(sin señal)')
                           for item in logger_summary), level='warning')
        return diagnosis['code']

    def redirect(source, location, status, category, cf_ray=None):
        detail = redirect_details(source, location, status, category, cf_ray, report['stage'])
        report['redirects'].append(detail)
        event('REDIRECCION', 'HTTP %s | tipo=%s | %s -> %s | CF-Ray=%s',
              status if status is not None else '(no disponible)', detail['type'],
              detail['sourceUrl'], detail['targetUrl'], cf_ray or '(no disponible)')
        return detail

    def challenge(source, destination, status, cf_ray, detection):
        if stop.is_set():
            return
        previous_ray = next((r['requestIds']['cf-ray'] for r in reversed(report['requests'])
                             if r.get('category') in ('document', 'commercial')
                             and r.get('requestIds', {}).get('cf-ray')), None)
        report['accessChallenge'] = {
            'sourceUrl': safe_url(source), 'targetUrl': safe_url(destination), 'httpStatus': status,
            'cfRay': cf_ray or previous_ray,
            'cfRaySource': 'current-response' if cf_ray else 'previous-response' if previous_ray else 'unavailable',
            'detection': detection, 'stage': report['stage'],
        }
        event('DESAFIO', 'ACCESS_CHALLENGE | etapa=%s | destino=%s | CF-Ray=%s | origen CF-Ray=%s',
              report['stage'], safe_url(destination), cf_ray or previous_ray or '(no disponible)',
              report['accessChallenge']['cfRaySource'], level='warning')
        fail('blocked', 'ACCESS_CHALLENGE', 'Desafío confirmado; no se sigue ni se resuelve automáticamente.')

    def accept_html(html, source, final_url):
        nonlocal html_snapshot
        documents = [item for item in report['requests'] if item.get('category') == 'document']
        if stop.is_set() or html_ready.is_set() or not documents or documents[-1]['httpStatus'] != 200:
            return False
        if 'text/html' not in documents[-1]['contentType'].lower():
            return False
        size = len(html.encode('utf-8'))
        if size > data['maxHtmlBytes']:
            fail('partial', 'HTML_TOO_LARGE', 'HTML excede el límite; no se procesa.')
            return False
        html_snapshot = html
        report.update(finalUrl=safe_url(final_url), pageSelection=selection_state(data['url'], final_url),
                      htmlReadySeconds=round(time.perf_counter() - started, 3), htmlSource=source)
        event('HTML_LISTO', 'HTML 200 disponible en %.3f s | fuente=%s | bytes=%s | verificación comercial pendiente del parser',
              report['htmlReadySeconds'], source, size)
        html_ready.set()
        return True

    async def read_response(response, record, kind):
        stage = 'response-headers'
        record.setdefault('bodyRead', {}).update(status='pending', bytes=None,
            contentLengthBytes=record.get('contentLengthBytes'), source='playwright-response-body')
        try:
            headers = getattr(response, 'headers', None)
            if not isinstance(headers, dict):
                raise TypeError('response headers unavailable')
            length = headers.get('content-length', '')
            if length.isdigit() and int(length) > data['maxHtmlBytes']:
                event('TOPE_TAMANO', '100%% superado | Content-Length=%s | límite=%s bytes', length, data['maxHtmlBytes'], level='warning')
                fail('partial', 'RESPONSE_TOO_LARGE', f'Content-Length={length} bytes; tope={data["maxHtmlBytes"]} bytes')
                record['bodyRead'].update(status='not-read', contentLengthBytes=int(length))
                return
            record['bodyRead']['contentLengthBytes'] = int(length) if length.isdigit() else None
            stage = 'response-body-access'
            body_method = getattr(response, 'body', None)
            if not callable(body_method):
                raise AttributeError('response body method unavailable')
            stage = 'response-body-read'
            raw = await body_method()
            if isinstance(raw, str):
                raw = raw.encode('utf-8')
            elif isinstance(raw, memoryview):
                raw = raw.tobytes()
            elif isinstance(raw, bytearray):
                raw = bytes(raw)
            if not isinstance(raw, bytes):
                raise TypeError('response body is not bytes')
            record['bodyRead'].update(status='read', bytes=len(raw))
            record['bodyReadSucceeded'] = True
            event('CUERPO', '%s | leído=True | bytes=%s | Content-Length=%s | fuente=Playwright Response.body()',
                  record['kind'], len(raw), record['bodyRead']['contentLengthBytes'] or '(no informado)')
            if len(raw) > data['maxHtmlBytes']:
                event('TOPE_TAMANO', '100%% superado | cuerpo=%s | límite=%s bytes', len(raw), data['maxHtmlBytes'], level='warning')
                fail('partial', 'RESPONSE_TOO_LARGE', f'Cuerpo={len(raw)} bytes; tope={data["maxHtmlBytes"]} bytes')
                return
            record['bytes'] = len(raw)
            usage = 100 * len(raw) / data['maxHtmlBytes']
            event('RESPUESTA', '%s | %s bytes | %.1f%% del tope por respuesta', record['kind'], len(raw), usage)
            if usage >= 75:
                event('TOPE_TAMANO', '%s | umbral=%s%% | uso=%.1f%% | límite=%s bytes',
                      record['kind'], 100 if usage >= 100 else 90 if usage >= 90 else 75,
                      usage, data['maxHtmlBytes'], level='warning')
            if kind:
                stage = 'commercial-json-validation'
                record['validJson'] = False
                if 'application/json' not in record['contentType'].lower():
                    event('JSON', '%s: Content-Type no es JSON; no se usará como respuesta comercial.', kind, level='warning')
                    return
                obj = json.loads(raw)
                record['validJson'] = isinstance(obj, dict)
                shape = {'product': lambda p: isinstance(p.get('titles'), dict) and isinstance(p.get('images'), list),
                         'pickers': lambda p: isinstance(p.get('pickerGroups'), list),
                         'specifications': lambda p: isinstance(p.get('specifications'), list)}
                record['expectedStructure'] = isinstance(obj, dict) and shape[kind](obj)
                event('JSON', '%s | objeto=%s | estructura esperada=%s', kind, record['validJson'], record['expectedStructure'])
                if not record['expectedStructure']:
                    event('RECOMENDACION', '%s: revisar cambios del proveedor antes de cambiar selectores; no se inferirán campos.', kind, level='warning')
                if b'bot-need-challenge' in raw.lower():
                    challenge(response.url, response.url, response.status, response.headers.get('cf-ray'), 'commercial-json-challenge')
                elif response.status == 200 and record['expectedStructure']:
                    responses[kind] = obj
            elif record['category'] == 'document' and 'text/html' in record['contentType'].lower():
                stage = 'html-decode'
                document = raw.decode('utf-8')
                stage = 'html-inspection'
                inspection, title, body_text = inspect_product_html(document, data['productId'])
                record['htmlInspection'] = inspection
                event('HTML_INSPECCION', 'título=%s | H1=%s | UUID solicitado=%s | precio=%s | imágenes=%s | especificaciones=%s | variantes=%s | ficha candidata=%s',
                      inspection['titlePresent'], inspection['h1Present'], inspection['requestedProductIdPresent'],
                      inspection['markers']['price'], inspection['markers']['images'],
                      inspection['markers']['specifications'], inspection['markers']['variants'],
                      inspection['looksLikeRequestedProduct'])
                if re.search(r'just a moment|verify.*human|security verification|captcha', title, re.I):
                    challenge(response.url, response.url, response.status, headers.get('cf-ray'), 'verification-document-title')
                elif re.search(r'bot-need-challenge', body_text, re.I):
                    challenge(response.url, response.url, response.status, headers.get('cf-ray'), 'displayed-challenge-error')
                elif method == 'crawl4ai_css' or embedded_commerce_candidate(document):
                    # CSS selection/validation happens in the selected parser. Do not invoke
                    # its candidate probe from Playwright's response event callback.
                    accept_html(document, 'navigation-response', response.url)
        except Exception as error:
            error_type = type(error).__name__
            read_stage = stage in {'response-headers', 'response-body-access', 'response-body-read'}
            code = ('BODY_READ_ATTRIBUTE_ERROR' if error_type == 'AttributeError' else 'BODY_READ_ERROR') if read_stage else (
                'HTML_INSPECTION_ATTRIBUTE_ERROR' if error_type == 'AttributeError' and stage.startswith('html-') else
                'COMMERCIAL_JSON_ATTRIBUTE_ERROR' if error_type == 'AttributeError' else 'RESPONSE_PROCESSING_ERROR')
            if read_stage:
                record['bodyRead'].update(status='failed', failedAt=stage, errorCode=code)
                record['bodyReadError'] = error_type
            else:
                record['validationError'] = {'stage': stage, 'errorCode': code, 'exceptionType': error_type}
            event('LECTURA', '%s | cuerpo leído=%s | bytes=%s | etapa=%s | código=%s | tipo=%s',
                  record['kind'], record.get('bodyReadSucceeded', False), record['bodyRead'].get('bytes'),
                  stage, code, error_type, level='warning')

    async def before_goto(page, **kwargs):
        report['navigationCount'] += 1
        if report['navigationCount'] != 1:
            raise RuntimeError('Solo una navegación por trabajo')
        identity = await page.evaluate('({webdriver: navigator.webdriver, userAgent: navigator.userAgent})')
        report.update(nativeAutomationVisible=identity['webdriver'] is True, userAgent=identity['userAgent'])
        if identity['webdriver'] is not True:
            raise RuntimeError('Automatización oculta inesperadamente; se detiene antes de navegar')
        report['stage'] = 'navigation'
        event('NAVEGACION', 'Iniciando navegación %s/1 | ruta=%s | automatización visible=%s',
              report['navigationCount'], urlsplit(data['url']).path, report['nativeAutomationVisible'])

        def on_request_failed(request):
            if report['status'] == 'received':
                return  # Closing after a successful HTML handoff deliberately cancels Page.goto.
            try:
                if not request.is_navigation_request() or request.frame != page.main_frame:
                    return
                failure = request.failure
            except Exception:
                return
            code = chromium_error_code(failure)
            record = {'failureType': 'navigation', 'source': 'playwright-requestfailed',
                      'chromiumErrorCode': code}
            report['navigationFailures'].append(record)
            del report['navigationFailures'][:-4]
            event('REQUEST_FAILED', 'documento principal | código=%s', code or 'NAVIGATION_REQUEST_FAILED',
                  level='warning')

        page.on('requestfailed', on_request_failed)

        async def route_navigation(route):
            request = route.request
            url = urlsplit(request.url)
            if (url.hostname, url.path) in denied_optional:
                report['optionalRequestsSuppressed'] += 1
                if report['optionalRequestsSuppressed'] == 1:
                    event('RECURSO_OPCIONAL', 'Se impide repetir el recurso opcional ya denegado; no se consulta de nuevo.', level='warning')
                await route.abort()
                return
            if request.is_navigation_request() and request.frame == page.main_frame:
                if is_challenge_url(request.url):
                    previous = request.redirected_from
                    source = previous.url if previous else data['url']
                    report['stage'] = 'redirect'
                    if not any(r['targetUrl'] == safe_url(request.url) for r in report['redirects']):
                        redirect(source, request.url, None, 'document')
                    challenge(source, request.url, None, None, 'redirect-request')
                    await route.abort()
                    return
                if url.scheme not in {'https', 'http'} or url.hostname != origin:
                    fail('partial', 'UNEXPECTED_REDIRECT', 'Redirección de documento a dominio no permitido')
                    await route.abort()
                    return
            await route.continue_()

        await page.route('**/*', route_navigation)

        def on_response(response):
            url = urlsplit(response.url)
            first_party = url.hostname == origin
            is_document = response.request.is_navigation_request() and response.frame == page.main_frame
            kind = paths.get(url.path) if first_party and method == 'nuxt' else None
            category = classify_resource(url.path, is_document=is_document,
                                         commercial_kind=kind, first_party=first_party)
            if 'firstHttpSeconds' not in report:
                report['firstHttpSeconds'] = round(time.perf_counter() - started, 3)
                event('TIEMPO', 'etapa=firstHttp | desde adquisición=%.3f s', report['firstHttpSeconds'])
            if is_document and 'documentHttpSeconds' not in report:
                report['documentHttpSeconds'] = round(time.perf_counter() - started, 3)
                event('TIEMPO', 'etapa=documentHttp | desde adquisición=%.3f s | HTTP=%s',
                      report['documentHttpSeconds'], response.status)
            protection_resource = category == 'protection'
            location = response.headers.get('location')
            redirected = response.status in (301, 302, 303, 307, 308) and bool(location)
            destination = urljoin(response.url, location) if redirected else response.url
            cf_ray = response.headers.get('cf-ray')
            if redirected:
                redirect(response.url, location, response.status, category, cf_ray)
            challenge_page = bool(first_party and (response.headers.get('cf-mitigated', '').lower() == 'challenge'
                or (category in ('document', 'commercial') and (is_challenge_url(response.url) or is_challenge_url(destination)))))
            path = diagnostic_path(urlsplit(safe_url(response.url)).path, category)
            reason = blocking_reason(category, response.status, challenge_page)
            if reason == 'ACCESS_CHALLENGE':
                if redirected:
                    report['stage'] = 'redirect'
                challenge(response.url, destination, response.status, cf_ray,
                          'redirect-location' if redirected else 'response-header-or-verification-url')
            elif reason:
                fail('blocked', reason, path)
            elif protection_resource:
                # A resource name alone does not establish a blocked response or an interstitial.
                event('ACCESO', 'Recurso de protección observado; la ficha no se clasifica como bloqueada solo por esta ruta.')
            if kind or is_document or protection_resource or challenge_page or response.status >= 400:
                raw_content_length = response.headers.get('content-length', '')
                record = {'url': safe_url(response.url), 'httpStatus': response.status,
                    'contentType': response.headers.get('content-type', ''),
                    'kind': kind or category, 'category': category,
                    'required': category in ('document', 'commercial'), 'explicitChallenge': challenge_page,
                    'contentLengthBytes': int(raw_content_length) if raw_content_length.isdigit() else None,
                    'bodyRead': {'status': 'not-requested', 'bytes': None,
                                 'contentLengthBytes': int(raw_content_length) if raw_content_length.isdigit() else None,
                                 'source': 'rendered-dom' if method == 'playwright_dom' else None},
                    'requestIds': {key: response.headers[key] for key in ('cf-ray', 'x-request-id') if key in response.headers}}
                report['requests'].append(record)
                document_length = response.headers.get('content-length', '')
                if is_document and document_length.isdigit() and int(document_length) > data['maxHtmlBytes']:
                    fail('partial', 'RESPONSE_TOO_LARGE', 'Content-Length del documento excede el límite.')
                event('HTTP', '%s | estado=%s | Content-Type=%s | ruta=%s | CF-Ray=%s',
                      record['kind'], response.status, record['contentType'] or '(ausente)',
                      path, record['requestIds'].get('cf-ray', '(ausente)'))
                if is_document and proxy_config is not None and response.status == 407:
                    fail('error', 'PROXY_AUTH_ERROR', 'El proxy rechazó la autenticación; no se reintenta.')
                if response.status >= 400 and not reason and not (is_document and proxy_config is not None and response.status == 407):
                    report['resourceErrors'].append(record.copy())
                    event('RECURSO_OPCIONAL' if category == 'optional' else 'RECURSO_SECUNDARIO',
                          'HTTP %s | categoría=%s | se conserva el documento comercial permitido.',
                          response.status, category, level='warning')
                    if category == 'optional':
                        denied_optional.add((url.hostname, url.path))
                if (kind or (is_document and method != 'playwright_dom')) and response.status == 200 and not challenge_page:
                    record['bodyRead']['status'] = 'scheduled'
                    if record['bodyRead']['source'] is None:
                        record['bodyRead']['source'] = 'playwright-response-body'
                    task = asyncio.create_task(read_response(response, record, kind))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)

        page.on('response', on_response)
        return page

    async def before_return(page, html, **kwargs):
        report['stage'] = 'html-read'
        event('HTML', 'Documento renderizado recibido; comprobando verificación y tamaño.')
        title = await page.title()
        if re.search(r'just a moment|verify.*human|security verification|captcha', title, re.I):
            challenge(data['url'], page.url, None, None, 'verification-document-title')
        # Optional errors serialized inside Nuxt/scripts are not a challenge page.
        # Inspect displayed document text rather than searching all embedded state.
        visible_text = await page.evaluate("document.body ? document.body.innerText : ''")
        if re.search(r'bot-need-challenge', visible_text, re.I):
            challenge(data['url'], page.url, None, None, 'displayed-challenge-error')
        if method == 'playwright_dom' and not stop.is_set() and '_dom' not in responses:
            dom_started = time.perf_counter()
            try:
                from .dom_playwright import collect
                responses['_dom'] = await collect(page)
            except Exception:
                report['parserError'] = {'code': 'DOM_READ_ERROR', 'stage': 'rendered-dom-read'}
            finally:
                report['domReadSeconds'] = round(time.perf_counter() - dom_started, 3)
                event('TIEMPO', 'etapa=domRead | duración=%.3f s', report['domReadSeconds'])
        report['finalUrl'] = safe_url(page.url)
        report['pageSelection'] = selection_state(data['url'], page.url)
        event('SELECCION_URL', 'Estado=%s | solicitada=%s | final=%s', report['pageSelection']['status'],
              report['pageSelection']['requested'], report['pageSelection']['final'])
        accepted = accept_html(html, 'browser-dom-hook', page.url)
        # Handoff to acquire(): keep Crawl4AI from starting Markdown/scraping
        # while the owner drains already received JSON and closes this context.
        if accepted or html_ready.is_set():
            await stop.wait()
        return page

    async def after_goto(page, **kwargs):
        # A complete embedded commerce payload needs no image/body-visibility or
        # Markdown processing. The existing parser remains the source of truth.
        html = await page.content()
        if method == 'playwright_dom' or embedded_commerce_candidate(html):
            await before_return(page, html)

    crawler.crawler_strategy.set_hook('before_goto', before_goto)
    crawler.crawler_strategy.set_hook('after_goto', after_goto)
    crawler.crawler_strategy.set_hook('before_return_html', before_return)

    async def monitor():
        warned = set()
        next_progress = 5
        while True:
            private, rss = memory_mb()
            elapsed = time.perf_counter() - started
            report['peakPrivateMb'] = max(report['peakPrivateMb'], private)
            report['peakRssMb'] = max(report['peakRssMb'], rss)
            for resource, used, limit, unit in (
                ('TIEMPO', elapsed, data['timeoutSeconds'], 's'),
                ('MEMORIA', private, data['maxMemoryMb'], 'MiB privados'),
            ):
                for threshold in (75, 90):
                    if used >= limit * threshold / 100 and (resource, threshold) not in warned:
                        warned.add((resource, threshold))
                        event('TOPE_' + resource, 'umbral=%s%% | usado=%.1f/%s %s | etapa=%s',
                              threshold, used, limit, unit, report['stage'], level='warning')
            if elapsed >= next_progress:
                event('PROGRESO', 'etapa=%s | %.1f/%s s | memoria privada=%.1f/%s MiB | HTTP observados=%s',
                      report['stage'], elapsed, data['timeoutSeconds'], private, data['maxMemoryMb'], len(report['requests']))
                next_progress = elapsed + 5
            if private > data['maxMemoryMb']:
                event('TOPE_MEMORIA', '100%% superado | privado=%.1f/%s MiB', private, data['maxMemoryMb'], level='warning')
                fail('partial', 'MEMORY_LIMIT', f'Memoria privada={private} MiB; tope={data["maxMemoryMb"]} MiB')
                return
            await asyncio.sleep(1)

    async def navigate():
        event('NAVEGADOR', 'Abriendo Chrome visible en Windows o Chromium headless en contenedor; perfil temporal nuevo.')
        await crawler.start()
        report['browserStartupSeconds'] = round(time.perf_counter() - started, 3)
        event('NAVEGADOR', 'Inicio completado en %.3f s.', report['browserStartupSeconds'])
        event('TIEMPO', 'etapa=browserStartup | duración=%.3f s', report['browserStartupSeconds'])
        report['stage'] = 'crawl-call'
        call_started = time.perf_counter()
        result = await crawler.arun(data['url'], config=CrawlerRunConfig(
            cache_mode=CacheMode.DISABLED, wait_until='domcontentloaded',
            page_timeout=max(1, int((navigation_deadline - time.monotonic()) * 1000)),
            delay_before_return_html=0, wait_for_images=False, scan_full_page=False,
            ignore_body_visibility=True, body_visibility_timeout=1,
            max_retries=0, magic=False, simulate_user=False,
            override_navigator=False, verbose=False, proxy_config=proxy_config))
        report['crawlCallSeconds'] = round(time.perf_counter() - call_started, 6)
        event('CRAWL_RESULT', 'success=%s | HTTP=%s | arun=%.6f s | before_goto=%s | respuestas registradas=%s',
              result.success, result.status_code, report['crawlCallSeconds'], report['navigationCount'], len(report['requests']))
        if tasks:
            await asyncio.wait(list(tasks), timeout=min(0.2, max(0, navigation_deadline - time.monotonic())))
        if not result.success or result.status_code != 200:
            failure_code = diagnose_failure(getattr(result, 'error_message', None), result.status_code)
            documents = [r for r in report['requests'] if r.get('category') == 'document']
            prior_document = documents[-1] if documents else None
            fallback_html = getattr(result, 'html', None)
            if (method == 'crawl4ai_css' and not stop.is_set() and prior_document
                    and prior_document.get('httpStatus') == 200
                    and 'text/html' in prior_document.get('contentType', '').lower()
                    and isinstance(fallback_html, str) and fallback_html):
                fallback_size = len(fallback_html.encode('utf-8'))
                prior_document['crawl4aiHtmlFallback'] = {'available': True, 'bytes': fallback_size}
                event('CRAWL_HTML_FALLBACK', 'Crawl4AI reportó fallo, pero conserva HTML del documento HTTP 200 | bytes=%s | se valida con el parser CSS.', fallback_size, level='warning')
                if fallback_size <= data['maxHtmlBytes']:
                    try:
                        inspection, title, visible_text = inspect_product_html(fallback_html, data['productId'])
                        prior_document['htmlInspection'] = inspection
                        if (re.search(r'just a moment|verify.*human|security verification|captcha', title, re.I)
                                or re.search(r'bot-need-challenge', visible_text, re.I)):
                            challenge(prior_document.get('url', data['url']), prior_document.get('url', data['url']),
                                      prior_document['httpStatus'], prior_document.get('requestIds', {}).get('cf-ray'),
                                      'crawl4ai-result-verification-html')
                            return ''
                        accepted = accept_html(fallback_html, 'crawl4ai-result-html', prior_document.get('url', data['url']))
                        if accepted:
                            event('HTML_INSPECCION', 'título=%s | H1=%s | UUID solicitado=%s | ficha candidata=%s',
                                  inspection['titlePresent'], inspection['h1Present'],
                                  inspection['requestedProductIdPresent'], inspection['looksLikeRequestedProduct'])
                            report['stage'] = 'html-ready'
                            return fallback_html
                    except Exception as error:
                        prior_document['fallbackInspectionError'] = {
                            'errorCode': 'HTML_INSPECTION_ATTRIBUTE_ERROR' if type(error).__name__ == 'AttributeError' else 'HTML_INSPECTION_ERROR',
                            'exceptionType': type(error).__name__}
                        event('CRAWL_HTML_FALLBACK', 'HTML retenido no pudo inspeccionarse | código=%s | tipo=%s',
                              prior_document['fallbackInspectionError']['errorCode'], type(error).__name__, level='warning')
                else:
                    fail('partial', 'HTML_TOO_LARGE', f'HTML de Crawl4AI={fallback_size} bytes; tope={data["maxHtmlBytes"]} bytes')
                    return ''
            if result.status_code == 403:
                fail('blocked', 'HTTP_403', 'Documento principal denegado; no se reintenta.')
            else:
                fail('partial' if failure_code.startswith('NAVIGATION_ERROR') else 'error', failure_code,
                     'Falló Crawl4AI; consultar navigationError. Detalle privado omitido; sin reintentos.')
            return ''
        if len(result.html.encode()) > data['maxHtmlBytes']:
            event('TOPE_TAMANO', '100%% superado en HTML renderizado | límite=%s bytes', data['maxHtmlBytes'], level='warning')
            fail('partial', 'HTML_TOO_LARGE', f'HTML renderizado={len(result.html.encode())} bytes; tope={data["maxHtmlBytes"]} bytes')
            return ''
        documents = [r for r in report['requests'] if r['kind'] == 'document']
        if not documents or 'text/html' not in documents[-1]['contentType'].lower():
            fail('partial', 'INVALID_CONTENT_TYPE', 'Documento no es HTML')
            return ''
        event('HTML', 'HTML disponible | %s bytes | JSON comerciales válidos=%s', len(result.html.encode()), ', '.join(sorted(responses)) or 'ninguno')
        report['stage'] = 'html-ready'
        return result.html

    monitor_task = asyncio.create_task(monitor())
    work = asyncio.create_task(navigate())
    stopper = asyncio.create_task(stop.wait())
    ready = asyncio.create_task(html_ready.wait())
    document = ''
    try:
        done, _ = await asyncio.wait([work, stopper, ready],
            timeout=max(0, navigation_deadline - time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
        if not done:
            event('TOPE_TIEMPO', '100%% alcanzado | presupuesto=%s s', data['timeoutSeconds'], level='warning')
            fail('partial', 'TIMEOUT', f'Presupuesto total agotado: {data["timeoutSeconds"]} s')
        elif html_ready.is_set() and not stop.is_set():
            if tasks:
                available = max(0, navigation_deadline - time.monotonic())
                response_wait = (min(0.2, available)
                                 if method != 'nuxt' or embedded_commerce_candidate(html_snapshot)
                                 else available)
                await asyncio.wait(list(tasks), timeout=response_wait)
            if not stop.is_set():
                document = html_snapshot
                report['status'] = 'received'
                report['crawlCallSeconds'] = round(time.perf_counter() - started - report['browserStartupSeconds'], 6)
                event('CRAWL_RESULT', 'HTML recibido por hook | arun postprocesamiento omitido | before_goto=%s', report['navigationCount'])
        elif work in done:
            document = await work
            if not stop.is_set():
                report['status'] = 'received'
    except Exception as error:
        code = diagnose_failure(error, source='exception')
        fail('error', code, 'Excepción de adquisición; consultar navigationError. Detalle privado omitido; sin reintentos.')
    finally:
        report['stage'] = 'browser-close'
        close_started = time.perf_counter()
        event('CIERRE', 'Cerrando navegador y tareas pendientes.')
        for task in [work, stopper, ready, monitor_task, *list(tasks)]:
            if not task.done():
                task.cancel()
        # Close the browser before joining canceled navigation/response reads; otherwise
        # cancellation can wait on a socket that only browser.close() releases.
        try:
            await asyncio.wait_for(crawler.close(), timeout=max(0.001, min(BROWSER_CLOSE_SECONDS,
                                                                        acquisition_deadline - time.monotonic())))
            report['browserClosed'] = True
            event('CIERRE', 'Navegador cerrado correctamente.')
        except Exception as error:
            report['closeError'] = type(error).__name__
            event('CIERRE', 'No se confirmó cierre completo | %s. Revisa la ventana abierta por esta ejecución.',
                  type(error).__name__, level='warning')
        await asyncio.wait([work, stopper, ready, monitor_task, *list(tasks)],
                           timeout=min(0.1, max(0, acquisition_deadline - time.monotonic())))
        report['closeSeconds'] = round(time.perf_counter() - close_started, 3)
        event('TIEMPO', 'etapa=browserClose | duración=%.3f s', report['closeSeconds'])
        report['seconds'] = round(time.perf_counter() - started, 3)
        event('RECURSOS', 'duración=%.3f s | pico privado=%.1f/%s MiB | RSS sumado=%.1f MiB (puede contar memoria compartida varias veces)',
              report['seconds'], report['peakPrivateMb'], data['maxMemoryMb'], report['peakRssMb'])
        event('ADQUISICION', 'estado=%s | navegaciones=%s | GET directos=%s | navegador cerrado=%s',
              report['status'], report['navigationCount'], report['directApiRequests'], report['browserClosed'])
        event('RECURSOS_OPCIONALES', 'Errores secundarios=%s | repeticiones opcionales impedidas=%s',
              len(report['resourceErrors']), report['optionalRequestsSuppressed'])
        if not document and html_snapshot:
            document = html_snapshot
            report['candidateBeforeAccessFailure'] = True
    return document, responses, report

"""One ordinary Crawl4AI/Playwright navigation. No stealth, API fetches or retries."""
import asyncio
import json
import os
import re
import time
from urllib.parse import urlsplit, urljoin

from .environment import ROOT
import psutil
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.async_crawler_strategy import AsyncPlaywrightCrawlerStrategy
from crawl4ai.browser_manager import BrowserManager
from .diagnostics import event
from .urls import is_challenge_url, redirect_details, safe_url, selection_state
from .proxy import proxy_failure_code


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


class NativeBrowserManager(BrowserManager):
    def _build_browser_args(self):
        options = super()._build_browser_args()
        # Crawl4AI includes this even with stealth=False. Keep automation visible.
        options['args'] = [arg for arg in options['args'] if 'AutomationControlled' not in arg]
        return options


def make_crawler(*, proxy_enabled=False):
    config = BrowserConfig(headless=os.name != 'nt', browser_type='chromium',
        chrome_channel='chrome' if os.name == 'nt' else 'chromium', ignore_https_errors=False,
        enable_stealth=False, use_persistent_context=False, verbose=False)
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


async def acquire(data, *, proxy_config=None):
    started = time.perf_counter()
    report = {'status': 'running', 'url': safe_url(data['url']), 'navigationCount': 0,
        'directApiRequests': 0, 'requests': [], 'peakPrivateMb': 0, 'peakRssMb': 0,
        'nativeAutomationVisible': None, 'browserClosed': False, 'stage': 'browser-start',
        'resourceErrors': [], 'optionalRequestsSuppressed': 0, 'redirects': []}
    responses, tasks = {}, set()
    denied_optional = set()
    stop = asyncio.Event()
    crawler = make_crawler(proxy_enabled=True) if proxy_config is not None else make_crawler()
    origin = urlsplit(data['url']).hostname
    endpoint = '/product-page/products/' + data['productId']
    paths = {endpoint: 'product', endpoint + '/pickers': 'pickers',
             endpoint + '/technical_specifications': 'specifications'}

    def fail(status, code, message):
        if not stop.is_set():
            report.update(status=status, failureStage=report['stage'], error={'code': code, 'message': message})
            event('PARADA', 'etapa=%s | estado=%s | código=%s | %s',
                  report['stage'], status, code, message, level='error' if status == 'error' else 'warning')
            stop.set()

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

    async def read_response(response, record, kind):
        try:
            length = response.headers.get('content-length', '')
            if length.isdigit() and int(length) > data['maxHtmlBytes']:
                event('TOPE_TAMANO', '100%% superado | Content-Length=%s | límite=%s bytes', length, data['maxHtmlBytes'], level='warning')
                fail('partial', 'RESPONSE_TOO_LARGE', f'Content-Length={length} bytes; tope={data["maxHtmlBytes"]} bytes')
                return
            raw = await response.body()
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
        except Exception as error:
            record['bodyReadError'] = type(error).__name__
            event('LECTURA', '%s | no se pudo leer/validar el cuerpo | %s', record['kind'], type(error).__name__, level='warning')

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
            kind = paths.get(url.path) if first_party else None
            category = classify_resource(url.path, is_document=is_document,
                                         commercial_kind=kind, first_party=first_party)
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
                record = {'url': safe_url(response.url), 'httpStatus': response.status,
                    'contentType': response.headers.get('content-type', ''),
                    'kind': kind or category, 'category': category,
                    'required': category in ('document', 'commercial'), 'explicitChallenge': challenge_page,
                    'requestIds': {key: response.headers[key] for key in ('cf-ray', 'x-request-id') if key in response.headers}}
                report['requests'].append(record)
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
                if (kind or is_document) and response.status == 200 and not challenge_page:
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
        report['finalUrl'] = safe_url(page.url)
        report['pageSelection'] = selection_state(data['url'], page.url)
        event('SELECCION_URL', 'Estado=%s | solicitada=%s | final=%s', report['pageSelection']['status'],
              report['pageSelection']['requested'], report['pageSelection']['final'])
        return page

    crawler.crawler_strategy.set_hook('before_goto', before_goto)
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
            await asyncio.sleep(0.2)

    async def navigate():
        event('NAVEGADOR', 'Abriendo Chrome visible en Windows o Chromium headless en contenedor; perfil temporal nuevo.')
        await crawler.start()
        report['browserStartupSeconds'] = round(time.perf_counter() - started, 3)
        event('NAVEGADOR', 'Inicio completado en %.3f s.', report['browserStartupSeconds'])
        result = await crawler.arun(data['url'], config=CrawlerRunConfig(
            cache_mode=CacheMode.DISABLED, wait_until='domcontentloaded', page_timeout=data['timeoutSeconds'] * 1000,
            delay_before_return_html=0.5, max_retries=0, magic=False, simulate_user=False,
            override_navigator=False, verbose=False, proxy_config=proxy_config))
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        if not result.success or result.status_code != 200:
            transport_error = proxy_failure_code(getattr(result, 'error_message', None), result.status_code) if proxy_config is not None else None
            if result.status_code == 403:
                fail('blocked', 'HTTP_403', 'Documento principal denegado; no se reintenta.')
            elif transport_error:
                fail('error', transport_error, 'Fallo de transporte proxy; detalle privado omitido y sin reintentos.')
            else:
                fail('partial', 'NAVIGATION_ERROR', f'HTML HTTP {result.status_code}; Crawl4AI success={result.success}')
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
    document = ''
    try:
        done, _ = await asyncio.wait([work, stopper], timeout=data['timeoutSeconds'], return_when=asyncio.FIRST_COMPLETED)
        if not done:
            event('TOPE_TIEMPO', '100%% alcanzado | presupuesto=%s s', data['timeoutSeconds'], level='warning')
            fail('partial', 'TIMEOUT', f'Presupuesto total agotado: {data["timeoutSeconds"]} s')
        elif work in done:
            document = await work
            if not stop.is_set():
                report['status'] = 'received'
    except Exception as error:
        message = re.sub(r'https?://[^\s\'"<>]+', lambda match: safe_url(match.group(0)), str(error))[:300]
        code = 'BROWSER_UNAVAILABLE' if "Executable doesn't exist" in str(error) else type(error).__name__
        if 'ERR_CERT' in str(error):
            code = 'TLS_ERROR'
        if proxy_config is not None:
            # Playwright errors may serialize proxy settings outside a URL.
            message = 'Fallo de navegador con proxy; detalle privado omitido. Tipo=' + type(error).__name__
            code = proxy_failure_code(error) or code
        fail('error', code, message)
    finally:
        report['stage'] = 'browser-close'
        event('CIERRE', 'Cerrando navegador y tareas pendientes.')
        for task in [work, stopper, monitor_task, *list(tasks)]:
            if not task.done():
                task.cancel()
        # Close the browser before joining canceled navigation/response reads; otherwise
        # cancellation can wait on a socket that only browser.close() releases.
        try:
            await asyncio.wait_for(crawler.close(), timeout=5)
            report['browserClosed'] = True
            event('CIERRE', 'Navegador cerrado correctamente.')
        except Exception as error:
            report['closeError'] = type(error).__name__
            event('CIERRE', 'No se confirmó cierre completo | %s. Revisa la ventana abierta por esta ejecución.',
                  type(error).__name__, level='warning')
        await asyncio.wait([work, stopper, monitor_task, *list(tasks)], timeout=2)
        report['seconds'] = round(time.perf_counter() - started, 3)
        event('RECURSOS', 'duración=%.3f s | pico privado=%.1f/%s MiB | RSS sumado=%.1f MiB (puede contar memoria compartida varias veces)',
              report['seconds'], report['peakPrivateMb'], data['maxMemoryMb'], report['peakRssMb'])
        event('ADQUISICION', 'estado=%s | navegaciones=%s | GET directos=%s | navegador cerrado=%s',
              report['status'], report['navigationCount'], report['directApiRequests'], report['browserClosed'])
        event('RECURSOS_OPCIONALES', 'Errores secundarios=%s | repeticiones opcionales impedidas=%s',
              len(report['resourceErrors']), report['optionalRequestsSuppressed'])
    return document, responses, report

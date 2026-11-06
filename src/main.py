"""Run with F5 or .venv/Scripts/python -m src --input <INPUT.json>."""
from .budget import (ACQUISITION_SECONDS, PERSIST_SECONDS, TOTAL_SECONDS, WORK_SECONDS,
                     arm_process_watchdog, bounded, process_budget)

if __name__ == '__main__':
    arm_process_watchdog()

import argparse
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .environment import ROOT
from apify import Actor, Event
from jsonschema import Draft7Validator, FormatChecker

from .extract import extract_html, parse_input
from .diagnostics import advice, event
from .urls import selection_parameters
from .proxy import ProxyConfigurationError, configure_proxy, proxy_observation, split_proxy_input


def save(path, data):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def local_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError('La ruta debe permanecer dentro de este proyecto')
    return path


def archive_previous_output(root, run_id):
    root = Path(root).resolve()
    if not run_id or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in run_id):
        raise ValueError('Identificador de ejecución inválido')
    source = root / 'output/product.json'
    target = root / 'output/history' / (run_id + '-previous-product.json')
    if not source.exists():
        return None
    if not source.resolve().is_relative_to(root) or not target.resolve().is_relative_to(root):
        raise ValueError('La salida debe permanecer dentro del proyecto')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError('No se sobrescribe el historial de productos')
    source.replace(target)
    return target.relative_to(root).as_posix()


async def run(input_path=None, *, budget=None):
    budget = budget or process_budget()
    try:
        async with asyncio.timeout(budget.remaining(WORK_SECONDS)):
            await _run(input_path, budget)
    except TimeoutError:
        event('TOPE_PROCESO', 'RUN_TIME_LIMIT | etapa de inicio/cierre | límite de trabajo=%s s', WORK_SECONDS, level='error')
        try:
            state = json.loads((ROOT / 'reports/run.json').read_text(encoding='utf-8'))
            state.update(status='error', finishedAt=now(), logicalExitCode=1,
                         error={'code': 'RUN_TIME_LIMIT', 'message': 'Plazo global agotado durante inicio/cierre.'})
            save('reports/run.json', state)
        except (OSError, ValueError):
            pass
        return 1


async def _run(input_path, budget):
    started = time.perf_counter()
    # F5 uses an Actor instance that closes normally instead of raising SystemExit
    # into the debugger. CLI and platform runs retain the SDK's normal exit code.
    debugging_f5 = os.environ.get('BACKMARKET_F5') == '1' and os.environ.get('APIFY_IS_AT_HOME') != '1'
    actor = Actor(exit_process=False) if debugging_f5 else Actor
    state = {'source': 'backmarket', 'status': 'running', 'startedAt': now(),
             'executionMode': None, 'extractionPerformed': False, 'savedResults': 0, 'stage': 'output-prepare',
             'runId': uuid.uuid4().hex, 'outputFile': None, 'timings': {},
             'timeBudget': {'processSeconds': TOTAL_SECONDS, 'acquisitionSeconds': ACQUISITION_SECONDS,
                            'workSeconds': WORK_SECONDS}}

    def timing(name, begin, prior=0):
        state['timings'][name] = round(time.perf_counter() - begin + prior, 3)
        event('TIEMPO', 'etapa=%s | duración=%.3f s | desde proceso=%.3f s',
              name, state['timings'][name], budget.elapsed())
    metadata = {'productId': None, 'url': None, 'currency': None, 'variantGroups': []}
    retained_failure = None
    exit_code = 0
    previous_output = archive_previous_output(ROOT, state['runId'])
    if previous_output:
        state['previousOutputFile'] = previous_output
    # Invalidate the previous run before SDK initialization or input validation can fail.
    state['stage'] = 'actor-init'
    save('reports/run.json', state)
    async with actor:
        event('INICIO', 'Back Market | una ficha | sin reintentos automáticos')
        state['timings']['initialization'] = round(budget.elapsed(), 3)
        event('TIEMPO', 'etapa=initialization | desde proceso=%.3f s', budget.elapsed())
        task = asyncio.current_task()
        work_finished = asyncio.Event()
        abort_requested = False

        async def abort(_=None):
            nonlocal abort_requested
            if abort_requested:
                return
            abort_requested = True
            event('ABORTANDO', 'Guardando estado y cerrando la ejecución.', level='warning')
            state.update(status='aborted', finishedAt=now())
            await actor.set_value('RUN_STATE', state)
            save('reports/run.json', state)
            task.cancel()
            # Let acquisition close its browser and the run persist final diagnostics
            # before closing the SDK. Do not race SDK shutdown against set_value().
            try:
                await asyncio.wait_for(work_finished.wait(), timeout=min(3, max(0.001, budget.remaining(PERSIST_SECONDS))))
            except TimeoutError:
                event('ABORTANDO', 'Tiempo de cierre agotado; el SDK completará la terminación.', level='warning')
            await actor.exit(exit_code=143, event_listeners_timeout=timedelta(seconds=0.25), cleanup_timeout=timedelta(seconds=1))

        actor.on(Event.ABORTING, abort)
        try:
            if previous_output:
                event('SALIDA_ANTERIOR', 'Conservada en %s; no es resultado de esta ejecución.', previous_output)
            await actor.set_value('RUN_STATE', state)
            state['stage'] = 'input'
            if input_path:
                if os.environ.get('APIFY_IS_AT_HOME') == '1':
                    raise ValueError('--input solo se permite en ejecución local')
                event('INPUT', 'Leyendo %s', input_path)
                raw = json.loads(local_path(input_path).read_text(encoding='utf-8-sig'))
                await actor.set_value(actor.configuration.input_key, raw)
            else:
                event('INPUT', 'Leyendo entrada con Actor.get_input().')
            product_input, proxy_settings = split_proxy_input(await actor.get_input())
            data = parse_input(product_input)
            state['extractionMethod'] = metadata['extractionMethod'] = data['extractionMethod']
            event('METODO', 'extractionMethod=%s', data['extractionMethod'])
            state['proxy'] = proxy_settings.diagnostic('pending' if proxy_settings.enabled else 'disabled')
            state.update(url=data['url'], productId=data['productId'], executionMode=data['executionMode'])
            metadata.update(productId=data['productId'], url=data['url'])
            event('INPUT', 'Válido | modo=%s | UUID=%s', data['executionMode'], data['productId'])
            event('SELECCION_URL', 'Parámetros solicitados=%s; se envía la URL completa sin convertir l/offerType a otros filtros.',
                  selection_parameters(data['url']))
            data['_acquisitionDeadline'] = time.monotonic() + min(data['timeoutSeconds'], budget.remaining(ACQUISITION_SECONDS))
            event('LIMITES', 'Adquisición=%s s (máx. 48, plazo compartido desde proceso) | respuesta=%s bytes (%.2f MiB; máx. 8) | memoria privada=%s MiB (máx. 2048)',
                  data['timeoutSeconds'], data['maxHtmlBytes'], data['maxHtmlBytes'] / 1048576, data['maxMemoryMb'])
            event('LIMITES', 'Proceso=55 s | adquisición hasta segundo 48 incluyendo cierre | trabajo hasta 52 s | una navegación, concurrencia=1.')
            if data['executionMode'] == 'offline':
                if data['extractionMethod'] == 'playwright_dom':
                    state.update(status='error', error={'code': 'DOM_LIVE_PAGE_REQUIRED',
                        'message': 'Playwright DOM requiere una página renderizada; no se sustituye por CSS/Nuxt en offline.'})
                    raise RuntimeError('No hay página renderizada en modo offline')
                state['proxy'] = proxy_settings.diagnostic('not-used-offline')
                state['stage'] = 'offline-read'
                path = local_path(data['htmlFile'])
                event('OFFLINE', 'Leyendo %s | no se abrirá navegador ni se consultará la red.', data['htmlFile'])
                if not path.is_file():
                    state.update(status='partial', error={'code': 'CAPTURES_MISSING',
                        'message': 'Falta la captura auténtica local; no se simulan datos ni se consulta la red.'})
                    raise FileNotFoundError('Captura HTML no disponible')
                if path.stat().st_size > data['maxHtmlBytes']:
                    state.update(status='partial', error={'code': 'HTML_TOO_LARGE',
                        'message': f'Captura: {path.stat().st_size} bytes; tope: {data["maxHtmlBytes"]} bytes'})
                    raise ValueError('Captura excede maxHtmlBytes')
                document = path.read_text(encoding='utf-8')
                responses = {}
                state.update(acquisition={'source': 'local-file', 'networkRequests': 0})
            else:
                state['stage'] = 'access-check'
                marker = ROOT / 'reports/access-blocked.json'
                if marker.exists() and os.environ.get('APIFY_IS_AT_HOME') != '1':
                    event('ACCESO', 'Parada previa registrada en reports/access-blocked.json; no se inicia Chrome.', level='warning')
                    try:
                        previous = json.loads(marker.read_text(encoding='utf-8'))
                        previous_error = previous.get('error') or {}
                        event('ACCESO', 'Causa anterior: %s | ficha=%s', previous_error.get('code'), previous.get('productId'), level='warning')
                        prior_acquisition = previous.get('acquisition') or {}
                        prior_challenge = prior_acquisition.get('accessChallenge') or {}
                        prior_ray = prior_challenge.get('cfRay') or next((request.get('requestIds', {}).get('cf-ray')
                            for request in reversed(prior_acquisition.get('requests', []))
                            if request.get('category') in ('document', 'commercial') and request.get('requestIds', {}).get('cf-ray')), None)
                        state['acquisition'] = {'status': 'blocked', 'source': 'prior-diagnostic',
                            'navigationCount': 0, 'directApiRequests': 0, 'requests': [],
                            'priorFailureStage': prior_acquisition.get('failureStage'), 'priorCfRay': prior_ray}
                        event('ACCESO', 'Etapa anterior=%s | CF-Ray anterior=%s; no corresponde a una solicitud nueva.',
                              prior_acquisition.get('failureStage'), prior_ray or '(no disponible)', level='warning')
                        if '/cdn-cgi/challenge-platform/' in str(previous_error.get('message', '')):
                            event('ACCESO', 'Ese recurso de protección por sí solo no confirma HTTP 403 ni CAPTCHA.', level='warning')
                    except (OSError, ValueError, AttributeError):
                        event('ACCESO', 'No se pudo leer el detalle del informe previo; se conserva la parada.', level='warning')
                    state.update(status='blocked', error={'code': 'PREVIOUS_ACCESS_BLOCK',
                        'message': 'Existe una parada previa documentada. Revisar el diagnóstico antes de otra navegación.'})
                    raise RuntimeError('Acceso bloqueado anteriormente; ver reports/access-blocked.json')
                proxy_config = None
                if proxy_settings.enabled:
                    proxy_started = time.perf_counter()
                    state['stage'] = 'proxy-setup'
                    state['proxy'] = proxy_settings.diagnostic('initializing')
                    event('PROXY', 'Configurando Apify Proxy autorizado | modo=%s | grupos=%s | país=%s | sesión IP=%s; una navegación y sin reintentos.',
                          proxy_settings.mode, ', '.join(proxy_settings.groups), proxy_settings.country or '(sin filtro)',
                          state['proxy']['sessionMode'])
                    if proxy_settings.mode == 'unblocker':
                        event('PROXY', 'UNBLOCKER no fija IP entre recursos. Conserva contexto de navegador; puede generar cargos por múltiples solicitudes.')
                    try:
                        proxy_config = await configure_proxy(actor, proxy_settings, session_id=state['runId'],
                                                             timeout_seconds=min(10, max(0.001, data['_acquisitionDeadline'] - time.monotonic() - 2)))
                    except ProxyConfigurationError:
                        state['proxy'] = proxy_settings.diagnostic('error')
                        raise
                    finally:
                        timing('proxySetup', proxy_started)
                    state['proxy'] = proxy_settings.diagnostic('configured')
                    event('PROXY', 'Estado=configured | conexión y petición todavía no verificadas.')
                else:
                    event('PROXY', 'Desactivado; se conserva la conexión directa.')
                state['stage'] = 'acquisition'
                if data['_acquisitionDeadline'] - time.monotonic() <= 2:
                    state.update(status='partial', error={'code': 'TIMEOUT',
                        'message': 'Inicio/proxy agotaron el plazo de adquisición; no se inicia otra navegación.'})
                    raise RuntimeError('Plazo compartido de adquisición agotado')
                acquisition_started = time.perf_counter()
                event('NAVEGADOR', 'Cargando Crawl4AI desde el entorno Python del proyecto.')
                from .browser import acquire
                if proxy_config is None:
                    document, responses, acquisition = await acquire(data)
                else:
                    document, responses, acquisition = await acquire(
                        data, proxy_config=proxy_config, proxy_mode=proxy_settings.mode)
                timing('acquisition', acquisition_started)
                state['acquisition'] = acquisition
                if proxy_config is not None:
                    state['proxy'].update(proxy_observation(acquisition))
                    event('PROXY', 'Estado=configured | connected=%s | HTTP documento=%s | petición correcta=%s; no acredita extracción completa.',
                          state['proxy']['connected'], state['proxy']['documentHttpStatus'], state['proxy']['documentRequestSucceeded'])
                if acquisition['status'] != 'received':
                    state['stage'] = 'acquisition.' + acquisition.get('failureStage', 'unknown')
                    state.update(status=acquisition['status'], error=acquisition.get('error'))
                    if acquisition['status'] == 'blocked':
                        save('reports/access-blocked.json', state)
                    if document and acquisition.get('candidateBeforeAccessFailure'):
                        retained_failure = (acquisition['status'], acquisition.get('error'))
                        event('CANDIDATO_PREVIO', 'Se validará solo el HTML 200 recibido antes del fallo; no se continúa navegando.', level='warning')
                    else:
                        raise RuntimeError('Navegación detenida; consultar adquisición en reports/run.json')
                data['_documentUrl'] = acquisition.get('finalUrl')
                state['extractionPerformed'] = True
            state['stage'] = 'transformation'
            extraction_started = time.perf_counter()
            event('EXTRACCION', 'Método=%s | HTML=%s bytes | fuentes HTTP comerciales recibidas=%s',
                  data['extractionMethod'], len(document.encode('utf-8')),
                  ', '.join(key for key in sorted(responses) if not key.startswith('_')) or '(ninguna)')
            try:
                if data['extractionMethod'] == 'nuxt':
                    output, metadata, missing, commercial = extract_html(document, data, responses)
                elif data['extractionMethod'] == 'crawl4ai_css':
                    from .dom_crawl4ai import extract_html as extract_css
                    output, metadata, missing, commercial = extract_css(document, data)
                else:
                    from .dom_playwright import extract_raw
                    if acquisition.get('parserError'):
                        raise ValueError('DOM_READ_ERROR')
                    output, metadata, missing, commercial = extract_raw(responses.get('_dom', {}), data)
            except Exception:
                if data['extractionMethod'] != 'nuxt':
                    code = (acquisition.get('parserError', {}).get('code', 'DOM_EXTRACTION_ERROR')
                            if data['extractionMethod'] == 'playwright_dom' else 'CSS_EXTRACTION_ERROR')
                    state.update(status='error', error={'code': code, 'message': 'Fallo del parser seleccionado; detalles privados omitidos.'})
                raise
            metadata['extractionMethod'] = data['extractionMethod']
            if metadata.get('domDiagnostics', {}).get('verificationPage'):
                state.update(status='blocked', error={'code': 'ACCESS_CHALLENGE',
                    'message': 'El documento del método seleccionado es una página de verificación.'})
                raise RuntimeError('Documento de verificación; no se guarda producto')
            timing('extraction', extraction_started,
                   state.get('acquisition', {}).get('domReadSeconds', 0) if data['extractionMethod'] == 'playwright_dom' else 0)
            if data['executionMode'] == 'live':
                product = commercial.get('product', {})
                uuid_matches = product.get('productId') == data['productId']
                state['acquisition']['productDocument'] = {
                    'verified': bool(uuid_matches and output['title']), 'uuidMatches': uuid_matches,
                    'titleFound': bool(output['title']), 'commercialSources': sorted(commercial)}
                event('DOCUMENTO_PRODUCTO', 'verificado=%s | UUID coincide=%s | título=%s | fuentes=%s',
                      bool(uuid_matches and output['title']), uuid_matches,
                      bool(output['title']), ','.join(sorted(commercial)) or '(ninguna)')
            if data['executionMode'] == 'live' and acquisition.get('pageSelection'):
                metadata['pageSelection'] = acquisition['pageSelection']
                if metadata['pageSelection']['status'] == 'changed_or_removed':
                    # Preserve the extractor's price logic; decline to attribute a changed
                    # page selection to the original URL without evidence of equivalence.
                    output['price'] = None
                    metadata['priceVerification'].update(status='unverified', reason='PAGE_SELECTION_CHANGED_BY_REDIRECT')
                    if 'price' not in missing:
                        missing.append('price')
                    missing.append('pageSelection')
                    metadata['sourceDiagnostics']['foundFields'] = [key for key in metadata['sourceDiagnostics']['foundFields'] if key != 'price']
                    event('SELECCION_URL', 'La redirección cambió/quitó selección comercial; precio no atribuible con certeza a la URL inicial.', level='warning')
            event('FUENTES', 'Datos comerciales identificados=%s | scripts JSON/Nuxt=%s',
                  ', '.join(sorted(commercial)) or 'ninguno', len(metadata.get('htmlInventory', [])))
            diagnosis = metadata['sourceDiagnostics']
            event('ESTRUCTURA', 'Nuxt=%s | JSON incrustado=%s | JSON-LD=%s | errores de análisis=%s | Product JSON-LD=%s',
                  diagnosis['nuxtScripts'], diagnosis['embeddedJsonScripts'], diagnosis['jsonLdScripts'],
                  diagnosis['scriptErrors'], diagnosis['jsonLdProductFound'])
            event('PRECIO' if data['extractionMethod'] == 'nuxt' else 'PRECIO_DOM',
                  'selectedOffer.price.amount=%s | moneda de oferta=%s | precio original presente=%s | precio original usado=False' if data['extractionMethod'] == 'nuxt' else
                  'precio visible verificado=%s | moneda=%s | marcadores de referencia=%s | precio original usado=False',
                  diagnosis['selectedOfferPriceFound'] if data['extractionMethod'] == 'nuxt' else output['price'] is not None,
                  diagnosis['selectedOfferCurrencyFound'] if data['extractionMethod'] == 'nuxt' else metadata['currency'],
                  diagnosis['originalPricePresent'])
            price_check = metadata['priceVerification']
            event('PRECIO_VERIFICADO', 'estado=%s | motivo=%s | opciones seleccionadas comprobadas=%s',
                  price_check['status'], price_check['reason'], price_check['selectedOptionChecks'],
                  level='info' if price_check['status'] == 'verified' else 'warning')
            mass = metadata['weightNormalization']
            event('PESO', 'peso físico=%s kg | estado=%s | motivo=%s | fuente=%s | peso de envío no disponible',
                  mass['valueKg'], mass['status'], mass['reason'], mass['sourceUrl'] or mass['source'])
            event('CAMPOS_ENCONTRADOS', '%s', ', '.join(diagnosis['foundFields']) or 'ninguno')
            event('EXTRACCION', 'Título=%s | precio=%s | moneda=%s | fuente=%s',
                  'presente' if output['title'] else 'ausente', output['price'], metadata['currency'], metadata['priceSource'])
            event('EXTRACCION', 'Imágenes=%s | características=%s | grupos=%s | opciones=%s',
                  len(output['images'] or []), len(output['characteristics'] or []), len(output['variations'] or []),
                  sum(len(group['variants']) for group in output['variations'] or []))
            if missing:
                event('DATOS_FALTANTES', '%s', ', '.join(missing), level='warning')
                event('RECOMENDACION',
                      'Revisar el JSON incrustado y htmlInventory en PRODUCT_METADATA. Un JSON-LD aislado solo acredita datos parciales.' if data['extractionMethod'] == 'nuxt' else
                      'Revisar domDiagnostics y el perfil de selectores con HTML auténtico. No inferir campos ausentes ni cambiar de método automáticamente.', level='warning')
                event('RECOMENDACION', 'Conservar Crawl4AI y el parser determinista mientras el HTML aporte los datos; cambiar de herramienta no garantiza recuperar campos ausentes.', level='warning')
            state['stage'] = 'schema-validation'
            schema = json.loads((ROOT / '.actor/dataset_schema.json').read_text(encoding='utf-8'))['fields']
            Draft7Validator(schema, format_checker=FormatChecker()).validate(output)
            event('CONTRATO', 'Once campos y tipos anidados válidos.')
            if retained_failure and not (output['title'] and output['price'] is not None and output['images']
                    and metadata['priceVerification']['status'] == 'verified'
                    and commercial.get('product', {}).get('productId') == data['productId']):
                state.update(status=retained_failure[0], error=retained_failure[1])
                raise RuntimeError('El candidato previo no pudo validarse; no se guarda producto')
            state.update(status=retained_failure[0] if retained_failure else
                         'success' if data['executionMode'] == 'live' and not missing else 'partial',
                         missingFields=missing)
            if retained_failure:
                state['error'] = retained_failure[1]
                exit_code = 1
                event('RESULTADO_CONSERVADO', 'Producto previo validado; se conservará un registro con estado de acceso=%s.', state['status'], level='warning')
            if not any(output[key] for key in ('title', 'price', 'images', 'characteristics', 'variations')):
                state.update(status='partial', error={'code': metadata.get('domDiagnostics', {}).get('errorCode') or 'NO_PRODUCT_DATA',
                    'message': 'No se identificaron datos comerciales; no se guarda un producto vacío.'})
                raise RuntimeError('No hay producto verificable en el documento recibido')
            if missing:
                exit_code = 1
            metadata.update(status=state['status'], executionMode=data['executionMode'],
                            extractionPerformed=state['extractionPerformed'])
            state['stage'] = 'storage'
            storage_started = time.perf_counter()
            if data['executionMode'] == 'offline':
                dataset = await actor.open_dataset(name='offline-validation')
                await dataset.push_data(output)
                state['savedResults'] = 1
                timing('datasetSave', storage_started)
            else:
                await actor.push_data(output)
                state['savedResults'] = 1
                timing('datasetSave', storage_started)
                # Filtered business data only. Never archive the complete page/framework configuration.
                if data['extractionMethod'] == 'nuxt':
                    capture_dir = ROOT / 'captures' / data['productId']
                    capture_dir.mkdir(parents=True, exist_ok=True)
                    payload = json.dumps({'productId': data['productId'], 'data': commercial}, ensure_ascii=False)
                    payload = payload.replace('<', '\\u003c')
                    (capture_dir / 'commercial.html').write_text(
                        '<!doctype html><html><head><meta charset="utf-8"><title>Filtered commercial capture</title></head>'
                        '<body><script id="captured-commercial-data" type="application/json">' + payload + '</script></body></html>',
                        encoding='utf-8')
                    save(capture_dir / 'provenance.json', {'url': data['url'], 'capturedAt': now(), 'filtered': True,
                         'originalHtmlRetained': False, 'originalHtmlSha256': metadata['htmlSha256'],
                         'htmlInventory': metadata['htmlInventory']})
            state['counts'] = {key: len(output[key] or []) for key in ('images', 'characteristics', 'variations')}
            state.update(price=output['price'], currency=metadata['currency'], priceSource=metadata['priceSource'])
            save('output/product.json', output)
            state['outputFile'] = 'output/product.json'
            save(f"output/{data['productId']}.json", output)
            event('GUARDADO', 'Dataset=%s | resultados=%s | copia=output/product.json',
                  'offline-validation (partial, sin extracción de red)' if data['executionMode'] == 'offline' else 'default',
                  state['savedResults'])
            state['stage'] = 'finished'
        except asyncio.CancelledError:
            if budget.remaining(WORK_SECONDS) <= 0:
                state.update(status='error', error={'code': 'RUN_TIME_LIMIT',
                    'message': 'Presupuesto de proceso agotado; finalizando sin reintentos.'})
                exit_code = 1
            else:
                state['status'] = 'aborted'
                exit_code = 143
        except Exception as error:
            if state['status'] in ('running', 'success') or state['stage'] == 'storage':
                code = ('INVALID_INPUT' if state['stage'] == 'input' else
                        'PROXY_CONFIGURATION_ERROR' if isinstance(error, ProxyConfigurationError) else type(error).__name__)
                state.update(status='error', error={'code': code, 'message': str(error)[:500]})
            failure = state.get('error') or {'code': type(error).__name__, 'message': str(error)[:500]}
            event('FALLO', 'etapa=%s | código=%s | %s', state['stage'], failure['code'], failure['message'], level='error')
            advice(failure['code'])
            exit_code = 1
        finally:
            state['finishedAt'] = now()
            state['elapsedSeconds'] = round(budget.elapsed(), 3)
            state['logicalExitCode'] = exit_code
            metadata.update(status=state['status'], executionMode=state['executionMode'],
                            extractionPerformed=state['extractionPerformed'])
            try:
                await bounded(actor.set_value('PRODUCT_METADATA', metadata), budget.remaining(PERSIST_SECONDS))
                await bounded(actor.set_value('RUN_STATE', state), budget.remaining(PERSIST_SECONDS))
            except TimeoutError:
                event('TOPE_PROCESO', 'Persistencia de diagnóstico agotada; se conserva el límite de proceso.', level='warning')
            save('reports/run.json', state)
            stamp = state['startedAt'].replace(':', '-')
            save(f'reports/runs/{stamp}.json', state)
            event('FIN', 'estado=%s | extracción de red=%s | guardados=%s | duración=%.3f s | última etapa=%s',
                  state['status'], state['extractionPerformed'], state['savedResults'], state['elapsedSeconds'], state['stage'])
            if not state['savedResults']:
                event('SALIDA', 'No se guardó un producto nuevo en esta ejecución. Revisa el diagnóstico; archivos anteriores pueden seguir existiendo.', level='warning')
            if debugging_f5 and exit_code:
                event('F5', 'Depuración finalizada sin excepción. El resultado sigue siendo %s (código lógico %s); ver reports/run.json.',
                      state['status'], exit_code, level='warning')
            event('DIAGNOSTICO', 'Informe: reports/run.json. Comparte la consola completa desde INICIO hasta FIN y cualquier traceback posterior; no incluyas cookies ni credenciales.')
            work_finished.set()
        cleanup_started = time.perf_counter()
        try:
            await actor.exit(exit_code=exit_code, event_listeners_timeout=timedelta(seconds=0.25),
                             cleanup_timeout=timedelta(seconds=min(1, max(0.001, budget.remaining()))))
        finally:
            timing('sdkClose', cleanup_started)


def main():
    parser = argparse.ArgumentParser(description='Back Market: Crawl4AI sin LLM')
    parser.add_argument('--input', help='JSON local dentro del proyecto')
    args = parser.parse_args()
    code = asyncio.run(run(args.input))
    if code and os.environ.get('BACKMARKET_F5') != '1':
        raise SystemExit(code)


if __name__ == '__main__':
    main()

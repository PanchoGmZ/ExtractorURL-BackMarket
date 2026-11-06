"""Run with F5 or .venv/Scripts/python -m src --input <INPUT.json>."""
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


async def run(input_path=None):
    started = time.perf_counter()
    # F5 uses an Actor instance that closes normally instead of raising SystemExit
    # into the debugger. CLI and platform runs retain the SDK's normal exit code.
    debugging_f5 = os.environ.get('BACKMARKET_F5') == '1' and os.environ.get('APIFY_IS_AT_HOME') != '1'
    actor = Actor(exit_process=False) if debugging_f5 else Actor
    state = {'source': 'backmarket', 'status': 'running', 'startedAt': now(),
             'executionMode': None, 'extractionPerformed': False, 'savedResults': 0, 'stage': 'output-prepare',
             'runId': uuid.uuid4().hex, 'outputFile': None}
    metadata = {'productId': None, 'url': None, 'currency': None, 'variantGroups': []}
    exit_code = 0
    previous_output = archive_previous_output(ROOT, state['runId'])
    if previous_output:
        state['previousOutputFile'] = previous_output
    # Invalidate the previous run before SDK initialization or input validation can fail.
    state['stage'] = 'actor-init'
    save('reports/run.json', state)
    async with actor:
        event('INICIO', 'Back Market | una ficha | sin reintentos automáticos')
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
                await asyncio.wait_for(work_finished.wait(), timeout=10)
            except TimeoutError:
                event('ABORTANDO', 'Tiempo de cierre agotado; el SDK completará la terminación.', level='warning')
            await actor.exit(exit_code=143, event_listeners_timeout=timedelta(seconds=1), cleanup_timeout=timedelta(seconds=5))

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
            data = parse_input(await actor.get_input())
            state.update(url=data['url'], productId=data['productId'], executionMode=data['executionMode'])
            metadata.update(productId=data['productId'], url=data['url'])
            event('INPUT', 'Válido | modo=%s | UUID=%s', data['executionMode'], data['productId'])
            event('SELECCION_URL', 'Parámetros solicitados=%s; se envía la URL completa sin convertir l/offerType a otros filtros.',
                  selection_parameters(data['url']))
            event('LIMITES', 'Adquisición=%s s (máx. 30) | respuesta=%s bytes (%.2f MiB; máx. 8) | memoria privada=%s MiB (máx. 2048)',
                  data['timeoutSeconds'], data['maxHtmlBytes'], data['maxHtmlBytes'] / 1048576, data['maxMemoryMb'])
            event('LIMITES', 'Una navegación, concurrencia=1. Cierre adicional: hasta 5 s más espera de tareas de 2 s.')
            if data['executionMode'] == 'offline':
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
                state['stage'] = 'acquisition'
                event('NAVEGADOR', 'Cargando Crawl4AI desde el entorno Python del proyecto.')
                from .browser import acquire
                document, responses, acquisition = await acquire(data)
                state['acquisition'] = acquisition
                if acquisition['status'] != 'received':
                    state['stage'] = 'acquisition.' + acquisition.get('failureStage', 'unknown')
                    state.update(status=acquisition['status'], error=acquisition.get('error'))
                    if acquisition['status'] == 'blocked':
                        save('reports/access-blocked.json', state)
                    raise RuntimeError('Navegación detenida; consultar adquisición en reports/run.json')
                state['extractionPerformed'] = True
            state['stage'] = 'transformation'
            event('EXTRACCION', 'Procesando HTML: %s bytes | JSON comerciales recibidos: %s',
                  len(document.encode('utf-8')), ', '.join(sorted(responses)) or 'ninguno; revisar datos incrustados')
            output, metadata, missing, commercial = extract_html(document, data, responses)
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
            event('PRECIO', 'selectedOffer.price.amount=%s | moneda de oferta=%s | precio original presente=%s | precio original usado=False',
                  diagnosis['selectedOfferPriceFound'], diagnosis['selectedOfferCurrencyFound'], diagnosis['originalPricePresent'])
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
                event('RECOMENDACION', 'Revisar el JSON incrustado y htmlInventory en PRODUCT_METADATA. Un JSON-LD aislado solo acredita datos parciales.', level='warning')
                event('RECOMENDACION', 'Conservar Crawl4AI y el parser determinista mientras el HTML aporte los datos; cambiar de herramienta no garantiza recuperar campos ausentes.', level='warning')
            state['stage'] = 'schema-validation'
            schema = json.loads((ROOT / '.actor/dataset_schema.json').read_text(encoding='utf-8'))['fields']
            Draft7Validator(schema, format_checker=FormatChecker()).validate(output)
            event('CONTRATO', 'Once campos y tipos anidados válidos.')
            state.update(status='success' if data['executionMode'] == 'live' and not missing else 'partial',
                         missingFields=missing)
            if not any(output[key] for key in ('title', 'price', 'images', 'characteristics', 'variations')):
                state.update(status='partial', error={'code': 'NO_PRODUCT_DATA',
                    'message': 'No se identificaron datos comerciales; no se guarda un producto vacío.'})
                raise RuntimeError('No hay producto verificable en el documento recibido')
            if missing:
                exit_code = 1
            metadata.update(status=state['status'], executionMode=data['executionMode'],
                            extractionPerformed=state['extractionPerformed'])
            state['stage'] = 'storage'
            if data['executionMode'] == 'offline':
                dataset = await actor.open_dataset(name='offline-validation')
                await dataset.push_data(output)
                state['savedResults'] = 1
            else:
                await actor.push_data(output)
                state['savedResults'] = 1
                # Filtered business data only. Never archive the complete page/framework configuration.
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
            await actor.set_value('PRODUCT_METADATA', metadata)
            event('GUARDADO', 'Dataset=%s | resultados=%s | copia=output/product.json',
                  'offline-validation (partial, sin extracción de red)' if data['executionMode'] == 'offline' else 'default',
                  state['savedResults'])
            state['stage'] = 'finished'
        except asyncio.CancelledError:
            state['status'] = 'aborted'
            exit_code = 143
        except Exception as error:
            if state['status'] in ('running', 'success') or state['stage'] == 'storage':
                code = 'INVALID_INPUT' if state['stage'] == 'input' else type(error).__name__
                state.update(status='error', error={'code': code, 'message': str(error)[:500]})
            failure = state.get('error') or {'code': type(error).__name__, 'message': str(error)[:500]}
            event('FALLO', 'etapa=%s | código=%s | %s', state['stage'], failure['code'], failure['message'], level='error')
            advice(failure['code'])
            exit_code = 1
        finally:
            state['finishedAt'] = now()
            state['elapsedSeconds'] = round(time.perf_counter() - started, 3)
            state['logicalExitCode'] = exit_code
            metadata.update(status=state['status'], executionMode=state['executionMode'],
                            extractionPerformed=state['extractionPerformed'])
            await actor.set_value('PRODUCT_METADATA', metadata)
            await actor.set_value('RUN_STATE', state)
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
        if exit_code:
            if debugging_f5:
                actor.exit_code = exit_code
            else:
                await actor.exit(exit_code=exit_code)


def main():
    parser = argparse.ArgumentParser(description='Back Market: Crawl4AI sin LLM')
    parser.add_argument('--input', help='JSON local dentro del proyecto')
    args = parser.parse_args()
    asyncio.run(run(args.input))


if __name__ == '__main__':
    main()

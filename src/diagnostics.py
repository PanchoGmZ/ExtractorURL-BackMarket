"""Console diagnostics for Back Market; never log bodies, cookies or credentials."""
import re
from apify import Actor


def navigation_failure(message, *, http_status=None, source='crawl-result'):
    """Project private error text onto fixed codes/signals; never return a text excerpt."""
    text = str(message) if isinstance(message, (str, BaseException)) else ''
    # Crawl4AI appends source code, which is not evidence of the error itself.
    text = text.split('Code context:', 1)[0]
    present = bool(text.strip())
    known_signals = (
        'ERR_PROXY_AUTH_REQUESTED', 'ERR_INVALID_AUTH_CREDENTIALS',
        'ERR_TUNNEL_CONNECTION_FAILED', 'ERR_PROXY_CONNECTION_FAILED',
        'ERR_NO_SUPPORTED_PROXIES', 'ERR_INVALID_ARGUMENT',
        'ERR_PROXY_CERTIFICATE_INVALID', 'ERR_CERT_AUTHORITY_INVALID',
        'ERR_CERT_COMMON_NAME_INVALID', 'ERR_CERT_DATE_INVALID', 'ERR_CERT_INVALID',
        'ERR_SSL_PROTOCOL_ERROR', 'ERR_SSL_VERSION_OR_CIPHER_MISMATCH',
        'CERTIFICATE_VERIFY_FAILED', 'ERR_NAME_NOT_RESOLVED', 'ERR_CONNECTION_REFUSED',
    )
    # Remove URLs and credential assignments before matching. No sanitized free text
    # is ever emitted: only constants from the allow-lists below leave this function.
    text = re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\'"<>]+', '[URL]', text)
    text = re.sub(r'''(?i)\b(?:password|passwd|username|token|authorization|cookie|set-cookie)['"]?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''', '[PRIVATE]', text)
    signals = [signal for signal in known_signals if re.search(r'(?<![A-Z0-9_])' + signal + r'(?![A-Z0-9_])', text)]
    operation = next((op for op in ('BrowserType.launch', 'Browser.new_context',
        'BrowserContext.new_page', 'BrowserContext.add_init_script', 'Page.goto', 'Page.evaluate') if op in text), None)
    exception_names = ('TypeError', 'AttributeError', 'ValueError', 'RuntimeError',
        'ModuleNotFoundError', 'ImportError', 'TimeoutError', 'TargetClosedError')
    exception_type = next((name for name in exception_names
        if re.search(r'\b' + name + r'\b', text) or (isinstance(message, BaseException) and type(message).__name__ == name)), None)
    wrapped = 'Unexpected error in _crawl_web' in text
    code, category = 'NAVIGATION_ERROR_UNCLASSIFIED', 'unknown'
    if http_status == 403:
        code, category = 'HTTP_403', 'access'
    elif (http_status == 407 or any(s in signals for s in ('ERR_PROXY_AUTH_REQUESTED', 'ERR_INVALID_AUTH_CREDENTIALS'))
          or re.search(r'\b407\b|proxy authentication (?:failed|required)|invalid proxy credentials', text, re.I)):
        code, category = 'PROXY_AUTH_ERROR', 'authentication'
    elif any(s.startswith(('ERR_CERT_', 'ERR_SSL_', 'ERR_PROXY_CERTIFICATE')) or s == 'CERTIFICATE_VERIFY_FAILED' for s in signals):
        code, category = 'TLS_ERROR', 'tls'
    elif 'ERR_TUNNEL_CONNECTION_FAILED' in signals:
        code, category = 'PROXY_TUNNEL_ERROR', 'http-tunnel'
    elif 'ERR_PROXY_CONNECTION_FAILED' in signals:
        code, category = 'PROXY_CONNECTION_ERROR', 'proxy-connection'
    elif ('ERR_NO_SUPPORTED_PROXIES' in signals or
          re.search(r'(?:proxy\.(?:server|username|password)|proxy (?:server|url|scheme))[^\n]*(?:expected|invalid|must be)', text, re.I)):
        code, category = 'PROXY_CONFIGURATION_ERROR', 'configuration'
    elif re.search(r'(?:browser|playwright)[^\n]*does not support[^\n]*prox', text, re.I):
        code, category = 'PLAYWRIGHT_PROXY_UNSUPPORTED', 'playwright-api'
    elif "Executable doesn't exist" in text:
        code, category = 'BROWSER_UNAVAILABLE', 'browser-start'
    elif 'TargetClosedError' == exception_type or 'Target page, context or browser has been closed' in text:
        code, category = 'BROWSER_CLOSED', 'browser-lifecycle'
    elif exception_type == 'TimeoutError' or re.search(r'Timeout \d+(?:\.\d+)?ms exceeded', text):
        code, category = 'TIMEOUT', 'timeout'
    elif operation and ('unexpected keyword argument' in text or exception_type in ('TypeError', 'ValueError')):
        code, category = 'PLAYWRIGHT_API_ERROR', 'playwright-api'
    elif any(s in signals for s in ('ERR_NAME_NOT_RESOLVED', 'ERR_CONNECTION_REFUSED')):
        code, category = 'NETWORK_ERROR', 'network'
    elif operation:
        code, category = 'PLAYWRIGHT_ERROR', 'playwright'
    elif wrapped or exception_type:
        code, category = 'CRAWL4AI_ERROR', 'crawl-wrapper'
    elif not present:
        code = 'NAVIGATION_ERROR_NO_DETAILS'
    return {'code': code, 'category': category, 'operation': operation,
            'signals': signals, 'exceptionType': exception_type, 'crawl4aiWrapped': wrapped,
            'errorMessagePresent': present, 'rawMessageOmitted': True,
            'httpStatus': http_status if type(http_status) is int else None,
            'source': source if source in ('crawl-result', 'exception', 'crawl4ai-logger') else 'unknown'}


def event(stage, message, *args, level='info'):
    getattr(Actor.log, level)('[%s] ' + message, stage, *args)


def advice(code):
    recommendations = {
        'PREVIOUS_ACCESS_BLOCK': (
            'Chrome no se abrió: la parada proviene de reports/access-blocked.json, no de un HTTP nuevo.',
            'Envía ese informe junto con la consola; revisar su causa antes de habilitar otra navegación.',
            'Para revisar solo el parser, usa offline con un HTML auténtico disponible.',
        ),
        'PROTECTION_RESOURCE_DETECTED': (
            'Se observó un recurso de protección; esto por sí solo no confirma HTTP 403 ni CAPTCHA.',
            'Envía el último HTTP, la ruta y el CF-Ray del informe. No cambies identidad, cookies ni proxies.',
        ),
        'HTTP_403': (
            'Detener las pruebas contra esa fuente. Solicitar acceso autorizado al proveedor con ruta, fecha y CF-Ray.',
            'No se recomienda cambiar de navegador o activar stealth para superar el 403.',
        ),
        'HTTP_401': (
            'El documento o una fuente comercial requiere autorización; revisar el acceso con el proveedor sin introducir sesiones personales.',
        ),
        'ACCESS_CHALLENGE': (
            'Detener la navegación; revisar acquisition.redirects, accessChallenge y CF-Ray en reports/run.json con el proveedor.',
            'No se sigue /testchallengepage ni se automatiza el desafío. El informe distingue CF-Ray actual de uno anterior disponible.',
        ),
        'BOT_NEED_CHALLENGE': (
            'El proveedor exige verificación: detener y solicitar una vía de acceso autorizada.',
        ),
        'TIMEOUT': (
            'Revisa la última etapa y HTTP registrados. BACKMARKET_TIMEOUT_SECONDS admite 5..30 s; no se eleva automáticamente.',
            'Si ya está en 30 s, revisa conectividad y tiempos de inicio; no encadenes reintentos.',
        ),
        'MEMORY_LIMIT': (
            'Cierra aplicaciones pesadas y compara el pico privado con el límite interno de memoria.',
            'BACKMARKET_MAX_MEMORY_MB admite 256..2048; aumenta solo con memoria disponible y mantén una ficha por trabajo.',
        ),
        'RESPONSE_TOO_LARGE': (
            'Compara bytes recibidos con BACKMARKET_MAX_HTML_BYTES. El rango es 1024..8388608 bytes (máximo 8 MiB).',
            'Aumenta ese límite solo si el documento esperado lo justifica; no se descargan catálogos.',
        ),
        'CAPTURES_MISSING': (
            'En offline, htmlFile debe apuntar a un HTML auténtico existente dentro de este proyecto.',
        ),
        'INVALID_INPUT': (
            'Corrige storage/key_value_stores/default/INPUT_BACKMARKET.json y guárdalo antes de F5.',
            'Usa una ficha HTTPS www.backmarket.com/en-us/p/slug/UUID; l y offerType son opcionales. No se admiten otros parámetros ni fragmento.',
        ),
        'INVALID_CONTENT_TYPE': (
            'Revisa Content-Type y ruta: una respuesta HTML no debe interpretarse como JSON comercial.',
        ),
        'UNEXPECTED_REDIRECT': (
            'Revisa el enlace original: el documento intentó salir del dominio permitido. No se siguió la redirección.',
        ),
        'NAVIGATION_ERROR': (
            'Revisa el HTTP y la etapa anterior. Comprueba Chrome instalado y la conexión; conserva la validación TLS.',
        ),
        'BROWSER_UNAVAILABLE': (
            'En Windows usa Google Chrome instalado; no se necesita cambiar a stealth ni instalar otro motor para este error.',
            'Revisa que F5 utilice .venv/Scripts/python.exe y que Crawl4AI encuentre el canal chrome.',
        ),
        'TLS_ERROR': (
            'Revisa fecha del equipo y certificados de la conexión empresarial. No desactives la validación TLS.',
        ),
        'PROXY_CONFIGURATION_ERROR': (
            'Revisa grupos, permisos y parámetros de proxy. Un fallo en proxy-setup ocurre antes de abrir la ficha; un rechazo posterior conserva su etapa de adquisición.',
            'Comprueba la autenticación del entorno y el plazo de configuración; no compartas contraseñas ni URLs de proxy.',
        ),
        'PROXY_AUTH_ERROR': (
            'El proxy rechazó la autenticación. Revisa acceso al grupo y credenciales del entorno; no se reintenta ni se cambia de proxy.',
        ),
        'PROXY_CONNECTION_ERROR': (
            'No se estableció la conexión con el proxy. Revisa disponibilidad y permisos en Apify; conserva TLS y no repitas con otras IPs.',
        ),
        'PROXY_TUNNEL_ERROR': (
            'El navegador informó un fallo del túnel HTTP CONNECT. Revisa transporte y permisos del servicio; no acredita un 403 del producto.',
        ),
        'PLAYWRIGHT_PROXY_UNSUPPORTED': (
            'Playwright informó que la combinación de navegador/proxy no se admite. Revisa operation/signals y parámetros; no cambies identidad ni TLS sin evidencia.',
        ),
        'PLAYWRIGHT_API_ERROR': (
            'La API de Playwright rechazó un parámetro. Revisa operation y versiones del build; esto no prueba una incompatibilidad general con Unblocker.',
        ),
        'PLAYWRIGHT_ERROR': (
            'Falló una operación de Playwright sin causa normalizada más específica. Comparte navigationError y la etapa, sin mensajes privados.',
        ),
        'CRAWL4AI_ERROR': (
            'Crawl4AI encapsuló una excepción; no se atribuye todavía a un defecto de la librería. Revisa exceptionType, operation y signals.',
        ),
        'NAVIGATION_ERROR_NO_DETAILS': (
            'Crawl4AI no entregó un mensaje utilizable. Revisa navigationError, captura segura del logger y la etapa; no se infiere la causa.',
        ),
        'NAVIGATION_ERROR_UNCLASSIFIED': (
            'Existe detalle privado pero no coincide con señales conocidas. No compartas el texto bruto; revisa navigationError y versiones antes de ajustar el transporte.',
        ),
        'NETWORK_ERROR': (
            'El navegador informó DNS o conexión rechazada; no se atribuye automáticamente al proxy ni al proveedor.',
        ),
        'BROWSER_CLOSED': (
            'El contexto, página o navegador se cerró antes de terminar. Revisa etapa y límites de recursos; no reintentes con otras IPs.',
        ),
        'BROWSER_CLOSE_ERROR': (
            'Revisa si quedó abierta la ventana creada por esta ejecución y ciérrala antes de otra prueba.',
        ),
    }
    if code == 'HTML_TOO_LARGE':
        code = 'RESPONSE_TOO_LARGE'
    for recommendation in recommendations.get(code, (
        'Envía el código de error y la consola desde INICIO hasta FIN; revisa también reports/run.json.',
    )):
        event('RECOMENDACION', recommendation, level='warning')

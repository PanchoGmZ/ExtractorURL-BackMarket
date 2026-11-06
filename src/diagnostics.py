"""Console diagnostics for Back Market; never log bodies, cookies or credentials."""
from apify import Actor


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
            'Revisa en Apify Console → Proxy los grupos habilitados y sus permisos/coste. No se abrió Back Market.',
            'Comprueba la autenticación del entorno y el plazo de configuración; no compartas contraseñas ni URLs de proxy.',
        ),
        'PROXY_AUTH_ERROR': (
            'El proxy rechazó la autenticación. Revisa acceso al grupo y credenciales del entorno; no se reintenta ni se cambia de proxy.',
        ),
        'PROXY_CONNECTION_ERROR': (
            'No se estableció el túnel del proxy. Revisa disponibilidad y permisos en Apify; conserva TLS y no repitas con otras IPs.',
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

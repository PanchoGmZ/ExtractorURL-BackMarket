# Preparación de Back Market para Apify Cloud

Fecha: 6 de noviembre de 2026.

## Plazo Tucumpa de 60 segundos

Proceso limitado a 55 s con guardián desde creación del proceso (o ACTOR_STARTED_AT en Cloud si es anterior), inicializado antes de imports pesados. Trabajo hasta 52 s, intento de persistencia hasta 53 s y cierre SDK de hasta 1 s. El plazo de adquisición termina como máximo al segundo 48 del proceso y reserva dentro de él 2 s para cerrar navegador. La configuración del proxy consume como máximo 10 s de ese mismo reloj; no suma un presupuesto independiente. El navegador recibe el tiempo restante como `page_timeout`. El guardián final sale con código 1 si el cierre o código síncrono se atasca; los diagnósticos son de mejor esfuerzo en ese caso.

Se configura DOMContentLoaded, sin pausa fija ni espera por imágenes/networkidle. El cuerpo HTML 200 de esa misma navegación puede entregarse antes si tiene UUID/marcadores comerciales y supera controles de desafío, sin esperar scripts secundarios. Los hooks `after_goto` y `before_return_html` también entregan el DOM y cancelan el postprocesamiento/Markdown innecesario de Crawl4AI. El parser existente valida producto/oferta y conserva `partial` si faltan datos. HTTP 200 no equivale a producto validado: `[DOCUMENTO_PRODUCTO]` informa coincidencia de UUID, título y fuentes reales. `[TIEMPO]`/`[HTML_LISTO]`, `RUN_STATE.timings` y `RUN_STATE.acquisition` separan etapas. No hay nuevas solicitudes ni reintentos.

Apify Console: reconstruir el commit y ejecutar con timeout **55 s**, memoria inicial **2048 MB** y `BACKMARKET_TIMEOUT_SECONDS=48` (o eliminar un valor anterior de 30). Usar un build ya construido. ACTOR_STARTED_AT permite descontar el inicio previo a Python si la plataforma lo proporciona; el Actor no controla asignación/cola ni la descarga de imagen. Los 5 s restantes del consumidor no garantizan un arranque frío. Tucumpa debe medir su latencia completa y cortar a 60 s. Validación local: 67 pruebas offline, sintaxis, cuatro esquemas y contrato 1688 aprobados; CLI --help correcto. No se midió extracción Cloud con estos cambios ni se ejecutó tráfico pagado.

`ignore_https_errors=True` se conserva exclusivamente para Unblocker por decisión del usuario: es temporal y **no segura para producción**. Directo/Residential mantienen validación TLS; 403 y desafíos conservan sus paradas.

## Histórico: instrumentación del fallo inmediato sin HTTP

Evidencia comunicada por el usuario: Unblocker configurado, Chromium Linux iniciado, `arun()` devuelve `success=False`, `status_code=None` y cero respuestas observadas tras unos 44 ms. No se dispone del error normalizado de esa ejecución ni se ha reproducido una navegación. No se atribuye el fallo a TLS, permisos, túnel o incompatibilidad sin evidencia.

Defecto de diagnóstico confirmado: `src/browser.py` solo consultaba `result.error_message` para reconocer dos clases de proxy; descartaba lo demás al generar `NAVIGATION_ERROR`. `private_proxy_logger()` también descartaba la copia que Crawl4AI registra por `error_status()`. El código instalado de Crawl4AI encapsula excepciones en un resultado sin HTTP, incluyendo texto y contexto de código, por lo que el valor `None` no acredita por sí mismo una respuesta denegada del proveedor.

Cambios locales: `navigation_failure()` en `src/diagnostics.py` convierte los errores en códigos, categorías, operaciones y señales permitidas; no devuelve fragmentos del mensaje. Descarta URLs, asignaciones de credenciales y contexto de código, y nunca conserva rutas o valores desconocidos. El logger público guarda como máximo cuatro proyecciones seguras en memoria. `src/browser.py` publica `[CRAWL_RESULT]`, `[CAUSA_NAVEGACION]` y `RUN_STATE.acquisition.navigationError`, separando el mensaje del resultado de la evidencia recuperada del logger. La etapa previa a `arun()` es `crawl-call`; se registra su duración y si se alcanzó `before_goto`. No se modifican transporte, TLS, sesiones, parser, precio, peso o contrato 1688.

Códigos nuevos: `PROXY_TUNNEL_ERROR`, `PLAYWRIGHT_PROXY_UNSUPPORTED`, `PLAYWRIGHT_API_ERROR`, `PLAYWRIGHT_ERROR`, `CRAWL4AI_ERROR`, `BROWSER_CLOSED`, `NETWORK_ERROR`, `NAVIGATION_ERROR_NO_DETAILS` y `NAVIGATION_ERROR_UNCLASSIFIED`. Se reutilizan autenticación/configuración de proxy, TLS, timeout y navegador ausente. Los códigos Playwright describen la señal concreta de la API, no una incompatibilidad global; `CRAWL4AI_ERROR` identifica una excepción encapsulada, no demuestra un defecto de esa biblioteca. HTTP 403 real y desafíos mantienen sus paradas.

Pruebas focalizadas offline: nueve casos nuevos (con tablas de errores/resultados simulados) y seis regresiones seleccionadas de proxy/directo/Residential/Unblocker, aprobados. Se verifican errores con HTTP `None`, fallo temprano, ausencia de detalle, privacidad, buffer del logger, no reintentos, cierre y mapeo real de `ProxyConfig`/`CrawlerRunConfig` a un `Browser.new_context` simulado. No se arrancó navegador, no se consultó Back Market ni servicios de proxy y no se usaron créditos Apify.

Nota histórica: esta instrumentación se publicó posteriormente; la causa actual requiere el diagnóstico del build vigente. No compartir mensajes brutos, URLs con autenticación, cookies ni tokens.

## Actualización: Unblocker opcional

Se conserva la integración oficial ya publicada en `fc34608101a527ad7324c0b5127a1cfa8daa4c8d`. `ProxySettings.mode` distingue transporte directo, datacenter, residencial y Unblocker. `split_proxy_input()` permite `UNBLOCKER` solo y en mayúsculas, exige la declaración de autorización y rechaza mezclas, grupos SERP y credenciales personalizadas. No cambia el estado desactivado por defecto.

`configure_proxy()` pasa `actor_proxy_input` al SDK y llama una sola vez a `new_url()` sin argumentos para Unblocker. Datacenter/Residential conservan `new_url(session_id=runId)`. El objeto de `ProxyConfig.from_string()` continúa llegando a `CrawlerRunConfig(proxy_config=...)` mediante `acquire()`; no se añade otra arquitectura de navegador. `RUN_STATE.proxy` y los logs incluyen modo y `sessionMode=not-supported` para Unblocker. Se conserva la sesión/contexto temporal del navegador, sin afinidad de IP ni cookies personales. Una nueva sesión residencial puede recibir otra IP; toda asignación depende de Apify.

Compatibilidad verificada offline con Apify SDK 4.2.0, Crawl4AI 0.9.5 y Playwright 1.64.1: generación local de URL, conversión de credenciales, parámetros de ejecución y construcción de `AsyncWebCrawler`/`AsyncPlaywrightCrawlerStrategy`. La selección headless Linux se simuló en Windows; no se arrancó Chromium ni se probó una conexión real. No se modifican BrowserConfig/TLS, Dockerfile, dependencias, parser comercial, precio, peso ni esquemas de Dataset/Output. El contrato conserva once campos y un único agregado.

Validación de esta actualización: 14 tests del módulo, 5 casos focalizados de ciclo del Actor y 4 casos de política HTTP, todos offline y aprobados. Incluyen ausencia exacta de argumentos en `new_url()` para Unblocker, sesión residencial, modo directo, autorización, mezclas rechazadas, privacidad, fallos sin reintento y un único registro agregado con dobles del navegador/SDK. Sintaxis/JSON y cuatro esquemas Apify válidos. No se ejecutaron navegación, inicialización de acceso a un proxy real, instalaciones, compras ni despliegue Apify.

La [documentación de Unblocker](https://docs.apify.com/proxy/unblocker) confirma ausencia de sesiones de IP y coste por solicitudes exitosas. Una página puede cargar muchos recursos: una navegación no implica un único cargo. No hay presupuesto monetario implementado ni nuevos bloqueos de recursos. El procesamiento interno del servicio es responsabilidad de Apify; el Actor mantiene cero reintentos y detención ante `HTTP_403`/`ACCESS_CHALLENGE`.

Pendiente de la prueba autorizada del usuario en Cloud: permisos efectivos para Unblocker, build actualizado, autenticación/conexión, certificados y coherencia de documento/scripts/Fetch/XHR sin IP persistente. No se garantiza que Nuxt y las tres fuentes comerciales se reciban completas: respuestas no JSON, cambios de selección o datos ausentes conservan sus diagnósticos y estado parcial/error. No se hace fallback a Residential ni se recuperan endpoints bloqueados. En Console: actualizar desde `main`, elegir Proxy → Special → Unblocker y confirmar autorización aplicable; consultar estado, HTTP y CF-Ray sin confundir `configured` con extracción exitosa. Detalles de input y costes en [README.md](README.md#apify-proxy-opcional).

## Histórico: integración opcional de Apify Proxy previa a Unblocker

Estado actual comunicado por el usuario: el Actor ya construyó y ejecutó Chromium en Apify Cloud; la primera navegación recibió HTTP 403 en el documento principal. Esta revisión no ha realizado nuevas pruebas de acceso. Las limitaciones de build descritas más abajo corresponden a la preparación inicial.

Se añade `src/proxy.py` con validación independiente del input y configuración oficial mediante `Actor.create_proxy_configuration(actor_proxy_input=...)`. El modo directo sigue por defecto. El módulo exige una declaración de autorización y un grupo explícito de datacenter o `RESIDENTIAL`; rechaza proxies personalizados, credenciales en el input, `UNBLOCKER`, `GOOGLE_SERP` y mezcla de residencial con otros grupos. La declaración no verifica permisos del proveedor ni sustituye el acceso al grupo en la cuenta Apify. Los permisos reales solo podrán comprobarse con la inicialización autorizada del SDK; no se certifican mediante estas pruebas offline.

El SDK genera una sola URL de sesión por trabajo (`new_url(session_id=runId)` para Residential/Datacenter, sin sesión para Unblocker), que pasa por `ProxyConfig.from_string()` a `CrawlerRunConfig`. Una navegación, cero reintentos y ninguna rotación. La inicialización oficial puede consultar cuenta/servicio de estado y ahora tiene como máximo 10 s del plazo compartido. No se ejecutaron esos controles contra servicios reales en esta revisión.

`src/main.py` separa el input de transporte antes del parser comercial y añade la etapa `proxy-setup`. Un fallo allí produce `PROXY_CONFIGURATION_ERROR`, código de salida 1, cero productos y ninguna navegación. `RUN_STATE.proxy` conserva activación, proveedor, grupos, país, modo de sesión, estado y observaciones HTTP. `configured` solo acredita configuración; `connected` registra respuesta HTTP de documento observada (excepto 407), no IP verificada; `documentRequestSucceeded` exige documento HTTP 200 sin desafío y no acredita extracción completa. Un 403 puede tener `connected=true` y sigue deteniendo la extracción. Se conserva `null` si falta evidencia. No se almacena allí URL de proxy, contraseña, token ni ID de sesión. El modo offline no inicializa proxy. Los marcadores locales de bloqueo conservan su comportamiento.

`src/browser.py` recibe el proxy opcional; los controles de HTTP 403, desafíos, TLS, límites y cierre siguen activos. `private_proxy_logger()` implementa los métodos públicos de `AsyncLoggerBase`; no sustituye `_log` ni otros métodos privados. Con proxy se suprimen logs internos crudos y mensajes privados de excepciones, conservando los logs propios de etapas, HTTP, CF-Ray, redirecciones y recursos. Se clasifican autenticación/túnel tanto en excepciones como en errores encapsulados por Crawl4AI; los fallos no reconocibles conservan `NAVIGATION_ERROR`. HTTP 407 es error de autenticación, no recurso opcional; un documento HTTP 403 también se reconoce cuando solo está disponible el estado final de Crawl4AI.

Archivos de esta revisión: `src/proxy.py`, `src/main.py`, `src/browser.py`, `src/diagnostics.py`, `.actor/input_schema.json`, `.actor/key_value_store_schema.json`, `scripts/validate.py`, `tests/test_proxy.py`, dos casos añadidos a `tests/test_cloud_lifecycle.py`, `README.md` y este informe. Se conserva un respaldo local previo. No se modifican `extract.py`, `nuxt.py`, `weight.py`, Dockerfile, dependencias ni esquemas de Dataset/Output; el producto mantiene once campos y un único registro agregado.

Validación final: 11 comprobaciones del módulo, 4 casos focalizados del ciclo del Actor y 4 casos de política HTTP, todos offline y aprobados. Se comprueba formato residencial y sesión con generación local de URL del SDK instalado (sin `initialize()`), configuración de Crawl4AI, privacidad de todos los métodos del logger público, errores encapsulados, timeout sin reintento, modo directo y un único registro de once campos con SDK/navegador simulados. Sintaxis/JSON correctos, cuatro esquemas Apify válidos y `pip check` sin conflictos. Se mantienen Crawl4AI 0.9.5, Apify SDK 4.2.0 y Playwright 1.64.1. No se lanzó navegador, no se consultó Back Market ni un proxy real, no se instalaron paquetes y no se consumieron servicios Apify.

Antes de activar: revisar en [Apify Console → Proxy](https://console.apify.com/proxy) los grupos habilitados y costes/condiciones de la cuenta; obtener el permiso aplicable del proveedor. La sección opcional del formulario permite seleccionar el grupo y confirmar `proxyAccessAuthorized`. Para desactivar: omitir `proxyConfiguration` o usar `{"useApifyProxy": false}`. Instrucciones y ejemplo en [README.md](README.md#apify-proxy-opcional).

Publicación solicitada en GitHub con mensaje `feat: add optional Apify proxy support`, conservando como referencia previa `687ec5571ded0f7b5c90a1da039bed33c9f01ae2`. El usuario actualizará el Actor existente manualmente desde Cloud. No se ejecuta `apify push` ni se modifica la cuenta Apify. Pendiente: build actualizado, permisos efectivos, conexión autorizada y una única navegación permitida. Esta preparación no demuestra acceso a Back Market: un nuevo `HTTP_403` imprescindible o `ACCESS_CHALLENGE` detiene el trabajo sin resolver desafíos ni cambiar de identidad. Los límites de recursos no constituyen presupuesto monetario ni límite total de tráfico; residencial incluye el tráfico de recursos descargados por el navegador. No se agregan bloqueos de recursos para reducir consumo.

## Cambios de empaquetado

- Se conserva Python/Crawl4AI/Playwright/Apify SDK y no se modifican `extract.py`, `nuxt.py` ni `weight.py`.
- Dockerfile Linux con directorio de trabajo explícito, usuario sin privilegios, dependencias fijadas, librerías de sistema, Chromium y headless shell, y arranque `python -m src.main`.
- Se retiró `--no-shell`: el modo headless sin canal explícito puede necesitar headless shell. La versión del navegador se instala con el Playwright fijado; no se cambian las versiones funcionales.
- Se coordinó el aborto: el SDK espera a que termine el trabajo/persistencia antes de cerrar, evitando competir con escrituras finales.
- Un documento sin datos no crea una fila vacía. Una extracción live parcial conserva los datos disponibles con estado `partial` y código 1.
- Se muestran los once campos en la vista del Dataset y el Output apunta a sus items.
- `.gitignore`, `.dockerignore` y `.actorignore` utilizan listas explícitas de archivos publicables/runtime. No se incluyen entradas de usuario, resultados, marcadores, capturas, credenciales, respaldos ni perfiles.
- Los módulos/experimentos históricos Node.js y documentos internos se conservan localmente, excluidos del proyecto publicable. El Actor publicado es Python.
- `input.example.json` es una plantilla vacía, sin URL ni UUID de prueba.

## Verificado sin navegación

- Sintaxis y versiones instaladas: Crawl4AI 0.9.5, Apify SDK 4.2.0, Playwright 1.64.1.
- Dependencias sin conflictos; no se instalaron dependencias nuevas.
- Cuatro esquemas de Apify válidos.
- Contrato de once campos y Dataset agregado; casos de error y cancelación con dobles en memoria del SDK.
- Arranque del módulo con `python -m src.main --help`.
- Marcadores locales excluidos del contenedor e ignorados en la rama de ejecución Cloud.
- No se ejecutó F5, navegador, scraping, proxy, `apify push` ni operación de cuenta Apify.

## Pendiente

Docker no está instalado en la computadora: no se realizó build local ni se instaló Docker. El build real, las dependencias Linux y el navegador deben verificarse al conectar el repositorio desde Apify Console. La comprobación offline no garantiza el acceso a Back Market desde Cloud.

El historial de acceso incluye una extracción local real del iPhone 15 Plus y un desafío confirmado para otra ficha. Se conserva la detección y parada ante `/testchallengepage`; no se intenta eludir el desafío.

Antes de considerar la versión estable: comprobar build, primera ejecución permitida, una segunda ficha, correspondencia de selección comercial, Results/Output y aborto real en Cloud. No se afirma capacidad de concurrencia ni rendimiento no medidos.

## Primera prueba manual en Apify

1. Conectar el repositorio GitHub, rama `main`, Dockerfile de la raíz.
2. Empezar con 2048 MB de memoria y timeout del run de 55 s.
3. Introducir una URL compatible y mantener `reference1688`; sin proxy ni variables especiales.
4. Revisar el estado del run y Results: como máximo un producto agregado; logs y metadatos explican resultados parciales o bloqueados.
5. Ante un desafío o denegación de una fuente imprescindible, detener y gestionar el acceso con el proveedor. No encadenar reintentos ni cambiar de identidad.

La ejecución/build futuros en Apify pueden consumir recursos del plan. No se consumieron créditos Apify durante esta preparación.

## Referencias oficiales

- [Crawl4AI y SDK Python de Apify](https://docs.apify.com/sdk/python/docs/guides/crawl4ai).
- [Playwright: navegadores y headless shell](https://playwright.dev/python/docs/browsers).
- [Output schema de Apify](https://docs.apify.com/platform/actors/development/actor-definition/output-schema).
- [Apify Proxy](https://docs.apify.com/proxy).

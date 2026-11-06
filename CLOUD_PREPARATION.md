# Preparación de Back Market para Apify Cloud

Fecha: 6 de noviembre de 2026.

## Revisión final: integración opcional de Apify Proxy

Estado actual comunicado por el usuario: el Actor ya construyó y ejecutó Chromium en Apify Cloud; la primera navegación recibió HTTP 403 en el documento principal. Esta revisión no ha realizado nuevas pruebas de acceso. Las limitaciones de build descritas más abajo corresponden a la preparación inicial.

Se añade `src/proxy.py` con validación independiente del input y configuración oficial mediante `Actor.create_proxy_configuration(actor_proxy_input=...)`. El modo directo sigue por defecto. El módulo exige una declaración de autorización y un grupo explícito de datacenter o `RESIDENTIAL`; rechaza proxies personalizados, credenciales en el input, `UNBLOCKER`, `GOOGLE_SERP` y mezcla de residencial con otros grupos. La declaración no verifica permisos del proveedor ni sustituye el acceso al grupo en la cuenta Apify. Los permisos reales solo podrán comprobarse con la inicialización autorizada del SDK; no se certifican mediante estas pruebas offline.

El SDK genera una sola URL de sesión por trabajo (`new_url(session_id=runId)`), que se convierte con `ProxyConfig.from_string()` y se pasa a `CrawlerRunConfig`. Se mantiene una navegación, `max_retries=0` y ninguna estrategia de rotación. No hay fallback a otra IP o conexión directa. El SDK puede consultar la cuenta y su propio servicio de estado para inicializar el proxy; esos controles no se han ejecutado en esta revisión. La inicialización tiene un presupuesto separado igual al límite de adquisición (hasta 30 s por defecto). La sesión solicita afinidad, sin garantizar una IP permanente por parte del proveedor.

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
2. Empezar con 2048 MB de memoria y timeout del run de 120 s.
3. Introducir una URL compatible y mantener `reference1688`; sin proxy ni variables especiales.
4. Revisar el estado del run y Results: como máximo un producto agregado; logs y metadatos explican resultados parciales o bloqueados.
5. Ante un desafío o denegación de una fuente imprescindible, detener y gestionar el acceso con el proveedor. No encadenar reintentos ni cambiar de identidad.

La ejecución/build futuros en Apify pueden consumir recursos del plan. No se consumieron créditos Apify durante esta preparación.

## Referencias oficiales

- [Crawl4AI y SDK Python de Apify](https://docs.apify.com/sdk/python/docs/guides/crawl4ai).
- [Playwright: navegadores y headless shell](https://playwright.dev/python/docs/browsers).
- [Output schema de Apify](https://docs.apify.com/platform/actors/development/actor-definition/output-schema).
- [Apify Proxy](https://docs.apify.com/proxy).

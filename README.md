# Back Market URL Extractor — TU CUMPA

Actor Python de una ficha por ejecución, con Crawl4AI y Playwright, sin LLM. Recibe una URL y guarda un único producto agregado compatible con el contrato 1688. Apify Proxy y Unblocker son transportes opcionales, desactivados por defecto.

## Uso en Apify Cloud

Conecta este repositorio desde Apify Console, rama `main`, usando el `Dockerfile` de la raíz. Los campos principales del formulario son:

- **URL del producto**, obligatoria.
- **Formato de salida**, `reference1688` predeterminado.
- **Método de extracción**, `nuxt` predeterminado; alternativas `playwright_dom` y `crawl4ai_css`.

Una sección adicional permite configurar Apify Proxy de forma opcional. Está desactivado por defecto.

Se admiten fichas HTTPS de `www.backmarket.com/en-us/p/slug/UUID` y los parámetros comerciales `l` y `offerType`, sin duplicados. No se resuelven enlaces abreviados ni se inventan identificadores. Los demás parámetros se rechazan explícitamente.

Configuración para Tucumpa: **2048 MB** de memoria y **55 s** de timeout del run, una URL. El límite externo del consumidor es 60 s; debe incluir también cola/asignación y arranque previo al proceso. No construir la imagen dentro de esa petición.

## Results / Output

La extracción live utiliza una sola llamada `actor.push_data(output)` sobre el **Dataset predeterminado**. `.actor/dataset_schema.json` define la vista Results y `.actor/output_schema.json` enlaza los items del Dataset para Output. El objeto incluye exactamente:

```text
url, title, price, productLadderPrices, moq, weight, dimensions,
images, characteristics, variations, productId
```

Imágenes, características y variantes permanecen dentro de ese objeto. No se genera un registro por opción o imagen.

La moneda, verificaciones, disponibilidad, procedencia del peso y diagnóstico se almacenan aparte en `PRODUCT_METADATA` y `RUN_STATE`. No es necesario abrir el key-value store para obtener el producto.

- `success`: un producto agregado y cierre normal.
- `partial`: si existen datos comerciales incompletos, se puede conservar un registro parcial; en live se termina con código 1 para que no figure como extracción completa.
- Bloqueo, documento sin datos o fallo anterior a extracción: no se guarda un producto inexistente y se termina con código 1. Si antes del fallo ya llegó HTML 200, solo se conserva un registro si después se validan identidad, título, precio y al menos una imagen; el estado continúa `blocked`/error, con diagnóstico y código 1.
- Aborto: estado `aborted`, cierre coordinado y código 143.

Cada run Cloud usa su Dataset predeterminado asignado por Apify; no se fija un ID de Dataset en código. Los archivos de ejecuciones locales no entran en la imagen. La salida local anterior se archiva antes de iniciar una nueva ejecución y `outputFile` empieza en `null`.

## Precio, peso y variantes

En Nuxt el precio viene exclusivamente de `selectedOffer.price.amount`, con moneda publicada y comprobación de la selección. DOM/CSS leen solo el precio actual visible dentro de la ficha y publican su procedencia distinta en `PRODUCT_METADATA.priceSource`; no crean una oferta `selectedOffer` ficticia. Nunca se sustituyen precios por `priceWhenNew`, precios tachados o JSON-LD. La ambigüedad de precio/moneda, una opción seleccionada de otro UUID o una redirección que cambie `l`/`offerType` dejan `price: null` con el motivo.

Los precios de variantes requieren asociación con opción, UUID y moneda. No se construyen combinaciones cartesianas.

El peso conserva el contrato `{"value": 0.201, "unit": "kg"}` o `null`. Convierte g/kg/oz/lb y comprueba plausibilidad del tipo identificado. Para marca Apple y modelo estructurado exactamente iPhone 15 Plus se permite la referencia de [Apple: 201 g](https://support.apple.com/es-es/111830), con procedencia y fecha en metadatos. No se aplica a otros modelos ni a coincidencias de título/URL. `201 oz` no se reinterpreta como `201 g`.

Es **peso físico**, no peso de envío: no se estima embalaje ni transporte. MOQ/dimensiones sin evidencia quedan en `null`; precios por volumen no disponibles son `[]`.

## Métodos DOM/CSS y selectores

Cada ejecución usa exclusivamente el método elegido, sin fallback automático:

| Valor de extractionMethod | Fuente |
| --- | --- |
| `nuxt` | Parser actual de datos incrustados y JSON comerciales que ya genera la página. |
| `playwright_dom` | `src/dom_playwright.py`: lectura directa de campos del DOM renderizado mediante un locator de Playwright y código fijo. Respeta visibilidad, atributos ARIA y estado actual de controles. |
| `crawl4ai_css` | `src/dom_crawl4ai.py`: `JsonCssExtractionStrategy` 0.9.5 sobre el HTML recibido/renderizado, eliminando scripts y recomendaciones antes de seleccionar campos. |

Los módulos nuevos comparten `src/dom_common.py` y el constructor de once campos `src/reference1688.py`, que también usa Nuxt. Modelo, color, almacenamiento, condición, batería, peso y dimensiones declaradas quedan en características y metadatos cuando están presentes. DOM/CSS no consultan endpoints ni leen JSON/Nuxt. Reutilizan la única navegación, proxy, plazos y cierre del Actor. Los endpoints internos no son fuentes imprescindibles para esos dos métodos; un error suyo se registra como secundario. Documento 403 o desafío confirmado siguen deteniendo la navegación.

Perfil conservador `semantic-v1`: un único `h1` en `main`/`role=main` o un contenedor Product de schema.org; UUID declarado/canónico o URL final compatible; precio de `[itemprop=offers] [itemprop=price]`, `[data-testid=product-price]`, `[data-current-price]` o `[data-price-type=current]`. Moneda de `priceCurrency`, atributo del precio o texto ISO/€ /£, nunca de `$` solo. Galería `[data-testid=product-gallery]`, `[data-test=product-gallery]` o `itemprop=image`. Especificaciones `th/td`, filas de dos `td`, `dt` seguido de `dd`. Opciones agrupadas por `fieldset/legend`, `radiogroup`/ARIA o `select/label`; precio e ID de cada opción se leen de sus propios atributos/enlaces. Se excluyen `aside`, recomendaciones, Product anidados sin h1, nodos ocultos y precios tachados/originales. No hay combinaciones cartesianas.

**Evidencia y límite:** las capturas reales locales contienen únicamente scripts JSON comerciales filtrados, sin DOM original. Los selectores anteriores están comprobados con los fixtures sintéticos de `tests/test_dom_methods.py`, incluidos productos distintos y recomendaciones adversarias; todavía no se afirma que coincidan con el markup actual de Back Market. Si faltan, `domDiagnostics` registra `DOM_*`/`CSS_*`, campos faltantes y verificación de identidad; no se inventan datos. Cambiar parser no corrige errores de transporte.

El contrato actual exige `dimensions: null`: un valor declarado se conserva como característica y `domDiagnostics.dimensionsDeclared`. En DOM/CSS el peso solo procede de una masa física explícita, coherente y con unidad, sin completar ausencias con el peso oficial de Apple. Nuxt conserva su tratamiento previo del fabricante. CSS estático no conoce todos los estilos externos ni el estado de propiedades JS; Playwright sí lee el estado renderizado. No se abren pestañas, paneles de especificaciones, galerías ni variantes adicionales.

En Apify Console, reconstruye el build de `main` y selecciona **Método de extracción**. Para F5 añade `"extractionMethod": "playwright_dom"` o `"crawl4ai_css"` al INPUT local. `nuxt` se usa si falta el campo. El modo offline de archivo admite CSS/Nuxt; Playwright DOM exige una página renderizada, sin sustituciones por otro parser.

`[METODO]`, `[PRECIO_DOM]`, `[DOCUMENTO_PRODUCTO]`, `[DATOS_FALTANTES]`, `[FIN]` y `RUN_STATE` permiten comparar. `PRODUCT_METADATA` incluye `extractionMethod`, `priceVerification`, `domDiagnostics` y opciones verificadas. No se persiste el HTML completo. Las pruebas de página local con red abortada se activan opcionalmente con `BACKMARKET_LOCAL_DOM_TEST=1` al ejecutar `python -m unittest discover -s tests -p test_dom_methods.py`; el validador portable omite esa prueba de navegador.

## Acceso y errores

La navegación registra HTTP, redirecciones saneadas, CF-Ray disponible, formatos JSON/Nuxt, campos ausentes y límites. No imprime cuerpos completos, cookies ni credenciales.

Un 403 de reseñas es un error opcional: no descarta HTML comercial permitido y no se vuelve a solicitar ese recurso denegado durante el trabajo. No se extraen reseñas. Un 401/403 de fuentes imprescindibles o un desafío explícito detiene la adquisición. Una redirección a `/testchallengepage` produce `ACCESS_CHALLENGE` y no se sigue ni se resuelve automáticamente.

El proxy solo se utiliza si se configura explícitamente y se declara autorización. No se implementan stealth, modificaciones de identidad ni rotación de IP desde el Actor. El navegador conserva su identidad nativa y la automatización visible. Que el Actor compile o funcione localmente no garantiza permiso o acceso desde los servidores de Apify.

## Ejecución local con F5 (Windows)

Se utiliza Python **3.13**, Chrome instalado y las extensiones Python/debugpy de VS Code.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
New-Item -ItemType Directory -Force storage/key_value_stores/default
Copy-Item input.example.json storage/key_value_stores/default/INPUT_BACKMARKET.json
```

En una instalación existente no recrees el entorno ni sobrescribas tu INPUT. Edita la URL en `storage/key_value_stores/default/INPUT_BACKMARKET.json`, guarda y selecciona **Back Market — Extraer producto**, F5.

El repositorio excluye `storage/`: en una copia nueva se crea con los comandos anteriores. En Cloud el Actor lee directamente `Actor.get_input()`; no necesita ese archivo.

F5 evita interrumpir el depurador con `SystemExit`; su proceso puede terminar con código 0 aunque el resultado sea fallido. Consultar `status` y `logicalExitCode` en `reports/run.json`. CLI y Cloud conservan el código de salida real del SDK.

## Contenedor y dependencias

- Base Linux: `apify/actor-python:3.13`, usuario sin privilegios.
- Crawl4AI **0.9.5**, Apify SDK **4.2.0**, Playwright **1.64.1** y dependencias fijadas.
- Chromium y headless shell correspondientes a esa versión de Playwright, con bibliotecas del sistema mediante `playwright install --with-deps chromium`.
- Arranque: `python -m src.main`; navegador headless en Linux.
- Sin PyTorch, Transformers, Ollama ni modelos descargados. Crawl4AI incluye clientes de IA/stealth entre sus dependencias transitivas; estas funciones no se ejecutan y el acceso a modelos está deshabilitado.

El build incluye un `--help` de arranque, sin abrir navegador. El usuario ya confirmó un contenedor Chromium funcional en Cloud; esta revisión no construye ni ejecuta una nueva imagen Cloud.

## Validación offline

```powershell
.\.venv\Scripts\python.exe -m scripts.validate
.\.venv\Scripts\python.exe -m pip check
apify validate-schema
```

No requieren capturas privadas, cuenta Apify ni navegación. Incluyen casos sintéticos claramente identificados y dobles del SDK para comprobar input Cloud, marcador local ignorado, un push agregado, desafío sin producto y aborto. No acreditan una nueva extracción real ni una ejecución Cloud.

El proceso tiene un guardián de 55 s desde su creación (CLI/F5/Cloud), o desde ACTOR_STARTED_AT si Cloud declara un inicio anterior, armado antes de importar el SDK y Crawl4AI. El trabajo se cancela al segundo 52, los diagnósticos se intentan persistir hasta el segundo 53 y el cierre SDK tiene hasta 1 s. El guardián final puede terminar con código 1 sin garantizar los últimos diagnósticos si hay código síncrono/SDK atascado. Configurar también el timeout del run en 55 s; Python y el SO no ofrecen plazos de tiempo real.

La adquisición comparte el plazo absoluto del segundo 48 con inicialización y proxy. El proxy tiene como máximo 10 s dentro de ese plazo; navegador/navegación usan lo que queda, reservando 2 s para cerrar Chromium. `BACKMARKET_TIMEOUT_SECONDS` admite 5..48 (predeterminado 48) y solo puede reducir el plazo compartido. Se mantienen 4 MiB por documento/JSON y 1024 MiB privados, ajustables con `BACKMARKET_MAX_HTML_BYTES` y `BACKMARKET_MAX_MEMORY_MB`.

Se configura `domcontentloaded`, sin `networkidle`, espera de imágenes, scroll ni pausa fija. El cuerpo HTML de la misma navegación puede entregarse antes si contiene marcadores comerciales y UUID y supera los controles de desafío. `after_goto` y `before_return_html` también entregan el DOM sin generar Markdown. Son candidatos: el parser Nuxt y las comprobaciones existentes verifican identidad comercial, oferta y campos, y marcan ausencias como `partial`. Las lecturas JSON que la página ya generó se recogen con espera acotada, sin GET adicionales. Un HTTP 200 por sí solo no confirma producto.

Logs `[TIEMPO]`, `[HTML_LISTO]` y `[DOCUMENTO_PRODUCTO]` separan inicialización, proxy, apertura, primer HTTP, HTML, extracción, Dataset y cierre. Los datos quedan en `RUN_STATE.timings` y `RUN_STATE.acquisition`. La desactivación TLS en Unblocker sigue siendo temporal y no segura para producción; Directo/Residential verifican certificados.

## Apify Proxy opcional

El modo directo sigue siendo el predeterminado. Solo habilita proxy para una fuente y un acceso autorizados por el proveedor; marcar la declaración del input no concede ni verifica esos permisos.

1. En [Apify Console → Proxy](https://console.apify.com/proxy), comprueba qué grupos tiene habilitados tu cuenta, países admitidos y condiciones/coste del plan. El selector de proxy del input permite elegir los grupos disponibles. Si no tienes acceso, gestiona el permiso correspondiente antes de ejecutar; el Actor no contrata servicios.
2. En el input del Actor, habilita Apify Proxy y selecciona un grupo ya disponible: Datacenter, Residential o **Special → Unblocker**. Confirma **Autorización para usar proxy en esta fuente**. El Actor no fuerza un país si lo omites; Unblocker realiza su selección geográfica automática.
3. Ejecuta una sola ficha. Revisa `[PROXY]`, `[HTTP]`, `[PARADA]` y `RUN_STATE.proxy`: `configured` solo significa que se configuró el transporte, no que Back Market permitió el acceso.
4. Para deshabilitarlo, selecciona **Sin proxy** (`useApifyProxy: false`) o elimina `proxyConfiguration`; puedes dejar `proxyAccessAuthorized: false`. El programa no invoca el SDK de proxy en ese modo, aunque existan variables de proxy en el entorno.

Ejemplo de input residencial para una ejecución autorizada. Reemplaza el marcador de URL por una ficha válida; el grupo oficial es `RESIDENTIAL` y debe estar habilitado en tu cuenta:

```json
{
  "url": "PEGA_AQUI_LA_URL_HTTPS_DEL_PRODUCTO",
  "outputFormat": "reference1688",
  "proxyConfiguration": {
    "useApifyProxy": true,
    "apifyProxyGroups": ["RESIDENTIAL"]
  },
  "proxyAccessAuthorized": true
}
```

Para datacenter, sustituye `RESIDENTIAL` por el nombre exacto del grupo disponible en Console. Para Unblocker, utiliza `"apifyProxyGroups": ["UNBLOCKER"]` y confirma autorización; el editor oficial lo muestra en **Special → Unblocker** cuando está disponible en tu cuenta. Tanto `RESIDENTIAL` como `UNBLOCKER` deben seleccionarse solos, sin mezclas y con esos nombres oficiales en mayúsculas. `GOOGLE_SERP` queda fuera del alcance. También se admite `apifyProxyCountry` con dos letras mayúsculas; para Unblocker se recomienda omitirlo salvo necesidad documentada. No se admiten URLs de proxy personalizadas, credenciales en el input ni fallback directo tras un error. La compatibilidad de formato se valida offline con SDK 4.2.0; los permisos efectivos requieren la inicialización real del SDK en tu ejecución autorizada. La visibilidad de un grupo en Console no acredita el acceso del proveedor al destino.

Interpretación de `RUN_STATE.proxy` y los logs:

| Indicador | Evidencia | Lo que no acredita |
|---|---|---|
| `status: configured` | El SDK produjo una configuración y se convirtió a `ProxyConfig`. | Conexión, IP asignada o permiso de Back Market. La comprobación interna del SDK puede advertir timeout sin fallar. |
| `connected: true` | El navegador observó una respuesta HTTP del documento, distinta de 407, usando la configuración de proxy. | IP concreta, acceso comercial o extracción completa. Un HTTP 403 también acredita respuesta y detiene el trabajo. |
| `documentRequestSucceeded: true` | El documento respondió HTTP 200 y no se detectó desafío. | Que título, precio, imágenes y variantes estén completos/verificados. |
| `RUN_STATE.status: success` | El extractor terminó sin campos esenciales pendientes y guardó un agregado. | Acceso futuro a otras fichas. |

`null` significa que no se observó evidencia suficiente. Los fallos de autenticación/túnel sin respuesta del destino registran `connected: false`. El HTTP observado aparece en `documentHttpStatus` y el CF-Ray disponible se conserva en `RUN_STATE.acquisition.requests`/`accessChallenge`. No se añade una consulta externa de IP ni una petición extra para comprobar conectividad.

La integración usa `Actor.create_proxy_configuration(actor_proxy_input=...)`, una sola URL y `ProxyConfig.from_string()` en `CrawlerRunConfig`, como describe la [guía oficial](https://docs.apify.com/sdk/python/docs/guides/crawl4ai). Datacenter/Residential llaman a `new_url(session_id=runId)`; Unblocker llama a `new_url()` sin enviar ningún parámetro de sesión. En todos los modos el Actor conserva una sola navegación, `max_retries=0`, contexto temporal normal de navegador y cierre al terminar. No solicita otra sesión, otra IP ni otro transporte después de una denegación.

`RUN_STATE.proxy.mode` distingue `direct`, `datacenter`, `residential` y `unblocker`. `sessionMode` es `single-run` para datacenter/residencial, `not-supported` para Unblocker y `null` sin proxy. Apify controla la asignación de IP: una nueva sesión puede recibir otra dirección y la afinidad residencial no garantiza una IP permanente. Unblocker no ofrece esa afinidad entre solicitudes, según su [documentación oficial](https://docs.apify.com/proxy/unblocker).

Compatibilidad y límites de Unblocker: las clases y parámetros instalados (`AsyncWebCrawler`, `BrowserConfig`, `AsyncPlaywrightCrawlerStrategy`, `ProxyConfig` y `CrawlerRunConfig`) se comprobaron offline. La selección de configuración headless Linux se simuló sin arrancar Chromium. El parser y la identidad nativa se conservan; la carga y entrega del DOM están acotadas por el plazo compartido. Esto acredita el cableado, no la compatibilidad real de todas las respuestas de Back Market con el servicio. Cambios de IP entre documento, scripts y Fetch/XHR pueden afectar cookies, selección comercial y disponibilidad. Si el servicio entrega HTML en lugar de JSON, cambia el estado Nuxt o no carga alguna fuente, se mantienen validación de Content-Type/estructura y diagnóstico `partial`/error. Se conserva `HTTP_403`/`ACCESS_CHALLENGE`; no se añaden GET de recuperación, solver ni lógica de evasión. La validación TLS permanece activa en Directo/Residential; en Unblocker se desactiva temporalmente por configuración actual, no segura para producción.

La inicialización oficial del SDK puede consultar la cuenta y comprobar acceso mediante el servicio de estado de Apify Proxy (en SDK 4.2.0, hasta dos intentos internos). Su plazo máximo es ahora 10 s dentro del presupuesto compartido, sin tiempo adicional. No se reintenta la ficha ni se cambian IPs ante HTTP 403/desafíos. En offline no se inicializa proxy ni se abre red.

El SDK toma la autenticación del entorno de Apify Cloud; no pegues tokens ni contraseñas en el input o el repositorio. Los logs propios solo muestran estado, grupo, país y observaciones HTTP seguras. Con proxy se suprimen los logs internos crudos mediante la interfaz pública `AsyncLoggerBase` de Crawl4AI, sin sustituir métodos privados como `_log`; se conservan los diagnósticos propios de etapa, HTTP, CF-Ray y cierre.

`PROXY_CONFIGURATION_ERROR` detiene antes de abrir la ficha; `PROXY_AUTH_ERROR` y `PROXY_CONNECTION_ERROR` clasifican errores reconocibles de autenticación/túnel, tanto excepciones como `error_message` encapsulado por Crawl4AI. Los fallos sin una señal reconocible conservan `NAVIGATION_ERROR`. No se imprime el contenido privado del error. Se mantienen `HTTP_403` y `ACCESS_CHALLENGE` ante denegaciones imprescindibles. No se convierte automáticamente un bloqueo anterior en una prueba con proxy.

Revisa [Proxy management del SDK](https://docs.apify.com/sdk/python/docs/concepts/proxy-management) y los [costes y requisitos vigentes](https://docs.apify.com/proxy). Los límites de tiempo/memoria/tamaño no equivalen a un presupuesto monetario ni limitan el tráfico total: residencial se cobra por tráfico y Unblocker por solicitudes exitosas según las unidades/condiciones del servicio. Una navegación puede cargar muchos recursos y generar múltiples cargos. El procesamiento interno de Unblocker depende de Apify; el Actor no añade reintentos. No se bloquean recursos adicionales para ahorrar tráfico ni se contratan servicios automáticamente. La validación de esta versión no ha activado ni probado servicios reales.

Ver [CLOUD_PREPARATION.md](CLOUD_PREPARATION.md) para el alcance verificado y lo pendiente.

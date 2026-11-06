# Back Market URL Extractor — TU CUMPA

Actor Python de una ficha por ejecución, con Crawl4AI y Playwright, sin LLM ni servicios externos de extracción. Recibe una URL y guarda un único producto agregado compatible con el contrato 1688.

## Uso en Apify Cloud

Conecta este repositorio desde Apify Console, rama `main`, usando el `Dockerfile` de la raíz. Los campos principales del formulario son:

- **URL del producto**, obligatoria.
- **Formato de salida**, `reference1688` predeterminado.

Una sección adicional permite configurar Apify Proxy de forma opcional. Está desactivado por defecto.

Se admiten fichas HTTPS de `www.backmarket.com/en-us/p/slug/UUID` y los parámetros comerciales `l` y `offerType`, sin duplicados. No se resuelven enlaces abreviados ni se inventan identificadores. Los demás parámetros se rechazan explícitamente.

Configuración inicial recomendada para la prueba manual: **2048 MB** de memoria y **120 s** de timeout del run, una URL y sin proxy. Son valores iniciales para observar los logs, no una capacidad de producción medida.

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
- Bloqueo, documento sin datos o fallo anterior a extracción: no se guarda un producto inexistente y se termina con código 1.
- Aborto: estado `aborted`, cierre coordinado y código 143.

Cada run Cloud usa su Dataset predeterminado asignado por Apify; no se fija un ID de Dataset en código. Los archivos de ejecuciones locales no entran en la imagen. La salida local anterior se archiva antes de iniciar una nueva ejecución y `outputFile` empieza en `null`.

## Precio, peso y variantes

El precio viene exclusivamente de `selectedOffer.price.amount`, con moneda publicada y comprobación de la selección. Nunca se sustituye por `priceWhenNew` ni por JSON-LD. Si no se verifica o una redirección modifica la selección `l`/`offerType`, queda `null` y se registra el motivo.

Los precios de variantes requieren asociación con opción, UUID y moneda. No se construyen combinaciones cartesianas.

El peso conserva el contrato `{"value": 0.201, "unit": "kg"}` o `null`. Convierte g/kg/oz/lb y comprueba plausibilidad del tipo identificado. Para marca Apple y modelo estructurado exactamente iPhone 15 Plus se permite la referencia de [Apple: 201 g](https://support.apple.com/es-es/111830), con procedencia y fecha en metadatos. No se aplica a otros modelos ni a coincidencias de título/URL. `201 oz` no se reinterpreta como `201 g`.

Es **peso físico**, no peso de envío: no se estima embalaje ni transporte. MOQ/dimensiones sin evidencia quedan en `null`; precios por volumen no disponibles son `[]`.

## Acceso y errores

La navegación registra HTTP, redirecciones saneadas, CF-Ray disponible, formatos JSON/Nuxt, campos ausentes y límites. No imprime cuerpos completos, cookies ni credenciales.

Un 403 de reseñas es un error opcional: no descarta HTML comercial permitido y no se vuelve a solicitar ese recurso denegado durante el trabajo. No se extraen reseñas. Un 401/403 de fuentes imprescindibles o un desafío explícito detiene la adquisición. Una redirección a `/testchallengepage` produce `ACCESS_CHALLENGE` y no se sigue ni se resuelve automáticamente.

No se activan proxies, stealth, modificaciones de identidad ni rotación de IP. El navegador conserva su identidad nativa y la automatización visible. Que el Actor compile o funcione localmente no garantiza permiso o acceso desde los servidores de Apify.

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

El build incluye un `--help` de arranque, sin abrir navegador. Docker no estaba disponible durante esta preparación; la imagen todavía debe construirse y comprobarse en Cloud.

## Validación offline

```powershell
.\.venv\Scripts\python.exe -m scripts.validate
.\.venv\Scripts\python.exe -m pip check
apify validate-schema
```

No requieren capturas privadas, cuenta Apify ni navegación. Incluyen casos sintéticos claramente identificados y dobles del SDK para comprobar input Cloud, marcador local ignorado, un push agregado, desafío sin producto y aborto. No acreditan una nueva extracción real ni una ejecución Cloud.

Los controles internos se mantienen en 30 s de adquisición, 4 MiB por documento/JSON y 1024 MiB privados de Python e hijos. Se pueden ajustar externamente con `BACKMARKET_TIMEOUT_SECONDS`, `BACKMARKET_MAX_HTML_BYTES` y `BACKMARKET_MAX_MEMORY_MB`. El cierre tiene un presupuesto adicional acotado; la memoria se muestrea y no es una cuota dura del SO.

## Apify Proxy opcional

El modo directo sigue siendo el predeterminado. Solo habilita proxy para una fuente y un acceso autorizados por el proveedor; marcar la declaración del input no concede ni verifica esos permisos.

1. En [Apify Console → Proxy](https://console.apify.com/proxy), comprueba qué grupos tiene habilitados tu cuenta, países admitidos y condiciones/coste del plan. El selector de proxy del input permite elegir los grupos disponibles. Si no tienes acceso, gestiona el permiso correspondiente antes de ejecutar; el Actor no contrata servicios.
2. En el input del Actor, habilita Apify Proxy, selecciona explícitamente un grupo ya disponible y, si corresponde, un país. Confirma **Autorización para usar proxy en esta fuente**. No se seleccionan automáticamente grupos, proxies residenciales ni países.
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

Para datacenter, sustituye `RESIDENTIAL` por el nombre exacto del grupo disponible en Console. No mezcles `RESIDENTIAL` con otros grupos; `UNBLOCKER` y `GOOGLE_SERP` quedan fuera del alcance de este Actor. También se admite `apifyProxyCountry` con dos letras mayúsculas; omitirlo no fuerza un país. No se admiten URLs de proxy personalizadas, credenciales en el input, listas de proxies, rotación ni fallback directo tras un error. La compatibilidad de formato se valida offline con SDK 4.2.0; los permisos efectivos requieren la inicialización real del SDK en tu ejecución autorizada. La visibilidad de un grupo en Console no acredita el acceso del proveedor al destino.

Interpretación de `RUN_STATE.proxy` y los logs:

| Indicador | Evidencia | Lo que no acredita |
|---|---|---|
| `status: configured` | El SDK produjo una configuración y se convirtió a `ProxyConfig`. | Conexión, IP asignada o permiso de Back Market. La comprobación interna del SDK puede advertir timeout sin fallar. |
| `connected: true` | El navegador observó una respuesta HTTP del documento, distinta de 407, usando la configuración de proxy. | IP concreta, acceso comercial o extracción completa. Un HTTP 403 también acredita respuesta y detiene el trabajo. |
| `documentRequestSucceeded: true` | El documento respondió HTTP 200 y no se detectó desafío. | Que título, precio, imágenes y variantes estén completos/verificados. |
| `RUN_STATE.status: success` | El extractor terminó sin campos esenciales pendientes y guardó un agregado. | Acceso futuro a otras fichas. |

`null` significa que no se observó evidencia suficiente. Los fallos de autenticación/túnel sin respuesta del destino registran `connected: false`. El HTTP observado aparece en `documentHttpStatus` y el CF-Ray disponible se conserva en `RUN_STATE.acquisition.requests`/`accessChallenge`. No se añade una consulta externa de IP ni una petición extra para comprobar conectividad.

La integración usa `Actor.create_proxy_configuration(actor_proxy_input=...)`, una sola `new_url(session_id=runId)` y `ProxyConfig.from_string()` en `CrawlerRunConfig`, como describe la [guía oficial](https://docs.apify.com/sdk/python/docs/guides/crawl4ai). Se reutiliza la sesión para todos los recursos de una única navegación. Apify controla la asignación de IP y su disponibilidad: una sesión no garantiza una IP permanente. El Actor no solicita otra sesión si hay un bloqueo.

La inicialización oficial del SDK puede consultar la cuenta y comprobar acceso mediante el servicio de estado de Apify Proxy (en SDK 4.2.0, hasta dos intentos de conectividad de ese servicio). Esto es independiente de la única navegación a la ficha y no reintenta HTTP 403 ni desafíos del destino. El plazo de inicialización usa el mismo límite de adquisición, como presupuesto separado; con los valores actuales son hasta 30 s adicionales. En offline no se inicializa proxy ni se abre red.

El SDK toma la autenticación del entorno de Apify Cloud; no pegues tokens ni contraseñas en el input o el repositorio. Los logs propios solo muestran estado, grupo, país y observaciones HTTP seguras. Con proxy se suprimen los logs internos crudos mediante la interfaz pública `AsyncLoggerBase` de Crawl4AI, sin sustituir métodos privados como `_log`; se conservan los diagnósticos propios de etapa, HTTP, CF-Ray y cierre.

`PROXY_CONFIGURATION_ERROR` detiene antes de abrir la ficha; `PROXY_AUTH_ERROR` y `PROXY_CONNECTION_ERROR` clasifican errores reconocibles de autenticación/túnel, tanto excepciones como `error_message` encapsulado por Crawl4AI. Los fallos sin una señal reconocible conservan `NAVIGATION_ERROR`. No se imprime el contenido privado del error. Se mantienen `HTTP_403` y `ACCESS_CHALLENGE` ante denegaciones imprescindibles. No se convierte automáticamente un bloqueo anterior en una prueba con proxy.

Revisa [Proxy management del SDK](https://docs.apify.com/sdk/python/docs/concepts/proxy-management) y los [costes y requisitos vigentes](https://docs.apify.com/proxy). Los límites de tiempo/memoria/tamaño no equivalen a un presupuesto monetario ni limitan el tráfico total: residencial se cobra por tráfico, incluidos recursos del navegador. No se bloquean recursos adicionales para ahorrar tráfico ni se contratan servicios automáticamente. La validación de esta versión no ha activado ni probado servicios reales.

Ver [CLOUD_PREPARATION.md](CLOUD_PREPARATION.md) para el alcance verificado y lo pendiente.

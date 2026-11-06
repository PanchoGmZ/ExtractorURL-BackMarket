# Preparación de Back Market para Apify Cloud

Fecha: 6 de noviembre de 2026.

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

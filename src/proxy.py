"""Opt-in Apify Proxy setup. Credentials stay in memory, never in diagnostics."""
import asyncio
from dataclasses import dataclass
import re


class ProxyConfigurationError(RuntimeError):
    """Safe public error; never forward SDK/URL exception text."""


@dataclass(frozen=True)
class ProxySettings:
    enabled: bool = False
    groups: tuple[str, ...] = ()
    country: str | None = None

    @property
    def mode(self):
        if not self.enabled:
            return 'direct'
        if self.groups == ('UNBLOCKER',):
            return 'unblocker'
        return 'residential' if self.groups == ('RESIDENTIAL',) else 'datacenter'

    def diagnostic(self, status):
        return {'enabled': self.enabled, 'provider': 'apify' if self.enabled else None,
                'mode': self.mode, 'groups': list(self.groups), 'country': self.country,
                'sessionMode': ('not-supported' if self.mode == 'unblocker' else 'single-run') if self.enabled else None,
                'status': status,
                'connected': None, 'documentHttpStatus': None, 'documentRequestSucceeded': None}


def split_proxy_input(raw):
    """Keep transport settings separate from the unchanged product parser."""
    if not isinstance(raw, dict):
        raise ValueError('INPUT debe ser un objeto JSON')
    product_input = dict(raw)
    config = product_input.pop('proxyConfiguration', {'useApifyProxy': False})
    authorized = product_input.pop('proxyAccessAuthorized', False)
    if type(authorized) is not bool:
        raise ValueError('proxyAccessAuthorized debe ser booleano')
    if not isinstance(config, dict) or set(config) - {
        'useApifyProxy', 'apifyProxyGroups', 'apifyProxyCountry', 'proxyUrls'
    }:
        raise ValueError('proxyConfiguration solo admite Apify Proxy, grupos y país')
    enabled = config.get('useApifyProxy', False)
    if type(enabled) is not bool:
        raise ValueError('useApifyProxy debe ser booleano')
    if config.get('proxyUrls', []) != []:
        raise ValueError('No se admiten URLs de proxy personalizadas ni credenciales en INPUT')
    groups = config.get('apifyProxyGroups', [])
    if (not isinstance(groups, list) or len(groups) > 10 or any(
        not isinstance(group, str) or not re.fullmatch(r'[A-Za-z0-9._~]{1,64}', group) for group in groups
    ) or len(groups) != len(set(groups))):
        raise ValueError('apifyProxyGroups debe contener nombres de grupos válidos sin duplicados')
    country = config.get('apifyProxyCountry')
    if country is not None and (not isinstance(country, str) or not re.fullmatch(r'[A-Z]{2}', country)):
        raise ValueError('apifyProxyCountry debe ser un código de país de dos letras mayúsculas')
    if enabled and not authorized:
        raise ValueError('Para habilitar proxy confirma proxyAccessAuthorized y los permisos del proveedor')
    if enabled and not groups:
        raise ValueError('Selecciona explícitamente un grupo Apify Proxy disponible; no se usa selección automática')
    if enabled and any(group.upper() == 'GOOGLE_SERP' for group in groups):
        raise ValueError('GOOGLE_SERP no es compatible con este extractor de productos')
    for special in ('RESIDENTIAL', 'UNBLOCKER'):
        if enabled and any(group.upper() == special for group in groups) and groups != [special]:
            raise ValueError(f'Selecciona {special} solo y con su nombre oficial en mayúsculas')
    return product_input, ProxySettings(enabled, tuple(groups) if enabled else (), country if enabled else None)


async def configure_proxy(actor, settings, *, session_id, timeout_seconds):
    """One SDK setup and URL; Unblocker has no IP session. No fallback."""
    if not settings.enabled:
        return None
    try:
        async with asyncio.timeout(timeout_seconds):
            configuration = await actor.create_proxy_configuration(actor_proxy_input={
                'useApifyProxy': True, 'apifyProxyGroups': list(settings.groups),
                'apifyProxyCountry': settings.country,
            })
            if configuration is None:
                raise ProxyConfigurationError('El SDK no devolvió una configuración Apify Proxy')
            if settings.mode == 'unblocker':
                # UNBLOCKER explicitly does not support the proxy session parameter.
                proxy_url = await configuration.new_url()
            else:
                proxy_url = await configuration.new_url(session_id=session_id)
            if not isinstance(proxy_url, str) or not proxy_url:
                raise ProxyConfigurationError('El SDK no devolvió una URL Apify Proxy')
            from crawl4ai import ProxyConfig
            return ProxyConfig.from_string(proxy_url)
    except Exception:
        # SDK and parser exceptions can contain credentials. Discard their text/cause.
        raise ProxyConfigurationError(
            'No se pudo configurar Apify Proxy. Revisa acceso al grupo, autenticación y plazo; '
            'no se abrió la ficha ni se cambió a conexión directa.'
        ) from None


def private_proxy_logger():
    """Prevent third-party raw error logs; acquisition emits safe stage/HTTP logs."""
    from crawl4ai.async_logger import AsyncLoggerBase

    class PrivateProxyLogger(AsyncLoggerBase):
        # Public logger interface: no dependence on AsyncLogger._log or file sinks.
        verbose = False
        log_file = None

        def debug(self, *args, **kwargs):
            return

        info = success = warning = error = url_status = error_status = debug

    return PrivateProxyLogger()


def proxy_failure_code(message, http_status=None):
    """Classify private transport details without returning or logging their text."""
    text = str(message or '')
    if http_status == 407 or re.search(r'\b407\b|ERR_(?:PROXY_AUTH|INVALID_AUTH_CREDENTIALS)', text):
        return 'PROXY_AUTH_ERROR'
    if 'ERR_PROXY_CONNECTION' in text or 'ERR_TUNNEL_CONNECTION' in text:
        return 'PROXY_CONNECTION_ERROR'
    return None


def proxy_observation(acquisition):
    """HTTP evidence is separate from SDK configuration and product completeness."""
    documents = [record for record in acquisition.get('requests', [])
                 if record.get('category') == 'document' and type(record.get('httpStatus')) is int]
    status = documents[-1]['httpStatus'] if documents else None
    code = (acquisition.get('error') or {}).get('code')
    connected = True if any(100 <= record['httpStatus'] <= 599 and record['httpStatus'] != 407
                            for record in documents) else None
    if connected is None and code in {'PROXY_AUTH_ERROR', 'PROXY_CONNECTION_ERROR'}:
        connected = False
    succeeded = (status == 200 and code != 'ACCESS_CHALLENGE'
                 and not documents[-1].get('explicitChallenge', False)) if documents else None
    return {'connected': connected, 'documentHttpStatus': status, 'documentRequestSucceeded': succeeded}

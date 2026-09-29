"""Exact endpoint GETs only. No redirects, credentials, proxy environment or persistence."""
import ipaddress
import socket
import time
import httpx
import httpcore
from urllib.parse import urlsplit
from .catalog import SOURCES, source_spec, official_url, CompanySourceError

MAX_BYTES = 1_000_000
TYPES = {'xml': {'application/rss+xml', 'application/atom+xml', 'application/xml', 'text/xml'},
         'html': {'text/html', 'application/xhtml+xml'}}


class _PublicBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host not in {urlsplit(s.endpoint).hostname for s in SOURCES.values()} or port != 443:
            raise CompanySourceError('UNSAFE_ADDRESS')
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
            raise CompanySourceError('UNSAFE_ADDRESS')
        # Connect to the checked numeric address, not a second DNS resolution of
        # the hostname. HTTPcore retains the original hostname for TLS/SNI.
        return super().connect_tcp(answers[0][4][0], port, timeout=timeout,
                                   local_address=local_address, socket_options=socket_options)


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self):
        super().__init__(retries=0, trust_env=False)
        # HTTPX 0.28.1 is pinned by the project. Its pool has no public backend
        # constructor argument; confine this compatibility seam to one class.
        self._pool._network_backend = _PublicBackend()


class OfficialTransport:
    def __init__(self, *, transport=None, sleep=time.sleep):
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise CompanySourceError('UNSUPPORTED_TRANSPORT')
        self._transport = transport if transport is not None else _PinnedTransport()
        self.sleep = sleep

    def close(self):
        self._transport.close()

    def get(self, source_id):
        spec = source_spec(source_id)
        official_url(spec.endpoint, spec.provider, endpoint=True)
        for attempt in range(2):
            transient = False
            try:
                request = httpx.Request('GET', spec.endpoint,
                    headers={'Accept': ', '.join(sorted(TYPES[spec.format])), 'Accept-Encoding': 'identity'},
                    extensions={'timeout': dict(connect=10., read=10., write=10., pool=10.)})
                response = self._transport.handle_request(request)
                try:
                    if response.status_code in (429, 500, 502, 503, 504):
                        transient = True
                    elif response.status_code != 200:
                        raise CompanySourceError('HTTP_STATUS')
                    else:
                        if (response.headers.get('content-type', '').split(';')[0].lower() not in TYPES[spec.format]
                                or response.headers.get('content-encoding', 'identity').lower() != 'identity'):
                            raise CompanySourceError('CONTENT_TYPE')
                        chunks, size = [], 0
                        for chunk in (response.content,) if response.is_stream_consumed else response.iter_raw():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise CompanySourceError('RESPONSE_LIMIT')
                            chunks.append(chunk)
                        return b''.join(chunks)
                finally:
                    response.close()
            except (httpx.TimeoutException, httpx.NetworkError, OSError):
                transient = True
            except httpx.HTTPError:
                raise CompanySourceError('TRANSPORT_ERROR') from None
            if transient and attempt == 0:
                self.sleep(1)
                continue
            raise CompanySourceError('UNAVAILABLE') from None

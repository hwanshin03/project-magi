"""Closed government GET endpoints with public-IP pinning and verified TLS."""
import ipaddress
import socket
import time
from urllib.parse import urlsplit
import httpcore
import httpx
from .catalog import SOURCES, source_spec, RegulatoryError

MAX_BYTES = 1_000_000
MIME = {'json': {'application/json'}, 'xml': {'application/rss+xml','application/atom+xml','application/xml','text/xml'}}


class _PublicBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host not in {urlsplit(s.endpoint).hostname for s in SOURCES.values()} or port != 443:
            raise RegulatoryError('UNSAFE_ADDRESS')
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
            raise RegulatoryError('UNSAFE_ADDRESS')
        return super().connect_tcp(answers[0][4][0], port, timeout=timeout,
                                   local_address=local_address, socket_options=socket_options)


class _PinnedTransport(httpx.HTTPTransport):
    def __init__(self):
        super().__init__(retries=0, trust_env=False)
        # Same isolated HTTPX 0.28.1 compatibility seam as company sources.
        # HTTPcore keeps the original hostname for certificate checks and SNI.
        self._pool._network_backend = _PublicBackend()


class RegulatoryTransport:
    def __init__(self, *, transport=None, sleep=time.sleep):
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise RegulatoryError('UNSUPPORTED_TRANSPORT')
        self._transport = transport if transport is not None else _PinnedTransport()
        self.sleep = sleep

    def close(self): self._transport.close()

    def get(self, source_key):
        spec = source_spec(source_key)
        for attempt in range(2):
            try:
                request = httpx.Request('GET', spec.endpoint,
                    headers={'Accept': ', '.join(sorted(MIME[spec.format])), 'Accept-Encoding':'identity'},
                    extensions={'timeout':dict(connect=10., read=10., write=10., pool=10.)})
                response = self._transport.handle_request(request)
                try:
                    if response.status_code in (429,500,502,503,504):
                        pass
                    elif response.status_code != 200:
                        raise RegulatoryError('HTTP_STATUS')
                    else:
                        if (response.headers.get('content-type','').split(';')[0].lower() not in MIME[spec.format]
                                or response.headers.get('content-encoding','identity').lower() != 'identity'):
                            raise RegulatoryError('CONTENT_TYPE')
                        chunks, size = [], 0
                        for chunk in (response.content,) if response.is_stream_consumed else response.iter_raw():
                            size += len(chunk)
                            if size > MAX_BYTES: raise RegulatoryError('RESPONSE_LIMIT')
                            chunks.append(chunk)
                        return b''.join(chunks)
                finally: response.close()
            except (httpx.TimeoutException, httpx.NetworkError, OSError):
                pass
            except httpx.HTTPError:
                raise RegulatoryError('TRANSPORT_ERROR') from None
            if attempt == 0: self.sleep(1)
        raise RegulatoryError('UNAVAILABLE')

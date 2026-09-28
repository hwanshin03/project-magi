"""Conservative URL normalization; no fetching, redirect resolution or fuzzy matching."""
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from ..security import url


def canonical_url(value):
    url(value)
    if value is None: raise ValueError('Article URL required')
    p=urlsplit(value)
    host=p.hostname.lower()
    if ':' in host: host='['+host+']'
    port=p.port
    authority=host+(':'+str(port) if port and (p.scheme,port) not in (('https',443),('http',80)) else '')
    query=sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid'))
    return urlunsplit((p.scheme.lower(),authority,p.path or '/',urlencode(query),''))

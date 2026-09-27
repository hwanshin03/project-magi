"""Fixed official GET endpoints, paced bounded retries and timestamped RAM cache."""
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote, urlencode
import httpx
from dotenv import load_dotenv
from .base import ResearchError, ErrorCode, utcnow


class _Redact(logging.Filter):
    def __init__(self,secret):
        super().__init__();self.secret=secret
    def filter(self,record):
        if self.secret:
            message=record.getMessage()
            for value in {self.secret,quote(self.secret,safe=''),urlencode({'k':self.secret})[2:]}:
                message=message.replace(value,'[REDACTED]')
            record.msg,record.args=message,()
        return True


@dataclass(frozen=True)
class Payload:
    body: bytes
    retrieved_at: object

    def json(self):
        try:
            return json.loads(self.body,parse_float=Decimal,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError): raise ResearchError(ErrorCode.INVALID_RESPONSE) from None


class ResearchHTTP:
    def __init__(self,provider,*,transport=None,now=utcnow,clock=time.monotonic,sleep=time.sleep,
                 user_agent=None,api_key=None,cache_size=16,ttl=300):
        if provider not in ('SEC','DART') or type(cache_size) is not int or cache_size<1 or ttl<0:
            raise ValueError('Invalid transport configuration')
        if (provider=='SEC' and user_agent is None) or (provider=='DART' and api_key is None):
            load_dotenv(Path(__file__).resolve().parents[3]/'.env',override=False)
        self.provider=provider
        self._key=api_key if api_key is not None else os.getenv('OPENDART_API_KEY')
        agent=user_agent if user_agent is not None else os.getenv('SEC_USER_AGENT')
        if provider=='SEC' and (not agent or not agent.strip() or '\n' in agent or '\r' in agent):
            raise ResearchError(ErrorCode.CONFIGURATION)
        if provider=='DART' and (not self._key or not self._key.strip()): raise ResearchError(ErrorCode.CONFIGURATION)
        self._client=httpx.Client(transport=transport,timeout=httpx.Timeout(15),follow_redirects=False,trust_env=False,
            headers={'User-Agent':agent} if provider=='SEC' else {})
        self.now,self.clock,self.sleep=now,clock,sleep
        self._cache=OrderedDict();self._capacity=cache_size;self._ttl=ttl;self._next=0
        self._lock=threading.Lock()
        # httpx INFO includes query strings; redact at the emitting logger before
        # application/root handlers see them. No raw response/exception logging.
        self._filter=_Redact(self._key if provider=='DART' else None)
        self._loggers=[logging.getLogger(name) for name in ('httpx','httpcore.http11','httpcore.http2','httpcore.connection','httpcore.proxy')]
        for logger in self._loggers: logger.addFilter(self._filter)

    def close(self):
        self._client.close();self._cache.clear()
        for logger in self._loggers: logger.removeFilter(self._filter)

    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def _url(self,path):
        if self.provider=='SEC':
            if path=='/files/company_tickers_exchange.json': return 'https://www.sec.gov'+path
            if re.fullmatch(r'/submissions/CIK\d{10}\.json|/api/xbrl/companyfacts/CIK\d{10}\.json',path):
                return 'https://data.sec.gov'+path
        elif path in ('/api/corpCode.xml','/api/company.json','/api/list.json','/api/fnlttSinglAcntAll.json'):
            return 'https://opendart.fss.or.kr'+path
        raise ResearchError(ErrorCode.INVALID_REQUEST)

    def get(self,path,params=None):
        url=self._url(path);params=dict(params or {})
        if any(re.search('(?i)key|token|authorization|secret',k) for k in params): raise ResearchError(ErrorCode.INVALID_REQUEST)
        key=(path,tuple(sorted(params.items())))
        with self._lock:
            old=self._cache.get(key)
            if old and self.clock()-old[0]<self._ttl:
                self._cache.move_to_end(key);return old[1]
            if self.provider=='DART': params['crtfc_key']=self._key
            code=ErrorCode.UNAVAILABLE
            for attempt in range(3):
                delay=self._next-self.clock()
                if delay>0: self.sleep(delay)
                self._next=self.clock()+(0.25 if self.provider=='SEC' else 0.5)
                response=None
                try:
                    # Stream with a hard compressed-body bound. ZIP expansion has
                    # an independent bound in the DART mapper.
                    with self._client.stream('GET',url,params=params) as stream:
                        response=stream
                        chunks=[];size=0
                        for chunk in stream.iter_bytes():
                            size+=len(chunk)
                            if size>32*1024*1024: raise ResearchError(ErrorCode.INVALID_RESPONSE)
                            chunks.append(chunk)
                        body=b''.join(chunks)
                except httpx.TransportError:
                    code=ErrorCode.UNAVAILABLE
                else:
                    status=response.status_code
                    if status==200:
                        payload=Payload(body,self.now())
                        if self.provider=='DART':
                            if path.endswith('.json'):
                                data=payload.json()
                                if not isinstance(data,dict): raise ResearchError(ErrorCode.INVALID_RESPONSE)
                                if data.get('status')!='000': return payload
                            if path.endswith('.xml') and not body.startswith(b'PK'): return payload
                        self._cache[key]=(self.clock(),payload)
                        while len(self._cache)>self._capacity or sum(len(v[1].body) for v in self._cache.values())>64*1024*1024:
                            self._cache.popitem(last=False)
                        return payload
                    if status not in (429,500,502,503,504):
                        code=ErrorCode.AUTHENTICATION if status in (401,403) else ErrorCode.NOT_FOUND if status==404 else ErrorCode.INVALID_REQUEST
                        raise ResearchError(code)
                    code=ErrorCode.RATE_LIMITED if status==429 else ErrorCode.UNAVAILABLE
                    retry=response.headers.get('Retry-After','')
                    if retry.isdigit():
                        if int(retry)>10: raise ResearchError(code)
                        self._next=max(self._next,self.clock()+int(retry))
                if attempt<2: self.sleep(0.25*(2**attempt))
            raise ResearchError(code)

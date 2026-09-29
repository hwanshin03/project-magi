"""NVIDIA-only metadata adapter. Network access requires an injected transport."""
from .provider import CompanyProvider


class NvidiaProvider(CompanyProvider):
    provider = 'nvidia_official'

    def __init__(self, source_ids=('nvidia_newsroom',), **kwargs):
        super().__init__(source_ids, **kwargs)

"""Samsung Electronics only, retaining internal 005930 / KR and original language."""
from .provider import CompanyProvider


class SamsungProvider(CompanyProvider):
    provider = 'samsung_electronics_official'

    def __init__(self, source_ids=('samsung_newsroom_en', 'samsung_newsroom_ko'), **kwargs):
        super().__init__(source_ids, **kwargs)

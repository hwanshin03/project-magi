"""Optional bounded untrusted display, with no agent integration."""
from ..news.presentation import render_news, render_news_context


POLICY_NOTE = ('PRIMARY means first-party, not unbiased or objectively correct. '
               'SOURCE COUNT != IMPORTANCE. Translations and repeated announcements '
               'are not independent corroboration. No financial impact or recommendation is inferred.')


def render_company_sources(pack, *, language='en', max_characters=40000):
    result = POLICY_NOTE + '\n' + render_news(pack, language=language, max_characters=max_characters)
    if len(result) > max_characters: raise ValueError('Company rendering exceeds bound')
    return result


def company_context(instructions, question, pack, *, max_characters=60000):
    # Data never enters system instructions. Existing shared context boundary is reused.
    return render_news_context(instructions + '\n' + POLICY_NOTE, question, pack, max_characters=max_characters)

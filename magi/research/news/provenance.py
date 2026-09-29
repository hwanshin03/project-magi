"""Descriptive source families, never importance scores or proof of independence."""


def source_family(article):
    # A company's newsroom, IR site and translations share one first-party family.
    if article.metadata.get('official_item_id'):
        return article.metadata['source_family']
    # Explicit syndication origin takes precedence over the republishing outlet.
    if article.metadata.get('syndication_origin'):
        return 'SYNDICATION:' + article.metadata['syndication_origin']
    return 'PUBLISHER:' + article.publisher

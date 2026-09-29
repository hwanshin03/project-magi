"""Duplicate groups preserve all versions and provenance. No headline similarity."""
from dataclasses import dataclass
from .models import NewsArticle


@dataclass(frozen=True)
class DuplicateGroup:
    representative: NewsArticle
    members: tuple
    likely_syndicated: bool


def deduplicate(articles):
    articles=tuple(articles)
    if any(not isinstance(a,NewsArticle) for a in articles): raise ValueError('Expected normalized articles')
    unique={a.article_id:a for a in articles}
    if any(unique[a.article_id]!=a for a in articles): raise ValueError('Conflicting article identity')
    ordered=sorted(unique.values(),key=lambda a:a.article_id)
    parents=list(range(len(ordered)))
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]];i=parents[i]
        return i
    keys={}
    for i,a in enumerate(ordered):
        identifiers=[('url',a.url)]
        if a.external_id: identifiers.append(('external',a.provider,a.external_id))
        # Explicit provider associations group only already-normalized top-level
        # articles. Members and independent publishers are never discarded.
        for similar_id in a.metadata.get('similar_external_ids', ()):
            identifiers.append(('external',a.provider,similar_id))
        wire=a.metadata.get('syndication_origin');story=a.metadata.get('syndication_id')
        if wire and story: identifiers.append(('wire',wire,story))
        for key in identifiers:
            if key in keys: parents[root(i)]=root(keys[key])
            else: keys[key]=i
    groups={}
    for i,a in enumerate(ordered): groups.setdefault(root(i),[]).append(a)
    result=[]
    for members in groups.values():
        # Representative is only a display convenience; revisions are never discarded.
        representative=max(members,key=lambda a:(a.retrieved_at,a.article_id))
        result.append(DuplicateGroup(representative,tuple(members),len({a.publisher for a in members})>1))
    return tuple(sorted(result,key=lambda g:g.representative.article_id))

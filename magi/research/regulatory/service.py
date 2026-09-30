"""Attributed facts and explicit publication relationships, without impact analysis."""
from datetime import datetime
from ..models import ResearchSource, EvidenceItem, SourceLocator, SourceType, Authority, Category
from .models import RegulatoryItem, RegulatoryEvent, RegulatoryBundle, RegulatoryRelationship
from .catalog import RegulatoryError


def deduplicate_items(items):
    unique={}
    for item in items:
        if not isinstance(item,RegulatoryItem): raise RegulatoryError('INVALID_ITEM')
        prior=unique.get(item.item_id)
        if prior is None or item.retrieved_at>prior.retrieved_at: unique[item.item_id]=item
    return tuple(unique[key] for key in sorted(unique))


def normalize(item):
    family='GOVERNMENT_REGULATORY'
    agency_family=family+':'+item.jurisdiction+':'+item.agency
    dates={name:getattr(item,name).isoformat() if getattr(item,name) else None for name in ('published_at','effective_at')}
    metadata={'item_id':item.item_id,'source_family':family,'agency_family':agency_family,
        'agency':item.agency,'jurisdiction':item.jurisdiction,'regulatory_type':item.regulatory_type.value,
        'regulatory_status':item.status.value,'legal_reference':item.legal_reference,
        'docket_or_reference':item.docket_or_reference,'affected_industries':item.affected_industries,
        'affected_entities':item.affected_entities,'source_metadata':item.metadata,**dates,
        'publication_precision':'TIMESTAMP' if isinstance(item.published_at,datetime) else 'DATE' if item.published_at else 'UNKNOWN',
        'effective_precision':'TIMESTAMP' if isinstance(item.effective_at,datetime) else 'DATE' if item.effective_at else 'UNKNOWN'}
    source=ResearchSource(SourceType.WEB_SOURCE,Authority.PRIMARY,'regulatory:'+item.source_key,
        item.title,item.agency,item.retrieved_at,url=item.url,
        published_at=item.published_at if isinstance(item.published_at,datetime) else None,
        document_type='REGULATORY_PUBLICATION',language=item.language,external_id=item.item_id,metadata=metadata)
    statement=item.agency+' published the following source statement: '+(item.summary or item.title)
    evidence=EvidenceItem(source.source_id,item.agency+' regulatory publication',Category.REGULATORY,
        statement,item.retrieved_at,as_of=item.published_at if isinstance(item.published_at,datetime) else None,
        source_locator=SourceLocator(article_section='official summary' if item.summary else 'official title'),
        metadata={**metadata,'attributed':True,'source_field':'summary' if item.summary else 'title'})
    event=None
    if item.metadata.get('classification_explicit'):
        event=RegulatoryEvent(item.agency,item.jurisdiction,item.regulatory_type,item.status,
            item.published_at,item.effective_at,item.legal_reference,item.docket_or_reference,
            (source.source_id,),(evidence.evidence_id,),item.affected_industries,item.affected_entities,
            {'item_id':item.item_id,'source_family':family,'agency_family':agency_family,
             'rule_references':item.metadata.get('rule_references',())})
    return source,evidence,event


def relationships_for(items):
    links=set()
    for left in items:
        for right in items:
            if left.item_id==right.item_id or (left.agency,left.jurisdiction)!=(right.agency,right.jurisdiction): continue
            shared=set(left.metadata.get('rule_references',())) & set(right.metadata.get('rule_references',()))
            # A shared explicit rule identifier is a family, not equivalence or
            # proof that a final document supersedes a particular proposal.
            if shared and left.item_id<right.item_id:
                links.add(RegulatoryRelationship('RULE_FAMILY',left.item_id,right.item_id))
            for kind,reference in left.metadata.get('related_references',()):
                if reference==right.docket_or_reference:
                    links.add(RegulatoryRelationship(kind,left.item_id,right.item_id))
            if (left.language!=right.language and left.regulatory_type==right.regulatory_type and left.status==right.status
                    and right.url in left.metadata.get('translation_urls',()) and left.url in right.metadata.get('translation_urls',())
                    and left.item_id<right.item_id):
                links.add(RegulatoryRelationship('TRANSLATION_OF',left.item_id,right.item_id))
    return tuple(sorted(links,key=lambda link:(link.kind,link.from_item_id,link.to_item_id)))


def build_regulatory_bundle(items, *, created_at):
    items=deduplicate_items(items)
    if len(items)>200: raise RegulatoryError('ITEM_LIMIT')
    rows=[normalize(item) for item in items]
    return RegulatoryBundle(items,tuple(r[0] for r in rows),tuple(r[1] for r in rows),
        tuple(r[2] for r in rows if r[2] is not None),relationships_for(items),created_at)


class RegulatoryService:
    def __init__(self, provider): self.provider=provider

    def collect(self, query):
        result=self.provider.fetch_items(query)
        if len(result)>query.limit or any(i.source_key!=query.source_key for i in result):
            raise RegulatoryError('INVALID_PROVIDER_RESULT')
        created_at=max((query.as_of,*(i.retrieved_at for i in result)))
        return build_regulatory_bundle(result,created_at=created_at)

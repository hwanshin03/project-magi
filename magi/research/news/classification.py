"""Convert explicitly attributed reporting, without language-model/keyword inference."""
from ..models import ResearchSource, EvidenceItem, SourceLocator, Category, Authority
from .models import (ResearchEvent, ResearchCatalyst, EventType, ContentKind,
                     VerificationStatus, Direction)

CATEGORIES={EventType.EARNINGS:Category.FINANCIAL,EventType.GUIDANCE:Category.GUIDANCE,
    EventType.REGULATORY:Category.REGULATORY,EventType.LEGAL:Category.RISK,
    EventType.MACRO:Category.MACRO,EventType.INDUSTRY:Category.INDUSTRY,
    EventType.MANAGEMENT:Category.MANAGEMENT,EventType.PRICE_MOVEMENT:Category.MARKET_PRICE}


def source_for(article,ticker,subject):
    return ResearchSource(article.source_type,article.authority,article.provider,article.title,
        article.publisher,article.retrieved_at,url=article.url,published_at=article.published_at,
        ticker=ticker,company_name=subject,language=article.language,
        external_id=(article.external_id if article.metadata.get('event_extraction')=='NONE' else article.article_id),metadata={'article_id':article.article_id,
            'provider_external_id':article.external_id,'authors':article.authors})


def normalize(article,ticker,subject,created_at):
    a=article;c=a.classification;s=source_for(a,ticker,subject)
    content=a.summary or a.title
    # Even FACT is an explicitly attributed source assertion, not MAGI certification.
    attribution=(c.claimant+' (as reported by '+a.publisher+')') if c.claimant else a.publisher
    statement=c.content_kind.value+' — '+attribution+': '+content
    unclassified=a.metadata.get('event_extraction')=='NONE'
    if unclassified and (c.event_type.value!='OTHER' or c.catalyst_type is not None):
        raise ValueError('Unclassified reporting cannot declare an event or catalyst')
    provenance=({'provider':a.provider,'classification_source':c.classification_source,
                 'source_field':'description' if a.summary else 'title'} if unclassified else {})
    e=EvidenceItem(s.source_id,subject,Category.OTHER if unclassified else CATEGORIES.get(c.event_type,Category.CATALYST),statement,
        a.retrieved_at,ticker=ticker,as_of=c.event_time or a.published_at,
        source_locator=SourceLocator(article_section='summary' if a.summary else 'headline'),
        metadata={'article_id':a.article_id,'content_kind':c.content_kind.value,'claimant':c.claimant,
                  'attributed':True,'verification_status':c.status.value,**provenance})
    if unclassified:
        return s,e,None,None
    event=ResearchEvent(subject,ticker,c.event_type,a.title,statement,c.event_time,a.published_at,
        (s.source_id,),(e.evidence_id,),c.status,metadata={
            'event_namespace':c.event_namespace,'event_key':c.event_key,
            'assertion_key':c.assertion_key,'assertion_value':c.assertion_value,
            'article_id':a.article_id,'market_moving':c.market_moving,
            'classification_source':c.classification_source,'sentiment':c.sentiment.value})
    catalyst=None
    if c.catalyst_type is not None:
        catalyst=ResearchCatalyst(ticker,c.catalyst_type,c.direction,c.time_horizon,statement,
            (e.evidence_id,),(s.source_id,),c.confidence,c.status,created_at,c.classification_source)
    return s,e,event,catalyst

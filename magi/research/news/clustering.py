"""Cluster only explicit shared event identities, never similar headlines or dates alone."""
from datetime import timezone
from ..models import Authority
from .models import EventCluster, VerificationStatus as Status


def cluster_events(events,sources):
    source_map={s.source_id:s for s in sources};groups={}
    for e in events:
        ns=e.metadata.get('event_namespace');key=e.metadata.get('event_key')
        # A date bucket separates explicit reused event keys across days.
        day=e.occurred_at.astimezone(timezone.utc).date().isoformat() if e.occurred_at else None
        group=(e.ticker,e.event_type.value,ns,key,day) if ns and key else (e.event_id,)
        groups.setdefault(group,[]).append(e)
    output=[]
    for members in groups.values():
        members=sorted(members,key=lambda e:e.event_id)
        refs=tuple(sorted({r for e in members for r in e.source_ids}))
        evidence=tuple(sorted({r for e in members for r in e.evidence_ids}))
        assertions={}
        for e in members:
            key=e.metadata.get('assertion_key')
            if key: assertions.setdefault(key,set()).add(e.metadata['assertion_value'])
        statuses={e.status for e in members}
        if Status.DISPUTED in statuses or any(len(v)>1 for v in assertions.values()): status=Status.DISPUTED
        elif Status.RUMOR in statuses: status=Status.RUMOR
        elif Status.UNCONFIRMED in statuses: status=Status.UNCONFIRMED
        elif Status.CONFIRMED_OFFICIAL in statuses: status=Status.CONFIRMED_OFFICIAL
        elif Status.CORROBORATED in statuses: status=Status.CORROBORATED
        else: status=Status.SINGLE_SOURCE
        # Multiple publishers alone cannot establish independent corroboration.
        times={e.occurred_at for e in members if e.occurred_at}
        output.append(EventCluster(members[0].ticker,members[0].event_type,
            next(iter(times)) if len(times)==1 else None,refs,evidence,
            tuple(r for r in refs if source_map[r].authority==Authority.PRIMARY),
            tuple(r for r in refs if source_map[r].authority==Authority.SECONDARY),
            tuple(e.event_id for e in members),status))
    return tuple(sorted(output,key=lambda c:c.cluster_id))

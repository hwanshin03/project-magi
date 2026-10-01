"""Immutable references to existing snapshots, never a second evidence store."""
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
import re
from ..models import identity, instant
from ..security import text


class InputFamily(str, Enum):
    RESEARCH = 'RESEARCH'
    FINANCIAL_SNAPSHOT = 'FINANCIAL_SNAPSHOT'
    NEWS = 'NEWS'
    REGULATORY = 'REGULATORY'
    MARKET = 'MARKET'


class Availability(str, Enum):
    NOT_SUPPLIED = 'NOT_SUPPLIED'
    AVAILABLE = 'AVAILABLE'
    EMPTY = 'EMPTY'
    UNAVAILABLE = 'UNAVAILABLE'
    PARTIAL = 'PARTIAL'


class LineageKind(str, Enum):
    DERIVED_FROM = 'DERIVED_FROM'
    REPRESENTATION_OF = 'REPRESENTATION_OF'
    TRANSLATION_OF = 'TRANSLATION_OF'
    VERSION_OF = 'VERSION_OF'


def label(value, limit=200):
    text(value)
    if len(value) > limit: raise ValueError('Balancing text exceeds bound')


def sequence(values, cls):
    if not isinstance(values, (tuple, list)) or any(type(v) is not cls for v in values):
        raise ValueError('Invalid balancing sequence')
    return tuple(values)


def strings(values):
    values = sequence(values, str)
    for value in values: label(value)
    return tuple(sorted(set(values)))


def fingerprint(value):
    """Use the existing canonical tagged encoder and SHA-256 identity utility.

    Only normalized registered objects/value tuples enter this boundary. Supplied
    observation/retrieval times are content; runtime clocks/paths never are.
    """
    from ..serialization import encode
    encoded = encode(value)
    def check(node):
        if isinstance(node, str):
            if re.search(r'(?:file://|/(?:Users|home|private|tmp|var|etc)/|[A-Za-z]:\\)', node):
                raise ValueError('Local paths cannot enter balancing fingerprints')
        elif isinstance(node, dict):
            for key, child in node.items(): check(key); check(child)
        elif isinstance(node, list):
            for child in node: check(child)
    check(encoded)
    return identity('', 'BF', encoded)


def finish(obj, name, prefix):
    payload = tuple((f.name, getattr(obj, f.name)) for f in fields(obj) if f.name != name)
    computed = identity('', prefix, fingerprint(payload))
    if getattr(obj, name) not in ('', computed): raise ValueError('Forged balancing identity')
    object.__setattr__(obj, name, computed)


@dataclass(frozen=True)
class InstrumentIdentity:
    market: str
    symbol: str

    def __post_init__(self):
        for value in (self.market, self.symbol):
            label(value, 32)
            if not re.fullmatch(r'[A-Z0-9][A-Z0-9_.-]*', value):
                raise ValueError('Instrument identity must be explicitly normalized')


@dataclass(frozen=True)
class EntityIdentity:
    namespace: str
    identifier: str
    display_name: str | None = None

    def __post_init__(self):
        label(self.namespace); label(self.identifier)
        if self.display_name is not None: label(self.display_name)


@dataclass(frozen=True)
class TargetIdentity:
    instrument: InstrumentIdentity | None = None
    entity: EntityIdentity | None = None
    mapping_asserted_by: str | None = None
    mapping_reference: str | None = None

    def __post_init__(self):
        if self.instrument is None and self.entity is None: raise ValueError('Target identity required')
        if self.instrument is not None and type(self.instrument) is not InstrumentIdentity: raise ValueError('Invalid instrument')
        if self.entity is not None and type(self.entity) is not EntityIdentity: raise ValueError('Invalid entity')
        if self.instrument is not None and self.entity is not None:
            label(self.mapping_asserted_by); label(self.mapping_reference)
        elif self.mapping_asserted_by is not None or self.mapping_reference is not None:
            raise ValueError('Mapping attribution requires both identities')


@dataclass(frozen=True)
class SelectionRequest:
    target: TargetIdentity
    as_of: datetime
    scope: str
    languages: tuple = ()
    policy_version: str = '7E.1-v1'
    input_manifest_id: str | None = None
    request_id: str = ''

    def __post_init__(self):
        if type(self.target) is not TargetIdentity: raise ValueError('Invalid target')
        instant(self.as_of); label(self.scope, 4000); label(self.policy_version)
        languages = strings(self.languages)
        if any(not re.fullmatch(r'[a-z]{2,3}(?:-[A-Z]{2})?', x) for x in languages): raise ValueError('Invalid language')
        object.__setattr__(self, 'languages', languages)
        if self.input_manifest_id is not None: label(self.input_manifest_id)
        finish(self, 'request_id', 'BR')


@dataclass(frozen=True)
class QualifiedReference:
    snapshot_id: str
    object_kind: str
    object_id: str | None
    content_fingerprint: str
    reference_id: str = ''

    def __post_init__(self):
        for value, prefix in ((self.snapshot_id, 'BI'), (self.content_fingerprint, 'BF')):
            label(value)
            if not re.fullmatch(prefix + r'_[0-9a-f]{64}', value): raise ValueError('Invalid fingerprint reference')
        label(self.object_kind)
        if self.object_id is not None: label(self.object_id)
        finish(self, 'reference_id', 'BQ')


@dataclass(frozen=True)
class InputSnapshot:
    family: InputFamily
    container: object
    snapshot_id: str = ''
    content_fingerprint: str = field(init=False)

    def __post_init__(self):
        from .inputs import family_for, validate_container
        if type(self.family) is not InputFamily or self.family != family_for(self.container):
            raise ValueError('Input family/container mismatch')
        validate_container(self.container)
        object.__setattr__(self, 'content_fingerprint', fingerprint(self.container))
        finish(self, 'snapshot_id', 'BI')


@dataclass(frozen=True)
class InputAvailability:
    family: InputFamily
    state: Availability
    snapshot_ids: tuple = ()
    error_codes: tuple = ()
    omissions: tuple = ()
    input_key: str = 'default'

    def __post_init__(self):
        label(self.input_key)
        if type(self.family) is not InputFamily or type(self.state) is not Availability: raise ValueError('Invalid availability')
        for name in ('snapshot_ids', 'error_codes', 'omissions'):
            object.__setattr__(self, name, strings(getattr(self, name)))
        if any(not re.fullmatch(r'[A-Z][A-Z0-9_.:-]{0,159}', c) for c in self.error_codes): raise ValueError('Use static error codes')
        if self.state in (Availability.NOT_SUPPLIED, Availability.UNAVAILABLE) and self.snapshot_ids:
            raise ValueError('Unavailable input cannot contain snapshots')
        if self.state in (Availability.AVAILABLE, Availability.EMPTY, Availability.PARTIAL) and not self.snapshot_ids:
            raise ValueError('Supplied state requires a snapshot, even when empty')
        if self.state == Availability.UNAVAILABLE and not self.error_codes: raise ValueError('Unavailable reason required')
        if self.state == Availability.PARTIAL and not (self.error_codes or self.omissions): raise ValueError('Partial reason required')
        if self.state in (Availability.NOT_SUPPLIED, Availability.AVAILABLE, Availability.EMPTY) and (self.error_codes or self.omissions):
            raise ValueError('Errors/omissions require unavailable or partial state')


@dataclass(frozen=True)
class LineageReference:
    kind: LineageKind
    child: QualifiedReference
    parent: QualifiedReference
    asserted_by: str
    basis: str

    def __post_init__(self):
        if type(self.kind) is not LineageKind: raise ValueError('Invalid lineage kind')
        if type(self.child) is not QualifiedReference or type(self.parent) is not QualifiedReference or self.child == self.parent:
            raise ValueError('Invalid lineage endpoints')
        label(self.asserted_by); label(self.basis)


@dataclass(frozen=True)
class EvidenceUniverse:
    request: SelectionRequest
    inputs: tuple
    availability: tuple
    declared_lineage: tuple = ()
    universe_id: str = ''
    references: tuple = field(init=False)
    lineage: tuple = field(init=False)
    manifest_id: str = field(init=False)

    def __post_init__(self):
        from .inputs import index_inputs, record_count, validate_lineage
        if type(self.request) is not SelectionRequest: raise ValueError('Invalid request')
        inputs = sequence(self.inputs, InputSnapshot)
        unique = {s.snapshot_id: s for s in inputs}
        for s in inputs:
            if s != unique[s.snapshot_id]: raise ValueError('Conflicting input identity')
        inputs = tuple(unique[k] for k in sorted(unique))
        object.__setattr__(self, 'inputs', inputs)
        availability = sequence(self.availability, InputAvailability)
        if {a.family for a in availability} != set(InputFamily):
            raise ValueError('Availability must explicitly cover each input family')
        if len({(a.family, a.input_key) for a in availability}) != len(availability):
            raise ValueError('Duplicate availability input key')
        availability = tuple(sorted(availability, key=lambda a: (a.family.value, a.input_key)))
        covered = []
        for a in availability:
            if any(key not in unique or unique[key].family != a.family for key in a.snapshot_ids):
                raise ValueError('Availability snapshot mismatch')
            supplied = tuple(unique[key] for key in a.snapshot_ids)
            covered.extend(a.snapshot_ids)
            known_omissions = {key for s in supplied for key in getattr(s.container, 'selection_omissions', ())}
            if not known_omissions <= set(a.omissions): raise ValueError('Existing omissions cannot be hidden')
            count = sum(record_count(s.container) for s in supplied)
            if a.state == Availability.AVAILABLE and not count: raise ValueError('Available input has no records')
            if a.state == Availability.EMPTY and count: raise ValueError('Empty input contains records')
        if len(covered) != len(set(covered)) or set(covered) != set(unique):
            raise ValueError('Every supplied snapshot needs exactly one manifest entry')
        object.__setattr__(self, 'availability', availability)
        manifest_id = fingerprint(tuple((s.snapshot_id, s.content_fingerprint) for s in inputs) + availability)
        object.__setattr__(self, 'manifest_id', manifest_id)
        if self.request.input_manifest_id is not None and self.request.input_manifest_id != manifest_id:
            raise ValueError('Request manifest mismatch')
        refs, derived = index_inputs(inputs)
        declared = sequence(self.declared_lineage, LineageReference)
        # Caller assertions are attributable but not verified semantic conclusions.
        if any(x.asserted_by == 'structured-input-v1' for x in declared): raise ValueError('Reserved lineage authority')
        declared = tuple(sorted(set(declared), key=fingerprint))
        object.__setattr__(self, 'declared_lineage', declared)
        lineage = tuple(sorted(set((*derived, *declared)), key=fingerprint))
        validate_lineage(refs, lineage)
        object.__setattr__(self, 'references', refs)
        object.__setattr__(self, 'lineage', lineage)
        finish(self, 'universe_id', 'BU')

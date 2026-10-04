"""Operation-local validation receipts; no global artifact or secret cache."""
from contextvars import ContextVar
from functools import wraps
from magi.storage import sensitive_operation, StorageError

_receipts = ContextVar('research_validation_receipts', default=None)


def operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _receipts.get() is not None:
            return function(*args, **kwargs)
        try:
            with sensitive_operation():
                token = _receipts.set({})
                try:
                    return function(*args, **kwargs)
                finally:
                    _receipts.reset(token)
        except StorageError:
            # Preserve the research boundary's existing sanitized error type.
            raise ValueError('Sensitive research content rejected') from None
    return wrapped


def _remember_validated(value, encoded):
    receipts = _receipts.get()
    if receipts is not None:
        # Retaining the object prevents Python id reuse during this operation.
        receipts[id(value)] = (value, encoded, _runtime_state(value))


def _already_validated(value, encoded):
    receipt = (_receipts.get() or {}).get(id(value))
    # Compare every serialized field, including derived identities. A frozen
    # object altered with object.__setattr__ cannot use an earlier receipt.
    return receipt is not None and receipt[0] is value and receipt[1] == encoded and receipt[2] == _runtime_state(value)


def _runtime_state(value):
    """Seal nonserialized indexes as well as the canonical artifact fields."""
    from dataclasses import fields, is_dataclass
    seen = set(); state = []
    def ref_key(ref):
        return tuple(getattr(ref,f.name) for f in fields(ref))
    def visit(obj):
        if id(obj) in seen: return
        seen.add(id(obj))
        if is_dataclass(obj):
            if hasattr(obj,'_reference_pairs'):
                state.append((id(obj),tuple((id(o),ref_key(r)) for o,r in obj._reference_pairs)))
            if hasattr(obj,'_objects'):
                state.append((id(obj),id(obj._objects),id(obj._snapshots),
                              tuple((ref_key(r),id(o)) for r,o in obj._objects.items())))
            for f in fields(obj):visit(getattr(obj,f.name))
        elif isinstance(obj,tuple):
            for child in obj:visit(child)
    visit(value)
    return tuple(state)


def _verify_runtime_indexes(original, rebuilt):
    """A fresh reconstruction must agree with all derived runtime indexes too."""
    from dataclasses import fields, is_dataclass
    from types import MappingProxyType
    seen = set()
    def visit(old,new):
        if id(old) in seen:return
        seen.add(id(old))
        if is_dataclass(old):
            if hasattr(new,'_reference_pairs'):
                from .balancing.inputs import members
                pairs=getattr(old,'_reference_pairs',())
                if (tuple(id(o) for o,_ in pairs) != tuple(id(o) for o in members(old.container))
                        or tuple(r for _,r in pairs) != tuple(r for _,r in new._reference_pairs)):
                    raise ValueError('Invalid runtime reference index')
            if hasattr(new,'_objects'):
                if not isinstance(getattr(old,'_objects',None),MappingProxyType) or not isinstance(getattr(old,'_snapshots',None),MappingProxyType):
                    raise ValueError('Invalid runtime universe index')
                expected={r:o for s in old.inputs for o,r in s._reference_pairs}
                if (old._objects.keys()!=expected.keys() or any(old._objects[r] is not o for r,o in expected.items())
                        or set(old._snapshots)!={s.snapshot_id for s in old.inputs}
                        or any(old._snapshots[s.snapshot_id] is not s for s in old.inputs)):
                    raise ValueError('Invalid runtime universe index')
            for f in fields(old):visit(getattr(old,f.name),getattr(new,f.name))
        elif isinstance(old,tuple):
            for a,b in zip(old,new):visit(a,b)
    visit(original,rebuilt)

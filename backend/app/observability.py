import os
ENABLED = os.getenv('LANGFUSE_ENABLED','false').lower()=='true'
try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None
_lf = Langfuse(public_key=os.getenv('LANGFUSE_PUBLIC_KEY'), secret_key=os.getenv('LANGFUSE_SECRET_KEY'), host=os.getenv('LANGFUSE_HOST','https://cloud.langfuse.com')) if (ENABLED and Langfuse) else None

def start_trace(name, metadata=None):
    if not _lf: return None
    return _lf.trace(name=name, input=metadata or {})

def end_trace(trace, status='success', output=None):
    if not trace: return
    trace.update(output=output or {}, metadata={'status': status})

def start_span(trace, name, metadata=None):
    if not trace: return None
    return trace.span(name=name, input=metadata or {})

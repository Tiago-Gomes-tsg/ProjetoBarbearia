from .observability import tracked_cache as cache
import hashlib
from ipaddress import ip_address


def get_client_ip(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    candidatos = []
    if forwarded_for:
        candidatos.append(forwarded_for.split(',')[0].strip())
    candidatos.append(request.META.get('REMOTE_ADDR', ''))
    for candidato in candidatos:
        try:
            return str(ip_address(candidato))
        except ValueError:
            continue
    return 'unknown'


def is_rate_limited(request, scope, limit, window_seconds, identifier=None):
    raw_identity = str(identifier or get_client_ip(request))
    identity = hashlib.sha256(raw_identity.encode('utf-8')).hexdigest()
    empresa_id = getattr(getattr(request, 'empresa', None), 'pk', 'global')
    key = f"rate-limit:{empresa_id}:{scope}:{identity}"

    if cache.add(key, 1, window_seconds):
        return False

    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, window_seconds)
        return False

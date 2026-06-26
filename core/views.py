import os
import time
from uuid import uuid4

from django.db import connection
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache

from agendamentos.observability import log_event, metrics, process_metrics, tracked_cache


def _component_health():
    checks = {}
    healthy = True

    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        checks['database'] = {
            'status': 'ok',
            'vendor': connection.vendor,
            'latency_ms': round((time.perf_counter() - started) * 1_000, 3),
        }
    except Exception as exc:
        healthy = False
        log_event(
            'health_database_failed',
            level='error',
            exc_info=(type(exc), exc, exc.__traceback__),
            error_type=exc.__class__.__name__,
        )
        checks['database'] = {
            'status': 'error',
            'error_type': exc.__class__.__name__,
            'latency_ms': round((time.perf_counter() - started) * 1_000, 3),
        }

    started = time.perf_counter()
    cache_key = f'health:{uuid4().hex}'
    try:
        tracked_cache.set(cache_key, 'ok', timeout=10)
        cache_ok = tracked_cache.get(cache_key) == 'ok'
        tracked_cache.delete(cache_key)
        if not cache_ok:
            raise RuntimeError('cache_read_after_write_failed')
        checks['cache'] = {
            'status': 'ok',
            'latency_ms': round((time.perf_counter() - started) * 1_000, 3),
        }
    except Exception as exc:
        healthy = False
        log_event(
            'health_cache_failed',
            level='error',
            exc_info=(type(exc), exc, exc.__traceback__),
            error_type=exc.__class__.__name__,
        )
        checks['cache'] = {
            'status': 'error',
            'error_type': exc.__class__.__name__,
            'latency_ms': round((time.perf_counter() - started) * 1_000, 3),
        }
    return healthy, checks


def _require_superuser(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_active or not user.is_superuser:
        # 404 nao revela que a superficie de diagnostico existe.
        raise Http404('Pagina nao encontrada.')


@never_cache
def readiness_check(request):
    """Sinal minimo para o orquestrador, sem versao, vendor ou latencias."""

    healthy, _ = _component_health()
    return JsonResponse(
        {'status': 'ok' if healthy else 'degraded'},
        status=200 if healthy else 503,
    )


@never_cache
def health_check(request):
    """Diagnostico detalhado disponivel somente na sessao do superuser."""

    _require_superuser(request)
    healthy, checks = _component_health()
    return JsonResponse(
        {
            'status': 'ok' if healthy else 'degraded',
            'checks': checks,
            'service': 'projeto-barbearia',
            'version': os.getenv('RENDER_GIT_COMMIT', os.getenv('APP_VERSION', 'dev'))[:12],
            'request_id': getattr(request, 'request_id', None),
        },
        status=200 if healthy else 503,
    )


@never_cache
def metrics_check(request):
    """Metricas detalhadas disponiveis somente na sessao do superuser."""

    _require_superuser(request)
    return JsonResponse({
        'service': 'projeto-barbearia',
        'metrics': metrics.snapshot(),
        'process': process_metrics(),
        'request_id': getattr(request, 'request_id', None),
    })

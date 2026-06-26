"""Observabilidade estruturada, segura e sem dependencia de fornecedor."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache as django_cache
from django.db import connection


request_id_var = ContextVar('request_id', default=None)
user_id_var = ContextVar('user_id', default=None)
tenant_id_var = ContextVar('tenant_id', default=None)
action_var = ContextVar('action', default=None)

SENSITIVE_KEYS = re.compile(
    r'(password|senha|token|secret|api[_-]?key|authorization|cookie|session|csrf)',
    re.IGNORECASE,
)
PERSONAL_KEYS = re.compile(
    r'(email|e-mail|telefone|phone|cpf|cnpj|ip|user[_-]?agent|nome)',
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r'([A-Z0-9._%+-])[^@\s]*(@[A-Z0-9.-]+\.[A-Z]{2,})', re.IGNORECASE)
BEARER_RE = re.compile(r'(?i)bearer\s+[A-Za-z0-9._~+/=-]+')
CREDENTIAL_RE = re.compile(
    r'(?i)\b(password|senha|token|secret|api[_-]?key)\b\s*([=:])\s*([^\s,;]+)'
)
PHONE_RE = re.compile(r'(?<!\d)\d{10,14}(?!\d)')

logger = logging.getLogger('agendamentos.observability')


def _mask_personal(value):
    text = str(value)
    if '@' in text:
        return EMAIL_RE.sub(r'\1***\2', text)
    digits = re.sub(r'\D', '', text)
    if len(digits) >= 7:
        return f'***{digits[-4:]}'
    return '[MASKED]'


def sanitize(value, key=''):
    """Remove segredos e mascara dados pessoais inclusive em estruturas aninhadas."""

    if SENSITIVE_KEYS.search(str(key)):
        return '[REDACTED]'
    if PERSONAL_KEYS.search(str(key)) and value not in (None, ''):
        return _mask_personal(value)
    if isinstance(value, dict):
        return {str(k): sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item, key) for item in value]
    if isinstance(value, str):
        value = BEARER_RE.sub('Bearer [REDACTED]', value)
        value = CREDENTIAL_RE.sub(r'\1\2[REDACTED]', value)
        value = EMAIL_RE.sub(r'\1***\2', value)
        return PHONE_RE.sub('[MASKED_PHONE]', value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class SafeJsonFormatter(logging.Formatter):
    """Formata todos os logs como JSON e inclui o contexto ativo da requisicao."""

    RESERVED = set(logging.LogRecord(None, 0, '', 0, '', (), None).__dict__) | {
        'message', 'asctime',
    }

    def format(self, record):
        payload = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': 'fatal' if record.levelno >= logging.CRITICAL else record.levelname.lower(),
            'logger': record.name,
            'event': getattr(record, 'event', 'log'),
            'message': record.getMessage(),
            'request_id': getattr(record, 'request_id', None) or request_id_var.get(),
            'user_id': getattr(record, 'user_id', None) or user_id_var.get(),
            'tenant_id': getattr(record, 'tenant_id', None) or tenant_id_var.get(),
            'action': getattr(record, 'action', None) or action_var.get(),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self.RESERVED
            and key not in {'event', 'request_id', 'user_id', 'tenant_id', 'action'}
            and not key.startswith('_')
        }
        if extras:
            payload['context'] = extras
        if record.exc_info:
            payload['stack_trace'] = self.formatException(record.exc_info)
        return json.dumps(sanitize(payload), ensure_ascii=False, separators=(',', ':'))


def update_request_context(*, request_id=None, user_id=None, tenant_id=None, action=None):
    if request_id is not None:
        request_id_var.set(str(request_id))
    if user_id is not None:
        user_id_var.set(str(user_id))
    if tenant_id is not None:
        tenant_id_var.set(str(tenant_id))
    if action is not None:
        action_var.set(str(action))


def log_event(event, level='info', exc_info=None, **context):
    getattr(logger, level)(
        event,
        extra={'event': event, **context},
        exc_info=exc_info,
    )


class _Metrics:
    def __init__(self):
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.values = {
            'requests_total': 0,
            'request_errors_total': 0,
            'request_duration_ms_total': 0.0,
            'queries_total': 0,
            'query_errors_total': 0,
            'query_duration_ms_total': 0.0,
            'cache_hits_total': 0,
            'cache_misses_total': 0,
            'cache_writes_total': 0,
            'exceptions_total': 0,
        }

    def add(self, key, value=1):
        with self.lock:
            self.values[key] = self.values.get(key, 0) + value

    def snapshot(self):
        with self.lock:
            values = dict(self.values)
        values['uptime_seconds'] = round(time.time() - self.started_at, 3)
        return values


metrics = _Metrics()


def process_metrics(elapsed=None, cpu_start=None):
    cpu_seconds = time.process_time()
    result = {'cpu_time_seconds': round(cpu_seconds, 6)}
    if elapsed and cpu_start is not None and elapsed > 0:
        result['cpu_percent_request'] = round(
            max(0.0, (cpu_seconds - cpu_start) / elapsed * 100), 2
        )
    try:
        import psutil

        result['memory_rss_bytes'] = psutil.Process().memory_info().rss
    except ImportError:
        result['memory_rss_bytes'] = None
    try:
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux informa KiB; macOS informa bytes.
        if result['memory_rss_bytes'] is None:
            result['memory_rss_bytes'] = int(
                rss if os.uname().sysname == 'Darwin' else rss * 1024
            )
    except (ImportError, AttributeError):
        result.setdefault('memory_rss_bytes', None)
    return result


def _safe_sql(sql):
    sql = re.sub(r"'(?:''|[^'])*'", '?', str(sql))
    sql = re.sub(r'\b\d{5,}\b', '?', sql)
    return ' '.join(sql.split())[:1_000]


class QueryTimingWrapper:
    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        error = None
        try:
            return execute(sql, params, many, context)
        except Exception as exc:
            error = exc
            metrics.add('query_errors_total')
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1_000
            metrics.add('queries_total')
            metrics.add('query_duration_ms_total', duration_ms)
            if getattr(settings, 'QUERY_LOGGING_ENABLED', True):
                log_event(
                    'database_query',
                    level='error' if error else 'info',
                    exc_info=(type(error), error, error.__traceback__) if error else None,
                    database=connection.alias,
                    vendor=connection.vendor,
                    duration_ms=round(duration_ms, 3),
                    many=bool(many),
                    sql=_safe_sql(sql),
                    error_type=error.__class__.__name__ if error else None,
                )


def _cache_key_id(key):
    return hashlib.sha256(str(key).encode('utf-8')).hexdigest()[:16]


class TrackedCache:
    """Proxy do cache que mede hit/miss sem expor chaves ou conteudo."""

    _missing = object()

    def get(self, key, default=None, version=None):
        value = django_cache.get(key, self._missing, version=version)
        hit = value is not self._missing
        metrics.add('cache_hits_total' if hit else 'cache_misses_total')
        log_event('cache_access', operation='get', hit=hit, key_id=_cache_key_id(key))
        return value if hit else default

    def set(self, key, value, timeout=None, version=None):
        result = django_cache.set(key, value, timeout=timeout, version=version)
        metrics.add('cache_writes_total')
        log_event('cache_access', operation='set', hit=None, key_id=_cache_key_id(key))
        return result

    def add(self, key, value, timeout=None, version=None):
        added = django_cache.add(key, value, timeout=timeout, version=version)
        metrics.add('cache_misses_total' if added else 'cache_hits_total')
        log_event('cache_access', operation='add', hit=not added, key_id=_cache_key_id(key))
        return added

    def incr(self, key, delta=1, version=None):
        result = django_cache.incr(key, delta=delta, version=version)
        metrics.add('cache_hits_total')
        log_event('cache_access', operation='incr', hit=True, key_id=_cache_key_id(key))
        return result

    def delete(self, key, version=None):
        result = django_cache.delete(key, version=version)
        metrics.add('cache_writes_total')
        log_event('cache_access', operation='delete', hit=bool(result), key_id=_cache_key_id(key))
        return result


tracked_cache = TrackedCache()


class RequestObservabilityMiddleware:
    """Request ID, query timing, metricas e contexto em toda resposta Django."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = str(uuid4())
        request.request_id = request_id
        tokens = (
            request_id_var.set(request_id),
            user_id_var.set(None),
            tenant_id_var.set(None),
            action_var.set(f'{request.method} {request.path}'),
        )
        started = time.perf_counter()
        cpu_start = time.process_time()
        try:
            with connection.execute_wrapper(QueryTimingWrapper()):
                response = self.get_response(request)
            elapsed = time.perf_counter() - started
            update_request_context(
                user_id=getattr(getattr(request, 'user', None), 'pk', None),
                tenant_id=getattr(getattr(request, 'empresa', None), 'pk', None),
            )
            status_code = getattr(response, 'status_code', 500)
            metrics.add('requests_total')
            metrics.add('request_duration_ms_total', elapsed * 1_000)
            if status_code >= 500:
                metrics.add('request_errors_total')
            perf = process_metrics(elapsed, cpu_start)
            log_event(
                'request_completed',
                method=request.method,
                path=request.path,
                status_code=status_code,
                duration_ms=round(elapsed * 1_000, 3),
                **perf,
            )
            if elapsed * 1_000 >= settings.ALERT_SLOW_REQUEST_MS:
                log_event(
                    'anomaly_slow_request',
                    level='warning',
                    duration_ms=round(elapsed * 1_000, 3),
                    threshold_ms=settings.ALERT_SLOW_REQUEST_MS,
                )
            if perf.get('cpu_percent_request', 0) >= settings.ALERT_CPU_PERCENT:
                log_event(
                    'anomaly_cpu',
                    level='warning',
                    cpu_percent=perf['cpu_percent_request'],
                    threshold_percent=settings.ALERT_CPU_PERCENT,
                )
            memory_bytes = perf.get('memory_rss_bytes') or 0
            if memory_bytes >= settings.ALERT_MEMORY_MB * 1024 * 1024:
                log_event(
                    'anomaly_memory',
                    level='warning',
                    memory_rss_bytes=memory_bytes,
                    threshold_mb=settings.ALERT_MEMORY_MB,
                )
            snapshot = metrics.snapshot()
            if snapshot['requests_total'] >= 20:
                error_rate = (
                    snapshot['request_errors_total'] / snapshot['requests_total'] * 100
                )
                if error_rate >= settings.ALERT_ERROR_RATE_PERCENT:
                    log_event(
                        'anomaly_error_rate',
                        level='warning',
                        error_rate_percent=round(error_rate, 2),
                        threshold_percent=settings.ALERT_ERROR_RATE_PERCENT,
                    )
            response['X-Request-ID'] = request_id
            return response
        finally:
            request_id_var.reset(tokens[0])
            user_id_var.reset(tokens[1])
            tenant_id_var.reset(tokens[2])
            action_var.reset(tokens[3])

    def process_exception(self, request, exception):
        metrics.add('exceptions_total')
        log_event(
            'unhandled_exception',
            level='error',
            exc_info=(type(exception), exception, exception.__traceback__),
            exception_type=exception.__class__.__name__,
            method=request.method,
            path=request.path,
        )
        return None

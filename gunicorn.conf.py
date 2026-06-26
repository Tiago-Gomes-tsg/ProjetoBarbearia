"""Gunicorn usa o mesmo formato JSON sanitizado da aplicacao Django."""

import os


bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv('WEB_CONCURRENCY', '2'))
threads = int(os.getenv('GUNICORN_THREADS', '4'))
timeout = int(os.getenv('GUNICORN_TIMEOUT', '120'))
capture_output = True
accesslog = None  # request_completed ja registra cada request com contexto e metricas.
errorlog = '-'

logconfig_dict = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'safe_json': {'()': 'agendamentos.observability.SafeJsonFormatter'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'safe_json',
        },
    },
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
    'loggers': {
        'gunicorn.error': {
            'handlers': ['console'],
            'level': os.getenv('GUNICORN_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
    },
}

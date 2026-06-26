"""Contexto de tenant usado pelo ORM durante uma requisição.

O contexto usa ``ContextVar`` para funcionar corretamente tanto em código
síncrono quanto assíncrono. Consultas administrativas globais precisam entrar
explicitamente em ``unscoped_context``; a ausência acidental de tenant falha
fechada nos managers dos modelos de negócio.
"""

from contextlib import contextmanager
from contextvars import ContextVar


_empresa_atual = ContextVar('empresa_atual', default=None)
_acesso_global = ContextVar('acesso_global_tenants', default=False)


def get_current_empresa():
    return _empresa_atual.get()


def is_unscoped_access_allowed():
    return _acesso_global.get()


@contextmanager
def empresa_context(empresa):
    token = _empresa_atual.set(empresa)
    try:
        yield empresa
    finally:
        _empresa_atual.reset(token)


@contextmanager
def unscoped_context():
    """Libera consultas globais somente em rotinas de plataforma explícitas."""

    token = _acesso_global.set(True)
    try:
        yield
    finally:
        _acesso_global.reset(token)

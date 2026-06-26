"""Políticas de acesso centralizadas para o painel multi-tenant."""

from functools import wraps

from django.http import Http404

from .models import MembroEmpresa


def membro_ativo(user):
    if not getattr(user, 'is_authenticated', False):
        return None
    try:
        membro = user.membro_empresa
    except MembroEmpresa.DoesNotExist:
        return None
    return membro if membro.ativo and membro.empresa.ativo else None


def usuario_e_owner(user):
    if user.is_superuser:
        return True
    membro = membro_ativo(user)
    return bool(membro and membro.is_owner)


def usuario_e_manager(user):
    if user.is_superuser:
        return True
    membro = membro_ativo(user)
    return bool(membro and membro.is_manager)


def empresa_required(view_func):
    """Evita respostas globais quando host e usuário não identificam tenant."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if getattr(request, 'empresa', None) is None:
            raise Http404('Barbearia não encontrada.')
        return view_func(request, *args, **kwargs)

    return wrapper

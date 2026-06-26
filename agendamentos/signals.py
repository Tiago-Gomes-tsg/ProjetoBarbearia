from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.contrib.sessions.models import Session
from .observability import tracked_cache as cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import (
    Barbeiro,
    BloqueioAgenda,
    ConfigLoyalty,
    ConfiguracaoBarbearia,
    Corte,
    Empresa,
    MembroEmpresa,
    UserSession,
)
from .security import get_client_ip


MAX_ACTIVE_SESSIONS_PER_USER = 3


@receiver(user_logged_in)
def limitar_sessoes_ativas(sender, request, user, **kwargs):
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    if not request.session.session_key:
        request.session.save()

    session_key = request.session.session_key
    UserSession.objects.update_or_create(
        session_key=session_key,
        defaults={
            'usuario': user,
            'ip': get_client_ip(request),
            'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:255],
        },
    )

    sessoes = list(UserSession.objects.filter(usuario=user).order_by('-ultimo_uso', '-criada_em'))
    for sessao in sessoes[MAX_ACTIVE_SESSIONS_PER_USER:]:
        Session.objects.filter(session_key=sessao.session_key).delete()
        sessao.delete()


@receiver(user_logged_out)
def remover_sessao_ativa(sender, request, user, **kwargs):
    session_key = request.session.session_key
    if session_key:
        UserSession.objects.filter(session_key=session_key).delete()


@receiver(post_save, sender=Empresa)
def criar_configuracoes_da_empresa(sender, instance, created, **kwargs):
    """Onboarding cria os singletons por tenant fora do caminho de leitura."""

    if kwargs.get('raw') or not created:
        return
    ConfiguracaoBarbearia.all_objects.get_or_create(
        empresa=instance,
        defaults={'nome_barbearia': instance.nome},
    )
    ConfigLoyalty.all_objects.get_or_create(empresa=instance)


@receiver(post_save, sender=Barbeiro)
def sincronizar_membro_empresa(sender, instance, **kwargs):
    """Compatibilidade segura enquanto o cadastro profissional ainda guarda flags."""

    if kwargs.get('raw') or not instance.usuario_id or not instance.empresa_id:
        return
    papel = (
        MembroEmpresa.OWNER if instance.is_dono
        else MembroEmpresa.MANAGER if instance.is_gerente
        else MembroEmpresa.BARBER
    )
    membro, created = MembroEmpresa.all_objects.get_or_create(
        usuario_id=instance.usuario_id,
        defaults={
            'empresa_id': instance.empresa_id,
            'papel': papel,
            'ativo': instance.ativo,
        },
    )
    if not created:
        # Depois da criação, MembroEmpresa é a fonte de verdade do papel.
        membro.empresa_id = instance.empresa_id
        membro.ativo = instance.ativo
        membro.save(update_fields=['empresa', 'ativo'])


def limpar_cache_publico(sender, instance, **kwargs):
    if kwargs.get('raw'):
        return
    empresa_id = getattr(instance, 'empresa_id', None)
    if empresa_id:
        cache.delete(f'public-index-base:v2:{empresa_id}')


for modelo in (Barbeiro, BloqueioAgenda, ConfiguracaoBarbearia, Corte):
    post_save.connect(limpar_cache_publico, sender=modelo, dispatch_uid=f'limpar_cache_publico_{modelo.__name__}_save')
    post_delete.connect(limpar_cache_publico, sender=modelo, dispatch_uid=f'limpar_cache_publico_{modelo.__name__}_delete')

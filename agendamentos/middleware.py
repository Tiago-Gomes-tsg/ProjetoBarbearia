from datetime import timedelta

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponseNotFound
from django.shortcuts import redirect
from django.utils import timezone
from zoneinfo import ZoneInfo

from .models import AceiteTermos, Empresa, MembroEmpresa, UserSession
from .observability import update_request_context
from .security import get_client_ip
from .tenancy import empresa_context, unscoped_context


class TenantMiddleware:
    """Resolve a empresa pelo subdomínio ou pelo vínculo do usuário autenticado."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _slug_do_host(request):
        host = request.get_host().split(':', 1)[0].lower().rstrip('.')
        dominio_base = settings.TENANT_BASE_DOMAIN.lower().strip().lstrip('.')
        if not dominio_base or host == dominio_base:
            return None
        sufixo = f'.{dominio_base}'
        if not host.endswith(sufixo):
            return None
        subdominio = host[:-len(sufixo)]
        if not subdominio or '.' in subdominio or subdominio in settings.TENANT_RESERVED_SUBDOMAINS:
            return None
        return subdominio

    @staticmethod
    def _membro(user):
        if not getattr(user, 'is_authenticated', False) or user.is_superuser:
            return None
        return MembroEmpresa.all_objects.select_related('empresa').filter(
            usuario=user,
            ativo=True,
            empresa__ativo=True,
        ).first()

    def __call__(self, request):
        # Admin e onboarding são superfícies globais exclusivas da plataforma.
        superficie_global = request.path.startswith(('/admin/', '/plataforma/'))
        if superficie_global and request.user.is_superuser:
            request.empresa = None
            request.membro_empresa = None
            update_request_context(user_id=request.user.pk)
            with unscoped_context():
                return self.get_response(request)
        if superficie_global and request.user.is_authenticated:
            return HttpResponseNotFound('Página não encontrada.')

        slug = self._slug_do_host(request)
        empresa_host = Empresa.objects.filter(slug=slug, ativo=True).first() if slug else None
        membro = self._membro(request.user)

        if slug and empresa_host is None:
            return HttpResponseNotFound('Barbearia não encontrada.')
        if empresa_host and membro and membro.empresa_id != empresa_host.pk:
            # 404 evita confirmar a existência de outro tenant.
            return HttpResponseNotFound('Barbearia não encontrada.')

        empresa = empresa_host or (membro.empresa if membro else None)
        if empresa is None and settings.TENANT_ALLOW_SINGLE_FALLBACK:
            empresas = list(Empresa.objects.filter(ativo=True)[:2])
            if len(empresas) == 1:
                empresa = empresas[0]

        request.empresa = empresa
        request.membro_empresa = membro if membro and membro.empresa_id == getattr(empresa, 'pk', None) else None
        update_request_context(
            user_id=getattr(request.user, 'pk', None),
            tenant_id=getattr(empresa, 'pk', None),
        )

        if (
            request.path.startswith('/painel/')
            and request.user.is_authenticated
            and not request.user.is_superuser
            and request.membro_empresa is None
        ):
            logout(request)

        painel_sem_tenant = (
            request.path.startswith('/painel/')
            and request.path not in {'/painel/login/', '/painel/logout/'}
            and empresa is None
        )
        if painel_sem_tenant:
            return HttpResponseNotFound('Barbearia não encontrada.')

        if empresa is None:
            return self.get_response(request)
        with empresa_context(empresa):
            timezone.activate(ZoneInfo(empresa.timezone))
            try:
                return self.get_response(request)
            finally:
                timezone.deactivate()


class ActiveUserSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.path.startswith('/painel/'):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response['Pragma'] = 'no-cache'

        user = getattr(request, 'user', None)
        session_key = getattr(request.session, 'session_key', None)
        if user and user.is_authenticated and session_key:
            sessao = UserSession.objects.filter(session_key=session_key, usuario=user).first()
            if sessao and sessao.ultimo_uso < timezone.now() - timedelta(minutes=10):
                sessao.ip = get_client_ip(request)
                sessao.user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:255]
                sessao.save(update_fields=['ip', 'user_agent', 'ultimo_uso'])

        return response


class TermsAcceptanceMiddleware:
    """Exige aceite vigente antes de liberar qualquer função do painel."""

    ROTAS_LIVRES = ('/painel/login/', '/painel/logout/', '/painel/termos/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        empresa = getattr(request, 'empresa', None)
        deve_validar = (
            request.path.startswith('/painel/')
            and request.path not in self.ROTAS_LIVRES
            and user
            and user.is_authenticated
            and not user.is_superuser
            and empresa is not None
        )
        if deve_validar:
            aceite_vigente = AceiteTermos.objects.filter(
                usuario=user,
                versao_termos=settings.TERMS_VERSION,
                versao_privacidade=settings.PRIVACY_POLICY_VERSION,
            ).exists()
            if not aceite_vigente:
                return redirect('aceitar_termos')
        return self.get_response(request)

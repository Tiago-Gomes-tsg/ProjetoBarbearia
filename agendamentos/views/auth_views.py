from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from agendamentos.models import AceiteTermos, MembroEmpresa
from agendamentos.observability import log_event
from agendamentos.permissions import empresa_required
from agendamentos.security import get_client_ip, is_rate_limited


class TenantLoginView(LoginView):
    """Autentica somente memberships ativos e compatíveis com o subdomínio."""

    template_name = 'agendamentos/login.html'

    def post(self, request, *args, **kwargs):
        username = (request.POST.get('username') or '').strip().lower()
        bloqueado = is_rate_limited(
            request,
            'login-ip',
            settings.LOGIN_RATE_LIMIT_IP,
            settings.LOGIN_RATE_LIMIT_WINDOW,
        )
        if username:
            bloqueado = is_rate_limited(
                request,
                'login-username',
                settings.LOGIN_RATE_LIMIT_USERNAME,
                settings.LOGIN_RATE_LIMIT_WINDOW,
                username,
            ) or bloqueado
        if bloqueado:
            form = self.get_form()
            form.add_error(None, 'Muitas tentativas. Aguarde alguns minutos e tente novamente.')
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        usuario = form.get_user()
        if usuario.is_superuser:
            return super().form_valid(form)

        membro = MembroEmpresa.all_objects.select_related('empresa').filter(
            usuario=usuario,
            ativo=True,
            empresa__ativo=True,
        ).first()
        empresa_request = getattr(self.request, 'empresa', None)
        if membro is None or (
            empresa_request is not None and membro.empresa_id != empresa_request.pk
        ):
            form.add_error(None, 'Usuário ou senha inválidos para esta barbearia.')
            return self.form_invalid(form)
        return super().form_valid(form)


@login_required(login_url='login')
@empresa_required
def aceitar_termos(request):
    """Registra o aceite atual sem permitir que outro usuário aceite em seu nome."""

    if request.user.is_superuser:
        return redirect('painel_home')

    aceite_atual = AceiteTermos.objects.filter(
        usuario=request.user,
        versao_termos=settings.TERMS_VERSION,
        versao_privacidade=settings.PRIVACY_POLICY_VERSION,
    ).first()

    if request.method == 'POST':
        if aceite_atual is not None:
            return redirect('painel_home')
        if request.POST.get('aceite') != 'on':
            messages.error(request, 'Marque a confirmação para continuar.')
        else:
            ip = get_client_ip(request)
            membro = getattr(request, 'membro_empresa', None)
            aceite_atual, _ = AceiteTermos.objects.get_or_create(
                usuario=request.user,
                versao_termos=settings.TERMS_VERSION,
                versao_privacidade=settings.PRIVACY_POLICY_VERSION,
                defaults={
                    'ip': None if ip == 'unknown' else ip,
                    'user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:255],
                    'aceite_em_nome_da_empresa': bool(membro and membro.is_owner),
                },
            )
            log_event(
                'terms_accepted',
                user_id=request.user.pk,
                tenant_id=request.empresa.pk,
                company_acceptance=aceite_atual.aceite_em_nome_da_empresa,
                terms_version=settings.TERMS_VERSION,
                privacy_version=settings.PRIVACY_POLICY_VERSION,
            )
            messages.success(request, 'Termos registrados com sucesso.')
            destino = request.POST.get('next') or 'painel_home'
            if destino != 'painel_home' and not url_has_allowed_host_and_scheme(
                destino,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                destino = 'painel_home'
            return redirect(destino)

    return render(request, 'agendamentos/aceitar_termos.html', {
        'versao_termos': settings.TERMS_VERSION,
        'versao_privacidade': settings.PRIVACY_POLICY_VERSION,
        'next': request.GET.get('next', ''),
        'aceite_atual': aceite_atual,
    })


@login_required(login_url='login')
@empresa_required
def alterar_minha_senha(request):
    """Permite trocar a senha provisória sem expor ou registrar seu conteúdo."""

    form = PasswordChangeForm(request.user, request.POST or None)
    for nome, campo in form.fields.items():
        campo.widget.attrs.update({
            'maxlength': 128,
            'autocomplete': 'current-password' if nome == 'old_password' else 'new-password',
        })

    status = 200
    if request.method == 'POST':
        if is_rate_limited(request, 'alterar-senha', 5, 15 * 60, request.user.pk):
            form.add_error(None, 'Muitas tentativas. Aguarde alguns minutos para tentar novamente.')
            status = 429
        elif form.is_valid():
            usuario = form.save()
            update_session_auth_hash(request, usuario)
            log_event('password_changed', user_id=usuario.pk, tenant_id=request.empresa.pk)
            messages.success(request, 'Sua senha foi alterada com sucesso.')
            return redirect('painel_home')

    return render(
        request,
        'agendamentos/alterar_minha_senha.html',
        {'form': form},
        status=status,
    )

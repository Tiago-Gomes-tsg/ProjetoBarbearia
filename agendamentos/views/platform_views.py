from datetime import timedelta
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from agendamentos.models import (
    AceiteTermos,
    Agendamento,
    AssinaturaCliente,
    Barbeiro,
    Cliente,
    ConfiguracaoBarbearia,
    Corte,
    Empresa,
    MembroEmpresa,
    TransacaoFinanceira,
    UserSession,
)
from agendamentos.observability import log_event
from agendamentos.platform_forms import EmpresaGestaoForm, EmpresaOnboardingForm
from agendamentos.tenant_backup import (
    arquivos_do_tenant,
    excluir_arquivos_do_tenant,
    exportar_tenant,
    importar_tenant,
)
from agendamentos.tenancy import empresa_context


def _superuser_required(view_func):
    @wraps(view_func)
    @login_required(login_url='/admin/login/')
    def wrapper(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_superuser:
            raise Http404('Pagina nao encontrada.')
        return view_func(request, *args, **kwargs)

    return wrapper


def _cor_texto_contrastante(cor_hex):
    vermelho, verde, azul = (int(cor_hex[i:i + 2], 16) for i in (1, 3, 5))
    luminancia = (vermelho * 299 + verde * 587 + azul * 114) / 1000
    return '#121212' if luminancia >= 150 else '#ffffff'


def _url_tenant(request, slug):
    porta = request.get_port()
    porta_sufixo = f':{porta}' if porta not in {'80', '443'} else ''
    return f'{request.scheme}://{slug}.{settings.TENANT_BASE_DOMAIN}{porta_sufixo}'


def _mapa_agregado(queryset, value_name):
    return {
        row['empresa_id']: row[value_name] or 0
        for row in queryset
    }


def _dados_dashboard_empresas(request):
    hoje = timezone.localdate()
    inicio_30d = hoje - timedelta(days=29)
    membros = _mapa_agregado(
        MembroEmpresa.all_objects.values('empresa_id').annotate(total=Count('id')),
        'total',
    )
    clientes = _mapa_agregado(
        Cliente.all_objects.values('empresa_id').annotate(total=Count('id')),
        'total',
    )
    agendamentos = _mapa_agregado(
        Agendamento.all_objects.values('empresa_id').annotate(total=Count('id')),
        'total',
    )
    agendamentos_30d = _mapa_agregado(
        Agendamento.all_objects.filter(data__gte=inicio_30d)
        .values('empresa_id').annotate(total=Count('id')),
        'total',
    )
    receita_30d = _mapa_agregado(
        TransacaoFinanceira.all_objects.filter(
            data__gte=inicio_30d,
            tipo='ENTRADA',
        ).values('empresa_id').annotate(total=Sum('valor')),
        'total',
    )
    ultimo_agendamento = {
        row['empresa_id']: row['ultima_data']
        for row in Agendamento.all_objects.values('empresa_id').annotate(
            ultima_data=Max('data')
        )
    }
    proprietarios = {}
    for proprietario in (
        Barbeiro.all_objects.filter(is_dono=True)
        .select_related('usuario')
        .order_by('empresa_id', 'pk')
    ):
        proprietarios.setdefault(proprietario.empresa_id, proprietario)
    aceites_empresa = {}
    for aceite in (
        AceiteTermos.all_objects.filter(
            aceite_em_nome_da_empresa=True,
            versao_termos=settings.TERMS_VERSION,
            versao_privacidade=settings.PRIVACY_POLICY_VERSION,
        )
        .select_related('usuario')
        .order_by('empresa_id', '-aceito_em')
    ):
        aceites_empresa.setdefault(aceite.empresa_id, aceite)

    empresas = []
    for empresa in Empresa.objects.order_by('-ativo', 'nome'):
        prefix = f'empresa-{empresa.pk}'
        proprietario = proprietarios.get(empresa.pk)
        empresas.append({
            'obj': empresa,
            'membros': membros.get(empresa.pk, 0),
            'clientes': clientes.get(empresa.pk, 0),
            'agendamentos': agendamentos.get(empresa.pk, 0),
            'agendamentos_30d': agendamentos_30d.get(empresa.pk, 0),
            'receita_30d': receita_30d.get(empresa.pk, Decimal('0.00')),
            'ultimo_agendamento': ultimo_agendamento.get(empresa.pk),
            'proprietario': proprietario,
            'email_contato': proprietario.usuario.email if proprietario else '',
            'telefone_contato': proprietario.telefone if proprietario else '',
            'aceite_empresa': aceites_empresa.get(empresa.pk),
            'url': _url_tenant(request, empresa.slug),
            'gestao_form': EmpresaGestaoForm(instance=empresa, prefix=prefix),
        })

    resumo = {
        'total': len(empresas),
        'ativas': sum(1 for item in empresas if item['obj'].ativo),
        'inativas': sum(1 for item in empresas if not item['obj'].ativo),
        'pagamentos_atrasados': sum(
            1 for item in empresas
            if item['obj'].status_pagamento == Empresa.PAGAMENTO_ATRASADO
        ),
        'clientes': sum(item['clientes'] for item in empresas),
        'agendamentos_30d': sum(item['agendamentos_30d'] for item in empresas),
        'receita_operacional_30d': sum(
            (item['receita_30d'] for item in empresas),
            Decimal('0.00'),
        ),
        'receita_recorrente_prevista': sum(
            (
                item['obj'].valor_mensal or Decimal('0.00')
                for item in empresas
                if item['obj'].ativo
            ),
            Decimal('0.00'),
        ),
    }
    return empresas, resumo


@_superuser_required
@never_cache
def empresas_plataforma(request):
    form = EmpresaOnboardingForm(request.POST or None)
    if (
        request.method == 'POST'
        and form.is_valid()
        and not request.user.check_password(form.cleaned_data['senha_superuser'])
    ):
        form.add_error('senha_superuser', 'Senha do superuser incorreta.')

    if request.method == 'POST' and form.is_valid():
        dados = form.cleaned_data
        with transaction.atomic():
            empresa = Empresa(
                nome=dados['nome_empresa'],
                slug=dados['slug'],
                timezone=dados['timezone'],
                ativo=True,
            )
            empresa.full_clean()
            empresa.save()

            with empresa_context(empresa):
                usuario = User.objects.create_user(
                    username=dados['username_dono'],
                    email=dados['email_dono'],
                    password=dados['senha_dono'],
                    is_active=True,
                )
                MembroEmpresa.objects.create(
                    usuario=usuario,
                    papel=MembroEmpresa.OWNER,
                    ativo=True,
                )
                dono = Barbeiro.objects.create(
                    usuario=usuario,
                    nome=dados['nome_dono'],
                    telefone=dados['telefone_dono'],
                    is_dono=True,
                    is_gerente=False,
                    aceita_agendamentos_online=dados['aceita_agendamentos_dono'],
                    hora_inicio=dados['hora_inicio'],
                    hora_fim=dados['hora_fim'],
                    intervalo_minutos=dados['intervalo_minutos'],
                    trabalha_sabado=dados['trabalha_sabado'],
                    ativo=True,
                )
                Corte.objects.create(
                    barbeiro=dono,
                    nome=dados['servico_inicial'],
                    preco=dados['preco_servico'],
                )

                configuracao = ConfiguracaoBarbearia.objects.get(empresa=empresa)
                configuracao.nome_barbearia = empresa.nome
                configuracao.slogan = dados['slogan']
                configuracao.cor_destaque = dados['cor_destaque']
                configuracao.cor_destaque_agendamento = dados['cor_destaque']
                cor_texto = _cor_texto_contrastante(dados['cor_destaque'])
                configuracao.cor_texto_botao = cor_texto
                configuracao.cor_texto_botao_agendamento = cor_texto
                configuracao.save()

        log_event('tenant_created', tenant_id=empresa.pk)
        messages.success(
            request,
            f'Empresa “{empresa.nome}” criada. O proprietário deverá aceitar os termos no primeiro acesso.',
        )
        return redirect('empresas_plataforma')

    empresas, resumo = _dados_dashboard_empresas(request)
    return render(request, 'agendamentos/plataforma_empresas.html', {
        'form': form,
        'empresas': empresas,
        'resumo': resumo,
        'tenant_base_domain': settings.TENANT_BASE_DOMAIN,
    })


@_superuser_required
@require_POST
def atualizar_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    prefix = f'empresa-{empresa.pk}'
    form = EmpresaGestaoForm(request.POST, instance=empresa, prefix=prefix)
    if form.is_valid():
        form.save()
        log_event('tenant_commercial_data_updated', tenant_id=empresa.pk)
        messages.success(request, 'Dados comerciais atualizados.')
    else:
        messages.error(request, 'Nao foi possivel atualizar: ' + ' '.join(
            error for errors in form.errors.values() for error in errors
        ))
    return redirect('empresas_plataforma')


@_superuser_required
@require_POST
def alternar_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    ativar = request.POST.get('novo_status') == 'ativo'
    motivo = (request.POST.get('motivo_inativacao') or '').strip()
    empresa.ativo = ativar
    empresa.desativada_em = None if ativar else timezone.now()
    empresa.motivo_inativacao = '' if ativar else motivo
    empresa.save(update_fields=['ativo', 'desativada_em', 'motivo_inativacao'])

    if not ativar:
        user_ids = MembroEmpresa.all_objects.filter(empresa=empresa).values_list(
            'usuario_id', flat=True
        )
        sessions = list(UserSession.objects.filter(usuario_id__in=user_ids))
        Session.objects.filter(session_key__in=[item.session_key for item in sessions]).delete()
        UserSession.objects.filter(usuario_id__in=user_ids).delete()

    log_event(
        'tenant_status_changed',
        tenant_id=empresa.pk,
        active=ativar,
    )
    messages.success(
        request,
        'Barbearia ativada e liberada.' if ativar else 'Barbearia desativada e acessos encerrados.',
    )
    return redirect('empresas_plataforma')


@_superuser_required
@require_GET
@never_cache
def exportar_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    try:
        content = exportar_tenant(empresa)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('empresas_plataforma')

    response = HttpResponse(content, content_type='application/json; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="backup_{empresa.slug}_{timezone.localdate().isoformat()}.json"'
    )
    response['Cache-Control'] = 'no-store, private'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@_superuser_required
@require_POST
def importar_empresa(request):
    upload = request.FILES.get('backup_json')
    if not upload:
        messages.error(request, 'Selecione um arquivo JSON de backup.')
        return redirect('empresas_plataforma')
    if not request.user.check_password(request.POST.get('senha_superuser', '')):
        messages.error(request, 'Senha do superuser incorreta.')
        return redirect('empresas_plataforma')
    try:
        empresa = importar_tenant(upload)
    except ValidationError as exc:
        log_event(
            'tenant_backup_import_rejected',
            level='warning',
            error_count=len(exc.messages),
        )
        messages.error(request, ' '.join(exc.messages))
        return redirect('empresas_plataforma')
    messages.success(request, f'Backup de “{empresa.nome}” restaurado com sucesso.')
    return redirect('empresas_plataforma')


@_superuser_required
@require_POST
def excluir_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    if request.POST.get('confirmar_slug', '').strip() != empresa.slug:
        messages.error(request, 'Digite o slug exato da barbearia para confirmar a exclusao.')
        return redirect('empresas_plataforma')
    if request.POST.get('confirmar_exclusao') != 'on':
        messages.error(request, 'Marque a confirmacao de exclusao permanente.')
        return redirect('empresas_plataforma')
    if not request.user.check_password(request.POST.get('senha_superuser', '')):
        messages.error(request, 'Senha do superuser incorreta.')
        return redirect('empresas_plataforma')

    tenant_id = empresa.pk
    tenant_files = arquivos_do_tenant(empresa)
    user_ids = list(
        MembroEmpresa.all_objects.filter(empresa=empresa)
        .values_list('usuario_id', flat=True)
    )
    with transaction.atomic():
        AssinaturaCliente.all_objects.filter(empresa=empresa).delete()
        empresa.delete()
        User.objects.filter(pk__in=user_ids, is_superuser=False).delete()

    file_delete_failures = excluir_arquivos_do_tenant(tenant_files, tenant_id)

    log_event(
        'tenant_deleted',
        level='warning',
        tenant_id=tenant_id,
        deleted_user_count=len(user_ids),
        deleted_file_count=len(tenant_files) - file_delete_failures,
        file_delete_failure_count=file_delete_failures,
    )
    messages.success(request, 'Barbearia e usuarios vinculados excluidos permanentemente.')
    return redirect('empresas_plataforma')

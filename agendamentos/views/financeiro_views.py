from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal, InvalidOperation
from agendamentos.observability import log_event
from ..models import Barbeiro, ItemEstoque, LancamentoComissao, LancamentoSalario, TransacaoFinanceira
from .dashboard_views import checar_se_e_dono, checar_se_e_gerente,  obter_dados_usuario
import calendar


def _decimal_form(valor, default='0'):
    try:
        return Decimal(str(valor or default).replace(',', '.'))
    except InvalidOperation as exc:
        log_event(
            'invalid_decimal_fallback',
            level='warning',
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return Decimal(default)


def _vencimento_salario(ano, mes, dia):
    ultimo = calendar.monthrange(ano, mes)[1]
    return timezone.datetime(ano, mes, min(dia, ultimo)).date()


def _sincronizar_salarios(ano, mes):
    barbeiros = Barbeiro.objects.filter(
        ativo=True,
        tipo_remuneracao__in=['FIXO', 'AMBOS'],
        salario_fixo__gt=0,
        dia_pagamento_salario__isnull=False,
    )
    for barbeiro in barbeiros:
        LancamentoSalario.objects.get_or_create(
            barbeiro=barbeiro,
            competencia_ano=ano,
            competencia_mes=mes,
            defaults={
                'valor_salario': barbeiro.salario_fixo,
                'data_vencimento': _vencimento_salario(ano, mes, barbeiro.dia_pagamento_salario),
                'pago': False,
            },
        )

@login_required
def gestao_financeira(request):
    is_dono    = checar_se_e_dono(request.user)
    is_gerente = checar_se_e_gerente(request.user)

    if not (is_dono or is_gerente):
        return redirect('painel_home')

    if request.method == 'POST' and not is_dono:
        return redirect('gestao_financeira')

    if request.method == 'POST':
        tipo        = request.POST.get('tipo')
        categoria   = request.POST.get('categoria')
        valor       = request.POST.get('valor')
        descricao   = request.POST.get('descricao')
        barbeiro_id = request.POST.get('barbeiro')

        transacao = TransacaoFinanceira.objects.create(
            tipo=tipo, categoria=categoria,
            valor=valor, descricao=descricao,
            data=timezone.now().date()
        )
        if barbeiro_id:
            transacao.barbeiro_id = barbeiro_id
            transacao.save()
        return redirect('gestao_financeira')

    # O range manual tem prioridade; sem range, o periodo mensal/anual controla o resumo.
    periodo       = request.GET.get('periodo', 'mensal')
    hoje          = timezone.now().date()
    mes           = int(request.GET.get('mes', hoje.month))
    ano           = int(request.GET.get('ano', hoje.year))
    data_inicio   = request.GET.get('data_inicio', '')
    data_fim      = request.GET.get('data_fim', '')

    transacoes = TransacaoFinanceira.objects.all()
    comissoes  = LancamentoComissao.objects.all()

    if data_inicio or data_fim:
        if data_inicio:
            transacoes = transacoes.filter(data__gte=data_inicio)
        if data_fim:
            transacoes = transacoes.filter(data__lte=data_fim)
    elif periodo == 'mensal':
        transacoes = transacoes.filter(data__month=mes, data__year=ano)
    elif periodo == 'anual':
        transacoes = transacoes.filter(data__year=ano)

    _sincronizar_salarios(ano, mes)
    salarios = LancamentoSalario.objects.select_related('barbeiro').filter(
        competencia_ano=ano,
        competencia_mes=mes,
    )

    total_entradas      = transacoes.filter(tipo='ENTRADA').aggregate(Sum('valor'))['valor__sum'] or 0
    total_saidas        = transacoes.filter(tipo='SAIDA').aggregate(Sum('valor'))['valor__sum'] or 0
    saldo_atual         = total_entradas - total_saidas
    comissoes_pendentes = comissoes.filter(pago=False).aggregate(Sum('valor_comissao'))['valor_comissao__sum'] or 0
    salarios_pendentes = salarios.filter(pago=False).aggregate(Sum('valor_salario'))['valor_salario__sum'] or 0

    anos_disponiveis = list(range(hoje.year - 2, hoje.year + 2))

    context = {
        'barbeiro':            obter_dados_usuario(request.user),
        'transacoes':          transacoes.order_by('-data', '-id'),
        'comissoes':           comissoes.order_by('-data_gerada', '-id'),
        'salarios':            salarios.order_by('data_vencimento', 'barbeiro__nome'),
        'barbeiros':           Barbeiro.objects.all(),
        'total_entradas':      total_entradas,
        'total_saidas':        total_saidas,
        'saldo_atual':         saldo_atual,
        'comissoes_pendentes': comissoes_pendentes,
        'salarios_pendentes':  salarios_pendentes,
        'periodo_atual':       periodo,
        'is_dono':             is_dono,
        'modo_leitura':        not is_dono,
        'mes_filtro':          mes,
        'ano_filtro':          ano,
        'data_inicio_filtro':  data_inicio,
        'data_fim_filtro':     data_fim,
        'anos_disponiveis':    anos_disponiveis,
    }
    return render(request, 'agendamentos/gestao_financeira.html', context)



@login_required
@require_POST
@transaction.atomic
def pagar_comissao(request, comissao_id):
    if not checar_se_e_dono(request.user):
        return redirect('painel_home')

    comissao = get_object_or_404(LancamentoComissao.objects.select_for_update(), id=comissao_id)
    if not comissao.pago:
        comissao.pago = True
        comissao.save()

        # O repasse de comissao tambem sai do caixa para manter o saldo coerente.
        TransacaoFinanceira.objects.create(
            tipo='SAIDA',
            categoria='COMISSAO',
            valor=comissao.valor_comissao,
            descricao=f"Pagamento de comissão para {comissao.barbeiro.nome}",
            barbeiro=comissao.barbeiro,
            data=timezone.now().date()
        )
    return redirect('gestao_financeira')


@login_required
@require_POST
@transaction.atomic
def pagar_salario(request, salario_id):
    if not checar_se_e_dono(request.user):
        return redirect('painel_home')

    salario = get_object_or_404(
        LancamentoSalario.objects.select_for_update().select_related('barbeiro'),
        id=salario_id,
    )
    if not salario.pago:
        transacao = TransacaoFinanceira.objects.create(
            tipo='SAIDA',
            categoria='SALARIO',
            valor=salario.valor_salario,
            descricao=f"Pagamento de salario para {salario.barbeiro.nome} ({salario.competencia_mes:02d}/{salario.competencia_ano})",
            barbeiro=salario.barbeiro,
            data=timezone.now().date(),
        )
        salario.pago = True
        salario.data_pagamento = timezone.now().date()
        salario.transacao_financeira = transacao
        salario.save(update_fields=['pago', 'data_pagamento', 'transacao_financeira'])
    return redirect('gestao_financeira')


@login_required
def gestao_estoque(request):
    is_dono = checar_se_e_dono(request.user)
    is_gerente = checar_se_e_gerente(request.user)

    if not (is_dono or is_gerente):
        return redirect('painel_home')

    if request.method == 'POST' and not is_dono:
        return redirect('gestao_estoque')

    if request.method == 'POST':
        nome              = request.POST.get('nome')
        quantidade_atual  = int(request.POST.get('quantidade_atual') or 0)
        quantidade_minima = int(request.POST.get('quantidade_minima') or 2)
        preco_compra      = _decimal_form(request.POST.get('preco_compra'))
        preco_venda       = request.POST.get('preco_venda')

        item = ItemEstoque.objects.create(
            nome=nome,
            quantidade_atual=quantidade_atual,
            quantidade_minima=quantidade_minima,
            preco_compra=preco_compra,
            preco_venda=_decimal_form(preco_venda) if preco_venda else None
        )

        custo_total = quantidade_atual * preco_compra
        if custo_total > 0:
            TransacaoFinanceira.objects.create(
                tipo='SAIDA',
                categoria='ESTOQUE',
                valor=custo_total,
                descricao=f"Compra de estoque inicial: {nome} ({quantidade_atual} un)",
                item_estoque=item,
                data=timezone.now().date()
            )
        return redirect('gestao_estoque')

    itens = ItemEstoque.objects.all().order_by('nome')
    alerta_estoque_baixo = any(
        item.quantidade_atual <= item.quantidade_minima for item in itens
    )

    context = {
        'barbeiro':            obter_dados_usuario(request.user),
        'itens':               itens,
        'alerta_estoque_baixo': alerta_estoque_baixo,
        'modo_leitura':        not is_dono,
        'is_dono':             is_dono,
        'is_gerente':          is_gerente,
    }
    return render(request, 'agendamentos/estoque.html', context)

@login_required
@require_POST
@transaction.atomic
def restocar_item(request, item_id):
    if not checar_se_e_dono(request.user):
        return redirect('painel_home')

    if request.method == 'POST':
        item = get_object_or_404(ItemEstoque, id=item_id)
        quantidade_adicional = int(request.POST.get('quantidade_adicional', 0))

        if item.quantidade_atual + quantidade_adicional > 10_000:
            messages.error(request, 'O estoque total nao pode ultrapassar 10.000 unidades por item.')
            return redirect('gestao_estoque')
        
        if quantidade_adicional > 0:
            item.quantidade_atual += quantidade_adicional
            item.save()
            
            # Todo reabastecimento pago aumenta quantidade e registra a despesa correspondente.
            custo_total = quantidade_adicional * item.preco_compra
            TransacaoFinanceira.objects.create(
                tipo='SAIDA',
                categoria='ESTOQUE',
                valor=custo_total,
                descricao=f"Restoque de item: {item.nome} (+{quantidade_adicional} un)",
                item_estoque=item,
                data=timezone.now().date()
            )
    return redirect('gestao_estoque')

@login_required
@require_POST
@transaction.atomic
def baixar_item(request, item_id):
    if not checar_se_e_dono(request.user):
        return redirect('painel_home')

    if request.method == 'POST':
        item = get_object_or_404(ItemEstoque, id=item_id)
        quantidade_baixa = int(request.POST.get('quantidade_baixa', 0))
        motivo = request.POST.get('motivo')

        if 0 < quantidade_baixa <= item.quantidade_atual:
            item.quantidade_atual -= quantidade_baixa
            item.save()
            
            # VENDA gera entrada financeira; REMOVER apenas baixa a quantidade.
            if motivo == 'VENDA' and item.preco_venda and item.preco_venda > 0:
                valor_faturado = quantidade_baixa * item.preco_venda
                TransacaoFinanceira.objects.create(
                    tipo='ENTRADA',
                    categoria='PRODUTO',
                    valor=valor_faturado,
                    descricao=f"Venda de balcão: {item.nome} (x{quantidade_baixa})",
                    item_estoque=item,
                    data=timezone.now().date()
                )
    return redirect('gestao_estoque')

@login_required
@require_POST
@transaction.atomic
def excluir_item(request, item_id):
    # Gerentes visualizam estoque, mas somente o dono pode excluir itens.
    if not checar_se_e_dono(request.user):
        return redirect('painel_home')

    item = get_object_or_404(ItemEstoque, id=item_id)
    item.delete()
    
    return redirect('gestao_estoque')

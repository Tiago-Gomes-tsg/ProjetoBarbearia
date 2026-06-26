from django.shortcuts import get_object_or_404, render
from django.conf import settings
from agendamentos.observability import log_event, tracked_cache as cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.urls import reverse
from agendamentos.models import (
    Agendamento,
    Barbeiro,
    BloqueioAgenda,
    Cliente,
    ConfiguracaoBarbearia,
    Corte,
    CupomDesconto,
    EntradaListaEspera,
)
from agendamentos.permissions import empresa_required
from agendamentos.security import is_rate_limited
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
from urllib.parse import quote


PUBLIC_INDEX_CACHE_VERSION = 'v2'


def _dados_whatsapp_confirmacao(request, agendamento):
    """Monta o contato do profissional sem consultar ou expor outro tenant."""

    barbeiro = agendamento.barbeiro
    contato = barbeiro if (barbeiro.receber_confirmacao_whatsapp and barbeiro.telefone_whatsapp) else (
        Barbeiro.objects.filter(
            empresa=request.empresa,
            is_dono=True,
            receber_confirmacao_whatsapp=True,
        )
        .exclude(pk=barbeiro.pk)
        .order_by('pk')
        .first()
    )
    if contato is None or not contato.telefone_whatsapp:
        return None

    cancelamento_url = request.build_absolute_uri(reverse(
        'cancelar_por_token',
        args=[agendamento.token_cancelamento],
    ))
    mensagem = '\n'.join((
        f'Olá, {contato.nome}!',
        'Meu agendamento foi realizado com sucesso:',
        f'Barbeiro: {barbeiro.nome}',
        f'Cliente: {agendamento.cliente.nome}',
        f'Telefone do cliente: {agendamento.cliente.telefone}',
        f'Data: {agendamento.data:%d/%m/%Y}',
        f'Horário: {agendamento.horario}',
        f'Serviço: {agendamento.corte.nome}',
        f'Link de cancelamento: {cancelamento_url}',
    ))
    return {
        'url': f'https://wa.me/{contato.telefone_whatsapp}?text={quote(mensagem, safe="")}',
        'nome': contato.nome,
        'telefone': contato.telefone,
        'usa_contato_dono': contato.pk != barbeiro.pk,
    }


def _to_minutes(horario):
    hora, minuto = map(int, horario.split(':'))
    return hora * 60 + minuto


def _horario_publico_valido(barbeiro, data_agendamento, horario):
    dia_semana = str((data_agendamento.weekday() + 1) % 7)
    if dia_semana not in barbeiro.dias_trabalho.split(','):
        return False

    if BloqueioAgenda.objects.filter(
        barbeiro=barbeiro,
        data_inicio__lte=data_agendamento,
        data_fim__gte=data_agendamento,
    ).exists():
        return False

    try:
        horario_min = _to_minutes(horario)
    except (TypeError, ValueError):
        return False

    inicio_min = barbeiro.hora_inicio.hour * 60 + barbeiro.hora_inicio.minute
    fim_min = barbeiro.hora_fim.hour * 60 + barbeiro.hora_fim.minute
    intervalo = barbeiro.intervalo_minutos or 30

    if horario_min < inicio_min or horario_min >= fim_min:
        return False
    if (horario_min - inicio_min) % intervalo != 0:
        return False

    if barbeiro.pausa_inicio and barbeiro.pausa_fim:
        pausa_inicio = barbeiro.pausa_inicio.hour * 60 + barbeiro.pausa_inicio.minute
        pausa_fim = barbeiro.pausa_fim.hour * 60 + barbeiro.pausa_fim.minute
        if pausa_inicio <= horario_min < pausa_fim:
            return False

    if data_agendamento == timezone.localdate():
        agora = timezone.localtime() + timedelta(minutes=30)
        data_hora = timezone.make_aware(datetime.combine(
            data_agendamento,
            datetime.min.time().replace(hour=horario_min // 60, minute=horario_min % 60),
        ), timezone.get_current_timezone())
        if data_hora <= agora:
            return False

    return True


def _dados_publicos_base(empresa):
    cache_key = f'public-index-base:{PUBLIC_INDEX_CACHE_VERSION}:{empresa.pk}'
    dados = cache.get(cache_key)
    if dados is not None:
        return dados

    barbeiros = list(Barbeiro.objects.filter(
        ativo=True,
        aceita_agendamentos_online=True,
    ).order_by('nome'))
    cortes = list(Corte.objects.select_related('barbeiro').filter(
        barbeiro__ativo=True,
        barbeiro__aceita_agendamentos_online=True,
    ).order_by('barbeiro__nome', 'nome'))
    configuracao = ConfiguracaoBarbearia.objects.get(empresa=empresa)

    barbeiros_config_dict = {}
    for barbeiro in barbeiros:
        bloqueios = BloqueioAgenda.objects.filter(
            barbeiro=barbeiro,
            data_fim__gte=timezone.localdate()
        )
        datas_bloqueadas = []
        for bloqueio in bloqueios:
            dia = bloqueio.data_inicio
            while dia <= bloqueio.data_fim:
                datas_bloqueadas.append(dia.strftime('%Y-%m-%d'))
                dia += timedelta(days=1)

        barbeiros_config_dict[barbeiro.id] = {
            'hora_inicio': barbeiro.hora_inicio.strftime('%H:%M'),
            'hora_fim': barbeiro.hora_fim.strftime('%H:%M'),
            'intervalo_minutos': barbeiro.intervalo_minutos,
            'dias_trabalho': [int(dia) for dia in barbeiro.dias_trabalho.split(',') if dia.strip().isdigit()],
            'pausa_inicio': barbeiro.pausa_inicio.strftime('%H:%M') if barbeiro.pausa_inicio else None,
            'pausa_fim': barbeiro.pausa_fim.strftime('%H:%M') if barbeiro.pausa_fim else None,
            'datas_bloqueadas': datas_bloqueadas,
            'exibir_cupons_publico': barbeiro.exibir_cupons_publico,
        }

    dados = {
        'barbeiros': barbeiros,
        'cortes': cortes,
        'barbeiros_config': barbeiros_config_dict,
        'configuracao': configuracao,
    }
    cache.set(cache_key, dados, settings.PUBLIC_CACHE_TIMEOUT)
    return dados

@empresa_required
def index(request):
    sucesso = False
    dados_agendamento = {}
    erro_validacao = None

    if request.method == 'POST':
        nome = request.POST.get('nome', '').strip()
        telefone = request.POST.get('telefone', '').strip()
        barbeiro_id = request.POST.get('barbeiro')
        corte_id = request.POST.get('corte')
        data_raw = request.POST.get('data', '').strip()
        horario = request.POST.get('horario', '').strip()
        data_agendamento = parse_date(data_raw) if data_raw else None
        telefone_normalizado = Cliente.normalizar_telefone(telefone)
        partes_horario = horario.split(':') if horario else []
        horario_valido = (
            len(partes_horario) == 2
            and all(parte.isdigit() for parte in partes_horario)
            and 0 <= int(partes_horario[0]) <= 23
            and 0 <= int(partes_horario[1]) <= 59
        )

        if is_rate_limited(request, 'agendamento-ip', 20, 60 * 60):
            erro_validacao = "Muitas tentativas de agendamento em pouco tempo. Aguarde alguns minutos e tente novamente."
        elif telefone_normalizado and is_rate_limited(
            request, 'agendamento-telefone', 6, 60 * 60, telefone_normalizado
        ):
            erro_validacao = "Muitas tentativas para este telefone em pouco tempo. Aguarde alguns minutos e tente novamente."
        elif not all([nome, telefone, barbeiro_id, corte_id, data_agendamento, horario]):
            erro_validacao = "Preencha todos os campos para concluir o agendamento."
        elif not str(barbeiro_id).isdigit() or not str(corte_id).isdigit():
            erro_validacao = "Barbeiro ou serviço inválido. Refaça a seleção."
        elif len(nome) < 3 or len(nome) > 100 or not 10 <= len(telefone_normalizado) <= 13:
            erro_validacao = "Informe um nome e um telefone válidos."
        elif len(cupom_codigo := request.POST.get('cupom_codigo', '').strip()) > 20:
            erro_validacao = "Cupom inválido."
        elif not horario_valido:
            erro_validacao = "Horário inválido. Escolha um horário disponível na lista."
        elif data_agendamento < timezone.localdate() or data_agendamento > timezone.localdate() + timedelta(days=31):
            erro_validacao = "Data inválida. Escolha uma data disponível na agenda."
        else:
            try:
                with transaction.atomic():
                    barbeiro = Barbeiro.objects.filter(
                        id=barbeiro_id,
                        ativo=True,
                        aceita_agendamentos_online=True,
                    ).first()
                    corte = Corte.objects.filter(id=corte_id, barbeiro=barbeiro).first() if barbeiro else None

                    if not barbeiro or not corte:
                        erro_validacao = "Barbeiro ou serviço inválido. Refaça a seleção."
                    elif not _horario_publico_valido(barbeiro, data_agendamento, horario):
                        erro_validacao = "Horário inválido. Escolha um horário disponível na lista."
                    else:
                        cliente, _ = Cliente.objects.get_or_create(
                            telefone_normalizado=telefone_normalizado,
                            defaults={'nome': nome, 'telefone': telefone},
                        )

                        cliente_ja_agendado_no_dia = Agendamento.objects.filter(
                            cliente=cliente,
                            data=data_agendamento,
                            status='agendado'
                        ).exists()

                        if cliente_ja_agendado_no_dia:
                            erro_validacao = "Você já possui um agendamento ativo para este dia! Caso precise mudar o horário, por favor, desmarque o anterior primeiro."
                        else:
                            horario_ocupado = Agendamento.objects.filter(
                                barbeiro=barbeiro,
                                data=data_agendamento,
                                horario=horario,
                                status='agendado'
                            ).exists()

                            if horario_ocupado:
                                erro_validacao = "Este horário acabou de ser preenchido por outro cliente. Por favor, escolha outro horário."
                            else:
                                cupom_codigo = cupom_codigo.upper()
                                preco_original = corte.preco
                                desconto_reais = Decimal('0.00')
                                cupom_usado = None
                                cupom_descricao = ''

                                if cupom_codigo and barbeiro.exibir_cupons_publico:
                                    cupom_obj = CupomDesconto.objects.filter(
                                        codigo=cupom_codigo,
                                        ativo=True,
                                        validade__gte=timezone.localdate()
                                    ).filter(
                                        Q(usos_maximo=0) | Q(usos_realizados__lt=F('usos_maximo'))
                                    ).first()

                                    if cupom_obj:
                                        preco = corte.preco
                                        if cupom_obj.tipo == 'PERCENTUAL':
                                            desconto_reais = (preco * cupom_obj.valor_desconto / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                                        else:
                                            desconto_reais = min(cupom_obj.valor_desconto, preco)

                                        cupom_atualizado = CupomDesconto.objects.filter(
                                            pk=cupom_obj.pk,
                                            ativo=True,
                                            validade__gte=timezone.localdate()
                                        ).filter(
                                            Q(usos_maximo=0) | Q(usos_realizados__lt=F('usos_maximo'))
                                        ).update(usos_realizados=F('usos_realizados') + 1)

                                        if cupom_atualizado:
                                            cupom_usado = cupom_obj
                                            cupom_descricao = f"{cupom_obj.codigo} (-R$ {desconto_reais:.2f})"
                                        else:
                                            desconto_reais = Decimal('0.00')

                                valor_final = max(preco_original - desconto_reais, Decimal('0.00'))

                                agendamento = Agendamento.objects.create(
                                    cliente=cliente,
                                    barbeiro=barbeiro,
                                    corte=corte,
                                    data=data_agendamento,
                                    horario=horario,
                                    status='agendado',
                                    cupom_desconto=cupom_usado,
                                    valor_original=preco_original,
                                    desconto_aplicado=desconto_reais,
                                    valor_final=valor_final,
                                )
                                whatsapp = _dados_whatsapp_confirmacao(request, agendamento)

                                sucesso = True
                                dados_agendamento = {
                                    'id_cliente': cliente.id,
                                    'nome': cliente.nome,
                                    'barbeiro': barbeiro.nome,
                                    'corte': corte.nome,
                                    'data': data_agendamento.isoformat(),
                                    'horario': horario,
                                    'cupom': cupom_descricao or None,
                                    'preco_final': float(valor_final),
                                    'token_cancelamento': str(agendamento.token_cancelamento),
                                    'whatsapp': whatsapp,
                                }
            except IntegrityError as exc:
                log_event(
                    'public_booking_conflict',
                    level='warning',
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                erro_validacao = "Este horário acabou de ser preenchido por outro cliente. Por favor, escolha outro horário."

    dados_publicos = _dados_publicos_base(request.empresa)
    barbeiros = dados_publicos['barbeiros']
    cortes = dados_publicos['cortes']
    barbeiros_config_dict = dados_publicos['barbeiros_config']
    configuracao = dados_publicos['configuracao']
    # Somente horarios ativos bloqueiam a selecao no calendario publico.
    agendamentos_existentes = Agendamento.objects.filter(status='agendado')
    horarios_ocupados_lista = [
        {
            'barbeiro_id': str(agend.barbeiro_id),
            'data': agend.data.strftime('%Y-%m-%d'),
            'horario': agend.horario
        }
        for agend in agendamentos_existentes
    ]
    aviso_publico = configuracao if configuracao.aviso_esta_visivel() else None

    contexto = {
        'barbeiros': barbeiros,
        'cortes': cortes,
        'horarios_ocupados': horarios_ocupados_lista,
        'barbeiros_config': barbeiros_config_dict,
        'sucesso': sucesso,
        'dados_agendamento': dados_agendamento,
        'erro_validacao': erro_validacao,
        'aviso': configuracao,
        'aviso_publico': aviso_publico,
        'telefone_placeholder': request.empresa.telefone_placeholder,
        'endereco_barbearia': request.empresa.endereco_formatado,
        'mapa_latitude': request.empresa.latitude,
        'mapa_longitude': request.empresa.longitude,
        'lista_espera_publica_ativa': configuracao.exibir_lista_espera_publica,
    }
    return render(request, 'agendamentos/index.html', contexto)


@empresa_required
def cancelar_por_token(request, token):
    """Cancelamento público usa uma credencial aleatória, nunca apenas telefone/ID."""

    agendamento = get_object_or_404(
        Agendamento.objects.select_related('barbeiro', 'corte', 'cliente'),
        token_cancelamento=token,
    )
    if request.method == 'POST' and agendamento.status == 'agendado':
        if is_rate_limited(request, 'cancelamento-token', 10, 10 * 60, token):
            return render(request, 'agendamentos/cancelar.html', {
                'agendamento': agendamento,
                'erro': 'Muitas tentativas. Aguarde alguns minutos.',
            }, status=429)
        agendamento.status = 'cancelado'
        agendamento.save(update_fields=['status'])
        return render(request, 'agendamentos/cancelar.html', {
            'agendamento': agendamento,
            'sucesso': True,
        })
    return render(request, 'agendamentos/cancelar.html', {
        'agendamento': agendamento,
    })

@require_POST
@empresa_required
def entrar_lista_espera_publico(request):
    """View PÚBLICA (sem login) — chamada via AJAX do index.html."""
    configuracao = ConfiguracaoBarbearia.objects.filter(empresa=request.empresa).first()
    if configuracao and not configuracao.exibir_lista_espera_publica:
        return JsonResponse(
            {'status': 'erro', 'mensagem': 'A lista de espera nao esta disponivel para esta barbearia.'},
            status=403,
        )

    try:
        dados = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'status': 'erro', 'mensagem': 'Dados inválidos.'})

    nome          = dados.get('nome', '').strip()
    telefone      = dados.get('telefone', '').strip()
    barbeiro_id   = dados.get('barbeiro_id')
    data_desejada = dados.get('data')
    data_espera   = parse_date(data_desejada) if data_desejada else None
    telefone_normalizado = Cliente.normalizar_telefone(telefone)

    if is_rate_limited(request, 'lista-espera-ip', 20, 60 * 60):
        return JsonResponse({'status': 'erro', 'mensagem': 'Muitas tentativas. Aguarde alguns minutos e tente novamente.'}, status=429)
    if telefone_normalizado and is_rate_limited(
        request, 'lista-espera-telefone', 5, 60 * 60, telefone_normalizado
    ):
        return JsonResponse({'status': 'erro', 'mensagem': 'Muitas tentativas para este telefone. Aguarde alguns minutos e tente novamente.'}, status=429)

    if not all([nome, telefone, barbeiro_id, data_desejada]):
        return JsonResponse({'status': 'erro', 'mensagem': 'Preencha todos os campos.'})
    if len(nome) > 100 or not 10 <= len(telefone_normalizado) <= 13:
        return JsonResponse({'status': 'erro', 'mensagem': 'Nome ou telefone inválido.'})
    if not str(barbeiro_id).isdigit() or not data_espera or data_espera < timezone.localdate():
        return JsonResponse({'status': 'erro', 'mensagem': 'Dados inválidos.'})

    cliente, _ = Cliente.objects.get_or_create(
        telefone_normalizado=telefone_normalizado,
        defaults={'nome': nome, 'telefone': telefone},
    )
    barbeiro = Barbeiro.objects.filter(
        id=barbeiro_id,
        ativo=True,
        aceita_agendamentos_online=True,
    ).first()
    if not barbeiro:
        return JsonResponse({'status': 'erro', 'mensagem': 'Barbeiro não encontrado.'})

    ja_cadastrado = EntradaListaEspera.objects.filter(
        cliente=cliente, barbeiro=barbeiro,
        data=data_espera, status='aguardando'
    ).exists()

    if ja_cadastrado:
        return JsonResponse({
            'status': 'ja_cadastrado',
            'mensagem': 'Você já está na lista de espera para este dia!'
        })

    try:
        EntradaListaEspera.objects.create(
            cliente=cliente, barbeiro=barbeiro, data=data_espera
        )
    except IntegrityError as exc:
        log_event(
            'waitlist_duplicate',
            level='warning',
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JsonResponse({
            'status': 'ja_cadastrado',
            'mensagem': 'Você já está na lista de espera para este dia!'
        })

    return JsonResponse({
        'status': 'sucesso',
        'mensagem': f'{nome}, você entrou na lista de espera! Entraremos em contato quando um horário abrir.'
    })

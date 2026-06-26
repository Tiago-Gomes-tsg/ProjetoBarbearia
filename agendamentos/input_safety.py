"""Limites transversais para entradas HTTP.

Os atributos HTML melhoram a experiencia, mas nao sao uma barreira de seguranca.
Este middleware aplica os mesmos limites a formularios, query strings e JSON antes
que conversoes ou gravacoes alcancem o banco.
"""

from decimal import Decimal, InvalidOperation
import re

from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse


TEXT_LIMITS = {
    'acao': 40,
    'aviso_cor': 10,
    'aviso_mensagem': 500,
    'aviso_titulo': 100,
    'busca': 100,
    'codigo': 20,
    'comentario': 500,
    'confirmar_slug': 63,
    'cupom_codigo': 20,
    'descricao': 500,
    'email': 254,
    'email_dono': 254,
    'horario': 5,
    'horario_preferido': 5,
    'lista_espera_config_presente': 1,
    'motivo': 100,
    'nome': 100,
    'nome_barbearia': 80,
    'nome_empresa': 120,
    'nome_reserva': 100,
    'plano_contratado': 50,
    'motivo_inativacao': 200,
    'observacoes_internas': 500,
    'nova_senha': 128,
    'nova_senha_confirmacao': 128,
    'new_password1': 128,
    'new_password2': 128,
    'old_password': 128,
    'password': 128,
    'senha': 128,
    'senha_confirmacao': 128,
    'senha_dono': 128,
    'senha_superuser': 128,
    'confirmar_senha': 128,
    'slogan': 100,
    'status': 20,
    'novo_status': 20,
    'telefone': 20,
    'telefone_dono': 20,
    'cep': 9,
    'logradouro': 120,
    'bairro': 80,
    'cidade': 80,
    'uf': 2,
    'numero': 20,
    'complemento': 120,
    'ddd': 2,
    'timezone': 64,
    'username': 150,
    'username_dono': 150,
}

# Faixas de negocio, e nao apenas a capacidade maxima do tipo no banco.
NUMBER_LIMITS = {
    'ano': (2000, 2100),
    'intervalo_minutos': (5, 480),
    'mes': (1, 12),
    'meta_cortes': (0, 100_000),
    'meta_faturamento': (0, 99_999_999.99),
    'pontos': (-1_000_000, 1_000_000),
    'pontos_para_resgate': (1, 1_000_000),
    'pontos_por_corte': (1, 1_000_000),
    'porcentagem_comissao': (0, 100),
    'preco': (0.01, 9_999.99),
    'preco_servico': (0.01, 9_999.99),
    'preco_compra': (0, 9_999.99),
    'preco_venda': (0, 9_999.99),
    'quantidade_adicional': (1, 10_000),
    'quantidade_atual': (0, 10_000),
    'quantidade_baixa': (1, 10_000),
    'quantidade_minima': (0, 10_000),
    'salario_fixo': (0, 999_999.99),
    'usos_maximo': (0, 100_000),
    'valor': (0.01, 99_999_999.99),
    'valor_desconto': (0.01, 9_999.99),
    'valor_mensal': (0.01, 9_999.99),
    'desconto_reais_resgate': (0.01, 9_999.99),
    'desconto_percentual': (0, 100),
    'dia_pagamento_salario': (1, 28),
    'latitude': (-90, 90),
    'longitude': (-180, 180),
    'barbeiro': (1, 9_223_372_036_854_775_807),
    'barbeiro_id': (1, 9_223_372_036_854_775_807),
    'bloqueio_id': (1, 9_223_372_036_854_775_807),
    'cliente_id': (1, 9_223_372_036_854_775_807),
    'corte': (1, 9_223_372_036_854_775_807),
    'cupom_id': (1, 9_223_372_036_854_775_807),
    'entrada_id': (1, 9_223_372_036_854_775_807),
    'meta_id': (1, 9_223_372_036_854_775_807),
    'nota': (1, 5),
    'plano_id': (1, 9_223_372_036_854_775_807),
    'assinatura_id': (1, 9_223_372_036_854_775_807),
}

INTEGER_FIELDS = {
    'ano', 'assinatura_id', 'barbeiro', 'barbeiro_id', 'bloqueio_id',
    'cliente_id', 'corte', 'cupom_id', 'entrada_id', 'mes', 'meta_cortes',
    'meta_id', 'nota', 'plano_id', 'pontos', 'pontos_para_resgate',
    'pontos_por_corte', 'quantidade_adicional', 'quantidade_atual',
    'quantidade_baixa', 'quantidade_minima', 'usos_maximo',
    'dia_pagamento_salario',
}
INTEGER_RE = re.compile(r'^-?\d{1,20}$')
DECIMAL_RE = re.compile(r'^-?\d{1,12}(?:\.\d{1,2})?$')
COORDINATE_RE = re.compile(r'^-?\d{1,3}(?:\.\d{1,6})?$')
DECIMAL_PATTERNS = {
    'latitude': COORDINATE_RE,
    'longitude': COORDINATE_RE,
}

DEFAULT_TEXT_LIMIT = 2_000
MAX_COLLECTION_ITEMS = 500
MAX_JSON_DEPTH = 8


class UnsafeInput(ValueError):
    def __init__(self, field, reason):
        self.field = field
        self.reason = reason
        super().__init__(f'{field}: {reason}')


def normalize_decimal_input(value, *, default=None):
    """Normaliza decimais de formularios BR/US para validacao e Decimal."""

    if value is None:
        return default
    texto = str(value).strip().replace(' ', '').replace('\xa0', '')
    if texto == '':
        return default
    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):
            texto = texto.replace('.', '').replace(',', '.')
        else:
            texto = texto.replace(',', '')
    else:
        texto = texto.replace(',', '.')
    return texto


def _validate_scalar(field, value):
    if value is None:
        return
    value = str(value)
    limit = TEXT_LIMITS.get(field, DEFAULT_TEXT_LIMIT)
    if len(value) > limit:
        raise UnsafeInput(field, f'limite de {limit} caracteres excedido')

    numeric_range = NUMBER_LIMITS.get(field)
    if numeric_range and value.strip():
        normalized = normalize_decimal_input(value)
        if len(normalized) > 24:
            raise UnsafeInput(field, 'numero excessivamente grande')
        pattern = INTEGER_RE if field in INTEGER_FIELDS else DECIMAL_PATTERNS.get(field, DECIMAL_RE)
        if not pattern.fullmatch(normalized):
            raise UnsafeInput(field, 'formato numerico invalido')
        try:
            number = Decimal(normalized)
        except (InvalidOperation, ValueError):
            raise UnsafeInput(field, 'numero invalido') from None
        if not number.is_finite():
            raise UnsafeInput(field, 'numero deve ser finito')
        minimum, maximum = map(Decimal, map(str, numeric_range))
        if number < minimum or number > maximum:
            raise UnsafeInput(field, f'numero fora da faixa {minimum} a {maximum}')


def _validate_json(value, field='json', depth=0):
    if depth > MAX_JSON_DEPTH:
        raise UnsafeInput(field, 'estrutura JSON profunda demais')
    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise UnsafeInput(field, 'objeto JSON grande demais')
        for key, item in value.items():
            key = str(key)
            if len(key) > 100:
                raise UnsafeInput('json', 'nome de campo grande demais')
            _validate_json(item, key, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise UnsafeInput(field, 'lista JSON grande demais')
        for item in value:
            _validate_json(item, field, depth + 1)
    elif isinstance(value, (str, int, float, Decimal)):
        _validate_scalar(field, value)


class InputSafetyMiddleware:
    """Falha cedo para entradas capazes de causar overflow ou consumo excessivo."""

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _error(request, exc):
        from .observability import log_event

        log_event(
            'input_rejected',
            level='warning',
            field=exc.field,
            reason=exc.reason,
        )
        is_json_request = (
            request.content_type == 'application/json'
            or request.headers.get('x-requested-with') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('accept', '')
        )
        if is_json_request:
            return JsonResponse(
                {
                    'erro': 'Entrada invalida.',
                    'campo': exc.field,
                    'detalhe': exc.reason,
                    'request_id': getattr(request, 'request_id', None),
                },
                status=400,
            )

        messages.error(request, f'Entrada invalida em "{exc.field}": {exc.reason}.')
        destino = request.META.get('HTTP_REFERER') or request.path or '/'
        return HttpResponseRedirect(destino)

    def __call__(self, request):
        try:
            for source in (request.GET, request.POST):
                if len(source) > MAX_COLLECTION_ITEMS:
                    raise UnsafeInput('formulario', 'quantidade excessiva de campos')
                for field, values in source.lists():
                    if len(values) > MAX_COLLECTION_ITEMS:
                        raise UnsafeInput(field, 'quantidade excessiva de valores')
                    for value in values:
                        _validate_scalar(field, value)

            if request.content_type == 'application/json' and request.body:
                import json

                try:
                    payload = json.loads(request.body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # A view continua responsavel pela mensagem de JSON malformado.
                    payload = None
                if payload is not None:
                    _validate_json(payload)
        except UnsafeInput as exc:
            return self._error(request, exc)

        return self.get_response(request)

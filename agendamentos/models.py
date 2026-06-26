from django.contrib.auth.models import User
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxLengthValidator,
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.utils import timezone
from django.db.models import Q
from django.db import models
from decimal import Decimal
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .managers import TenantManager
from .tenancy import get_current_empresa

VALID_IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']
HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r'^#[0-9A-Fa-f]{6}$',
    message='Informe uma cor hexadecimal no formato #RRGGBB.',
)
TENANT_SLUG_VALIDATOR = RegexValidator(
    regex=r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$',
    message='Use somente letras minúsculas, números e hífens, sem hífen nas extremidades.',
)


def normalizar_telefone_whatsapp(valor):
    """Converte um telefone brasileiro para o formato numérico exigido pelo wa.me."""

    digitos = re.sub(r'\D', '', valor or '')
    if len(digitos) in {10, 11}:
        return f'55{digitos}'
    if len(digitos) in {12, 13} and digitos.startswith('55'):
        return digitos
    return ''


def validar_telefone_whatsapp(valor):
    if valor and not normalizar_telefone_whatsapp(valor):
        raise ValidationError(
            'Informe um telefone brasileiro com DDD, por exemplo (11) 99999-9999.'
        )


def telefone_placeholder_empresa(empresa):
    ddd = re.sub(r'\D', '', getattr(empresa, 'ddd', '') or '')
    return f'Ex: ({ddd}) 99999-9999' if len(ddd) == 2 else 'Ex: (DDD) 99999-9999'


def hex_color_field(default):
    """Campo de cor com validação também no Admin, scripts e imports."""

    return models.CharField(
        max_length=7,
        default=default,
        validators=[HEX_COLOR_VALIDATOR],
    )


def _tenant_upload_path(instance, categoria, filename):
    """Evita colisões e não expõe nomes enviados pelo usuário no storage."""

    empresa_id = instance.empresa_id or 'sem-empresa'
    extensao = Path(filename).suffix.lower()
    return f'tenants/{empresa_id}/{categoria}/{uuid4().hex}{extensao}'


def upload_foto_barbeiro(instance, filename):
    return _tenant_upload_path(instance, 'barbeiros', filename)


def upload_aviso(instance, filename):
    return _tenant_upload_path(instance, 'branding/avisos', filename)


def upload_capa(instance, filename):
    return _tenant_upload_path(instance, 'branding/capas', filename)


def upload_background(instance, filename):
    return _tenant_upload_path(instance, 'branding/backgrounds', filename)


def upload_logo(instance, filename):
    return _tenant_upload_path(instance, 'branding/logos', filename)


def upload_favicon(instance, filename):
    return _tenant_upload_path(instance, 'branding/favicons', filename)


class Empresa(models.Model):
    """Tenant principal: uma linha representa uma barbearia independente."""

    PAGAMENTO_EM_DIA = 'EM_DIA'
    PAGAMENTO_PENDENTE = 'PENDENTE'
    PAGAMENTO_ATRASADO = 'ATRASADO'
    STATUS_PAGAMENTO_CHOICES = [
        (PAGAMENTO_EM_DIA, 'Em dia'),
        (PAGAMENTO_PENDENTE, 'Pendente'),
        (PAGAMENTO_ATRASADO, 'Atrasado'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    nome = models.CharField(max_length=120)
    slug = models.SlugField(
        max_length=63,
        unique=True,
        validators=[TENANT_SLUG_VALIDATOR],
    )
    data_cadastro = models.DateTimeField(auto_now_add=True)
    ativo = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, default='America/Sao_Paulo')
    plano_contratado = models.CharField(max_length=50, blank=True, default='Freelancer')
    status_pagamento = models.CharField(
        max_length=12,
        choices=STATUS_PAGAMENTO_CHOICES,
        default=PAGAMENTO_EM_DIA,
    )
    valor_mensal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[
            MinValueValidator(Decimal('0')),
            MaxValueValidator(Decimal('999999.99')),
        ],
    )
    proximo_vencimento = models.DateField(null=True, blank=True)
    observacoes_internas = models.TextField(
        max_length=500,
        blank=True,
        default='',
        validators=[MaxLengthValidator(500)],
    )
    desativada_em = models.DateTimeField(null=True, blank=True)
    motivo_inativacao = models.CharField(max_length=200, blank=True, default='')
    cep = models.CharField(max_length=9, blank=True, default='')
    logradouro = models.CharField(max_length=120, blank=True, default='')
    bairro = models.CharField(max_length=80, blank=True, default='')
    cidade = models.CharField(max_length=80, blank=True, default='')
    uf = models.CharField(max_length=2, blank=True, default='')
    numero = models.CharField(max_length=20, blank=True, default='')
    complemento = models.CharField(max_length=120, blank=True, default='')
    ddd = models.CharField(max_length=2, blank=True, default='')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ('nome',)

    def save(self, *args, **kwargs):
        self.slug = (self.slug or '').strip().lower()
        self.cep = re.sub(r'\D', '', self.cep or '')
        self.ddd = re.sub(r'\D', '', self.ddd or '')
        self.uf = (self.uf or '').strip().upper()
        if not self.slug:
            raise ValidationError({'slug': 'O slug da empresa é obrigatório.'})
        TENANT_SLUG_VALIDATOR(self.slug)
        if self.cep and len(self.cep) != 8:
            raise ValidationError({'cep': 'Informe um CEP com 8 digitos.'})
        if self.ddd and len(self.ddd) != 2:
            raise ValidationError({'ddd': 'Informe um DDD com 2 digitos.'})
        if self.uf and not re.fullmatch(r'[A-Z]{2}', self.uf):
            raise ValidationError({'uf': 'Informe a UF com 2 letras.'})
        if self.slug in getattr(settings, 'TENANT_RESERVED_SUBDOMAINS', set()):
            raise ValidationError({'slug': 'Este subdomínio é reservado pela plataforma.'})
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError({'timezone': 'Timezone IANA inválido.'}) from exc
        self.clean_fields()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nome

    @property
    def telefone_placeholder(self):
        return telefone_placeholder_empresa(self)

    @property
    def endereco_formatado(self):
        partes = []
        if self.logradouro:
            partes.append(f'{self.logradouro}, {self.numero}' if self.numero else self.logradouro)
        if self.bairro:
            partes.append(self.bairro)
        cidade_uf = ' - '.join(parte for parte in (self.cidade, self.uf) if parte)
        if cidade_uf:
            partes.append(cidade_uf)
        if self.cep:
            partes.append(f'CEP {self.cep[:5]}-{self.cep[5:]}')
        return ', '.join(partes)


class TenantOwnedModel(models.Model):
    """Base para dados que nunca podem existir fora de uma empresa."""

    # A FK nasce anulável na primeira migration para permitir o backfill seguro
    # da base single-tenant; a migration seguinte torna o campo obrigatório.
    empresa = models.ForeignKey(
        Empresa,
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)s_itens',
    )

    objects = TenantManager()
    all_objects = models.Manager()
    tenant_related_fields = ()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        empresa_atual = get_current_empresa()
        if self.empresa_id is None and empresa_atual is not None:
            self.empresa = empresa_atual
        elif empresa_atual is not None and self.empresa_id != empresa_atual.pk:
            raise ValidationError('Não é permitido gravar dados em outra empresa.')

        update_fields = kwargs.get('update_fields')
        campos_tenant = {'empresa', 'empresa_id'} | {
            nome for campo in self.tenant_related_fields for nome in (campo, f'{campo}_id')
        }
        validar_relacoes = update_fields is None or bool(set(update_fields) & campos_tenant)
        if self.empresa_id is not None and validar_relacoes:
            for campo in self.tenant_related_fields:
                relacionado = getattr(self, campo, None)
                if relacionado is not None and relacionado.empresa_id != self.empresa_id:
                    raise ValidationError(
                        f'O relacionamento "{campo}" pertence a outra empresa.'
                    )
        if update_fields is None:
            self.clean_fields()
        else:
            campos_atualizados = {
                item if isinstance(item, str) else item.name for item in update_fields
            }
            excluir = [
                campo.name
                for campo in self._meta.fields
                if campo.name not in campos_atualizados
                and campo.attname not in campos_atualizados
            ]
            self.clean_fields(exclude=excluir)
        super().save(*args, **kwargs)


class MembroEmpresa(TenantOwnedModel):
    """Papel do usuário dentro do tenant, separado do perfil profissional."""

    OWNER = 'OWNER'
    MANAGER = 'MANAGER'
    BARBER = 'BARBER'
    PAPEL_CHOICES = [
        (OWNER, 'Dono'),
        (MANAGER, 'Gerente'),
        (BARBER, 'Barbeiro'),
    ]

    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='membro_empresa',
    )
    papel = models.CharField(max_length=12, choices=PAPEL_CHOICES, default=BARBER)
    ativo = models.BooleanField(default=True)

    @property
    def is_owner(self):
        return self.papel == self.OWNER

    @property
    def is_manager(self):
        return self.papel in {self.OWNER, self.MANAGER}

    def __str__(self):
        return f'{self.usuario.username} — {self.get_papel_display()}'


class Barbeiro(TenantOwnedModel):
    usuario = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil_barbeiro')
    nome = models.CharField(max_length=100)
    telefone = models.CharField(
        max_length=20,
        blank=True,
        default='',
        validators=[validar_telefone_whatsapp],
        help_text='Obrigatório para o proprietário e opcional para funcionários.',
    )
    foto_perfil = models.FileField(
        upload_to=upload_foto_barbeiro,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=VALID_IMAGE_EXTENSIONS)],
        verbose_name="Foto do profissional",
        help_text="Imagem JPG, PNG ou WEBP. Recomendado: 600x600 px."
    )
    is_dono = models.BooleanField(default=False)
    is_gerente = models.BooleanField(default=False)
    hora_inicio = models.TimeField(default='09:00')
    hora_fim = models.TimeField(default='18:00')
    intervalo_minutos = models.IntegerField(
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(480)],
    )
    ativo = models.BooleanField(default=True)
    aceita_agendamentos_online = models.BooleanField(
        default=True,
        verbose_name='Disponível no agendamento público',
        help_text='Desative para manter o acesso ao painel sem aparecer para clientes.',
    )
    exibir_cupons_publico = models.BooleanField(
        default=False,
        verbose_name="Exibir cupons no agendamento público",
        help_text="Quando ativo, a seção de cupom aparece para clientes que selecionarem este barbeiro."
    )
    receber_confirmacao_whatsapp = models.BooleanField(
        default=True,
        verbose_name='Receber confirmacao por WhatsApp',
        help_text='Quando desativado, o agendamento continua funcionando sem link para este contato.',
    )

    # Dias em que o profissional aparece como opcao na agenda publica.
    trabalha_domingo = models.BooleanField(default=False)
    trabalha_segunda = models.BooleanField(default=True)
    trabalha_terca = models.BooleanField(default=True)
    trabalha_quarta = models.BooleanField(default=True)
    trabalha_quinta = models.BooleanField(default=True)
    trabalha_sexta = models.BooleanField(default=True)
    trabalha_sabado = models.BooleanField(default=True)

    # Remuneracao usada para relatorios e calculo automatico de comissoes.
    TIPO_REMUNERACAO_CHOICES = [
        ('FIXO', 'Salário Fixo'),
        ('COMISSAO', 'Apenas Comissão'),
        ('AMBOS', 'Fixo + Comissão'),
    ]
    tipo_remuneracao = models.CharField(max_length=10, choices=TIPO_REMUNERACAO_CHOICES, default='COMISSAO')
    salario_fixo = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('999999.99'))],
    )
    porcentagem_comissao = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=30.00,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
    )
    dia_pagamento_salario = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text='Obrigatorio para salario fixo. Use 1 a 28 para evitar meses sem vencimento.',
    )

    pausa_inicio = models.TimeField(
        null=True, blank=True,
        verbose_name="Início da pausa/almoço",
        help_text="Deixe vazio se não houver pausa. Ex: 12:00"
    )
    pausa_fim = models.TimeField(
        null=True, blank=True,
        verbose_name="Fim da pausa/almoço",
        help_text="Ex: 13:00"
    )

    @property
    def dias_trabalho(self):
        dias = []
        if self.trabalha_domingo: dias.append('0')
        if self.trabalha_segunda: dias.append('1')
        if self.trabalha_terca: dias.append('2')
        if self.trabalha_quarta: dias.append('3')
        if self.trabalha_quinta: dias.append('4')
        if self.trabalha_sexta: dias.append('5')
        if self.trabalha_sabado: dias.append('6')
        return ",".join(dias)

    @property
    def telefone_whatsapp(self):
        return normalizar_telefone_whatsapp(self.telefone)

    def clean(self):
        super().clean()
        if self.is_dono and not self.telefone_whatsapp:
            raise ValidationError({
                'telefone': 'O telefone de contato do proprietário é obrigatório.',
            })

    def save(self, *args, **kwargs):
        if self.tipo_remuneracao == 'COMISSAO':
            self.salario_fixo = Decimal('0.00')
            self.dia_pagamento_salario = None
        elif self.tipo_remuneracao in {'FIXO', 'AMBOS'}:
            if self.salario_fixo is None or self.salario_fixo <= 0:
                raise ValidationError({'salario_fixo': 'Informe um salario fixo maior que zero.'})
            if not self.dia_pagamento_salario:
                raise ValidationError({'dia_pagamento_salario': 'Informe o dia de pagamento do salario.'})
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        if self.is_dono:
            tipo = "Dono"
        elif self.is_gerente:
            tipo = "Gerente"
        else:
            tipo = "Funcionário"
        return f"{self.nome} ({tipo})"

class Corte(TenantOwnedModel):
    tenant_related_fields = ('barbeiro',)
    barbeiro = models.ForeignKey(Barbeiro, on_delete=models.CASCADE, related_name='cortes')
    nome = models.CharField(max_length=100)
    preco = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('9999.99'))],
    )

    def __str__(self):
        return f"{self.nome} - {self.barbeiro.nome} (R$ {self.preco})"

class Cliente(TenantOwnedModel):
    nome = models.CharField(max_length=100)
    telefone = models.CharField(max_length=20)
    telefone_normalizado = models.CharField(max_length=20, editable=False)
    data_cadastro = models.DateTimeField(auto_now_add=True)

    data_nascimento = models.DateField(
        null=True, blank=True,
        verbose_name="Data de nascimento"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'telefone_normalizado'],
                name='uniq_cliente_empresa_fone_norm',
            ),
        ]
        indexes = [
            models.Index(fields=['empresa', 'nome'], name='cliente_emp_nome_idx'),
        ]

    def __str__(self):
        return f"Cliente ID: {self.id} - {self.nome} ({self.telefone})"

    @staticmethod
    def normalizar_telefone(valor):
        return re.sub(r'\D+', '', valor or '')

    def save(self, *args, **kwargs):
        self.telefone_normalizado = self.normalizar_telefone(self.telefone)
        super().save(*args, **kwargs)

    # Metricas rapidas usadas no perfil do cliente.
    def cortes_no_mes(self):
        hoje = timezone.localdate()
        return self.agendamentos.filter(
            data__year=hoje.year, data__month=hoje.month, status='concluido'
        ).count()

    def gasto_no_mes(self):
        hoje = timezone.localdate()
        agendamentos = self.agendamentos.filter(
            data__year=hoje.year, data__month=hoje.month, status='concluido'
        ).select_related('corte')
        return sum((agendamento.valor_cobrado for agendamento in agendamentos), Decimal('0.00'))

class Agendamento(TenantOwnedModel):
    tenant_related_fields = ('cliente', 'barbeiro', 'corte', 'cupom_desconto')
    STATUS_CHOICES = [
        ('agendado', 'Agendado'),
        ('concluido', 'Concluído'),
        ('cancelado', 'Cancelado'),
    ]

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='agendamentos')
    barbeiro = models.ForeignKey(Barbeiro, on_delete=models.CASCADE)
    corte = models.ForeignKey(Corte, on_delete=models.CASCADE)
    data = models.DateField()
    horario = models.CharField(max_length=5)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='agendado')
    nome_reserva = models.CharField(max_length=100, null=True, blank=True)
    cupom_desconto = models.ForeignKey(
        'CupomDesconto',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agendamentos',
    )
    valor_original = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )
    desconto_aplicado = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )
    desconto_assinatura_aplicado = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )
    valor_final = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )
    token_cancelamento = models.UUIDField(default=uuid4, unique=True, editable=False)

    def __str__(self):
        return f"{self.cliente.nome} - {self.data} às {self.horario} [{self.get_status_display()}]"

    @property
    def valor_original_servico(self):
        return self.valor_original if self.valor_original is not None else self.corte.preco

    @property
    def valor_cobrado(self):
        return self.valor_final if self.valor_final is not None else self.corte.preco

    @property
    def tem_desconto(self):
        return self.desconto_aplicado and self.desconto_aplicado > 0

    class Meta:
        # Apenas agendamentos ativos bloqueiam horario e novo agendamento do cliente no mesmo dia.
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'barbeiro', 'data', 'horario'],
                condition=Q(status='agendado'),
                name='uniq_agendamento_empresa_horario',
            ),
            models.UniqueConstraint(
                fields=['empresa', 'cliente', 'data'],
                condition=Q(status='agendado'),
                name='uniq_agendamento_empresa_cliente_dia',
            ),
        ]
        indexes = [
            models.Index(fields=['empresa', 'data', 'status'], name='ag_emp_data_status_idx'),
            models.Index(fields=['empresa', 'barbeiro', 'data'], name='ag_emp_barb_data_idx'),
        ]
    
# Estoque consumido ou vendido pela barbearia.
class ItemEstoque(TenantOwnedModel):
    nome = models.CharField(max_length=100)
    quantidade_atual = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(10_000)]
    )
    quantidade_minima = models.PositiveIntegerField(
        default=2,
        validators=[MaxValueValidator(10_000)],
        help_text="Aviso para quando o estoque estiver baixo",
    )
    preco_compra = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('9999.99'))],
        help_text="Quanto custou para a barbearia",
    )
    preco_venda = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('9999.99'))],
        help_text="Preço caso seja vendido ao cliente",
    )
    data_ultima_atualizacao = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.nome} ({self.quantidade_atual} un)"

    class Meta:
        indexes = [
            models.Index(fields=['empresa', 'nome'], name='estoque_emp_nome_idx'),
        ]

    @property
    def valor_total_custo(self):
        return self.quantidade_atual * self.preco_compra


# Comissoes geradas quando um atendimento e marcado como concluido.
class LancamentoComissao(TenantOwnedModel):
    tenant_related_fields = ('barbeiro', 'agendamento')
    barbeiro = models.ForeignKey(Barbeiro, on_delete=models.CASCADE, related_name='comissoes')
    agendamento = models.ForeignKey('Agendamento', on_delete=models.SET_NULL, null=True, blank=True)
    valor_servico = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )
    valor_comissao = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )
    pago = models.BooleanField(default=False, help_text="Se a comissão já foi repassada ao funcionário")
    data_gerada = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"Comissão {self.barbeiro.nome} - R$ {self.valor_comissao}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'agendamento'],
                condition=Q(agendamento__isnull=False),
                name='uniq_comissao_empresa_agendamento',
            ),
        ]


# Entradas e saidas usadas para montar o fluxo de caixa.
class TransacaoFinanceira(TenantOwnedModel):
    tenant_related_fields = ('item_estoque', 'barbeiro', 'agendamento')
    TIPO_CHOICES = [
        ('ENTRADA', 'Entrada (Faturamento)'),
        ('SAIDA', 'Saída (Despesa)'),
    ]
    
    CATEGORIA_CHOICES = [
        ('SERVICO', 'Serviço de Barbearia'),
        ('PRODUTO', 'Venda de Produto'),
        ('ASSINATURA', 'Receita de Assinatura Mensal'),
        ('OUTRAS_RECEITAS', 'Outras Receitas (Entrada)'),
        ('ESTOQUE', 'Compra de Insumos/Produtos'),
        ('COMISSAO', 'Pagamento de Comissão'),
        ('SALARIO', 'Pagamento de Salário'),
        ('ALUGUEL', 'Aluguel e Contas estruturais'),
        ('OUTROS', 'Outras Despesas (Saída)'),
    ]

    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES)
    categoria = models.CharField(max_length=20, choices=CATEGORIA_CHOICES)
    valor = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal('0.01')),
            MaxValueValidator(Decimal('99999999.99')),
        ],
    )
    descricao = models.TextField(
        max_length=500,
        blank=True,
        null=True,
        validators=[MaxLengthValidator(500)],
        help_text="Detalhes da movimentação",
    )
    data = models.DateField(default=timezone.now)
    
    item_estoque = models.ForeignKey(ItemEstoque, on_delete=models.SET_NULL, null=True, blank=True)
    barbeiro = models.ForeignKey(Barbeiro, on_delete=models.SET_NULL, null=True, blank=True)
    agendamento = models.ForeignKey(Agendamento, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.tipo} | {self.categoria} | R$ {self.valor}"

    class Meta:
        indexes = [
            models.Index(fields=['empresa', 'data', 'tipo'], name='fin_emp_data_tipo_idx'),
            models.Index(fields=['empresa', 'categoria'], name='fin_emp_categoria_idx'),
        ]


class LancamentoSalario(TenantOwnedModel):
    tenant_related_fields = ('barbeiro', 'transacao_financeira')
    barbeiro = models.ForeignKey(Barbeiro, on_delete=models.CASCADE, related_name='salarios')
    competencia_ano = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(2100)]
    )
    competencia_mes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    valor_salario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('999999.99'))],
    )
    data_vencimento = models.DateField()
    data_pagamento = models.DateField(null=True, blank=True)
    pago = models.BooleanField(default=False)
    transacao_financeira = models.OneToOneField(
        TransacaoFinanceira,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lancamento_salario',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'barbeiro', 'competencia_ano', 'competencia_mes'],
                name='uniq_salario_empresa_barbeiro_competencia',
            ),
        ]
        indexes = [
            models.Index(fields=['empresa', 'pago', 'data_vencimento'], name='sal_emp_pago_venc_idx'),
        ]

    def __str__(self):
        return f'Salario {self.barbeiro.nome} {self.competencia_mes:02d}/{self.competencia_ano}'


class ConfiguracaoBarbearia(TenantOwnedModel):
    """
    Uma configuração visual por empresa.
    """
    empresa = models.OneToOneField(
        Empresa,
        on_delete=models.CASCADE,
        related_name='configuracao',
    )
    aviso_ativo = models.BooleanField(default=False, verbose_name="Exibir aviso")
    aviso_titulo = models.CharField(
        max_length=100, blank=True, default="Aviso Importante",
        verbose_name="Título do aviso"
    )
    aviso_mensagem = models.TextField(
        max_length=500,
        blank=True,
        validators=[MaxLengthValidator(500)],
        verbose_name="Mensagem do aviso",
        help_text="Este texto será exibido na tela inicial de agendamento."
    )
    aviso_imagem = models.FileField(
        upload_to=upload_aviso,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=VALID_IMAGE_EXTENSIONS)],
        verbose_name="Imagem do aviso",
        help_text="Imagem JPG, PNG ou WEBP. Recomendado: 1200x675 px."
    )
    aviso_data_inicio = models.DateField(
        null=True,
        blank=True,
        verbose_name="Exibir a partir de"
    )
    aviso_data_fim = models.DateField(
        null=True,
        blank=True,
        verbose_name="Exibir ate"
    )
    aviso_cor = models.CharField(
        max_length=10,
        choices=[('amarelo','Amarelo'), ('vermelho','Vermelho'), ('azul','Azul')],
        default='amarelo',
        verbose_name="Cor do aviso"
    )

    # Identidade exibida no painel e na pagina publica.
    nome_barbearia = models.CharField(
        max_length=80, default='Barbearia',
        verbose_name="Nome da Barbearia"
    )
    slogan = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name="Slogan / Subtítulo",
        help_text="Texto pequeno abaixo do nome no sidebar. Ex: Cortes & Estilo"
    )

    # Foto usada como identidade visual da barbearia.
    foto_barbearia = models.FileField(
        upload_to=upload_capa,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=VALID_IMAGE_EXTENSIONS)],
        verbose_name="Foto da barbearia",
        help_text="Imagem JPG, PNG ou WEBP. Recomendado: 1600x900 px."
    )
    foto_fundo_publico = models.FileField(
        upload_to=upload_background,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=VALID_IMAGE_EXTENSIONS)],
        verbose_name="Foto de fundo da pagina publica",
        help_text="Imagem JPG, PNG ou WEBP. Recomendado: 1920x1080 px."
    )

    logo = models.FileField(
        upload_to=upload_logo,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=VALID_IMAGE_EXTENSIONS)],
        verbose_name='Logo',
        help_text='Logo em JPG, PNG ou WEBP. Prefira fundo transparente.',
    )
    favicon = models.FileField(
        upload_to=upload_favicon,
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=VALID_IMAGE_EXTENSIONS)],
        verbose_name='Favicon',
        help_text='Ícone quadrado em JPG, PNG ou WEBP.',
    )

    # Paletas salvas no banco alimentam as variaveis CSS de painel e agendamento.
    TEMA_CHOICES = [
        ('dark', 'Escuro'),
        ('light', 'Claro'),
    ]
    tema_painel_padrao = models.CharField(
        max_length=10,
        choices=TEMA_CHOICES,
        default='dark',
        verbose_name="Tema padrao do painel"
    )
    tema_agendamento_padrao = models.CharField(
        max_length=10,
        choices=TEMA_CHOICES,
        default='dark',
        verbose_name="Tema padrao do agendamento"
    )
    exibir_lista_espera_publica = models.BooleanField(
        default=True,
        verbose_name='Exibir lista de espera no agendamento publico',
        help_text='Quando desativado, clientes nao veem nem conseguem entrar na lista de espera publica.',
    )

    cor_destaque_agendamento = models.CharField(
        max_length=7,
        default='#ffb74d',
        validators=[HEX_COLOR_VALIDATOR],
        verbose_name="Cor de destaque do agendamento (hex)",
        help_text="Cor principal usada na pagina publica de agendamento. Ex: #ffb74d"
    )
    cor_texto_botao_agendamento = models.CharField(
        max_length=7,
        default='#121212',
        validators=[HEX_COLOR_VALIDATOR],
        verbose_name="Cor do texto nos botoes do agendamento"
    )

    cor_destaque = models.CharField(
        max_length=7, default='#ffb74d',
        validators=[HEX_COLOR_VALIDATOR],
        verbose_name="Cor de destaque (hex)",
        help_text="Cor principal usada em botões, links ativos e destaques. Ex: #ffb74d"
    )
    cor_texto_botao = models.CharField(
        max_length=7, default='#121212',
        validators=[HEX_COLOR_VALIDATOR],
        verbose_name="Cor do texto nos botões",
        help_text="Use #ffffff para texto claro ou #121212 para texto escuro"
    )
    tema_escuro_fundo = hex_color_field('#111413')
    tema_escuro_sidebar = hex_color_field('#171a19')
    tema_escuro_card = hex_color_field('#1d2220')
    tema_escuro_input = hex_color_field('#26302c')
    tema_escuro_borda = hex_color_field('#2f3a35')
    tema_escuro_texto = hex_color_field('#e5ebe8')
    tema_escuro_texto_forte = hex_color_field('#ffffff')
    tema_escuro_texto_suave = hex_color_field('#abb8b2')

    tema_claro_fundo = hex_color_field('#dfe7e3')
    tema_claro_sidebar = hex_color_field('#e8efeb')
    tema_claro_card = hex_color_field('#f1f5f0')
    tema_claro_input = hex_color_field('#d4dfda')
    tema_claro_borda = hex_color_field('#afbeb8')
    tema_claro_texto = hex_color_field('#24302b')
    tema_claro_texto_forte = hex_color_field('#111a17')
    tema_claro_texto_suave = hex_color_field('#53645d')

    agendamento_tema_escuro_fundo = hex_color_field('#101413')
    agendamento_tema_escuro_sidebar = hex_color_field('#171d1a')
    agendamento_tema_escuro_card = hex_color_field('#1c2420')
    agendamento_tema_escuro_input = hex_color_field('#26332d')
    agendamento_tema_escuro_borda = hex_color_field('#314038')
    agendamento_tema_escuro_texto = hex_color_field('#e5ebe8')
    agendamento_tema_escuro_texto_forte = hex_color_field('#ffffff')
    agendamento_tema_escuro_texto_suave = hex_color_field('#afbbb5')

    agendamento_tema_claro_fundo = hex_color_field('#e3e6dc')
    agendamento_tema_claro_sidebar = hex_color_field('#eef1e8')
    agendamento_tema_claro_card = hex_color_field('#f5f6ee')
    agendamento_tema_claro_input = hex_color_field('#dde3d8')
    agendamento_tema_claro_borda = hex_color_field('#b7c0b3')
    agendamento_tema_claro_texto = hex_color_field('#263028')
    agendamento_tema_claro_texto_forte = hex_color_field('#111812')
    agendamento_tema_claro_texto_suave = hex_color_field('#5c675d')

    class Meta:
        verbose_name = "Configuração da Barbearia"
        verbose_name_plural = "Configurações da Barbearia"

    def __str__(self):
        return "Configurações Gerais"

    def aviso_esta_visivel(self, hoje=None):
        hoje = hoje or timezone.localdate()
        if not self.aviso_ativo or not self.aviso_mensagem:
            return False
        if self.aviso_data_inicio and hoje < self.aviso_data_inicio:
            return False
        if self.aviso_data_fim and hoje > self.aviso_data_fim:
            return False
        return True
    
# Planos mensais e assinaturas ativas dos clientes.

class PlanoMensal(TenantOwnedModel):
    nome = models.CharField(max_length=100, verbose_name="Nome do plano")
    valor_mensal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('9999.99'))],
        verbose_name="Valor mensal (R$)",
    )
    descricao = models.TextField(
        max_length=500,
        blank=True,
        validators=[MaxLengthValidator(500)],
        verbose_name="Descrição / Benefícios",
    )
    desconto_percentual = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('100.00'),
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        verbose_name="Desconto nos cortes (%)",
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Plano Mensal"
        verbose_name_plural = "Planos Mensais"
        indexes = [
            models.Index(fields=['empresa', 'ativo'], name='plano_emp_ativo_idx'),
        ]

    def __str__(self):
        return f"{self.nome} — R$ {self.valor_mensal}/mês ({self.desconto_percentual}% off)"

    def total_assinantes_ativos(self):
        return self.assinantes.filter(status='ativo').count()


class AssinaturaCliente(TenantOwnedModel):
    tenant_related_fields = ('cliente', 'plano')
    STATUS_CHOICES = [
        ('ativo', 'Ativo'),
        ('suspenso', 'Suspenso'),
        ('cancelado', 'Cancelado'),
    ]
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name='assinaturas')
    plano = models.ForeignKey(PlanoMensal, on_delete=models.PROTECT, related_name='assinantes')
    data_inicio = models.DateField(verbose_name="Início da assinatura")
    data_renovacao = models.DateField(verbose_name="Próxima renovação")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='ativo')
    observacoes = models.TextField(
        max_length=500,
        blank=True,
        validators=[MaxLengthValidator(500)],
    )

    class Meta:
        verbose_name = "Assinatura de Cliente"
        verbose_name_plural = "Assinaturas de Clientes"
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'cliente'],
                name='uniq_assinatura_empresa_cliente',
            ),
        ]

    def __str__(self):
        return f"{self.cliente.nome} — {self.plano.nome} [{self.get_status_display()}]"

    @property
    def esta_ativo(self):
        return self.status == 'ativo' and self.data_renovacao >= timezone.localdate()

    def cortes_no_mes_atual(self):
        hoje = timezone.localdate()
        return Agendamento.all_objects.filter(
            empresa=self.empresa,
            cliente=self.cliente,
            status='concluido',
            data__year=hoje.year,
            data__month=hoje.month
        ).count()

# Bloqueios retiram datas da agenda publica em folgas, ferias ou indisponibilidades.
class BloqueioAgenda(TenantOwnedModel):
    tenant_related_fields = ('barbeiro',)
    barbeiro = models.ForeignKey(Barbeiro, on_delete=models.CASCADE, related_name='bloqueios')
    data_inicio = models.DateField(verbose_name="Início do bloqueio")
    data_fim = models.DateField(verbose_name="Fim do bloqueio")
    motivo = models.CharField(max_length=100, blank=True, default="Folga")

    class Meta:
        verbose_name = "Bloqueio de Agenda"
        verbose_name_plural = "Bloqueios de Agenda"
        indexes = [
            models.Index(fields=['empresa', 'barbeiro', 'data_fim'], name='bloq_emp_barb_fim_idx'),
        ]

    def __str__(self):
        return f"{self.barbeiro.nome}: {self.data_inicio} a {self.data_fim} ({self.motivo})"

# Avaliacoes usam token publico para o cliente responder sem login.
class AvaliacaoAtendimento(TenantOwnedModel):
    tenant_related_fields = ('agendamento',)
    NOTAS = [(i, f'{i} estrela' if i == 1 else f'{i} estrelas') for i in range(1, 6)]
    agendamento = models.OneToOneField(
        Agendamento, on_delete=models.CASCADE, related_name='avaliacao'
    )
    nota = models.PositiveSmallIntegerField(choices=NOTAS)
    comentario = models.TextField(
        max_length=500,
        blank=True,
        validators=[MaxLengthValidator(500)],
    )
    token = models.CharField(max_length=64, unique=True)
    respondida = models.BooleanField(default=False)
    data_avaliacao = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Avaliação de Atendimento"
        verbose_name_plural = "Avaliações de Atendimentos"

    def __str__(self):
        return f"Avaliacao {self.agendamento.cliente.nome} - nota {self.nota}"

# Configuracao e saldo do programa de fidelidade.
class ConfigLoyalty(TenantOwnedModel):
    """Configuração de fidelidade exclusiva de uma empresa."""
    empresa = models.OneToOneField(
        Empresa,
        on_delete=models.CASCADE,
        related_name='config_loyalty',
    )
    ativo = models.BooleanField(default=False, verbose_name="Programa ativo")
    pontos_por_corte = models.PositiveIntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(1_000_000)],
        verbose_name="Pontos por corte concluído",
    )
    pontos_para_resgate = models.PositiveIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(1_000_000)],
        verbose_name="Pontos necessários para resgatar",
    )
    desconto_reais_resgate = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=10.00,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('9999.99'))],
        verbose_name="Desconto em R$ por resgate"
    )

    class Meta:
        verbose_name = "Configuração de Fidelidade"

    def __str__(self):
        return "Programa de Fidelidade"

class SaldoLoyalty(TenantOwnedModel):
    tenant_related_fields = ('cliente',)
    cliente = models.OneToOneField(
        Cliente, on_delete=models.CASCADE, related_name='saldo_loyalty'
    )
    pontos = models.IntegerField(
        default=0,
        validators=[MinValueValidator(-1_000_000), MaxValueValidator(1_000_000)],
    )

    def __str__(self):
        return f"{self.cliente.nome}: {self.pontos} pts"

class TransacaoLoyalty(TenantOwnedModel):
    tenant_related_fields = ('cliente', 'agendamento')
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='transacoes_loyalty'
    )
    # Valores positivos somam pontos; negativos registram resgates.
    pontos = models.IntegerField(
        validators=[MinValueValidator(-1_000_000), MaxValueValidator(1_000_000)]
    )
    descricao = models.CharField(max_length=200)
    data = models.DateTimeField(auto_now_add=True)
    agendamento = models.ForeignKey(
        Agendamento, on_delete=models.SET_NULL, null=True, blank=True
    )

    def __str__(self):
        sinal = "+" if self.pontos > 0 else ""
        return f"{self.cliente.nome}: {sinal}{self.pontos} pts — {self.descricao}"

# Lista de espera por cliente, barbeiro e data desejada.
class EntradaListaEspera(TenantOwnedModel):
    tenant_related_fields = ('cliente', 'barbeiro')
    STATUS_CHOICES = [
        ('aguardando', 'Aguardando'),
        ('notificado', 'Notificado'),
        ('convertido', 'Convertido'),
        ('expirado', 'Expirado'),
    ]
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='lista_espera'
    )
    barbeiro = models.ForeignKey(
        Barbeiro, on_delete=models.CASCADE, related_name='lista_espera'
    )
    data = models.DateField()
    horario_preferido = models.CharField(max_length=5, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='aguardando')
    data_entrada = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Lista de Espera"
        verbose_name_plural = "Lista de Espera"
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'cliente', 'barbeiro', 'data'],
                condition=Q(status='aguardando'),
                name='uniq_lista_espera_empresa_aguardando',
            ),
        ]
        indexes = [
            models.Index(fields=['empresa', 'data', 'status'], name='espera_emp_data_status_idx'),
        ]

    def __str__(self):
        return f"{self.cliente.nome} → {self.barbeiro.nome} em {self.data}"

# Metas mensais calculadas com atendimentos concluidos.
class MetaBarbeiro(TenantOwnedModel):
    tenant_related_fields = ('barbeiro',)
    barbeiro = models.ForeignKey(
        Barbeiro, on_delete=models.CASCADE, related_name='metas'
    )
    mes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    ano = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(2100)]
    )
    meta_cortes = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(100_000)]
    )
    meta_faturamento = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('99999999.99'))],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'barbeiro', 'mes', 'ano'],
                name='uniq_meta_empresa_barbeiro_mes',
            ),
        ]
        verbose_name = "Meta de Barbeiro"
        verbose_name_plural = "Metas de Barbeiros"

    def __str__(self):
        return f"Meta {self.barbeiro.nome} — {self.mes:02d}/{self.ano}"

    def cortes_realizados(self):
        return Agendamento.all_objects.filter(
            empresa=self.empresa,
            barbeiro=self.barbeiro,
            data__year=self.ano, data__month=self.mes, status='concluido'
        ).count()

    def faturamento_realizado(self):
        return float(
            sum(
                (agendamento.valor_cobrado for agendamento in Agendamento.all_objects.filter(
                    empresa=self.empresa,
                    barbeiro=self.barbeiro,
                    data__year=self.ano, data__month=self.mes, status='concluido'
                ).select_related('corte')),
                Decimal('0.00')
            )
        )

    def pct_cortes(self):
        if not self.meta_cortes:
            return 0
        return min(int(self.cortes_realizados() / self.meta_cortes * 100), 100)

    def pct_faturamento(self):
        if not float(self.meta_faturamento):
            return 0
        return min(int(self.faturamento_realizado() / float(self.meta_faturamento) * 100), 100)

# Cupons validados no agendamento publico antes de aplicar desconto.
class CupomDesconto(TenantOwnedModel):
    TIPO_CHOICES = [
        ('PERCENTUAL', 'Percentual (%)'),
        ('FIXO', 'Valor Fixo (R$)'),
    ]
    codigo = models.CharField(max_length=20, verbose_name="Código")
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, default='PERCENTUAL')
    valor_desconto = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('9999.99'))],
        verbose_name="Valor do desconto",
    )
    validade = models.DateField(verbose_name="Válido até")
    usos_maximo = models.PositiveIntegerField(
        default=1,
        validators=[MaxValueValidator(100_000)],
        help_text="0 = sem limite",
        verbose_name="Usos máximos",
    )
    usos_realizados = models.PositiveIntegerField(
        default=0, validators=[MaxValueValidator(100_000)]
    )
    ativo = models.BooleanField(default=True)
    descricao = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Cupom de Desconto"
        verbose_name_plural = "Cupons de Desconto"
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'codigo'],
                name='uniq_cupom_empresa_codigo',
            ),
        ]

    def __str__(self):
        suf = '%' if self.tipo == 'PERCENTUAL' else 'R$'
        return f"{self.codigo} — {self.valor_desconto}{suf}"

    def save(self, *args, **kwargs):
        self.codigo = (self.codigo or '').strip().upper()
        super().save(*args, **kwargs)

    @property
    def esta_valido(self):
        if not self.ativo:
            return False
        if timezone.localdate() > self.validade:
            return False
        if self.usos_maximo > 0 and self.usos_realizados >= self.usos_maximo:
            return False
        return True


class AceiteTermos(TenantOwnedModel):
    """Registro imutável do consentimento operacional de cada membro do tenant."""

    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='aceites_termos_empresa',
    )
    versao_termos = models.CharField(max_length=20)
    versao_privacidade = models.CharField(max_length=20)
    aceito_em = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    aceite_em_nome_da_empresa = models.BooleanField(
        default=False,
        verbose_name='Aceite realizado em nome da barbearia',
    )

    class Meta:
        verbose_name = 'Aceite de termos'
        verbose_name_plural = 'Aceites de termos'
        constraints = [
            models.UniqueConstraint(
                fields=['empresa', 'usuario', 'versao_termos', 'versao_privacidade'],
                name='uniq_aceite_termos_empresa_versoes',
            ),
        ]
        indexes = [
            models.Index(
                fields=['empresa', 'usuario', 'aceito_em'],
                name='aceite_emp_user_data_idx',
            ),
        ]

    def __str__(self):
        return f'{self.usuario.username} — termos {self.versao_termos}'


class UserSession(models.Model):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessoes_ativas')
    session_key = models.CharField(max_length=40, unique=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    ultimo_uso = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-ultimo_uso',)
        verbose_name = "Sessao de Usuario"
        verbose_name_plural = "Sessoes de Usuarios"

    def __str__(self):
        return f"{self.usuario.username} - {self.session_key}"

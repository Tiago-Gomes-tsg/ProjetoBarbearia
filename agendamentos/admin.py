from django.contrib import admin
from django.forms.models import BaseInlineFormSet
from django.utils import timezone

from .models import (
    AceiteTermos,
    Agendamento,
    AssinaturaCliente,
    AvaliacaoAtendimento,
    Barbeiro,
    BloqueioAgenda,
    Cliente,
    ConfigLoyalty,
    ConfiguracaoBarbearia,
    Corte,
    CupomDesconto,
    Empresa,
    EntradaListaEspera,
    ItemEstoque,
    LancamentoComissao,
    LancamentoSalario,
    MembroEmpresa,
    MetaBarbeiro,
    PlanoMensal,
    SaldoLoyalty,
    TransacaoFinanceira,
    TransacaoLoyalty,
    UserSession,
)


def _somente_superuser(request):
    return request.user.is_active and request.user.is_superuser


# Defesa duplicada: o middleware devolve 404 e o próprio AdminSite também nega.
admin.site.has_permission = _somente_superuser
admin.site.site_header = 'Administração da Plataforma Barbearia'
admin.site.site_title = 'Plataforma Barbearia'
admin.site.index_title = 'Tenants e dados operacionais'
admin.site.site_url = '/plataforma/empresas/'


class MembroEmpresaInline(admin.TabularInline):
    model = MembroEmpresa
    extra = 0
    fields = ('usuario', 'papel', 'ativo')
    autocomplete_fields = ('usuario',)


@admin.action(description='Ativar empresas selecionadas')
def ativar_empresas(modeladmin, request, queryset):
    queryset.update(ativo=True, desativada_em=None, motivo_inativacao='')


@admin.action(description='Desativar empresas selecionadas')
def desativar_empresas(modeladmin, request, queryset):
    queryset.update(ativo=False, desativada_em=timezone.now())


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = (
        'nome', 'slug', 'ativo', 'status_pagamento', 'plano_contratado',
        'valor_mensal', 'proximo_vencimento', 'total_membros', 'total_clientes',
        'data_cadastro',
    )
    list_filter = ('ativo', 'status_pagamento', 'timezone')
    search_fields = ('nome', 'slug', 'cidade', 'uf', 'cep')
    readonly_fields = ('id', 'data_cadastro')
    inlines = (MembroEmpresaInline,)
    actions = (ativar_empresas, desativar_empresas)
    save_on_top = True

    @admin.display(description='Membros')
    def total_membros(self, obj):
        return MembroEmpresa.all_objects.filter(empresa=obj, ativo=True).count()

    @admin.display(description='Clientes')
    def total_clientes(self, obj):
        return Cliente.all_objects.filter(empresa=obj).count()


@admin.register(MembroEmpresa)
class MembroEmpresaAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'empresa', 'papel', 'ativo')
    list_filter = ('empresa', 'papel', 'ativo')
    search_fields = ('usuario__username', 'usuario__email', 'empresa__nome')
    autocomplete_fields = ('usuario',)
    list_select_related = ('empresa', 'usuario')


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'nome', 'telefone', 'data_nascimento', 'data_cadastro')
    search_fields = ('nome', 'telefone', 'telefone_normalizado')
    list_filter = ('empresa', 'data_cadastro')
    readonly_fields = ('telefone_normalizado', 'data_cadastro')
    list_select_related = ('empresa',)


@admin.register(Barbeiro)
class BarbeiroAdmin(admin.ModelAdmin):
    list_display = (
        'empresa', 'nome', 'telefone', 'usuario', 'papel_legado',
        'aceita_agendamentos_online', 'hora_inicio', 'hora_fim', 'dias_trabalho', 'ativo',
    )
    list_filter = (
        'empresa', 'is_dono', 'is_gerente', 'aceita_agendamentos_online',
        'exibir_cupons_publico', 'receber_confirmacao_whatsapp', 'tipo_remuneracao', 'ativo',
    )
    search_fields = ('nome', 'telefone', 'usuario__username', 'usuario__email')
    autocomplete_fields = ('usuario',)
    list_select_related = ('empresa', 'usuario')

    @admin.display(description='Perfil legado')
    def papel_legado(self, obj):
        return 'Dono' if obj.is_dono else 'Gerente' if obj.is_gerente else 'Barbeiro'

    @admin.display(description='Dias de trabalho')
    def dias_trabalho(self, obj):
        nomes = ('Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb')
        return ', '.join(nomes[int(dia)] for dia in obj.dias_trabalho.split(',') if dia)


@admin.register(LancamentoSalario)
class LancamentoSalarioAdmin(admin.ModelAdmin):
    list_display = (
        'empresa', 'barbeiro', 'competencia_mes', 'competencia_ano',
        'valor_salario', 'data_vencimento', 'pago', 'data_pagamento',
    )
    list_filter = ('empresa', 'pago', 'competencia_ano', 'competencia_mes')
    search_fields = ('barbeiro__nome',)
    list_select_related = ('empresa', 'barbeiro', 'transacao_financeira')


@admin.register(Corte)
class CorteAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'nome', 'barbeiro', 'preco')
    list_filter = ('empresa', 'barbeiro')
    search_fields = ('nome', 'barbeiro__nome')
    list_select_related = ('empresa', 'barbeiro')


@admin.register(Agendamento)
class AgendamentoAdmin(admin.ModelAdmin):
    list_display = ('id', 'empresa', 'cliente', 'barbeiro', 'corte', 'data', 'horario', 'status', 'valor_final')
    list_filter = ('empresa', 'status', 'data', 'barbeiro')
    search_fields = ('cliente__nome', 'cliente__telefone', 'barbeiro__nome')
    ordering = ('-data', 'horario')
    readonly_fields = ('token_cancelamento',)
    list_select_related = ('empresa', 'cliente', 'barbeiro', 'corte')


@admin.register(ItemEstoque)
class ItemEstoqueAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'nome', 'quantidade_atual', 'quantidade_minima', 'preco_compra', 'preco_venda', 'data_ultima_atualizacao')
    list_filter = ('empresa',)
    search_fields = ('nome',)
    readonly_fields = ('data_ultima_atualizacao',)


@admin.register(LancamentoComissao)
class LancamentoComissaoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'barbeiro', 'agendamento', 'valor_servico', 'valor_comissao', 'pago', 'data_gerada')
    list_filter = ('empresa', 'pago', 'data_gerada')
    search_fields = ('barbeiro__nome',)
    list_select_related = ('empresa', 'barbeiro', 'agendamento')


@admin.register(TransacaoFinanceira)
class TransacaoFinanceiraAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'data', 'tipo', 'categoria', 'valor', 'barbeiro', 'agendamento', 'descricao')
    list_filter = ('empresa', 'tipo', 'categoria', 'data')
    search_fields = ('descricao', 'barbeiro__nome')
    date_hierarchy = 'data'
    list_select_related = ('empresa', 'barbeiro', 'agendamento')


@admin.register(ConfiguracaoBarbearia)
class ConfiguracaoBarbeariaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'nome_barbearia', 'tema_painel_padrao', 'tema_agendamento_padrao', 'aviso_ativo')
    search_fields = ('empresa__nome', 'nome_barbearia')
    list_select_related = ('empresa',)


class AssinaturaClienteInlineFormSet(BaseInlineFormSet):
    def save_new(self, form, commit=True):
        objeto = super().save_new(form, commit=False)
        objeto.empresa = self.instance.empresa
        if commit:
            objeto.save()
            form.save_m2m()
        return objeto


class AssinaturaClienteInline(admin.TabularInline):
    model = AssinaturaCliente
    formset = AssinaturaClienteInlineFormSet
    extra = 0
    fields = ('cliente', 'data_inicio', 'data_renovacao', 'status')
    readonly_fields = ('data_inicio',)


@admin.register(PlanoMensal)
class PlanoMensalAdmin(admin.ModelAdmin):
    list_display = (
        'empresa',
        'nome',
        'valor_mensal',
        'desconto_percentual',
        'total_assinantes_ativos',
        'ativo',
    )
    list_filter = ('empresa', 'ativo')
    search_fields = ('nome',)
    inlines = (AssinaturaClienteInline,)


@admin.register(AssinaturaCliente)
class AssinaturaClienteAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'cliente', 'plano', 'data_inicio', 'data_renovacao', 'status')
    list_filter = ('empresa', 'status', 'plano')
    search_fields = ('cliente__nome', 'cliente__telefone')
    date_hierarchy = 'data_renovacao'
    list_select_related = ('empresa', 'cliente', 'plano')


@admin.register(BloqueioAgenda)
class BloqueioAgendaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'barbeiro', 'data_inicio', 'data_fim', 'motivo')
    list_filter = ('empresa', 'barbeiro')
    ordering = ('-data_inicio',)
    list_select_related = ('empresa', 'barbeiro')


@admin.register(AvaliacaoAtendimento)
class AvaliacaoAtendimentoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'agendamento', 'nota', 'respondida', 'data_avaliacao')
    list_filter = ('empresa', 'nota', 'respondida', 'agendamento__barbeiro')
    readonly_fields = ('token',)
    ordering = ('-data_avaliacao',)
    list_select_related = ('empresa', 'agendamento')


@admin.register(ConfigLoyalty)
class ConfigLoyaltyAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'ativo', 'pontos_por_corte', 'pontos_para_resgate', 'desconto_reais_resgate')
    list_select_related = ('empresa',)


@admin.register(SaldoLoyalty)
class SaldoLoyaltyAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'cliente', 'pontos')
    list_filter = ('empresa',)
    search_fields = ('cliente__nome',)
    ordering = ('-pontos',)
    list_select_related = ('empresa', 'cliente')


@admin.register(TransacaoLoyalty)
class TransacaoLoyaltyAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'cliente', 'pontos', 'descricao', 'data')
    list_filter = ('empresa', 'data')
    search_fields = ('cliente__nome', 'descricao')
    ordering = ('-data',)
    readonly_fields = ('data',)
    list_select_related = ('empresa', 'cliente')


@admin.register(EntradaListaEspera)
class EntradaListaEsperaAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'cliente', 'barbeiro', 'data', 'status', 'data_entrada')
    list_filter = ('empresa', 'status', 'barbeiro', 'data')
    search_fields = ('cliente__nome',)
    ordering = ('data', 'data_entrada')
    list_select_related = ('empresa', 'cliente', 'barbeiro')


@admin.register(MetaBarbeiro)
class MetaBarbeiroAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'barbeiro', 'mes', 'ano', 'meta_cortes', 'meta_faturamento')
    list_filter = ('empresa', 'ano', 'mes', 'barbeiro')
    ordering = ('-ano', '-mes', 'barbeiro__nome')
    list_select_related = ('empresa', 'barbeiro')


@admin.register(CupomDesconto)
class CupomDescontoAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'codigo', 'tipo', 'valor_desconto', 'validade', 'usos_realizados', 'usos_maximo', 'ativo', 'esta_valido')
    list_filter = ('empresa', 'tipo', 'ativo')
    search_fields = ('codigo', 'descricao')

    @admin.display(boolean=True, description='Válido?')
    def esta_valido(self, obj):
        return obj.esta_valido


class SomenteLeituraAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser and obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AceiteTermos)
class AceiteTermosAdmin(SomenteLeituraAdmin):
    list_display = (
        'empresa', 'usuario', 'aceite_em_nome_da_empresa', 'versao_termos',
        'versao_privacidade', 'aceito_em', 'ip',
    )
    list_filter = (
        'empresa', 'aceite_em_nome_da_empresa', 'versao_termos',
        'versao_privacidade', 'aceito_em',
    )
    search_fields = ('usuario__username', 'usuario__email', 'ip')
    readonly_fields = (
        'empresa', 'usuario', 'aceite_em_nome_da_empresa', 'versao_termos',
        'versao_privacidade', 'aceito_em', 'ip', 'user_agent',
    )
    list_select_related = ('empresa', 'usuario')


@admin.register(UserSession)
class UserSessionAdmin(SomenteLeituraAdmin):
    list_display = ('usuario', 'ip', 'criada_em', 'ultimo_uso')
    list_filter = ('criada_em', 'ultimo_uso')
    search_fields = ('usuario__username', 'ip', 'user_agent')
    readonly_fields = ('usuario', 'session_key', 'ip', 'user_agent', 'criada_em', 'ultimo_uso')

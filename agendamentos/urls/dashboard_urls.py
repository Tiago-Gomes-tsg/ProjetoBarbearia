from django.urls import path
from django.contrib.auth import views as auth_views
from agendamentos.views import auth_views as tenant_auth_views, dashboard_views, financeiro_views

urlpatterns = [
    path('', dashboard_views.painel_home, name='painel_home'),
    path('api/insights-gemini/', dashboard_views.api_insights_gemini, name='api_insights_gemini'),
    path('login/', tenant_auth_views.TenantLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('termos/', tenant_auth_views.aceitar_termos, name='aceitar_termos'),
    path('minha-senha/', tenant_auth_views.alterar_minha_senha, name='alterar_minha_senha'),
    path('alterar-status/<int:agendamento_id>/<str:novo_status>/', dashboard_views.alterar_status, name='alterar_status'),
    
    path('servicos/', dashboard_views.lista_servicos, name='lista_servicos'),
    path('servicos/adicionar/', dashboard_views.adicionar_servico, name='adicionar_servico'),
    path('servicos/deletar/<int:corte_id>/', dashboard_views.deletar_servico, name='deletar_servico'),
    
    path('equipe/', dashboard_views.lista_equipe, name='lista_equipe'),
    path('equipe/adicionar/', dashboard_views.adicionar_membro, name='adicionar_membro'),
    path('equipe/editar/<int:barbeiro_id>/', dashboard_views.editar_membro, name='editar_membro'),
    path('equipe/alternar-status/<int:barbeiro_id>/', dashboard_views.alternar_status_funcionario, name='alternar_status_funcionario'),

    path('historico/', dashboard_views.historico_agendamentos, name='historico'),
    path('historico/mudar-status/<int:agendamento_id>/', dashboard_views.mudar_status_historico, name='mudar_status_historico'),
    path('cliente/<int:cliente_id>/', dashboard_views.perfil_cliente, name='perfil_cliente'),
    
    path('financeiro/', financeiro_views.gestao_financeira, name='gestao_financeira'),
    path('financeiro/pagar-comissao/<int:comissao_id>/', financeiro_views.pagar_comissao, name='pagar_comissao'),
    path('financeiro/pagar-salario/<int:salario_id>/', financeiro_views.pagar_salario, name='pagar_salario'),
    path('estoque/', financeiro_views.gestao_estoque, name='gestao_estoque'),

    path('estoque/restocar/<int:item_id>/', financeiro_views.restocar_item, name='restocar_item'),
    path('estoque/baixar/<int:item_id>/', financeiro_views.baixar_item, name='baixar_item'),

    path('estoque/excluir/<int:item_id>/', financeiro_views.excluir_item, name='excluir_item'),

    path('configuracoes/', dashboard_views.configuracoes_barbearia, name='configuracoes_barbearia'),

    path('planos/', dashboard_views.lista_planos, name='lista_planos'),

    # Avaliacoes feitas pelos clientes apos atendimentos concluidos.
    path('avaliacoes/', dashboard_views.lista_avaliacoes, name='lista_avaliacoes'),

    # Programa de fidelidade, pontos e resgates.
    path('fidelidade/', dashboard_views.configurar_fidelidade, name='configurar_fidelidade'),

    # Bloqueios que retiram datas da agenda publica.
    path('bloqueios/', dashboard_views.lista_bloqueios, name='lista_bloqueios'),
    path('api/bloqueios/<int:barbeiro_id>/', dashboard_views.api_bloqueios_barbeiro, name='api_bloqueios'),

    # Lista de espera acompanhada pelo painel.
    path('lista-espera/', dashboard_views.lista_espera_painel, name='lista_espera_painel'),

    # Metas mensais por barbeiro.
    path('metas/', dashboard_views.gerenciar_metas, name='metas'),

    # Cupons aplicados no agendamento publico.
    path('cupons/', dashboard_views.gerenciar_cupons, name='gerenciar_cupons'),
    path('api/validar-cupom/', dashboard_views.validar_cupom_ajax, name='validar_cupom'),

    # Relatorios exportados em CSV para planilhas.
    path('exportar/desempenho/', dashboard_views.exportar_desempenho_excel, name='exportar_desempenho'),
    path('exportar/historico/', dashboard_views.exportar_historico_csv, name='exportar_historico'),
    path('exportar/financeiro/', dashboard_views.exportar_financeiro_csv, name='exportar_financeiro'),
]

from django.urls import path
from agendamentos.views import cliente_views, dashboard_views

urlpatterns = [
    # Fluxo publico de agendamento e cancelamento feito pelo cliente.
    path('', cliente_views.index, name='index'),
    path('cancelar/<uuid:token>/', cliente_views.cancelar_por_token, name='cancelar_por_token'),

    # Recursos publicos chamados por link direto ou AJAX, sem login no painel.
    path('avaliar/<str:token>/', dashboard_views.avaliar_atendimento, name='avaliar_atendimento'),
    path('lista-espera/entrar/', cliente_views.entrar_lista_espera_publico, name='entrar_lista_espera'),
]

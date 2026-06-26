from django.urls import path

from agendamentos.views import platform_views


urlpatterns = [
    path('empresas/', platform_views.empresas_plataforma, name='empresas_plataforma'),
    path('empresas/importar/', platform_views.importar_empresa, name='importar_empresa'),
    path('empresas/<uuid:empresa_id>/atualizar/', platform_views.atualizar_empresa, name='atualizar_empresa'),
    path('empresas/<uuid:empresa_id>/alternar/', platform_views.alternar_empresa, name='alternar_empresa'),
    path('empresas/<uuid:empresa_id>/exportar/', platform_views.exportar_empresa, name='exportar_empresa'),
    path('empresas/<uuid:empresa_id>/excluir/', platform_views.excluir_empresa, name='excluir_empresa'),
]

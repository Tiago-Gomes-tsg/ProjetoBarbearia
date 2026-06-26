from django.apps import apps
from django.core.checks import Error, Tags, register

from .models import TenantOwnedModel


@register(Tags.models)
def verificar_models_multi_tenant(app_configs, **kwargs):
    """Impede que novos models operacionais nasçam sem isolamento por empresa."""

    globais_permitidos = {'Empresa', 'UserSession'}
    erros = []
    app = apps.get_app_config('agendamentos')
    for model in app.get_models():
        if model.__name__ in globais_permitidos:
            continue
        if not issubclass(model, TenantOwnedModel):
            erros.append(Error(
                f'{model.__name__} não herda TenantOwnedModel.',
                hint='Models de negócio devem possuir empresa e usar TenantManager.',
                obj=model,
                id='agendamentos.E001',
            ))
    return erros

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models

from .tenancy import (
    get_current_empresa,
    is_unscoped_access_allowed,
)


class TenantQuerySet(models.QuerySet):
    """QuerySet que mantém o tenant também nas operações de criação."""

    def for_empresa(self, empresa):
        return self.filter(empresa=empresa)

    def create(self, **kwargs):
        empresa = get_current_empresa()
        if empresa is not None:
            empresa_id = kwargs.get('empresa_id')
            empresa_obj = kwargs.get('empresa')
            if empresa_id not in (None, empresa.pk):
                raise ValidationError('Não é permitido criar dados em outra empresa.')
            if empresa_obj is not None and empresa_obj.pk != empresa.pk:
                raise ValidationError('Não é permitido criar dados em outra empresa.')
            kwargs.setdefault('empresa', empresa)
        return super().create(**kwargs)

    def bulk_create(self, objs, **kwargs):
        empresa = get_current_empresa()
        if empresa is not None:
            for obj in objs:
                if obj.empresa_id not in (None, empresa.pk):
                    raise ValidationError('Não é permitido criar dados em outra empresa.')
                obj.empresa = empresa
        for obj in objs:
            obj.full_clean(validate_unique=False, validate_constraints=False)
        return super().bulk_create(objs, **kwargs)

    def update(self, **kwargs):
        empresa = get_current_empresa()
        if empresa is not None:
            empresa_id = kwargs.get('empresa_id')
            empresa_obj = kwargs.get('empresa')
            if empresa_id not in (None, empresa.pk):
                raise ValidationError('Não é permitido mover dados para outra empresa.')
            if empresa_obj is not None and getattr(empresa_obj, 'pk', empresa_obj) != empresa.pk:
                raise ValidationError('Não é permitido mover dados para outra empresa.')

            for nome in getattr(self.model, 'tenant_related_fields', ()):
                chave = f'{nome}_id'
                if chave not in kwargs or kwargs[chave] is None:
                    continue
                campo = self.model._meta.get_field(nome)
                existe = campo.remote_field.model.all_objects.filter(
                    pk=kwargs[chave],
                    empresa=empresa,
                ).exists()
                if not existe:
                    raise ValidationError(f'O relacionamento "{nome}" pertence a outra empresa.')
        for nome, valor in kwargs.items():
            if hasattr(valor, 'resolve_expression'):
                continue
            try:
                campo = self.model._meta.get_field(nome.removesuffix('_id'))
            except FieldDoesNotExist:
                continue
            if not campo.is_relation:
                campo.clean(valor, None)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        protegidos = {'empresa', 'empresa_id'} | {
            nome
            for campo in getattr(self.model, 'tenant_related_fields', ())
            for nome in (campo, f'{campo}_id')
        }
        if set(fields) & protegidos:
            raise ValidationError('Use save() para alterar campos relacionados ao tenant.')
        for obj in objs:
            obj.full_clean(validate_unique=False, validate_constraints=False)
        return super().bulk_update(objs, fields, **kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Manager padrão: escopo automático e comportamento fail-closed."""

    def get_queryset(self):
        queryset = super().get_queryset()
        empresa = get_current_empresa()
        if empresa is not None:
            return queryset.filter(empresa=empresa)
        if is_unscoped_access_allowed():
            return queryset
        return queryset.none()

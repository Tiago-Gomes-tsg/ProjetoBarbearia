"""Backup JSON portavel e restauracao estrita de um unico tenant."""

import base64
import hashlib
import json
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.models import User
from django.core import serializers
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction

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
    MembroEmpresa,
    MetaBarbeiro,
    PlanoMensal,
    SaldoLoyalty,
    TransacaoFinanceira,
    TransacaoLoyalty,
)
from .observability import log_event
from .tenancy import empresa_context


SCHEMA_VERSION = 1
USER_FIELDS = (
    'password',
    'last_login',
    'username',
    'first_name',
    'last_name',
    'email',
    'is_active',
    'date_joined',
)

# Ordem topologica das FKs para restaurar com validacao progressiva.
BACKUP_MODELS = (
    MembroEmpresa,
    Barbeiro,
    Cliente,
    Corte,
    ItemEstoque,
    PlanoMensal,
    ConfiguracaoBarbearia,
    ConfigLoyalty,
    CupomDesconto,
    Agendamento,
    LancamentoComissao,
    TransacaoFinanceira,
    AssinaturaCliente,
    BloqueioAgenda,
    AvaliacaoAtendimento,
    SaldoLoyalty,
    TransacaoLoyalty,
    EntradaListaEspera,
    MetaBarbeiro,
    AceiteTermos,
)
MODEL_BY_LABEL = {model._meta.label_lower: model for model in BACKUP_MODELS}


def _serialized(objects, fields=None):
    return json.loads(serializers.serialize('json', objects, fields=fields))


def arquivos_do_tenant(empresa):
    """Retorna referencias unicas para limpeza definitiva apos apagar o banco."""

    items = {}
    for model in BACKUP_MODELS:
        file_fields = [
            field for field in model._meta.fields
            if field.get_internal_type() == 'FileField'
        ]
        if not file_fields:
            continue
        for obj in model.all_objects.filter(empresa=empresa):
            for field in file_fields:
                field_file = getattr(obj, field.name)
                if field_file and field_file.name:
                    items.setdefault(field_file.name, field.storage)
    return [(storage, name) for name, storage in items.items()]


def excluir_arquivos_do_tenant(items, tenant_id):
    failures = 0
    for storage, name in items:
        try:
            if storage.exists(name):
                storage.delete(name)
        except Exception as exc:
            failures += 1
            log_event(
                'tenant_file_delete_failed',
                level='error',
                exc_info=(type(exc), exc, exc.__traceback__),
                tenant_id=tenant_id,
            )
    return failures


def _read_file(field_file, total_bytes):
    digest = hashlib.sha256()
    chunks = []
    size = 0
    with field_file.storage.open(field_file.name, 'rb') as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            total_bytes += len(chunk)
            if total_bytes > settings.TENANT_BACKUP_MAX_BYTES:
                raise ValidationError(
                    'Os arquivos do tenant ultrapassam o limite configurado para backup JSON.'
                )
            digest.update(chunk)
            chunks.append(chunk)
    raw = b''.join(chunks)
    return {
        'name': field_file.name,
        'size': size,
        'sha256': digest.hexdigest(),
        'content_base64': base64.b64encode(raw).decode('ascii'),
    }, total_bytes


def exportar_tenant(empresa):
    user_ids = list(
        MembroEmpresa.all_objects.filter(empresa=empresa)
        .values_list('usuario_id', flat=True)
    )
    users = _serialized(
        User.objects.filter(pk__in=user_ids).order_by('pk'),
        fields=USER_FIELDS,
    )
    records = []
    file_objects = {}
    total_records = 0

    for model in BACKUP_MODELS:
        queryset = model.all_objects.filter(empresa=empresa).order_by('pk')
        count = queryset.count()
        total_records += count
        if total_records > settings.TENANT_BACKUP_MAX_RECORDS:
            raise ValidationError('O tenant ultrapassa o limite de registros do backup JSON.')
        objects = list(queryset)
        records.extend(_serialized(objects))
        file_fields = [field for field in model._meta.fields if field.get_internal_type() == 'FileField']
        for obj in objects:
            for field in file_fields:
                field_file = getattr(obj, field.name)
                if field_file and field_file.name:
                    file_objects.setdefault(field_file.name, field_file)

    files = []
    missing_files = []
    total_file_bytes = 0
    for name, field_file in sorted(file_objects.items()):
        try:
            item, total_file_bytes = _read_file(field_file, total_file_bytes)
            files.append(item)
        except FileNotFoundError:
            missing_files.append(name)

    company = _serialized(Empresa.objects.filter(pk=empresa.pk))[0]
    payload = {
        'schema': 'projeto-barbearia-tenant-backup',
        'schema_version': SCHEMA_VERSION,
        'exported_at': datetime.now(dt_timezone.utc).isoformat(),
        'contains_sensitive_data': True,
        'empresa': company,
        'users': users,
        'records': records,
        'files': files,
        'missing_files': missing_files,
        'statistics': {
            'users': len(users),
            'records': len(records),
            'files': len(files),
            'file_bytes': total_file_bytes,
        },
    }
    log_event(
        'tenant_backup_exported',
        tenant_id=empresa.pk,
        record_count=len(records),
        file_count=len(files),
        missing_file_count=len(missing_files),
    )
    return json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')


def _load_payload(upload):
    if upload.size > settings.TENANT_BACKUP_UPLOAD_MAX_BYTES:
        raise ValidationError('O arquivo JSON ultrapassa o limite permitido para importacao.')
    raw = b''.join(upload.chunks())
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError('Arquivo de backup JSON invalido.') from exc
    if not isinstance(payload, dict):
        raise ValidationError('Estrutura de backup invalida.')
    if payload.get('schema') != 'projeto-barbearia-tenant-backup':
        raise ValidationError('Este JSON nao e um backup de tenant reconhecido.')
    if payload.get('schema_version') != SCHEMA_VERSION:
        raise ValidationError('Versao do backup nao suportada.')
    return payload


def _validate_payload(payload):
    company = payload.get('empresa')
    users = payload.get('users', [])
    records = payload.get('records', [])
    files = payload.get('files', [])
    if not isinstance(company, dict) or company.get('model') != Empresa._meta.label_lower:
        raise ValidationError('Registro da empresa ausente ou invalido.')
    if not all(isinstance(group, list) for group in (users, records, files)):
        raise ValidationError('Colecoes do backup invalidas.')
    if len(records) > settings.TENANT_BACKUP_MAX_RECORDS:
        raise ValidationError('Quantidade de registros acima do limite permitido.')

    company_pk = Empresa._meta.pk.to_python(company.get('pk'))
    company_fields = company.get('fields') or {}
    candidate = Empresa(id=company_pk, **company_fields)
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    if Empresa.objects.filter(pk=company_pk).exists():
        raise ValidationError('A empresa deste backup ja existe pelo ID.')
    if Empresa.objects.filter(slug=candidate.slug).exists():
        raise ValidationError('Ja existe uma empresa com o slug deste backup.')

    user_ids = set()
    usernames = set()
    for record in users:
        if not isinstance(record, dict) or record.get('model') != User._meta.label_lower:
            raise ValidationError('Backup contem um usuario nao permitido.')
        user_pk = User._meta.pk.to_python(record.get('pk'))
        fields = record.get('fields') or {}
        if set(fields) - set(USER_FIELDS):
            raise ValidationError('Backup contem campos de usuario nao permitidos.')
        username = fields.get('username', '')
        if user_pk in user_ids or username in usernames:
            raise ValidationError('Backup contem usuarios duplicados.')
        if User.objects.filter(pk=user_pk).exists() or User.objects.filter(username__iexact=username).exists():
            raise ValidationError(f'O usuario "{username}" ja existe na plataforma.')
        user_ids.add(user_pk)
        usernames.add(username)

    record_pks = {}
    referenced_files = set()
    for record in records:
        if not isinstance(record, dict) or record.get('model') not in MODEL_BY_LABEL:
            raise ValidationError('Backup contem um modelo nao permitido.')
        model = MODEL_BY_LABEL[record['model']]
        fields = record.get('fields') or {}
        if str(fields.get('empresa')) != str(company_pk):
            raise ValidationError('Backup contem registro associado a outra empresa.')
        pk = model._meta.pk.to_python(record.get('pk'))
        if model.all_objects.filter(pk=pk).exists():
            raise ValidationError(f'Conflito de ID ao restaurar {record["model"]}.')
        model_keys = record_pks.setdefault(record['model'], set())
        if pk in model_keys:
            raise ValidationError(f'ID duplicado no modelo {record["model"]}.')
        model_keys.add(pk)
        for field in model._meta.fields:
            if field.get_internal_type() == 'FileField' and fields.get(field.name):
                referenced_files.add(fields[field.name])
        if model in {MembroEmpresa, Barbeiro, AceiteTermos}:
            if fields.get('usuario') not in user_ids:
                raise ValidationError('Registro referencia usuario fora do backup.')

    total_file_bytes = 0
    file_items = {}
    prefix = f'tenants/{company_pk}/'
    for item in files:
        if not isinstance(item, dict):
            raise ValidationError('Entrada de arquivo invalida.')
        name = item.get('name', '')
        if name not in referenced_files or not name.startswith(prefix) or name in file_items:
            raise ValidationError('Backup contem arquivo fora do tenant ou nao referenciado.')
        try:
            content = base64.b64decode(item.get('content_base64', ''), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValidationError('Conteudo base64 invalido no backup.') from exc
        total_file_bytes += len(content)
        if total_file_bytes > settings.TENANT_BACKUP_MAX_BYTES:
            raise ValidationError('Arquivos restaurados ultrapassam o limite permitido.')
        if len(content) != item.get('size') or hashlib.sha256(content).hexdigest() != item.get('sha256'):
            raise ValidationError('Checksum de arquivo invalido no backup.')
        file_items[name] = content

    return candidate, users, records, file_items


def _restore_files(file_items, records):
    storage_by_name = {}
    for record in records:
        model = MODEL_BY_LABEL[record['model']]
        fields = record.get('fields') or {}
        for field in model._meta.fields:
            if field.get_internal_type() == 'FileField' and fields.get(field.name):
                storage_by_name[fields[field.name]] = field.storage

    for name, content in file_items.items():
        storage = storage_by_name[name]
        if storage.exists(name):
            with storage.open(name, 'rb') as existing:
                if hashlib.sha256(existing.read()).digest() == hashlib.sha256(content).digest():
                    continue
            storage.delete(name)
        saved_name = storage.save(name, ContentFile(content))
        if saved_name != name:
            raise ValidationError('O storage alterou o caminho de um arquivo restaurado.')


def importar_tenant(upload):
    payload = _load_payload(upload)
    candidate, users, records, file_items = _validate_payload(payload)

    with transaction.atomic():
        company_fixture = json.dumps([payload['empresa']])
        company_deserialized = next(serializers.deserialize('json', company_fixture))
        company_deserialized.save()
        empresa = Empresa.objects.get(pk=candidate.pk)

        for deserialized in serializers.deserialize('json', json.dumps(users)):
            deserialized.object.full_clean(validate_unique=False, validate_constraints=False)
            deserialized.save()

        with empresa_context(empresa):
            for deserialized in serializers.deserialize('json', json.dumps(records)):
                obj = deserialized.object
                obj.full_clean(validate_unique=False, validate_constraints=False)
                for relation_name in getattr(obj, 'tenant_related_fields', ()):
                    related = getattr(obj, relation_name, None)
                    if related is not None and related.empresa_id != empresa.pk:
                        raise ValidationError('Backup contem relacionamento cruzado entre tenants.')
                deserialized.save()
        # Uma falha de storage tambem reverte a restauracao do banco. Arquivos ja
        # gravados podem ser reutilizados com seguranca em uma nova tentativa pelo checksum.
        _restore_files(file_items, records)
    log_event(
        'tenant_backup_imported',
        tenant_id=empresa.pk,
        record_count=len(records),
        file_count=len(file_items),
    )
    return empresa

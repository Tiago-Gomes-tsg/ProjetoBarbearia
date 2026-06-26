# Arquitetura multi-tenant

O sistema usa banco compartilhado com isolamento por linha. Cada registro de
negócio possui `empresa_id`, e o tenant atual é resolvido antes da view.

## Fluxo de uma requisição

1. `TenantMiddleware` extrai o slug de `slug.TENANT_BASE_DOMAIN`.
2. Para usuários autenticados, o middleware confirma que `MembroEmpresa`
   pertence à empresa do host.
3. `empresa_context()` grava a empresa em um `ContextVar`, seguro para execução
   síncrona e assíncrona.
4. `TenantManager` acrescenta o filtro por empresa em todas as consultas dos
   modelos de negócio.
5. Sem tenant, o manager retorna um queryset vazio. Consultas globais só são
   possíveis por `all_objects` ou `unscoped_context()` em rotinas de plataforma.

O middleware não substitui a validação de relacionamentos: `TenantOwnedModel`
também recusa uma gravação que tente relacionar objetos de empresas diferentes.

## RBAC

`MembroEmpresa` é a fonte de autorização:

- `OWNER`: administração da própria empresa;
- `MANAGER`: visão gerencial da própria empresa;
- `BARBER`: agenda, metas e avaliações do próprio profissional;
- `is_superuser`: administração da plataforma pelo Django Admin.

Os campos legados `Barbeiro.is_dono` e `Barbeiro.is_gerente` permanecem apenas
para compatibilidade da tela atual. Um signal os sincroniza com `MembroEmpresa`,
mas as verificações de permissão usam o membership.

## Criar uma nova barbearia

### Portal recomendado

1. Entre em `/plataforma/empresas/` com um superuser.
2. Preencha empresa, slug, fuso, cor, proprietário, agenda e serviço inicial.
3. Confirme a operação com a senha atual do superuser.
4. O portal cria tudo atomicamente: `Empresa`, `User`, membership OWNER,
   `Barbeiro`, `ConfiguracaoBarbearia`, `ConfigLoyalty` e `Corte`.
5. O proprietário entra em `https://slug.seudominio.com/painel/login/`, aceita
   pessoalmente os termos e conclui logo, favicon, capa, equipe e serviços.

### Django Admin manual

1. Entre em `/admin/` com um superuser e cadastre `Empresa`.
2. O signal cria automaticamente `ConfiguracaoBarbearia` e `ConfigLoyalty`.
3. Em Autenticação, cadastre o `User` sem marcar superuser ou staff.
4. Cadastre `MembroEmpresa` com a mesma empresa, usuário e papel OWNER.
5. Cadastre `Barbeiro` com a mesma empresa/usuário e marque `is_dono` apenas
   para compatibilidade da interface atual.
6. Cadastre pelo menos um `Corte` vinculado a esse profissional.
7. Nunca misture empresas nas FKs; o model rejeitará a gravação cruzada.

Slugs como `www`, `admin`, `app`, `api` e `plataforma` são reservados. A lista pode ser ampliada em
`TENANT_RESERVED_SUBDOMAINS`.

## Desenvolvimento local

Com uma única empresa, `TENANT_ALLOW_SINGLE_FALLBACK=True` permite continuar
usando `http://localhost:8000/`. Para testar subdomínios, use hosts como
`empresa-a.localhost:8000`, suportados pelos navegadores modernos.

Com duas ou mais empresas e fallback desativado, a raiz sem subdomínio não
expõe dados de nenhuma delas.

## Render e Supabase

Variáveis mínimas:

- `SECRET_KEY`;
- `DEBUG=False`;
- `DATABASE_URL`: URL PostgreSQL/Pooler fornecida pelo Supabase;
- `ALLOWED_HOSTS=.seudominio.com,seu-servico.onrender.com`;
- `CSRF_TRUSTED_ORIGINS=https://*.seudominio.com,https://seu-servico.onrender.com`;
- `TENANT_BASE_DOMAIN=seudominio.com`;
- `TENANT_ALLOW_SINGLE_FALLBACK=False`.

Para um processo web persistente, prefira o pooler em modo session quando essa
for a opção disponível. Se for necessário usar transaction mode, defina
`DB_DISABLE_PREPARED_STATEMENTS=True`.

O DNS deve apontar o domínio curinga `*.seudominio.com` para o serviço Render.
O arquivo `render.yaml` usa `/health/`, que verifica também a conexão com o banco.

O primeiro deploy usa HSTS por uma hora e não ativa preload. Depois de confirmar
que o domínio principal e todos os subdomínios respondem somente por HTTPS,
aumente `SECURE_HSTS_SECONDS` para `31536000` e avalie definir
`SECURE_HSTS_PRELOAD=True`. Essa ativação gradual evita tornar um erro de DNS ou
certificado persistente no navegador.

### Mídia

O filesystem do serviço web não é usado para mídia em produção. Configure um
bucket público exclusivo para logos, capas, avisos e fotos de profissionais:

- `AWS_ACCESS_KEY_ID`;
- `AWS_SECRET_ACCESS_KEY`;
- `AWS_STORAGE_BUCKET_NAME`;
- `AWS_S3_ENDPOINT_URL`;
- `AWS_S3_REGION_NAME`.

Os arquivos são gravados em `media/tenants/<uuid-da-empresa>/...`, com nomes
aleatórios. Documentos privados futuros devem usar outro bucket, privado, com
URLs assinadas.

## Migrações

As migrations foram separadas para evitar um tenant padrão permanente:

1. `0004`: cria Empresa/MembroEmpresa, adiciona FKs anuláveis e migra a base
   single-tenant;
2. `0005`: valida o backfill, torna as FKs obrigatórias e cria constraints e
   índices compostos;
3. `0006`: cria tokens de cancelamento únicos para agendamentos antigos;
4. `0007`: normaliza telefones e aplica unicidade por empresa;
5. `0008`: leva a validação hexadecimal para todos os campos de tema.

Antes do primeiro deploy, faça backup e execute em staging:

```text
python manage.py check --deploy
python manage.py migrate
python manage.py collectstatic --no-input
python manage.py test
```

## PostgreSQL RLS

O isolamento principal está na aplicação e é coberto por testes com duas
empresas. RLS pode ser acrescentada como defesa adicional, mas apenas com uma
role PostgreSQL sem `BYPASSRLS`, políticas em todas as tabelas e tenant definido
por transação. Conectar o Django como proprietário das tabelas e apenas “ativar
RLS” no Supabase não cria uma barreira efetiva.

## Regras para código novo

- Todo model de negócio deve herdar de `TenantOwnedModel`.
- Nunca receba `empresa_id` do navegador para decidir o tenant.
- Use `request.empresa` em serviços e caches.
- Objetos por ID devem usar o manager padrão dentro da requisição.
- `all_objects` é exclusivo de migrations, signals controlados e plataforma.
- Chaves de cache e rate limit devem conter o UUID da empresa.
- Exports e tarefas agendadas precisam executar dentro de `empresa_context()`.

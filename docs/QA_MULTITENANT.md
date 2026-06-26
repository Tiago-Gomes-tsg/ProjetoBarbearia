# QA multi-tenant com duas barbearias

Esta checklist valida manualmente as mesmas fronteiras cobertas pela suíte
automatizada. Use dados claramente identificados com `QA A` e `QA B` para que
qualquer vazamento fique visível.

## Preparação

- [ ] Faça backup do banco usado no teste.
- [ ] Execute `python manage.py migrate`.
- [ ] Execute `python manage.py createsuperuser` caso ainda não exista um.
- [ ] Inicie o servidor e entre em `/plataforma/empresas/` com o superuser.
- [ ] Confirme que um usuário comum recebe 404 em `/admin/` e `/plataforma/empresas/`.

## Criar os dois tenants

No portal da plataforma, crie:

- [ ] `QA Barbearia A`, slug `qa-barbearia-a`, cor `#b7791f`, dono `owner-qa-a`.
- [ ] `QA Barbearia B`, slug `qa-barbearia-b`, cor `#2563eb`, dono `owner-qa-b`.
- [ ] Use e-mails, senhas e nomes de serviços diferentes.
- [ ] Confirme a operação com a senha atual do superuser.

Em desenvolvimento, abra:

- `http://qa-barbearia-a.localhost:8000/`
- `http://qa-barbearia-b.localhost:8000/`

## Identidade e acesso

- [ ] Cada página pública mostra somente nome, cor, logo, favicon, capa e aviso do próprio tenant.
- [ ] O owner A não autentica no host B; o resultado deve permanecer na tela de login.
- [ ] No primeiro acesso, cada owner é obrigado a aceitar termos e privacidade.
- [ ] Após aumentar `TERMS_VERSION`, o sistema exige um novo aceite.
- [ ] Dono, gerente e barbeiro veem apenas menus compatíveis com seus papéis.
- [ ] IDs da empresa B usados em URLs da empresa A retornam 404.

## Cadastros e operação

Cadastre itens com sufixos A/B e verifique ausência no tenant oposto:

- [ ] Funcionários, memberships, donos e gerentes.
- [ ] Serviços/cortes e preços.
- [ ] Clientes, inclusive o mesmo telefone nas duas empresas.
- [ ] Agendamentos no mesmo dia/horário em empresas distintas.
- [ ] Bloqueios de agenda e lista de espera.
- [ ] Itens, reposição e baixa de estoque.
- [ ] Entradas, saídas, comissões e totais financeiros.
- [ ] Planos mensais e assinaturas.
- [ ] Cupons com o mesmo código e valores diferentes.
- [ ] Metas, avaliações e filtros por barbeiro.
- [ ] Configuração, saldo e transações de fidelidade.

## Relatórios, gráficos e APIs

- [ ] Dashboard A não incorpora agendamentos ou valores de B em cards e gráficos.
- [ ] Dashboard B não incorpora dados de A.
- [ ] CSV de histórico e financeiro A não contém nomes ou descrições B.
- [ ] Insight Gemini usa somente agregados da empresa atual.
- [ ] Validação de cupom no host A retorna o valor A, mesmo com código igual em B.
- [ ] APIs autenticadas retornam 302/403 sem login ou papel suficiente.
- [ ] A API Gemini retorna 429 após o limite configurado por usuário.

## Cancelamento e avaliações públicas

- [ ] Token de cancelamento A funciona no host A.
- [ ] O mesmo token no host B retorna 404.
- [ ] Cancelar A não altera o agendamento B.
- [ ] Token de avaliação A não abre dados no host B.
- [ ] Uma avaliação já respondida não pode ser alterada novamente.

## Uploads e segurança de entrada

- [ ] JPG, PNG e WEBP válidos são aceitos dentro dos limites.
- [ ] Um arquivo texto renomeado para `.png` é recusado.
- [ ] Uma imagem truncada ou excessivamente grande é recusada.
- [ ] Arquivos salvos aparecem em `tenants/<uuid-da-empresa>/...`.
- [ ] Imagens de branding são reencodadas em WEBP e não preservam EXIF.
- [ ] Nome de cliente acima do limite e telefone inválido são recusados.
- [ ] Requisições POST sem CSRF no painel são recusadas.

## Rate limit

- [ ] Repetir login inválido até o limite retorna HTTP 429.
- [ ] Agendamento, lista de espera, cupom, avaliação e cancelamento limitam abuso por IP/identificador.
- [ ] Em produção com mais de um worker, configure `REDIS_URL`; cache local limita por processo, não globalmente.

## Automação equivalente

Execute:

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py collectstatic --no-input
```

Os testes `MultiTenantIsolationTests` e `PlatformOnboardingTests` criam duas
empresas, tentam acessos cruzados e percorrem todas as entidades tenant-owned.

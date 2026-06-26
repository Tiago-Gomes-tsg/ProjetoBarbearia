# Segurança e LGPD

## Controles implementados

- Tenant resolvido no servidor; `empresa_id` nunca é aceito do navegador como autoridade.
- Querysets de negócio falham fechados e filtram automaticamente por empresa.
- Relacionamentos entre empresas diferentes são recusados também na gravação.
- Admin e onboarding de tenants são exclusivos de superusers.
- RBAC centralizado em `MembroEmpresa` com OWNER, MANAGER e BARBER.
- Aceite versionado de termos e privacidade antes do acesso ao painel.
- Cancelamento e avaliação usam tokens aleatórios e permanecem vinculados ao host do tenant.
- POSTs internos usam CSRF; cookies podem ser `Secure`, `HttpOnly` e `SameSite=Lax`.
- ORM do Django é usado em todas as consultas com entrada do usuário, evitando concatenação SQL.
- CSVs neutralizam células que poderiam executar fórmulas em planilhas.
- Uploads validam tamanho, MIME, assinatura, decodificação e dimensões; branding é reencodado sem EXIF.
- Chaves de APIs, banco e storage ficam em variáveis de ambiente ignoradas pelo Git.

## Rate limit e APIs

Login é limitado por IP e username. Agendamento, cancelamento, lista de espera,
cupom e avaliação possuem limites próprios; Gemini é limitado por usuário para
controlar custo. As chaves incluem o tenant sempre que há um tenant resolvido.

`LocMemCache` é suficiente apenas para desenvolvimento ou um único processo.
Com múltiplos workers/instâncias, configure `REDIS_URL` para que todos compartilhem
os mesmos contadores. Monitore respostas 401, 403, 404 e 429 sem registrar senhas,
tokens completos, telefones ou payloads pessoais.

## Responsabilidades LGPD antes da produção

- Definir formalmente controlador, operador, encarregado e canal do titular.
- Publicar política de privacidade para clientes, não apenas termos internos.
- Documentar bases legais e finalidades de agenda, cobrança, fidelidade e marketing.
- Coletar somente dados necessários; marketing deve ser separado da execução do serviço.
- Definir prazos de retenção e rotinas de anonimização/exclusão para clientes inativos.
- Implementar processo autenticado para acesso, correção, portabilidade e exclusão.
- Manter inventário de subprocessadores: Render, Supabase, e-mail, WhatsApp e Gemini.
- Não enviar nomes, telefones, avaliações abertas ou outros dados pessoais ao Gemini;
  use apenas métricas agregadas, como o código atual faz.
- Criptografar backups, restringir acesso e testar restauração periodicamente.
- Preparar resposta a incidentes, revogação de sessões e comunicação de violação.
- Revisar juridicamente termos, política e contratos antes do lançamento comercial.

## Operação segura

- Use MFA para superusers e contas de infraestrutura quando o provedor oferecer.
- Não use a conta superuser na operação diária de uma barbearia.
- Troque credenciais imediatamente se `.env`, dumps ou logs forem expostos.
- Mantenha dependências atualizadas e rode `manage.py check --deploy` em releases.
- Suba HSTS gradualmente; preload só após validar HTTPS em todos os subdomínios.
- Use bucket público somente para branding. Documentos privados exigem bucket privado e URLs assinadas.
- PostgreSQL RLS pode ser adicionado como segunda barreira, usando uma role sem `BYPASSRLS`.

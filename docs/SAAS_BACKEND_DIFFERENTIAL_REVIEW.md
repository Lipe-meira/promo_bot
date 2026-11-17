# Revisão diferencial de segurança — preparação do backend (17/11/2026)

Escopo: `2803af7..HEAD` na branch `codex/saas-backend-render-neon-prep`, somente `apps/api`
(`src/`, `migrations/`), `render.yaml` e o workflow de CI. Estratégia: **SMALL/DEEP** (11 arquivos
de código, 468 inserções, 35 remoções), classificada por **risco** (autenticação/admissão e TLS = ALTO),
não por tamanho. Revisão feita pelo próprio autor das alterações: **não substitui a auditoria
independente** exigida antes de qualquer provisionamento.

## Baseline e raio de impacto

- Código removido relevante para segurança: nenhum. A CLI perdeu o argumento posicional de e-mail
  (`e3743f4`, commit de entrega original); não há removidas de commits de correção de segurança.
- `resolve_user` tem **1 chamador** (`app.py::current_user`), que protege **todas** as rotas `/v1`;
  `create_engine`, 1 chamador; `prepare_database` roda na subida (validação das configurações) e nos
  engines. Nenhum módulo do bot é tocado; nenhuma dependência nova (`uv lock --check` limpo).
- Cobertura de testes por ramo novo: mapeada abaixo; **percentual de cobertura não medido** (não há
  `coverage` configurado no projeto).

## Achados

| ID | Severidade | Estado | Descrição |
| --- | --- | --- | --- |
| F-1 | Média | **corrigido** | `ApiSettings` ecoava o valor rejeitado (`input_value=…`, primeiros e últimos ~25 caracteres) nos erros de validação de subida: uma chave secreta ou uma URL curta com senha apareciam em logs. Já existia para `database_url`; a nova chave secreta ampliaria. `hide_input_in_errors=True` ([settings.py:54](../apps/api/src/promo_api/settings.py)). RED: 3 testes. `schemas.py` já usava a opção; as configurações a haviam esquecido. |
| F-2 | Baixa | **corrigido** | Os engines de operador herdavam o `command_timeout` de 30 s da API: uma migration legítima mais longa seria abortada no cliente. O prazo agora é escolha explícita do engine da API ([engine.py:51](../apps/api/src/promo_api/db/engine.py)). RED: 1 teste. |
| F-3 | (teste) | **corrigido** | O teste de recuperação encerrava todos os backends do banco, derrubando conexões de fixtures compartilhadas; só falhava na suíte completa. Agora encerra só o backend do próprio engine. |
| R-1 | **Média** (plano Free) | **mitigado** (auditoria independente, N1) | `/readyz` é público e consultava o PostgreSQL a cada chamada. Quem conhecesse a URL do Render poderia **impedir o Neon de suspender**: 0,25 CU 24 h por dia ≈ 182 CU-h/mês contra 100 CU-h do plano Free (a própria Neon cita 400 h de 0,25 CU). Agora um sucesso é lembrado por 30 min e uma falha por 5 s, com uma única sondagem para chamadas concorrentes: um fluxo contínuo acorda o compute ~14 % do tempo (≈ 26 CU-h/mês). Ver "Auditoria independente". Resta o amplificador autenticado (R-8). |
| R-2 | Baixa | **aberto** | A recusa da consulta ao Clerk não tem limitação: uma identidade convidada, mas com primário não verificado, gera uma chamada à Backend API por requisição; um 429 do Clerk vira 503 e pode atrasar a admissão de outros. Atenuação simples: cache negativo curto por `(sub, e-mail)` e/ou limite de concorrência. Não implementado para manter a correção mínima. |
| R-3 | Informativa | aceito | Os motivos `NO_INVITATION` e `PRIMARY_EMAIL_NOT_VERIFIED` diferem: um chamador autenticado cuja claim traga um e-mail pode inferir se há convite para ele. Só é explorável no cenário da própria lacuna; o ganho para UI/operador compensa. |
| R-4 | Informativa | aceito | Não se compara o `id` da resposta do Clerk ao `sub` pedido. Com TLS, base URL fixa e sem redirecionamento, uma divergência é implausível; a verificação seria defesa em profundidade barata se desejada. |
| R-5 | Média | **aberto** | Os nomes dos campos da resposta de `GET /users/{id}` (`email_addresses[].email_address`, `verification.status`) seguem a documentação dos tipos, sem confirmação literal do JSON. Erro = **falha fechada** (ninguém admitido), não aberta; precisa do primeiro login real. |
| R-6 | Média | **aberto** | Pooler do Neon + asyncpg não validado contra um pooler real (configuração conservadora aplicada). |
| R-7 | Baixa | aceito | Primeiros pedidos concorrentes do mesmo convidado disparam uma consulta ao Clerk cada (limitado pelo pool); após a admissão, nenhuma. |
| R-8 | Média (plano Free) | **aberto** | Um token Clerk válido de quem **não** foi convidado custa 2 consultas por requisição, sem limite de taxa, e também acorda o Neon. Exige uma conta no Clerk; por isso a recomendação para a Fase 5 é o modo **Restricted** de cadastro do Clerk Development (só convidados criam conta), que fecha o amplificador na origem. Limite de taxa na API fica para antes de produção. |

## Auditoria independente (17/11/2026, sessão seguinte)

Feita por um revisor separado, somente leitura, que **não leu** esta revisão antes de concluir, mais
sondas e verificações próprias do segundo agente (nada contra serviço real). Veredito: **nenhum
bloqueador**; sete achados não bloqueantes, todos verificados antes de agir. Os de maior efeito
foram corrigidos na mesma branch, cada um com teste vermelho antes da correção.

| ID | Achado (verificado) | Estado | Commit |
| --- | --- | --- | --- |
| N1 | `/readyz` público consultava o banco em toda chamada (cota de CU-h do Neon e pool). Premissa conferida na documentação da Neon (100 CU-h, suspensão em 5 min) | **corrigido**: cache de 30 min (sucesso) / 5 s (falha) e sondagem única | `8c8599d` |
| N2 | Erros transitórios do servidor (`AdminShutdown`, `CannotConnectNow`, `TooManyConnections`, `ConnectionDoesNotExist`) viram `DBAPIError` genérico (o dialeto traduz todo `PostgresError` para `Error`) e saíam como **500**; falhas de DNS/rede (`OSError` que não é `ConnectionError`) idem. Reproduzido por sonda e lendo o dialeto | **corrigido**: 503 `DATABASE_UNAVAILABLE` para SQLSTATE 08/53/57, conexão invalidada e `OSError`; `ssl.SSLError` e erros de integridade/sintaxe continuam 500 | `ecb1bf5` |
| N3 | Exceção não tratada gravava `[parameters: …]` (e-mails de convidados, ids do Clerk) no log do servidor | **corrigido**: `hide_parameters=True` nos dois engines | `5d28948` |
| N4 | O corpo é lido **antes** da autenticação (medido: POST anônimo de 20 MB lido por inteiro antes do 401); instância Free tem 512 MB | **corrigido**: 413 `PAYLOAD_TOO_LARGE` acima de 16 KiB, sem ler o corpo declarado e parando no limite em transferência em blocos | `c838d7f` |
| N5 | `/openapi.json` público em produção | **corrigido** (desligado junto com `/docs`) | `5d28948` |
| N6 | Quem apaga a conta no Clerk e volta com o mesmo e-mail recebe `INVITATION_ALREADY_USED` (falha fechada, correta); a CLI não religa o usuário | **aceito, documentado**: no piloto, usar um e-mail novo ou SQL manual autorizado; um comando `rebind` fica para antes de produção | — |
| N7 | `confirm=<host>` não confirma o **nome do banco**; `-x database_url=…` deixa a senha em argumento; a CLI de admissão não tinha traceback tratado e não tem guarda de alvo | **parcial**: traceback tratado (só o tipo do erro) em `5d28948`. O restante é **aceito**: o projeto Neon será novo, com um único banco, e o runbook proíbe `-x database_url` | `5d28948` |

Pontos confirmados como corretos pelo revisor e por mim: algoritmo RS256 fixo no cabeçalho e no
`decode`; `kid` só do cache do JWKS; `exp/iat/nbf/iss/sub` obrigatórios; `azp` ausente recusado;
URL do JWKS só da configuração (HTTPS, sem redirecionamento); admissão com trava de linha e
reconferência de revogação; escopo por *membership* em todas as rotas de workspace; TLS verificado
para todo host que não seja loopback, com `require` elevado; parâmetros de URL desconhecidos
recusados; guardas das migrations antes de qualquer conexão; segredo e e-mail fora de logs.

Verificações independentes desta sessão (rodadas do zero, sem reutilizar números anteriores):
`uv lock --check`, `ruff format/check` e `mypy` limpos; `pytest` sem banco 193 → 221 aprovados;
PostgreSQL descartável 70 aprovados; contrato OpenAPI sem deriva; **Semgrep** (`p/python`,
`p/security-audit`, `p/secrets`, métricas desligadas) em 24 arquivos: **0 achados**; guardas da
migration exercitadas com hosts `.invalid` (pooler, sem confirmação, confirmação errada, TLS
desligado, variável vazia): todas recusadas sem vazar a senha; build e start em contêiner
`python:3.12-slim` (Python 3.12.16) com os comandos lidos do `render.yaml`: sem ferramentas de
desenvolvimento nem pacotes do bot, `/healthz` 200, `/readyz` 503 sem banco, `/openapi.json` e
`/docs` 404, POST de 20 KiB sem token 413, log sem segredos. **CodeQL não executado** (não
instalado).

## Cenários adversariais avaliados

Modelos de atacante: remoto sem credencial; usuário Clerk sem convite; convidado com e-mail primário
não verificado (a lacuna); operador local com acesso à lista de processos; dependência/rede hostil.

| Cenário | Resultado | Evidência |
| --- | --- | --- |
| Convite tomado por e-mail primário não verificado (outro endereço verificado) | **bloqueado**, convite preservado | `test_an_unverified_primary_is_not_admitted_…`; removendo a consulta, o teste falha |
| Queda ou resposta estranha do Clerk | **nunca admite** (503) | casos de falha (HTTP 401/403/429/5xx, rede, formato, redirecionamento) em `test_clerk_backend.py`; `test_a_provider_outage_never_admits_…` |
| Injeção no caminho/URL da Backend API; redirecionamento | caminho codificado, URL base fixa, sem redirecionamento | `test_a_subject_cannot_alter_the_request_path`, `test_a_redirect_is_not_followed…` |
| Segredo ou e-mail em log, erro, argumento de processo | ausentes | testes de log/erro do cliente e da CLI; `hide_input_in_errors`; simulação Linux sem senha no log |
| Rebaixar o TLS pela URL (`sslmode=disable`, `ssl=disable`, parâmetro desconhecido) | recusado na subida | `test_database_url.py` |
| Interceptar o TLS (certificado de CA desconhecida) | `SSLCertVerificationError` | handshake real com CA descartável |
| Migrar o banco errado ou por um pooler | recusado antes de conectar | `test_operator_engine.py`, CLI `alembic -x confirm=…` verificada |
| Corrida: 8 primeiros pedidos concorrentes; 6 identidades disputando um convite | um usuário; um vencedor | testes existentes, ainda verdes |
| Convite revogado entre a checagem prévia e a transação | recusado (o bloco com `FOR UPDATE` reconfere) | `identity._admit` mantém a verificação sob trava |
| Esgotar o pool / banco suspenso | 503 com `Retry-After`, recuperação | `test_database_resilience_postgres.py` |

## Limites desta revisão

Não executados: CodeQL e Semgrep (as regras do registro exigem rede e nenhum scanner estava instalado;
o lint de segurança equivalente do Ruff — `S,B,ASYNC,PERF,SIM` — rodou em `src/` com **1 achado, falso
positivo revisado**: a chave sintética usada só para gerar o `openapi.json`); teste de carga; qualquer
serviço real (Render, Neon, Clerk). Confiança: **alta** nos caminhos cobertos por teste e por
mutação; **média** no conjunto, enquanto a integração real não for exercitada.

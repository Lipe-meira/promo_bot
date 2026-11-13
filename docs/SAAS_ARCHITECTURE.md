# Arquitetura do Promo Bot Web SaaS (direção aprovada)

> Estado: **direção arquitetural**, não capacidade implementada. Hoje só existe a S1
> (`WorkspaceRuntimeSpec`). Não há API, frontend, login, cofre, worker hospedado nem publicação
> automática geral. O [README](../README.md) continua sendo a fonte do que funciona; seções
> históricas por fase não autorizam operações live. Mudanças nas decisões abaixo exigem registro
> explícito (novo ADR ou edição datada), nunca alteração silenciosa.

## Objetivo

Transformar o bot local em um painel web multiusuário: o usuário cria a conta, conecta o Telegram,
escolhe canais de origem e destino, informa suas credenciais AliExpress, acompanha promoções,
links convertidos, históricos e erros e, no futuro, ativa a clonagem automática sem manter um
computador ligado.

**Regra de produto:** nenhum botão ativo sem operação real no backend; nada de métricas fictícias
nem mocks permanentes apresentados como dados reais.

## Control plane e data plane

| Plano | Responsabilidade | Componentes |
| --- | --- | --- |
| Control plane | Identidade, workspaces, configurações, credenciais, jobs, projeções e API do painel | Frontend, API FastAPI, PostgreSQL |
| Data plane | Monitorar Telegram, persistir/deduplicar, converter links, entregar, supervisionar | Worker Python existente, SQLite por workspace |

- **Frontend** (Next.js, React, TypeScript; Vercel): apenas apresentação e chamadas à API.
  Tema escuro com detalhes roxos, sidebar, dashboard, onboarding, canais, promoções, integrações,
  configurações.
- **API** (FastAPI): valida identidade (Clerk como primeira opção) e autoriza **por workspace**;
  gerencia usuários, workspaces, configurações e operações administrativas. **Nunca abre sessões
  Telethon** no processo HTTP.
- **Worker** (Python existente; Railway): consome jobs, integra Telegram e AliExpress, emite
  heartbeats e roda isolado por workspace. Reaproveita relay, filas, recovery e histórico atuais.

## Persistência

- **PostgreSQL** (control plane): usuários, workspaces, credenciais criptografadas, jobs e
  projeções para o painel.
- **SQLite** (data plane): o banco runtime atual, **um por workspace**, com migrations, backups,
  locks e identidade de arquivo da T5A preservados.
- Fora de escopo: migrar o runtime SQLite para PostgreSQL.
- Testes PostgreSQL usam banco efêmero, em execução separada da suíte offline; nunca banco
  compartilhado ou operacional.

## Isolamento de workspaces

Um workspace nunca herda credenciais, configurações ou caminhos de outro usuário nem do servidor.
A S1 implementa o contrato de configuração (`promo_bot.config.workspace`):

- `EnvironmentSettings` do workspace tem como **única fonte o construtor** — sem variáveis de
  ambiente, `.env` nem fallback para credenciais globais.
- Só uma lista permitida de campos (credenciais Telegram/AliExpress, limites, log) é aceita.
  **Gates e caminhos não podem ser fornecidos**; tentativa é recusada (`WORKSPACE_SETTINGS_INVALID`).
- Caminhos derivam de um UUID canônico (uma única grafia) sob uma raiz controlada:
  `<raiz>/<uuid>/` com banco, sessão Telegram e perfil de navegador dentro dele.
- A raiz é validada no texto literal **e** no caminho resolvido. No Windows só `X:\...` é suportado;
  UNC, prefixos `\\?\` e `\\.\`, caminho relativo à unidade, `..`, caracteres inválidos, nomes
  reservados (`CON`, `COM1`...), ponto/espaço final, `?` e controles (inclusive NUL) são **recusados**,
  não normalizados. Raiz de disco, raiz ou ancestral que seja arquivo, link/junção que redirecione o
  workspace e sobreposição (inclusive por alias, caixa ou junção) com o runtime global legado
  também são recusados.
- **Política do runtime legado** (`~/.promo_bot` e `PROMO_BOT_RUNTIME_DIR`, usados só na comparação,
  nunca como valor): cada um é reduzido a um caminho comparável (`X:\...`), removendo o prefixo
  estendido `\\?\` antes e depois do `resolve()` (que o preserva). Igualdade, ancestral e descendente
  são recusados, existindo ou não os diretórios; a identidade física (`samefile`) é uma segunda
  barreira. Valor configurado mas **não comparável com segurança** (UNC, `\\?\UNC\`, volume,
  dispositivo `\\.\`, ou que não resolva) **falha fechado** (`WORKSPACE_LEGACY_RUNTIME_UNCOMPARABLE`),
  nunca é ignorado. Variável vazia equivale a não definida.
- A URL SQLite precisa fazer *round-trip* pelo parser do SQLAlchemy (sem abrir conexão) até exatamente
  o arquivo validado; caso contrário a raiz é recusada.
- A construção é **pura**: não cria arquivos, não importa Telegram/Alembic/`promo_bot.database` e não
  usa rede.
- `AppConfig` do workspace é um **snapshot privado**: reconstruído a partir de dados simples (nada do
  chamador é retido, inclusive modelos aninhados). `frozen=True` do schema não torna `dict`
  imutável e uma subclasse de `dict` continua aceitando métodos herdados (`dict.update`,
  `__init__`...), então os mapas (`providers`, limites, destinos shadow) são `FrozenMap`: um
  `Mapping` sobre `MappingProxyType` privado, **que não é `dict`**, sem métodos de escrita, com
  atribuição de atributos recusada e `__init__` inerte. Tentativa de mutação levanta `TypeError` ou
  `AttributeError` e **nunca altera o estado**. Contrato: leitura, iteração, `get`/`items`/`values`
  e `==` funcionam; `copy`, `deepcopy` e `pickle` devolvem um `FrozenMap` igualmente congelado;
  `model_dump()`/`model_dump_json()` devolvem **cópias simples e mutáveis, desacopladas** (formato
  idêntico ao do `AppConfig` legado) e `model_validate` do dump reconstrói uma config igual; a
  config é igual (`==`) a um `AppConfig` legado de mesmo conteúdo, e não é hasheável. Isso vale
  só na fronteira da S1 (subclasses internas de `AppConfig`); **o schema legado não foi alterado**.
  Fora do escopo: contornos deliberados do Python (`object.__setattr__`, `__dict__`, `gc`, `ctypes`).
  Quem precisar de um dict mutável deve usar o dump.
- Erros têm código estável e nunca ecoam valores; `repr` e `safe_summary()` omitem segredos.
  Valor gerenciado que não chegue às settings (ex.: alias divergente) é recusado
  (`WORKSPACE_SETTINGS_NOT_APPLIED`).
- Um guarda final confere uma tabela de valores fechados para todos os gates, independente dos
  defaults de `EnvironmentSettings`; um teste exige classificar qualquer novo booleano.
- `AppConfig` do workspace não pode ativar providers nem navegador (a ativação é deliberada,
  tarefa S7).
- Não validado: caminhos UNC/de rede e prefixos estendidos (recusados), caminhos acima de 260
  caracteres, comportamento de sistemas de arquivos de rede, e corridas (TOCTOU) entre a validação
  pura e a criação posterior do diretório (responsabilidade do supervisor, S8).

A CLI legada continua usando `EnvironmentSettings` sem mudanças.

## Segredos (futuro, S3)

Cofre com criptografia de envelope, segregação por workspace, auditoria de acesso e rotação.
Sessões Telegram (S9) ficam isoladas por workspace, protegidas em repouso e nunca compartilhadas.
Segredos não aparecem em logs, respostas de API, projeções nem no Git. Até o cofre existir,
nenhuma credencial real de usuário deve ser persistida pelo SaaS.

## Publicação e operações live

- Publicação real, busca live e envio automático permanecem **desabilitados por padrão** e não
  são controláveis por dados de workspace.
- Habilitar qualquer gate exige tarefa própria, validação offline e autorização explícita; os
  gates Telegram, AliExpress, Shopee e Mercado Livre atuais são preservados.
- Persistência antes do envio, deduplicação, validação de URLs e bloqueio de entregas ambíguas
  valem para todos os workspaces. Tracking confirmado não comprova comissão.
- Autorizar publicar uma branch não autoriza deploy, mensagens do bot ou mudança em produção.

## Roadmap

| Etapa | Conteúdo | Estado |
| --- | --- | --- |
| S1 | `WorkspaceRuntimeSpec`: configuração explícita e isolada | Implementada (em revisão) |
| S2 | ADR e documentação formal da arquitetura | Este documento é o registro inicial; ADR formal pendente |
| S3 | Cofre de credenciais | Pendente |
| S4 | Modelos e repositórios PostgreSQL do control plane | Pendente |
| S5 | FastAPI, autenticação, autorização e capabilities | Pendente |
| S6 | Next.js: estrutura, layout e autenticação | Pendente |
| S7 | Integração administrativa AliExpress | Pendente |
| S8 | Supervisor de workers, jobs, leases e heartbeats | Pendente |
| S9 | Login Telegram por QR e sessões isoladas | Pendente |
| S10 | Canais, entidades, avatares e verificação do destino | Pendente |
| S11 | Relay hospedado por workspace e projeções | Pendente |
| S12 | Promoções, conversão manual, histórico e imagens | Pendente |
| S13 | Dashboard real, eventos e refinamentos | Pendente |

Etapas posteriores (entrega controlada e publicação automática) só começam após as validações
necessárias. Cada etapa só é iniciada com aprovação.

## MVP técnico × produto operacional

O **MVP técnico** é privado e para testes controlados: sem billing, sem cadastro público, gates
fechados, usuários convidados. O **produto operacional** exige o que ainda não existe: cofre,
supervisão de workers, limites e auditoria, validação das integrações e, só depois, publicação
automática. Concluir o MVP técnico não significa produto pronto.

## Dependências e riscos principais

- **Telegram:** sessões de usuário hospedadas são sensíveis; risco de bloqueio por uso
  automatizado, limites de API e dependência do login por QR (S9).
- **AliExpress:** permissões de afiliado por conta, limites de API e atribuição de comissão não
  comprovada; Shopee e Mercado Livre seguem limitados por contratos.
- **SQLite por workspace:** simples e já validado, mas escala por número de workspaces e exige
  locks/backups por arquivo; avaliar PostgreSQL só se necessário, em decisão própria.
- **Cofre e isolamento:** falha aqui expõe credenciais de todos os usuários; é pré-requisito de
  qualquer credencial real.
- **Clerk e hospedagem (Vercel/Railway):** decisões iniciais ainda não provisionadas.
- **Custo operacional** de workers sempre ativos por workspace.

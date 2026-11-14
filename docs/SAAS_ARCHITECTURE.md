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
- **Configuração confiável = dado congelado, não um modelo Pydantic.** A S1 guarda um snapshot privado
  do `AppConfig` como dados simples profundamente imutáveis (`FrozenMap` sobre `MappingProxyType`,
  tuplas, `frozenset`), obtidos do dump **completo** do dado validado (nada do chamador é retido).
  **Valores e "campos definidos" são guardados separadamente (R-05):** a entrada é lida uma única
  vez, campo a campo (nunca por `model_dump(exclude_unset=True)`), a validação e o guarda de
  providers rodam sobre os valores completos, e um registro congelado à parte (`_ModelFieldsSet`)
  guarda o `model_fields_set` de cada modelo, em qualquer profundidade (campos, mapas, sequências).
  Modelos Pydantic recebidos nunca são confiados como estão: são sempre revalidados a partir de dados
  simples. `WorkspaceRuntimeSpec.app_config` é uma propriedade que devolve, **a cada acesso, um
  `AppConfig` legado genuíno e novo** (campos `dict` reais, serialização nativa do Pydantic), com os
  valores completos e o mesmo `model_fields_set` que o modelo legado equivalente teria
  (`model_construct`, API pública do Pydantic, após a validação).
  *Por que não só trocar por dump completo:* todos os campos passariam a constar como definidos e
  `exclude_unset` divergiria do legado. *Por que não manter o dump filtrado:* um contêiner padrão
  alterado depois da construção (ou um valor plantado com `model_construct`) fica fora de
  `model_fields_set` e desaparecia antes da validação, inclusive um provider proibido.
  Consequência deliberada: uma subclasse de `AppConfig` com campos próprios é recusada (antes, o
  campo extra não definido era descartado em silêncio).
  Contrato: o objeto devolvido pertence ao chamador e pode ser mutado, copiado ou serializado à
  vontade (`model_dump`/`model_dump_json` com `include`/`exclude`/`exclude_defaults`/`exclude_unset`,
  `TypeAdapter(AppConfig)`, modelo envolvente com campo `AppConfig`), com resultado idêntico ao do
  `AppConfig` legado equivalente; nada disso alcança o estado confiável. Dois acessos não compartilham
  objetos. O schema legado não foi alterado e não há subclasses nem serializers customizados.
  *Por que não `FrozenMap` dentro do modelo (R-03/R-04):* o campo `dict[...]` do schema público só
  serializa corretamente `dict`; um `@model_serializer` na subclasse não vale quando se serializa
  pelo tipo base (`TypeAdapter(AppConfig)`, modelo envolvente) e, em modo plain, ignora os filtros de
  seleção. Manter a imutabilidade fora do modelo elimina a causa em vez de remendar o serializer.
  Fora do escopo: contornos deliberados do Python (`object.__setattr__`, `__dict__`, `gc`, `ctypes`).
  Custo: uma validação Pydantic por acesso; quem lê muitas vezes deve guardar o objeto devolvido.
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

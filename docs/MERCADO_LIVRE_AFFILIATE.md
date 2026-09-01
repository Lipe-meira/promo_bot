# Mercado Livre Brasil — Fase 6

## Escopo do primeiro marco

Este marco é exclusivamente offline. Ele prepara contratos, estados, persistência mínima, modo
manual e um adaptador compatível com uma `Page` assíncrona do Playwright, validado com uma página
local falsa. Não abre o Mercado Livre, não autentica uma conta, não gera links reais e não envia
promoções.

Os seguintes gates permanecem fechados e independentes:

1. consulta live da API pública de catálogo;
2. acesso ao portal real;
3. geração real de um link;
4. worker contínuo;
5. entrega real ao chat operacional;
6. divulgação externa;
7. modo headless.

`DRY_RUN=true` e `PUBLISH_REAL_DEALS=false` são obrigatórios durante este marco.

## Gate contratual

As fontes oficiais consultadas em 1º de setembro de 2026 documentam o Gerador de Links no Portal
do Afiliado pelo computador, mas não documentam uma API pública de geração de links com comissão:

- [Gerador de Links](https://www.mercadolivre.com.br/l/afiliados-gere-seus-links);
- [Central de Afiliados e Criadores](https://www.mercadolivre.com.br/l/visite-o-portal-de-afiliados);
- [itens e buscas](https://developers.mercadolivre.com.br/pt_br/convivencia-me1-me2/itens-e-buscas);
- [preços de produtos](https://developers.mercadolivre.com.br/pt_br/produto-consulta-de-usuarios/api-de-precos).

Os [Termos do Programa de Desenvolvedores](https://developers.mercadolivre.com.br/pt_br/termos-e-condicoes)
incluem restrições a robôs, scraping, interferência em autenticação e engenharia reversa. Não foi
encontrada autorização explícita que permita automatizar o Gerador de Links. Portanto, nenhum
contrato de seletores do site real será criado e nenhuma interação live ocorrerá sem confirmação
escrita do Mercado Livre aplicável a esta conta e a este uso.

O cliente de catálogo concreto também permanece no gate. A documentação oficial separa catálogo
e preços, mostra chamadas autenticadas e anuncia alterações de endpoints/campos. Antes do cliente,
serão confirmados autenticação, endpoints, limites e campos permitidos. Um stub não poderá simular
sucesso.

## Entrega operacional não é divulgação pública

O `TELEGRAM_TARGET_CHAT_ID` atual representa uma caixa privada de revisão controlada pelo usuário.
Uma entrega nesse chat é `INTERNAL_REVIEW`; ela não significa aprovação nem divulgação pública.

O fluxo de negócio é separado do transporte:

```text
AWAITING_INTERNAL_REVIEW
  -> MANUALLY_APPROVED ou REJECTED
MANUALLY_APPROVED
  -> EXTERNAL_DISCLOSURE_PENDING ou REJECTED
EXTERNAL_DISCLOSURE_PENDING
  -> EXTERNALLY_DISCLOSED ou REJECTED
```

As entregas têm finalidade `INTERNAL_REVIEW` ou `EXTERNAL_DISCLOSURE`. A Fase 6 inicial bloqueia
qualquer entrega real e não implementa divulgação externa automática.

O Mercado Livre informa que links em Telegram devem ser compartilhados em canais públicos,
abertos e declarados, e não em grupos fechados. Isso se aplica a uma divulgação posterior feita
pelo usuário; não transforma a caixa operacional privada em canal público. Consulte as
[regras de compartilhamento](https://www.mercadolivre.com.br/l/afiliados-compartilhamento-de-publicacao)
antes de encaminhar uma oferta para terceiros.

## Persistência mínima

Não são criadas tabelas de job, perfil ou controle do worker neste marco:

- `affiliate_candidates` continua sendo a fila persistente e mantém tentativas e lease;
- `source_message_links` continua vinculando as mensagens ao candidato;
- `products`, `deals` e `affiliate_link_proofs` permanecem as fontes de verdade;
- `deals.review_state` separa revisão/aprovação/divulgação;
- `deliveries.purpose` separa entrega interna de divulgação externa;
- um lock exclusivo de arquivo/processo protege o perfil no Windows;
- a lease do candidato detecta processamento interrompido.

Estado de pausa e circuit breaker somente serão persistidos quando o worker contínuo for
autorizado; até lá não existe motivo para uma tabela própria.

## Perfil e lock do navegador

O perfil padrão fica em `%USERPROFILE%\.promo_bot\browser\mercadolivre\profile`, fora do Git.
Um caminho explícito deve permanecer fora do workspace e de diretórios públicos sincronizados.

O lock não bloqueante fica ao lado do perfil. O sistema operacional o libera quando o processo
encerra, inclusive após queda. A combinação de lock de processo com lease condicional no banco
impede dois processos de usar o mesmo perfil e impede que uma lease expirada confirme sucesso.

Cookies e local storage permanecem sob controle do perfil do navegador. O aplicativo não os lê,
exporta, imprime ou persiste no banco. HTML, headers autenticados, tokens, traces live e screenshots
de sucesso também são proibidos.

## Etiquetas

Etiqueta é opcional e nenhuma é usada por padrão. Uma etiqueta só pode ser selecionada se o
usuário já a tiver cadastrado e listado em `registered_labels`. O programa não cria nem inventa
identificadores. Status e logs informam apenas presença/contagem; não imprimem o valor completo.

## Allowlist do link afiliado

`allowed_affiliate_hosts` começa vazia. Ela aceita somente hostnames sanitizados, sem esquema,
caminho, porta, credenciais ou query. O usuário pode fornecer apenas o hostname confirmado por:

- documentação oficial; ou
- uma amostra gerada manualmente na interface oficial.

Não devem ser enviados ao programa ou ao Codex cookies, headers, tokens, link completo com
identificadores ou dados da sessão.

O resultado é aceito somente se for HTTPS, tiver host exato na allowlist e vier do campo de
resultado definido pelo contrato de UI revisado. A URL original nunca é fallback, e o link gerado
não é expandido.

## CLI offline

```text
promo-bot ml-browser status
promo-bot ml-browser generate --url <url>
promo-bot ml-browser authorize
```

`status` não abre navegador. `generate` somente canonicaliza e produz preview, sem link afiliado ou
entrega. `authorize` retorna `MERCADO_LIVRE_LIVE_BROWSER_GATE_CLOSED` enquanto o gate estiver
fechado. Comandos de worker, pausa, retomada e processamento real pertencem a marcos posteriores.

## Próximas autorizações

Cada item requer uma autorização separada: contrato oficial de catálogo, instalação/execução de
navegador, login manual visível, geração de um link conhecido, worker contínuo, entrega real à
caixa operacional, divulgação externa e headless. Nenhum gate implica automaticamente o seguinte.

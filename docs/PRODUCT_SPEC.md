# Projeto: bot local de promoções e links de afiliado

Quero desenvolver um bot local em Python para monitorar promoções, gerar links com os meus códigos de afiliado e enviar ofertas prontas para um chat ou canal privado no Telegram.

O programa será executado inicialmente no meu computador Windows, sem hospedagem paga e sem integração automática com WhatsApp. Eu mesmo encaminharei manualmente as promoções recebidas no Telegram para meus grupos do WhatsApp.

O projeto deverá ser confiável, modular, seguro e fácil de manter. Não quero uma demonstração superficial nem providers que aparentem funcionar usando dados falsos. Quando uma integração real depender de credenciais, aprovação de afiliado ou documentação privada, implemente a interface, mantenha o provider desativado e documente claramente o que falta.

## Regras iniciais de trabalho

Antes de alterar qualquer coisa:

1. informe o diretório atual;
2. confirme que ele é um workspace de desenvolvimento seguro;
3. inspecione todo o workspace;
4. verifique se já existe código relacionado;
5. execute git status, git branch --show-current e git remote -v quando estiver em um repositório;
6. preserve todo código e alteração existente;
7. não sobrescreva, reverta ou apague trabalho que não foi feito por você.

Se o projeto estiver vazio, crie uma estrutura nova, modular e organizada.

Nunca clone, inicialize ou desenvolva este projeto dentro de:

- C:\Windows;
- C:\Windows\System32;
- C:\Program Files;
- outro diretório de sistema.

Se o Codex não estiver com o repositório correto aberto, pare antes de criar arquivos e me oriente a abrir ou selecionar a pasta adequada.

Não implemente tudo em um arquivo gigante. Separe domínio, infraestrutura, integrações e interface. Trabalhe por fases, teste cada etapa e faça commits coerentes usando Conventional Commits.

Exemplos:

- feat: add telegram promotion listener
- feat: add shopee affiliate provider
- fix: prevent duplicated deals
- refactor: separate store providers
- test: add deal parser tests
- docs: add affiliate setup instructions
- chore: configure linting

## Objetivo geral

O sistema deverá possuir dois mecanismos independentes que utilizam o mesmo pipeline de processamento.

Pipeline conceitual:

~~~text
entrada
→ extração e normalização segura de URLs
→ identificação da loja e do produto
→ consulta de dados oficiais
→ verificação de preço, estoque e cupom
→ cálculo do preço final
→ histórico, confiança e ranking
→ geração do link afiliado
→ deduplicação
→ formatação por template
→ validação final
→ Telegram privado
~~~

## 1. Relay de promoções

Esse mecanismo deverá monitorar canais e grupos de promoções do Telegram nos quais minha conta já participa.

Quando surgir uma mensagem nova, o sistema deverá:

1. capturar a mensagem;
2. extrair todos os links;
3. identificar a loja;
4. expandir links encurtados com segurança;
5. remover parâmetros de afiliado e rastreadores anteriores;
6. identificar o produto por ID, ASIN, item ID ou URL canônica;
7. buscar novamente os dados oficiais do produto quando possível;
8. verificar preço, estoque e cupom;
9. gerar um link usando a minha conta de afiliado;
10. criar uma mensagem nova em português do Brasil;
11. validar a mensagem final;
12. enviar a mensagem pronta para meu Telegram privado;
13. registrar o processamento no banco para evitar repetição.

Não quero simplesmente encaminhar a mensagem original. A mensagem original deverá servir apenas como alerta e fonte inicial. O bot deverá preferencialmente buscar novamente título, preço, imagem, vendedor e disponibilidade no site, feed ou API da loja.

Remova do conteúdo final:

- nome do canal original;
- username do autor;
- link de afiliado original;
- parâmetros de rastreamento antigos;
- propaganda do canal original;
- chamadas como “entre no nosso grupo”;
- links para redes sociais;
- identificadores de outros afiliados.

Não copie imagens com marca-d’água de outros canais. Sempre que possível, utilize a imagem oficial do produto obtida pela API, feed ou página da loja. Se não houver imagem oficial confiável, envie somente texto.

O sistema nunca poderá reutilizar o link de afiliado original como fallback.

## 2. Descobridor de promoções independentes

Esse mecanismo deverá procurar promoções diretamente nos catálogos das lojas, sem depender de alguém publicá-las primeiro.

Lojas desejadas:

- Mercado Livre Brasil;
- Amazon Brasil;
- Shopee Brasil;
- AliExpress;
- KaBuM!.

O sistema deverá pesquisar categorias e palavras-chave configuráveis, como:

- placas de vídeo;
- processadores;
- SSD;
- memória RAM;
- placa-mãe;
- monitores;
- periféricos;
- celulares;
- consoles;
- eletrônicos em geral.

Essas categorias, palavras-chave e filtros deverão ser editáveis em arquivo de configuração, sem alteração do código.

A busca deverá:

1. consultar APIs e feeds oficiais quando disponíveis;
2. utilizar navegador automatizado apenas quando realmente necessário;
3. coletar nome, preço atual, preço anterior, imagem, estoque, vendedor, frete e condições de pagamento;
4. consultar cupons conhecidos e compatíveis;
5. calcular o menor preço final comprovável;
6. registrar histórico de preço;
7. comparar o preço atual com o histórico;
8. detectar descontos falsos ou pouco relevantes;
9. classificar a qualidade da promoção;
10. enviar apenas ofertas que ultrapassem critérios configurados.

Exemplo:

~~~text
Produto: RTX 5070
Preço anunciado: R$ 1.399,00
Desconto no PIX: R$ 100,00
Cupom HARDWARE10: R$ 100,00
Preço final: R$ 1.199,00
Mediana histórica: R$ 1.550,00
Desconto real aproximado: 22,6%
~~~

O preço final deverá informar claramente se depende de:

- PIX;
- boleto;
- cartão;
- quantidade de parcelas;
- juros;
- primeira compra;
- aplicativo;
- conta selecionada;
- valor mínimo;
- produto ou categoria específica;
- região ou CEP;
- quantidade limitada.

## Definição de promoção inédita

Neste projeto, “promoção inédita” significa uma oferta encontrada diretamente por um provider e que:

- ainda não existe no banco local;
- não foi observada nos canais monitorados durante o período configurado;
- possui preço, cupom ou condição melhor que a última oferta registrada;
- ultrapassa o score mínimo configurado.

O sistema não deverá afirmar que a promoção é inédita em toda a internet. Internamente, utilize o termo independently_discovered.

## Estratégia de cupons

Crie um módulo chamado CouponEngine.

O sistema nunca deverá gerar códigos aleatórios nem tentar milhares de combinações. Não implemente brute force de cupons.

Os cupons deverão vir de fontes legítimas:

- API ou feed do programa de afiliados;
- página oficial de cupons da loja;
- banners oficiais;
- mensagens dos canais monitorados;
- cupons cadastrados manualmente;
- cupons anteriormente conhecidos e ainda não expirados;
- vouchers fornecidos pela Awin.

Cada cupom deverá possuir, quando possível:

- código;
- loja;
- descrição;
- data inicial;
- data final;
- valor fixo ou percentual;
- valor mínimo da compra;
- desconto máximo;
- categorias permitidas;
- produtos permitidos;
- restrições de conta;
- restrição de aplicativo;
- restrição de forma de pagamento;
- fonte;
- horário da última validação;
- status da validação.

Status de cupom:

- DISCOVERED: encontrado, mas ainda não testado;
- DECLARED: anunciado oficialmente pela loja;
- VERIFIED: aplicado com sucesso;
- PERSONALIZED: funciona apenas para determinadas contas;
- APP_ONLY: funciona apenas no aplicativo;
- EXPIRED: expirado;
- FAILED: não aplicado;
- UNKNOWN: resultado inconclusivo.

Quando a API não informar se um cupom funciona para determinado produto, o sistema poderá usar Playwright, desde que a verificação por navegador esteja explicitamente ativada.

Fluxo permitido:

1. abrir a página do produto;
2. selecionar uma variação quando necessário;
3. adicionar ao carrinho;
4. aplicar um cupom conhecido;
5. ler o preço final;
6. remover o produto do carrinho;
7. limpar apenas o estado necessário antes do próximo teste.

Nunca finalize compras. Nunca avance para confirmação de pedido. Nunca armazene senhas no código.

O teste deverá ser controlado, lento e limitado. Se aparecer CAPTCHA, bloqueio, verificação de segurança ou pedido de login, pare aquela integração, registre o motivo e solicite intervenção manual.

Não implemente:

- bypass de CAPTCHA;
- evasão de fingerprint;
- proxy rotativo;
- mecanismo de evasão;
- automação de checkout;
- tentativa massiva de cupons.

Cupons personalizados deverão aparecer com aviso:

~~~text
⚠️ Cupom testado em uma conta específica e pode não estar disponível para todos.
~~~

## Controle da sessão do navegador

A verificação de cupons por navegador deverá ficar desativada por padrão e ser ativada explicitamente por provider.

Implemente:

- limite de testes por hora;
- intervalo mínimo entre testes;
- apenas um teste simultâneo por perfil;
- lock para impedir operações concorrentes no mesmo carrinho;
- timeout total por teste;
- captura de screenshot somente em falhas e sem dados pessoais;
- limpeza controlada do carrinho;
- detecção de sessão expirada;
- modo de login manual;
- circuit breaker após erros ou bloqueios consecutivos.

Nunca teste cupons em paralelo usando a mesma conta ou o mesmo perfil.

O resultado deverá registrar evidências estruturadas:

- preço antes;
- cupom aplicado;
- mensagem apresentada pela loja;
- desconto observado;
- preço depois;
- horário;
- provider;
- resultado;
- motivo da falha.

Uma verificação feita na minha conta não prova que o cupom funciona para todas as contas.

## Integração com Telegram

Utilize duas integrações separadas.

### Telethon

Use Telethon com sessão de usuário para ler canais e grupos nos quais minha conta participa.

Credenciais:

- TELEGRAM_API_ID;
- TELEGRAM_API_HASH;
- número da conta apenas durante a autenticação inicial;
- arquivo de sessão persistente fora do Git.

A sessão de usuário deverá apenas monitorar mensagens. Não publique automaticamente em canais externos usando minha conta.

### Telegram Bot API

Use um bot criado pelo BotFather para enviar promoções ao meu chat ou canal privado.

Credenciais:

- TELEGRAM_BOT_TOKEN;
- TELEGRAM_TARGET_CHAT_ID.

A mensagem poderá possuir botões:

- Abrir oferta;
- Revalidar;
- Descartar;
- Ver histórico.

No MVP, apenas o botão Abrir oferta é obrigatório. Os demais poderão ser implementados posteriormente, mas a arquitetura deverá permitir callbacks no futuro.

Não implemente automação para WhatsApp. O resultado deverá ser uma mensagem pronta para eu copiar ou encaminhar manualmente.

## Formatação das mensagens

Não utilize IA por padrão. Crie templates previsíveis e configuráveis.

Exemplo:

~~~text
🔥 OFERTA ENCONTRADA

🎮 {titulo}

❌ De: {preco_anterior}
✅ Por: {preco_atual}

🎟️ Cupom: {cupom}
💳 Condição: {condicao_pagamento}
📉 Desconto real: {percentual_desconto}

🛒 {link_afiliado}

⏰ Verificado em: {data_hora}

⚠️ Preço, estoque e cupom podem mudar a qualquer momento.
🔗 Link de afiliado: posso receber comissão pela compra.
~~~

Se não houver preço anterior confiável, não invente um preço riscado.

Se o cupom não tiver sido testado:

~~~text
🎟️ Cupom divulgado: {cupom} — ainda não verificado automaticamente.
~~~

Nunca permita que IA altere números, preços, cupons, URLs ou condições. Se futuramente for adicionado um modelo de linguagem para melhorar títulos, ele deverá receber apenas campos textuais permitidos e o resultado deverá passar por validação.

Permita múltiplos templates e seleção aleatória opcional sem alterar informações objetivas.

## Interface dos providers

Crie uma interface abstrata chamada StoreProvider, com capacidades declaradas individualmente.

Exemplo conceitual:

~~~python
class StoreProvider:
    async def identify_url(self, url): ...
    async def canonicalize_url(self, url): ...
    async def extract_product_id(self, url): ...
    async def get_product(self, product_id): ...
    async def search_products(self, query, filters): ...
    async def get_available_coupons(self, product): ...
    async def verify_coupon(self, product, coupon): ...
    async def generate_affiliate_link(self, product_url): ...
~~~

Cada provider deverá declarar se possui:

- pesquisa de produtos;
- consulta de preço;
- consulta de estoque;
- consulta de cupom;
- teste de cupom;
- geração de link afiliado;
- histórico;
- necessidade de navegador.

Não retorne valores falsos quando uma capacidade não existir. Utilize resultado explícito de não suportado, desativado, pendente ou erro.

## Shopee

Avalie como dependência opcional:

https://github.com/RenanGalvao/saapi

O SaAPI possui licença MIT e implementa um wrapper não oficial para a API de afiliados da Shopee.

Antes de adotá-lo:

- revise a licença;
- confira compatibilidade com Python 3.12;
- verifique endpoints e campos atuais;
- execute testes isolados;
- mantenha seu uso atrás do ShopeeProvider;
- não permita que tipos internos da biblioteca vazem para o domínio da aplicação.

Se estiver desatualizado ou incompatível, implemente um cliente mínimo para a API oficial da Shopee com httpx, seguindo a documentação disponível na minha conta.

Use APIs oficiais sempre que minhas credenciais permitirem. Não trate a simples adição de parâmetros UTM como geração de link afiliado. Um link só poderá ser marcado como afiliado se tiver sido gerado por mecanismo oficial.

## AliExpress

Avalie:

https://github.com/sergioteula/python-aliexpress-api

Use a API oficial de afiliados para:

- buscar produtos;
- obter hot products;
- consultar detalhes;
- obter preço promocional;
- gerar links;
- incluir meu tracking ID.

Nunca copie tokens ou credenciais de repositórios públicos.

## Amazon Brasil

Avalie:

https://github.com/sergioteula/python-amazon-paapi

O projeto suporta a Amazon Creators API e a API anterior. Para projeto novo, prefira o módulo amazon_creatorsapi e a Creators API quando estiverem disponíveis.

Use configuração do Brasil e minha tag de afiliado.

Se eu ainda não tiver acesso à API, mantenha o provider desativado ou manual. Não crie scraping agressivo da Amazon como solução automática.

A mensagem da Amazon deverá respeitar as regras vigentes do programa de associados, incluindo divulgação do afiliado e conteúdo útil.

## Mercado Livre Brasil

Utilize a API pública do Mercado Livre para consultar produtos, categorias e preços usando o site MLB.

Referência arquitetural:

https://github.com/alexjamesmx/E-Commerce-Offers-Telegram-Bot

Esse repositório foi criado para o México e usa MLM. Não copie diretamente. Adapte conceitos ao Brasil e revise licença, segurança e compatibilidade.

A geração do link afiliado do Mercado Livre é delicada. Não use endpoints privados, cookies copiados do DevTools ou tokens CSRF como solução principal.

Implemente modos:

- official_api: quando existir e estiver disponível na minha conta;
- browser: usando meu perfil autenticado e automação controlada;
- manual: marcando a oferta como pendente;
- disabled.

Se usar Playwright, mantenha o perfil autenticado em pasta externa ignorada pelo Git.

## KaBuM! e Awin

A KaBuM! utiliza a Awin em seu programa de afiliados.

Quando minha conta tiver acesso, utilize:

- Awin Product Feed;
- Awin Offers API;
- Awin Link Builder API;
- vouchers e promoções cadastradas pela loja.

Documentação:

https://help.awin.com/apidocs/introduction-1

A integração deverá:

- pesquisar produtos do feed da KaBuM!;
- identificar preço atual;
- consultar vouchers;
- gerar deeplink rastreado;
- testar cupons conhecidos apenas quando necessário;
- monitorar especialmente categorias de hardware.

Não presuma que todo cupom funciona em todo produto.

## Modelo de domínio e banco de dados

Utilize SQLite inicialmente, SQLAlchemy 2.x e Alembic desde o início.

Não use Pandas como banco de dados.

### source_messages

- plataforma;
- ID da mensagem;
- ID do canal de origem;
- horário;
- texto original;
- links extraídos;
- status do processamento;
- código de erro;
- erro resumido;
- horário de criação e atualização.

### products

- loja;
- ID externo;
- título;
- URL canônica;
- imagem;
- categoria;
- vendedor;
- moeda;
- horário de criação e atualização.

### deals

- produto;
- preço anterior;
- preço atual;
- preço final;
- frete;
- cupom;
- condição de pagamento;
- percentual de desconto;
- confiança;
- score;
- fonte;
- origem da descoberta;
- data de descoberta;
- data da última validação;
- link afiliado;
- status;
- status de envio.

### price_history

- produto;
- preço;
- moeda;
- frete;
- condição de pagamento;
- horário;
- fonte da coleta.

### coupons

- campos definidos na seção de cupons;
- status;
- última validação.

### processed_items

- loja;
- ID do produto;
- hash da oferta;
- última vez enviado;
- preço da última oferta;
- cupom da última oferta;
- cooldown até.

Crie índices para:

- loja e ID externo;
- hash da oferta;
- status;
- data da descoberta;
- última validação;
- produto e horário do histórico.

Não apague nem recrie automaticamente o banco quando o schema mudar. Aplique migrações preservando dados existentes.

## Invariantes monetários e temporais

Use Decimal para todos os valores monetários. Nunca utilize float para preços, descontos, frete ou economia.

Armazene:

- valor;
- moeda;
- forma de pagamento;
- quantidade de parcelas;
- juros;
- desconto PIX;
- frete;
- preço final;
- horário da coleta;
- fonte da informação.

Use timestamps com timezone e armazene internamente em UTC. Converta para o timezone configurado apenas na apresentação.

Não considere condições de pagamento equivalentes.

Exemplo:

- R$ 1.199 no PIX;
- R$ 1.299 em uma parcela;
- R$ 1.499 em doze parcelas.

Essas condições deverão ser armazenadas e comparadas separadamente.

## Estados da oferta

Toda oferta deverá possuir um estado explícito:

- DISCOVERED: produto encontrado;
- VALIDATING: dados sendo conferidos;
- PENDING_AFFILIATE: produto válido, mas sem link afiliado;
- READY: validado e com link afiliado;
- SENT: enviado ao Telegram;
- DISCARDED: rejeitado pelos critérios;
- EXPIRED: preço, estoque ou cupom expirou;
- ERROR: falha técnica;
- MANUAL_REVIEW: necessita intervenção humana.

Configuração padrão:

~~~env
PUBLISH_WITHOUT_AFFILIATE=false
COUPON_BROWSER_VERIFICATION=false
DRY_RUN=true
SEARCH_ENABLED=false
MAX_PROMOTIONS_PER_HOUR=10
~~~

Quando PUBLISH_WITHOUT_AFFILIATE=false, uma promoção sem link afiliado oficial deverá ser registrada como PENDING_AFFILIATE e não deverá ser enviada como promoção pronta.

No desenvolvimento, uma oferta pendente poderá aparecer apenas no console ou em relatório dry-run, claramente marcada como não publicável.

## Detecção de promoção real

Não confie somente em original_price informado pela loja.

Níveis de confiança:

- HIGH: preço confirmado, cupom verificado e histórico suficiente;
- MEDIUM: preço confirmado, mas histórico insuficiente ou cupom apenas declarado;
- LOW: depende da mensagem original ou de dado não confirmado.

Considere:

- menor preço registrado;
- mediana dos últimos sete dias;
- mediana dos últimos trinta dias;
- preço imediatamente anterior;
- diferença percentual;
- condição de pagamento;
- frete;
- vendedor;
- estoque.

Não compare preço PIX e preço parcelado como se fossem iguais.

Quando não houver histórico suficiente, aceite provisoriamente o preço anterior declarado pela loja apenas como referência de baixa confiança. Nunca apresente esse dado como histórico próprio.

## Deduplicação

Considere duplicada uma oferta com:

- mesma loja;
- mesmo ID de produto;
- mesma variação relevante;
- mesmo preço final;
- mesmo cupom;
- mesma condição de pagamento;
- dentro do cooldown.

Uma oferta poderá ser reenviada se:

- o preço cair além da margem configurada;
- surgir um cupom melhor;
- a condição de pagamento melhorar;
- o estoque retornar depois de um período;
- o cooldown expirar.

Configure cooldown padrão de 24 horas e permita edição.

A deduplicação deverá persistir após reiniciar o programa.

## Ranking de ofertas

Crie um DealRanker configurável.

Critérios:

- desconto real;
- diferença para a mediana;
- confiabilidade;
- popularidade da categoria;
- valor absoluto economizado;
- cupom verificado;
- disponibilidade;
- vendedor confiável;
- frete;
- recência.

Não use machine learning no MVP. Utilize pontuação explícita e testável.

Exemplo:

~~~text
+30 desconto real acima de 20%
+20 cupom verificado
+15 menor preço histórico
+10 vendedor oficial
+10 estoque confirmado
-20 cupom personalizado
-30 preço não confirmado
~~~

## Validação obrigatória antes do envio

Antes de enviar qualquer promoção, valide:

1. domínio pertencente à allowlist;
2. URL canônica válida;
3. produto e loja identificados;
4. ausência de parâmetros do afiliado original;
5. link afiliado gerado pelo provider correto;
6. preço atual confirmado;
7. condição de pagamento informada;
8. status do cupom informado corretamente;
9. oferta não considerada duplicada;
10. template sem placeholders inválidos.

Rejeite o envio se encontrar:

- link original de afiliado;
- domínio desconhecido;
- placeholder não substituído;
- preço inválido;
- link vazio;
- cupom apresentado como verificado sem evidência;
- produto no estado PENDING_AFFILIATE;
- limite de envios excedido.

Registre a decisão com código de motivo estruturado, sem secrets.

## Estrutura sugerida

~~~text
promo_bot/
├── src/
│   └── promo_bot/
│       ├── config/
│       │   ├── settings.py
│       │   └── store_capabilities.py
│       ├── database/
│       │   ├── models.py
│       │   ├── repository.py
│       │   └── session.py
│       ├── telegram/
│       │   ├── listener.py
│       │   ├── notifier.py
│       │   └── handlers.py
│       ├── providers/
│       │   ├── base.py
│       │   ├── amazon.py
│       │   ├── mercadolivre.py
│       │   ├── shopee.py
│       │   ├── aliexpress.py
│       │   └── kabum.py
│       ├── coupons/
│       │   ├── engine.py
│       │   ├── verifier.py
│       │   └── sources.py
│       ├── deals/
│       │   ├── detector.py
│       │   ├── ranker.py
│       │   ├── deduplicator.py
│       │   └── formatter.py
│       ├── browser/
│       │   ├── manager.py
│       │   └── profiles.py
│       ├── scheduler/
│       │   └── jobs.py
│       ├── security/
│       │   └── urls.py
│       ├── cli.py
│       └── main.py
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── browser/
│   └── fixtures/
├── docs/
│   ├── PRODUCT_SPEC.md
│   ├── ARCHITECTURE.md
│   ├── TELEGRAM_SETUP.md
│   ├── AFFILIATE_SETUP.md
│   └── STORES.md
├── AGENTS.md
├── config.example.yaml
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
├── run.ps1
└── README.md
~~~

A estrutura poderá ser ajustada com justificativa técnica, preservando separação de responsabilidades.

Não crie LICENSE automaticamente se o repositório ainda não possuir uma. Pergunte qual licença deverá ser utilizada.

## AGENTS.md e documentação permanente

Durante a Fase 1:

1. salve esta especificação em docs/PRODUCT_SPEC.md;
2. crie um AGENTS.md curto e operacional;
3. não copie toda a especificação para AGENTS.md.

O AGENTS.md deverá conter apenas instruções permanentes:

- arquitetura e diretórios;
- comandos de instalação;
- comandos de teste, lint e tipagem;
- fluxo de Git;
- política de segurança;
- definição de pronto;
- proibição de commits com secrets;
- regra de implementar somente a fase autorizada.

## Tecnologias preferidas

Utilize:

- Python 3.12;
- asyncio;
- httpx;
- Telethon;
- python-telegram-bot;
- SQLAlchemy 2.x;
- Alembic;
- aiosqlite;
- Pydantic Settings;
- APScheduler;
- Playwright;
- PyYAML;
- BeautifulSoup apenas quando necessário;
- pytest;
- pytest-asyncio;
- ruff;
- mypy.

Evite dependências desnecessárias.

Use uv se estiver disponível. Caso contrário, use venv e pip.

Quando usar uv, inclua uv.lock no Git.

Os comandos canônicos de qualidade deverão ser equivalentes a:

~~~bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
~~~

Se uv não estiver disponível, documente os comandos equivalentes usando o ambiente virtual.

## Configuração

Crie .env.example contendo apenas placeholders:

~~~env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_BOT_TOKEN=
TELEGRAM_TARGET_CHAT_ID=

SHOPEE_APP_ID=
SHOPEE_SECRET=

ALIEXPRESS_APP_KEY=
ALIEXPRESS_APP_SECRET=
ALIEXPRESS_TRACKING_ID=

AMAZON_CREDENTIAL_ID=
AMAZON_CREDENTIAL_SECRET=
AMAZON_ASSOCIATE_TAG=

MERCADOLIVRE_AFFILIATE_MODE=disabled

AWIN_API_TOKEN=
AWIN_PUBLISHER_ID=
AWIN_KABUM_ADVERTISER_ID=

DRY_RUN=true
SEARCH_ENABLED=false
PUBLISH_WITHOUT_AFFILIATE=false
COUPON_BROWSER_VERIFICATION=false
MAX_PROMOTIONS_PER_HOUR=10
~~~

O arquivo .env real nunca deverá entrar no Git.

Crie config.example.yaml com:

- canais de origem;
- categorias;
- palavras-chave;
- blacklist;
- desconto mínimo;
- score mínimo;
- cooldown;
- quantidade máxima por hora;
- intervalo de busca por loja;
- providers ativados;
- modos de afiliado;
- templates;
- divulgação de afiliado;
- preço máximo opcional;
- vendedores permitidos;
- vendedores bloqueados;
- timezone de apresentação;
- limites de requisição por provider.

## Interface de linha de comando

Implemente progressivamente:

- promo-bot doctor: verifica Python, configuração, banco, Telegram e providers;
- promo-bot init-db: cria ou atualiza o banco;
- promo-bot validate-config: valida .env e YAML sem revelar secrets;
- promo-bot send-test: envia mensagem de teste;
- promo-bot listen: inicia somente o relay;
- promo-bot scan --store LOJA: executa busca manual;
- promo-bot run: inicia listener e agendador;
- promo-bot deals: lista últimas ofertas;
- promo-bot coupons: mostra estado dos cupons;
- promo-bot providers: mostra capacidades e estado dos providers.

Utilize argparse inicialmente, salvo justificativa para outra biblioteca.

Comandos de fases futuras poderão existir como stubs explícitos. Eles deverão retornar “não implementado nesta fase” e nunca simular sucesso.

## Segurança obrigatória

- nunca grave tokens no código;
- nunca copie credenciais de repositórios públicos;
- nunca inclua .env, sessões, perfis de navegador, bancos, logs ou cookies no Git;
- valide domínios antes de acessar links recebidos pelo Telegram;
- implemente allowlist de lojas;
- proteja contra SSRF ao expandir URLs;
- valide cada redirecionamento, não apenas a URL inicial;
- bloqueie localhost, IPs privados, link-local, loopback, metadados de cloud e esquemas diferentes de HTTP/HTTPS;
- revalide o destino após resolução DNS;
- defina timeout em todas as requisições;
- use retry limitado com backoff;
- aplique rate limit por provider;
- não execute arquivos recebidos;
- não baixe conteúdo arbitrário;
- valide tipo e tamanho de imagens;
- não implemente bypass de CAPTCHA;
- não automatize checkout;
- não faça brute force de cupom;
- não envie spam;
- não registre secrets;
- sanitize URLs antes de registrá-las;
- crie modo DRY_RUN;
- crie chave geral para pausar buscas;
- limite promoções por hora.

Perfis de navegador, sessões e dados de runtime deverão ficar fora do repositório, em diretório configurável com pathlib.

## Estratégia de testes

Separe os testes:

- unit: sem rede, Telegram ou navegador;
- integration: usando mocks ou servidores locais;
- live: acessam serviços reais e ficam desativados por padrão;
- browser: utilizam Playwright e ficam desativados por padrão.

O comando padrão de testes não deverá:

- acessar lojas reais;
- enviar mensagens reais;
- modificar carrinhos;
- exigir credenciais;
- consumir APIs pagas;
- depender da internet.

Use fixtures sanitizadas para mensagens e respostas de APIs.

Registre markers:

- integration;
- live;
- browser.

Nunca execute testes live ou browser automaticamente antes de um commit sem minha autorização ou configuração explícita.

## Compatibilidade com Windows

O projeto deverá rodar inicialmente no Windows.

Crie:

- run.ps1;
- instruções para instalar Python e uv;
- instruções para criar ambiente virtual;
- instruções para instalar navegadores do Playwright quando essa fase chegar;
- instruções para execução manual;
- instruções opcionais para o Agendador de Tarefas do Windows.

Não presuma caminhos de Linux como /root ou /usr/bin.

Use pathlib para caminhos.

## Observabilidade

Implemente logs estruturados com:

- loja;
- produto;
- ID da mensagem;
- etapa;
- duração;
- resultado;
- código de erro;
- erro resumido.

Nunca coloque tokens, cookies, dados de sessão ou URLs com credenciais nos logs.

Crie comandos ou relatórios para consultar:

- promoções detectadas;
- promoções enviadas;
- promoções descartadas;
- cupons testados;
- erros por provider;
- última execução de cada job.

## Repositórios de referência

Esses projetos podem ser estudados, mas não devem ser copiados sem revisão de licença, segurança e compatibilidade.

### BlueBot

https://github.com/SaulloGabryel/BlueBot

Conceitos relevantes:

- monitoramento de canais;
- substituição de links.

Problemas que não devem ser reproduzidos:

- caminhos absolutos;
- XPaths frágeis;
- perfil de navegador versionado;
- geração incorreta de afiliado da Shopee;
- ausência de Amazon;
- mistura de Telegram e WhatsApp;
- configuração específica do autor.

### Smart Affiliate Bot

https://github.com/acellesantos/smart-affiliate-bot

Pode servir como referência para histórico, cache e scraping, mas:

- possui arquivo principal grande;
- Shopee aparece como TODO;
- não possui AliExpress;
- não há licença explícita clara.

Não copie código sem licença.

### AliExpress Affiliate Telegram Bot

https://github.com/MiilouDz/Aliexpress-Affiliate-Telegram-Bot

Não use diretamente. O projeto possui credenciais hardcoded no código público.

Prefira bibliotecas permissivas e APIs oficiais.

## Ordem completa de implementação

Não implemente as cinco lojas de uma vez.

### Fase 1 — Fundação

- diagnóstico do repositório;
- estrutura modular;
- pyproject.toml;
- configuração;
- validação de .env e YAML;
- modelos de domínio;
- banco SQLite;
- Alembic;
- logs;
- CLI básica;
- testes unitários;
- .env.example;
- config.example.yaml;
- .gitignore;
- AGENTS.md;
- docs/PRODUCT_SPEC.md;
- README;
- run.ps1;
- modo dry-run.

### Fase 2 — Relay do Telegram

- Telethon lendo canais configurados;
- parser de mensagens;
- extração de links;
- identificação de domínio das cinco lojas;
- normalização segura de URLs;
- deduplicação persistente;
- Telegram Bot enviando ao chat privado;
- botão Abrir oferta;
- link canônico apenas em visualização dry-run;
- oferta sem afiliado registrada como PENDING_AFFILIATE;
- nenhuma publicação pronta sem link afiliado válido.

### Fase 3 — Shopee Brasil

- integração exclusivamente com a Shopee Affiliate Open API Brasil;
- contrato real condicionado à confirmação no Explorer oficial autenticado;
- retomada dos candidatos Shopee persistidos pela Fase 2;
- dados oficiais de produto, preço, variações e disponibilidade;
- geração e comprovação de link curto afiliado oficial;
- máquinas de estado independentes para ingestão, enriquecimento, negócio e entrega;
- publicação real bloqueada por padrão;
- testes unitários completamente offline;
- teste live de consulta e geração de link separado da publicação live.

### Fase 4 — AliExpress

- API oficial;
- dados do produto;
- geração de link;
- template final;
- testes.

### Fase 5 — KaBuM!/Awin

- ofertas;
- vouchers;
- product feed;
- link builder;
- busca independente;
- primeiro protótipo opt-in de teste de cupom pelo Playwright.

### Fase 6 — Mercado Livre

- API MLB;
- pesquisa;
- preço e estoque;
- provider afiliado com modos official_api, manual, browser e disabled.

### Fase 7 — Amazon

- Creators API;
- dados oficiais;
- geração de link;
- regras específicas;
- conteúdo útil e divulgação de afiliado.

### Fase 8 — Descobridor completo

- agendador;
- watchlists;
- histórico;
- ranking;
- comparação;
- cupons;
- alertas independently_discovered.

## Critérios de aceite do MVP

O MVP estará pronto quando:

1. iniciar no Windows;
2. conectar ao Telegram sem secrets no código;
3. monitorar pelo menos um canal configurado;
4. identificar URLs das cinco lojas, mesmo com providers desativados;
5. expandir e normalizar URLs com segurança;
6. registrar mensagens no SQLite;
7. preservar deduplicação após reiniciar;
8. criar copy nova por template;
9. enviar promoção ao Telegram privado apenas quando publicável;
10. funcionar em dry-run;
11. possuir testes para parser, URLs, deduplicação e templates;
12. possuir documentação de instalação;
13. nunca enviar link antigo de afiliado;
14. marcar como PENDING_AFFILIATE quando meu link não puder ser gerado;
15. não inventar preço, estoque, cupom ou capacidade de provider;
16. deixar claro o nível de confiança e as condições do preço.

## Controle de execução das fases

Nesta execução, implemente somente a Fase 3 — Shopee Brasil, até o limite permitido pelo contrato
oficial autenticado. Não implementar assinatura, headers, queries, mutations ou parsing real a
partir de wrappers não oficiais ou suposições. Se o contrato oficial não estiver disponível, concluir
somente contratos internos, estados, persistência, retomada, mocks, testes offline e bloqueios de
publicação, e então parar no gate documental.

Antes de programar, apresente de forma concisa:

1. diagnóstico do workspace;
2. estado do Git e do remote;
3. arquitetura proposta;
4. plano específico da Fase 1;
5. riscos e dependências;
6. arquivos que pretende criar ou modificar.

Depois do diagnóstico, prossiga com a Fase 1 sem esperar nova confirmação, desde que:

- o workspace esteja correto;
- não exista conflito;
- não haja risco destrutivo;
- nenhuma decisão indispensável esteja ausente.

Se existir conflito, diretório incorreto, mudança remota incompatível ou escolha que altere materialmente o projeto, pare e solicite minha decisão.

Não implemente antecipadamente funcionalidades das fases seguintes.

É permitido criar interfaces, enums, tipos, mocks e pontos de extensão mínimos para evitar retrabalho arquitetural. Tudo que não estiver implementado deverá ser identificado explicitamente.

Ao concluir a Fase 1:

1. execute os testes;
2. execute o lint;
3. execute a verificação de tipos;
4. faça uma execução controlada;
5. revise o diff;
6. procure possíveis secrets;
7. faça commits coerentes;
8. faça push;
9. apresente o relatório;
10. pare e aguarde minha autorização explícita para iniciar a Fase 2.

Quando eu autorizar uma nova fase, implemente somente aquela fase e repita esse processo.

## Git, commits e GitHub

Repositório remoto:

https://github.com/Lipe-meira/promo_bot.git

Antes de implementar:

1. verifique se o workspace corresponde ao repositório;
2. execute git status;
3. execute git branch --show-current;
4. execute git remote -v;
5. busque o estado remoto sem sobrescrever alterações;
6. confirme que origin aponta para o repositório correto;
7. trabalhe na branch feat/promo-affiliate-bot-mvp;
8. não desenvolva diretamente na main.

Se a branch já existir remotamente, preserve o histórico e configure tracking corretamente. Se houver divergência ou conflito que exija decisão, pare.

Você está autorizado a fazer commits locais e push da branch de desenvolvimento após cada fase validada.

Antes do primeiro commit, garanta que .gitignore exclua:

- .env;
- arquivos com credenciais;
- tokens;
- cookies;
- sessões do Telethon;
- perfis do Chrome, Brave e Playwright;
- bancos SQLite locais;
- logs;
- caches;
- arquivos temporários;
- ambientes virtuais;
- arquivos do sistema operacional;
- imagens temporárias;
- downloads de produtos;
- screenshots com dados pessoais.

Nunca utilize git add . sem revisar git status.

Antes de cada commit:

1. execute git status;
2. revise git diff;
3. revise o conteúdo staged;
4. confirme que nenhum secret foi incluído;
5. execute os testes relacionados;
6. execute lint;
7. execute mypy;
8. corrija erros;
9. adicione apenas os arquivos relacionados;
10. faça commit descritivo.

Procure secrets tanto nos arquivos rastreados quanto no conteúdo staged. Se Gitleaks estiver disponível, utilize-o. Caso contrário, faça verificação local por padrões de credenciais sem imprimir os valores encontrados.

Durante cada fase, faça commits por marco funcional coerente, sem criar um commit por arquivo.

Para a Fase 1, são aceitáveis:

- chore: initialize python project structure
- feat: add configuration and database foundation
- test: add foundation tests
- docs: add local Windows setup

Faça push somente depois que todos os commits da fase estiverem validados.

Primeiro push:

~~~bash
git push -u origin feat/promo-affiliate-bot-mvp
~~~

Pushes seguintes:

~~~bash
git push
~~~

Ao terminar a fase, informe:

- resumo implementado;
- testes executados;
- resultados;
- lint e tipagem;
- execução controlada;
- arquivos principais alterados;
- mensagens dos commits;
- hashes;
- confirmação do push;
- pendências;
- integrações mockadas ou desativadas.

Regras de segurança do Git:

- nunca use git push --force;
- nunca use git reset --hard;
- nunca apague branches;
- nunca reescreva histórico publicado;
- nunca faça rebase remoto sem autorização;
- nunca use amend depois do push;
- nunca faça merge na main automaticamente;
- nunca crie Pull Request sem solicitação;
- nunca publique release ou tag sem autorização;
- nunca envie credenciais;
- nunca versione sessões ou perfis de navegador.

Se o push falhar:

1. preserve os commits locais;
2. não contorne autenticação;
3. informe o erro exato;
4. mostre o comando manual necessário;
5. continue preservando o trabalho local.

## Skills

Use, quando disponíveis:

- skill playwright para automações de navegador e testes relacionados;
- skill security-best-practices para tokens, sessões, cookies, URLs externas e .env;
- skill define-goal para manter fases e critérios de aceite.

Na Fase 1, priorize security-best-practices e define-goal. Não implemente automação Playwright antecipadamente apenas porque a skill está instalada.

Se alguma skill não estiver disponível, não bloqueie o projeto. Informe a ausência e prossiga com o melhor fluxo seguro.

A instalação das skills não substitui dependências Python como Playwright, Telethon ou os navegadores do Playwright.

## Resultado esperado desta execução

O resultado desta execução deverá ser somente a Fase 1 concluída, testada, documentada, commitada e enviada para a branch feat/promo-affiliate-bot-mvp.

Não avance para a Fase 2 até eu autorizar explicitamente.

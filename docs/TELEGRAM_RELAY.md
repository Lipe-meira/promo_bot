# Configuração e teste controlado do Telegram

## Credenciais locais

Crie `.env` a partir de `.env.example` e edite o arquivo somente no computador local. Nunca cole
tokens, códigos, senha 2FA, número de telefone ou conteúdo da sessão em conversas ou commits.

Variáveis necessárias:

- `TELEGRAM_API_ID` e `TELEGRAM_API_HASH`, obtidos em `my.telegram.org`;
- `TELEGRAM_BOT_TOKEN`, criado pelo BotFather;
- `TELEGRAM_TARGET_CHAT_ID`, correspondente ao chat privado escolhido.

Declare em `config.yaml` somente os canais que a conta deve monitorar:

```yaml
source_channels:
  - canal_configurado
```

O arquivo de sessão fica em
`%USERPROFILE%\.promo_bot\telegram\monitor.session` por padrão. O programa recusa um caminho de
sessão dentro do workspace Git.

## Autenticação inicial

```powershell
uv run promo-bot listen --authorize
```

Telefone, código de login e eventual senha 2FA são solicitados no terminal. Depois que a sessão
externa existir, use:

```powershell
uv run promo-bot listen
```

O monitor lê apenas os canais configurados, não marca mensagens como lidas e não envia, responde,
encaminha, reage, clica em botões ou baixa mídias usando a conta.

## Domínios de rede permitidos

A expansão aceita somente hosts exatos previamente revisados:

- Amazon Brasil: `amazon.com.br`, `www.amazon.com.br` e `amzn.to`;
- Mercado Livre: `mercadolivre.com.br`, `www.mercadolivre.com.br`,
  `produto.mercadolivre.com.br`, `meli.la` e caminhos `/sec/` do domínio oficial;
- Shopee: `shopee.com.br`, `www.shopee.com.br` e `s.shopee.com.br`;
- AliExpress: `aliexpress.com`, `www.aliexpress.com`, `pt.aliexpress.com`,
  `a.aliexpress.com` e `s.click.aliexpress.com`;
- KaBuM!: `kabum.com.br` e `www.kabum.com.br`.

Um redirecionamento intermediário fora dessa lista é recusado. Novos domínios não devem ser liberados
sem revisão explícita do seu controle, resolução DNS e finalidade.

## Catch-up conservador

Os defaults recuperam até 100 mensagens por canal nas últimas seis horas. Ajuste
`catch_up_lookback_hours` e `catch_up_max_messages_per_channel` com limites pequenos. O sistema não
baixa históricos completos.

## Preview offline

```powershell
uv run promo-bot send-test
```

Esse comando não usa Telegram. Ele apenas mostra a mensagem sintética e seu botão `Abrir oferta`.

## Teste live — requer autorização específica

O teste live permanece pendente até o operador autorizar expressamente sua execução. Ele faz duas
ações controladas:

1. lê no máximo uma mensagem do primeiro canal configurado, sem marcar como lida;
2. envia uma única mensagem sintética fixa ao chat privado do bot.

Somente depois da autorização, com `.env`, `config.yaml` e a sessão já configurados:

```powershell
$env:RUN_TELEGRAM_LIVE_TEST = "1"
uv run pytest -m live tests/live/test_telegram_live.py -v
Remove-Item Env:RUN_TELEGRAM_LIVE_TEST
```

Não use `--live` ou a variável de autorização em automações. Nenhum candidato
`PENDING_AFFILIATE` ou `MANUAL_REVIEW` participa desse teste.

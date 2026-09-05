# Configuração e teste controlado do Telegram

## 1. Proteja as credenciais

Crie `.env` a partir de `.env.example` e edite o arquivo somente no computador local. O `.env` e
o arquivo de sessão do Telethon não podem entrar no Git.

Nunca envie tokens, `TELEGRAM_API_HASH`, códigos OTP, senha 2FA, número de telefone ou conteúdo da
sessão ao Codex, ChatGPT, GitHub, issues, pull requests ou qualquer conversa. Se um segredo for
exposto, revogue-o no serviço de origem e gere outro.

## 2. Crie as credenciais da conta Telegram

`TELEGRAM_API_ID` e `TELEGRAM_API_HASH` pertencem à conta de usuário que monitorará os canais:

1. Acesse `https://my.telegram.org` diretamente no navegador e entre com a conta escolhida.
2. Abra **API development tools**.
3. Crie um aplicativo local, preenchendo os campos solicitados pelo Telegram.
4. Copie o **App api_id** para `TELEGRAM_API_ID` e o **App api_hash** para
   `TELEGRAM_API_HASH` no `.env`.

Esses valores são usados pelo Telethon. Eles não são o token do bot.

## 3. Crie e prepare o bot privado

1. No Telegram, abra o BotFather oficial (`@BotFather`) e confira o nome de usuário antes de enviar
   comandos.
2. Envie `/newbot`, escolha o nome e um nome de usuário terminado em `bot`.
3. Salve o token retornado somente em `TELEGRAM_BOT_TOKEN` no `.env`.
4. Abra a conversa privada com o bot recém-criado e pressione **Start** ou envie `/start`.

O bot não consegue iniciar sozinho uma conversa privada. O passo `/start` precisa ocorrer antes do
primeiro envio ou da descoberta do chat.

## 4. Descubra o `TELEGRAM_TARGET_CHAT_ID`

Depois de enviar `/start`, consulte localmente o método `getUpdates` da Telegram Bot API. O exemplo
abaixo lê o token do `.env`, não o imprime e mostra somente os chats presentes nas atualizações:

```powershell
$tokenLine = Get-Content .env | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=' } | Select-Object -First 1
$botToken = $tokenLine.Substring('TELEGRAM_BOT_TOKEN='.Length).Trim()
$updates = Invoke-RestMethod -Method Get -Uri ("https://api.telegram.org/bot{0}/getUpdates" -f $botToken)
$updates.result | ForEach-Object { $_.message.chat } | Where-Object { $_ } | Select-Object id, type, username
Remove-Variable botToken, tokenLine, updates
```

Use o `id` da conversa cujo `type` seja `private` como `TELEGRAM_TARGET_CHAT_ID` no `.env`. Se a
lista estiver vazia, envie outra mensagem ao bot e repita localmente. Não cole a resposta, o token
ou a URL completa em conversas ou relatórios.

## 5. Configure os canais de origem

Declare em `config.yaml` somente os canais que a conta de usuário deve monitorar. A conta precisa
ter acesso a cada canal. Use o nome público sem URL ou o identificador numérico do canal:

```yaml
source_channels:
  - canal_publico_sem_arroba
  - -1001234567890
```

Não inclua o chat privado de destino nessa lista. O Telethon observa apenas `source_channels`; a
Telegram Bot API é usada separadamente para o chat privado.

`source_channels` é a chave YAML exata, de nível superior, validada como uma sequência de strings.
Itens numéricos escritos pelo YAML também são normalizados para string. São aceitas estas formas:

```yaml
source_channels:
  - -1001234567890
  - canal_publico
  - "@outro_canal_publico"
```

O shadow mode nunca aceita um canal arbitrário fornecido apenas pela linha de comando. Um ID
numérico precisa constar na lista ou corresponder, depois da resolução pela API, a um username que
já conste nela. Um username público recebido por link só é resolvido se esse username estiver
textualmente na allowlist, desconsiderando `@` e diferenças entre maiúsculas e minúsculas.

### Obter a referência de uma mensagem

Use **Copiar link da mensagem** no aplicativo Telegram. O parser local aceita estritamente:

- canal ou supergrupo privado: `https://t.me/c/1234567890/77`, convertido localmente em
  `chat_id=-1001234567890` e `message_id=77`;
- canal público: `https://t.me/canal_publico/77`, convertido em `username=canal_publico` e
  `message_id=77`.

O parser não abre o link nem faz requisição. Query, fragmento, convite, link de preview `/s/` e
outros formatos são recusados. Em canais privados, a conta Telethon precisa ser membro e o
`chat_id` completo com prefixo `-100` deve estar em `source_channels`. Em canais públicos, a conta
precisa conseguir ler o canal e o username precisa estar na allowlist antes de qualquer resolução.

## 6. Faça a autorização inicial do Telethon

O arquivo de sessão fica em `%USERPROFILE%\.promo_bot\telegram\monitor.session` por padrão. O
programa recusa caminhos de sessão dentro do workspace Git.

Para criar ou recuperar somente a sessão, sem iniciar listener, relay ou Bot API, use o comando
explícito:

```powershell
uv run --env-file .env promo-bot telegram authorize-session
```

Ele conecta a conta de usuário, solicita telefone, OTP e 2FA no próprio terminal quando necessário,
grava `monitor.session` no runtime externo e desconecta. Não lê mensagens, não marca como lida e não
envia, edita ou encaminha mensagens. `TELEGRAM_BOT_TOKEN` e `TELEGRAM_TARGET_CHAT_ID` não são
necessários. Este comando não deve ser executado por automação.

Com `.env` e `config.yaml` prontos, execute localmente:

```powershell
uv run promo-bot listen --authorize
```

Informe no próprio terminal o telefone, o código OTP recebido e, se habilitada, a senha 2FA. Não
copie esses dados para o Codex, ChatGPT ou qualquer registro. Depois que a sessão externa existir,
inicie normalmente com:

```powershell
uv run promo-bot listen
```

O monitor lê apenas os canais configurados, não marca mensagens como lidas e não envia, responde,
encaminha, reage, clica em botões ou baixa mídias usando a conta de usuário.

## Domínios e redirecionamentos permitidos

A expansão aceita somente hosts exatos previamente revisados:

- Amazon Brasil: `amazon.com.br`, `www.amazon.com.br` e `amzn.to`;
- Mercado Livre: `mercadolivre.com.br`, `www.mercadolivre.com.br`,
  `produto.mercadolivre.com.br`, `meli.la` e caminhos `/sec/` do domínio oficial;
- Shopee: `shopee.com.br`, `www.shopee.com.br` e `s.shopee.com.br`;
- AliExpress: `aliexpress.com`, `www.aliexpress.com`, `pt.aliexpress.com`,
  `a.aliexpress.com` e `s.click.aliexpress.com`;
- KaBuM!: `kabum.com.br` e `www.kabum.com.br`.

Cada salto é validado. Redirecionamentos para fora da lista ou de HTTPS para HTTP são recusados.
Novos domínios não devem ser liberados sem revisão explícita do seu controle, resolução DNS e
finalidade.

## Catch-up conservador

Os defaults recuperam até 100 mensagens por canal nas últimas seis horas. Ajuste
`catch_up_lookback_hours` e `catch_up_max_messages_per_channel` com limites pequenos. O sistema não
baixa históricos completos.

## Preview offline

```powershell
uv run promo-bot send-test
```

Sem `--live`, esse comando não usa Telegram. Ele apenas mostra a mensagem sintética e o botão
`Abrir oferta`.

## Teste live — requer autorização específica

O teste live permanece pendente até o operador autorizar expressamente sua execução. Ele faz duas
ações controladas:

1. lê no máximo uma mensagem do primeiro canal configurado, sem marcar como lida;
2. envia uma única mensagem sintética fixa ao chat privado do bot.

Somente depois da autorização específica, com `.env`, `config.yaml` e a sessão já configurados:

```powershell
$env:RUN_TELEGRAM_LIVE_TEST = "1"
uv run pytest -m live tests/live/test_telegram_live.py -v
Remove-Item Env:RUN_TELEGRAM_LIVE_TEST
```

Não use `--live` ou a variável de autorização em automações. Nenhum candidato
`PENDING_AFFILIATE` ou `MANUAL_REVIEW` participa desse teste.

# Arquitetura da fundação

## Princípios

- Domínio independente de SQLAlchemy, CLI e integrações externas.
- Configuração secreta somente por ambiente; comportamento não secreto por YAML validado.
- Valores monetários com `Decimal` e moeda explícita.
- Tempo armazenado em UTC e convertido apenas na apresentação.
- SQLite evoluído exclusivamente por migrações Alembic, sem recriação destrutiva automática.
- Operações externas desativadas por padrão e resultados futuros sempre explícitos.

## Dependências entre módulos

```text
CLI
├── config ── YAML + ambiente
├── database ── SQLAlchemy + Alembic ── SQLite
└── observability ── logging JSON sanitizado

domain ── sem dependências de infraestrutura
```

`src/promo_bot/domain` contém valores e invariantes. `config` valida os dois canais de
configuração. `database` mapeia as entidades persistidas e oferece sessões/repositórios pequenos.
`observability` impede que campos comuns de credenciais e URLs sensíveis apareçam nos logs.

## Banco de dados

A migração inicial cria:

- `source_messages`;
- `products`;
- `deals`;
- `price_history`;
- `coupons`;
- `processed_items`.

Constraints e índices cobrem identidade externa do produto, identidade da mensagem, hashes de
oferta, estados e consultas temporais. SQLite armazena timestamps como UTC; o tipo `UTCDateTime`
restaura objetos timezone-aware. Colunas monetárias usam `NUMERIC`, nunca `FLOAT`.

## Limite da Fase 1

Não há módulos de Telegram, providers, URL expansion, cupons ativos, ranking, formatação de ofertas,
navegador ou scheduler. Os estados e campos persistidos formam pontos de extensão, mas nenhuma
capacidade posterior é anunciada como funcional.

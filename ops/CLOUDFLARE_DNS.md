# Cloudflare DNS Para `bot.codifica.tec.br`

## Objetivo
Apontar o subdominio `bot.codifica.tec.br` para a VPS `204.168.139.205`.

## Regra recomendada para o primeiro deploy
Crie o registro como **DNS only** primeiro.

Motivo:
- o Caddy na VPS precisa responder diretamente para emitir o certificado do dominio;
- a documentacao da Cloudflare recomenda iniciar em `DNS only` ao fazer onboarding/validacao inicial e so depois mover para `Proxied` quando o hostname estiver validado.

## Registro DNS
No painel da Cloudflare:
1. Abra a zona `codifica.tec.br`
2. Entre em **DNS**
3. Clique em **Add record**
4. Crie:

- Type: `A`
- Name: `bot`
- IPv4 address: `204.168.139.205`
- Proxy status: `DNS only`
- TTL: `Auto`

Opcional, se quiser IPv6:
- Type: `AAAA`
- Name: `bot`
- IPv6 address: `2a01:4f9:c014:ef48::1`
- Proxy status: `DNS only`
- TTL: `Auto`

## Depois do primeiro deploy
Quando o site estiver respondendo em:
- `https://bot.codifica.tec.br/`
- `https://bot.codifica.tec.br/api/healthz`

voce pode decidir entre:

### Opcao 1: manter `DNS only`
Use isso se quiser conexao direta ao origin e menor interferencia da Cloudflare.

### Opcao 2: mudar para `Proxied`
Use isso se quiser esconder melhor o IP de origem e usar protecoes da Cloudflare.

Se mudar para `Proxied`:
1. Edite o registro `bot`
2. Troque a nuvem cinza para laranja
3. Em **SSL/TLS** da Cloudflare, use **Full (strict)**

Como o Caddy emitira um certificado publico valido na origem, `Full (strict)` e o modo certo.

## Observacoes
- nao use `Flexible`
- nao exponha `postgres` nem `redis`
- para deploy inicial, validar primeiro com `DNS only` simplifica o processo

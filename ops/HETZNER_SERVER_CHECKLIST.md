# Checklist Da VPS Hetzner

## Provisionamento
- Ubuntu 22.04 LTS ou 24.04 LTS
- 2 vCPU / 4 GB RAM minimo para `postgres + redis + api + agents + dashboard + caddy`
- Volume local suficiente para imagens Docker e banco
- snapshot da VPS habilitado se fizer parte da sua rotina

## Rede
- IP publico fixo da VPS: `204.168.139.205`
- dominio opcional no primeiro deploy; inicialmente pode operar por IP
- portas liberadas:
  - `22/tcp`
  - `80/tcp`
  - `443/tcp`
- nao expor `5432` nem `6379` publicamente

## Acesso
- usuario administrativo sem usar `root` para deploy diario
- chave SSH instalada
- login por senha desabilitado depois do bootstrap

## Host
- horario/NTP corretos
- swap configurada se a VPS tiver pouca RAM
- `docker` e `docker compose plugin` instalados
- firewall ativo

## Repositorio
- repo GitHub: `https://github.com/ruan332/bot_polymarket`
- branch de deploy definida (`main` ou release tag)
- secrets do GitHub Actions configurados se usar deploy automatizado:
  - `VPS_HOST`
  - `VPS_USER`
  - `VPS_SSH_KEY`

## Ambiente
- copiar `.env.production.example` para `.env`
- preencher segredos reais
- iniciar com:
  - `SMOKE_TEST_MODE=true`
  - `LIVE_TRADING=false`

## Pos-deploy
- no primeiro deploy por IP:
  - `http://204.168.139.205/` abre dashboard
  - `http://204.168.139.205/api/healthz` responde `{"status":"ok"}`
- depois, quando houver dominio apontado:
  - `https://SEU_DOMINIO/`
  - `https://SEU_DOMINIO/api/healthz`
- `docker compose -f docker-compose.prod.yml ps` sem containers reiniciando
- logs de `api` e `agents` sem erro continuo

## Antes de live trading
- `SMOKE_TEST_MODE=false`
- validacao real de leitura Polymarket
- credenciais L2 do CLOB validadas
- `POLYMARKET_FUNDER` e `POLYMARKET_SIGNATURE_TYPE` confirmados
- backup do banco testado

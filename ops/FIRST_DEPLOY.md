# Primeiro Deploy

## 1. Preparar a VPS
```bash
sudo mkdir -p /opt/polymarket-bot
sudo chown "$USER":"$USER" /opt/polymarket-bot
git clone https://github.com/ruan332/bot_polymarket /opt/polymarket-bot
cd /opt/polymarket-bot
chmod +x scripts/bootstrap-hetzner-ubuntu.sh
./scripts/bootstrap-hetzner-ubuntu.sh
```

Saia e entre novamente no SSH para aplicar o grupo `docker`.

## 2. Preparar o ambiente
```bash
cd /opt/polymarket-bot
cp .env.production.example .env
nano .env
```

Preencha no minimo:
- `DOMAIN=bot.codifica.tec.br`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- chaves de IA
- `LIVE_TRADING=false`
- `SMOKE_TEST_MODE=false`
- `DATABASE_URL=postgresql://trading:<senha>@postgres:5432/trading_prod`

## 3. Subir a stack
```bash
cd /opt/polymarket-bot
chmod +x scripts/deploy-vps.sh
APP_DIR=/opt/polymarket-bot BRANCH=main ./scripts/deploy-vps.sh
```

## 4. Verificar
```bash
chmod +x scripts/post-deploy-check.sh
DOMAIN=bot.codifica.tec.br ./scripts/post-deploy-check.sh
```

## 5. Logs uteis
```bash
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f agents
docker compose -f docker-compose.prod.yml logs -f caddy
```

## 6. Ativacao segura
1. `SMOKE_TEST_MODE=true`, `LIVE_TRADING=false`
2. Confirmar API, dashboard e agentes
3. `SMOKE_TEST_MODE=false`, `LIVE_TRADING=false`
4. Confirmar leitura real de mercados
5. So depois considerar `LIVE_TRADING=true`

## 6. Separar paper e prod na mesma VPS

Quando quiser preservar o historico paper e iniciar live limpo:

```bash
cd /opt/polymarket-bot
FUNDER_ADDRESS=0x64d1C8A99308ca35f1B4F34e009B01F8165E1f96 bash scripts/migrate-live-env.sh
APP_ENV_FILE=.env bash scripts/deploy-vps.sh
DOMAIN=bot.codifica.tec.br bash scripts/post-deploy-check.sh
```

O script:
- faz backup do banco atual
- clona o banco atual para `trading_paper`
- cria `trading_prod` vazio com o schema da aplicacao
- gera `.env.paper`
- atualiza `.env` principal para live conservador com Redis separado

## 7. Quando adicionar dominio
Neste projeto, use `bot.codifica.tec.br` desde o inicio.

Antes do deploy:
1. configure o DNS na Cloudflare conforme `ops/CLOUDFLARE_DNS.md`
2. use `DNS only` no primeiro deploy
3. depois que o Caddy emitir certificado e a origem responder corretamente, opcionalmente mude para `Proxied`

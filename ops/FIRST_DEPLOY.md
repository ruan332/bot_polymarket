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
- `DOMAIN=204.168.139.205`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- chaves de IA
- `LIVE_TRADING=false`
- `SMOKE_TEST_MODE=true`

## 3. Subir a stack
```bash
cd /opt/polymarket-bot
chmod +x scripts/deploy-vps.sh
APP_DIR=/opt/polymarket-bot BRANCH=main ./scripts/deploy-vps.sh
```

## 4. Verificar
```bash
chmod +x scripts/post-deploy-check.sh
BASE_URL=http://204.168.139.205 ./scripts/post-deploy-check.sh
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

## 7. Quando adicionar dominio
Depois que um dominio estiver apontando para `204.168.139.205`:
- troque `DOMAIN` no `.env`
- rode novo deploy
- passe a validar via `https://SEU_DOMINIO`

# Deploy Na Hetzner

## Recomendacao
Use **GitHub + SSH + Docker Compose** como fluxo principal de deploy.

Portainer pode ser instalado depois como painel de observabilidade e operacao, mas **nao** como fonte de verdade de configuracao. O motivo e manutencao:
- codigo e compose versionados no GitHub;
- `.env` fica somente na VPS;
- deploy reproduzivel com `git pull` + `docker compose`;
- rollback simples por commit/tag;
- nenhuma alteracao manual no Portainer fica "fora" do Git.

## Por que nao usar Portainer como caminho principal
- O fluxo de manutencao fica mais fraco quando alguem edita stack pela UI.
- Mesmo com stack vinda de Git, o controle operacional acaba dividido entre repo e painel.
- Para este projeto, que tem agentes, API, dashboard, segredos e flags de trading, o melhor e manter **um unico source of truth**.

## Arquivos de producao
- `docker-compose.prod.yml`
- `ops/Caddyfile`
- `scripts/bootstrap-hetzner-ubuntu.sh`
- `scripts/deploy-vps.sh`
- `scripts/backup-postgres.sh`
- `scripts/post-deploy-check.sh`
- `.env.production.example`
- `ops/HETZNER_SERVER_CHECKLIST.md`
- `ops/FIRST_DEPLOY.md`

## Topologia recomendada
- 1 VPS Hetzner Ubuntu 22.04 ou 24.04
- Docker + Docker Compose Plugin
- Containers:
  - `postgres`
  - `redis`
  - `api`
  - `agents`
  - `dashboard`
  - `caddy`

## Passo a passo
1. Criar a VPS na Hetzner.
2. Apontar o dominio para o IP da VPS.
3. Acessar via SSH.
4. Rodar:

```bash
chmod +x scripts/bootstrap-hetzner-ubuntu.sh
./scripts/bootstrap-hetzner-ubuntu.sh
```

5. Clonar o repositorio:

```bash
sudo mkdir -p /opt/polymarket-bot
sudo chown "$USER":"$USER" /opt/polymarket-bot
git clone <SEU_REPO_GITHUB> /opt/polymarket-bot
cd /opt/polymarket-bot
```

6. Criar `.env` de producao a partir de `.env.example`.
7. Definir no `.env`:
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `DATABASE_URL`
- `REDIS_URL`
- `DOMAIN`
- `LIVE_TRADING=false` inicialmente
- `SMOKE_TEST_MODE=false` para leitura real do Polymarket
- se for live:
  - `POLYMARKET_PRIVATE_KEY`
  - `POLYMARKET_FUNDER`
  - `POLYMARKET_SIGNATURE_TYPE`
  - opcionalmente `POLYMARKET_API_KEY`
  - `POLYMARKET_API_SECRET`
  - `POLYMARKET_API_PASSPHRASE`

8. Subir:

```bash
chmod +x scripts/deploy-vps.sh
APP_DIR=/opt/polymarket-bot BRANCH=main ./scripts/deploy-vps.sh
```

## Estrategia de manutencao
- `main` ou tags de release como referencia de deploy.
- Toda mudanca entra por commit no GitHub.
- Deploy via SSH manual ou GitHub Actions chamando `scripts/deploy-vps.sh`.
- Nunca editar stack diretamente no host sem refletir no Git.

## Rollback
```bash
cd /opt/polymarket-bot
git checkout <tag-ou-commit>
docker compose -f docker-compose.prod.yml up -d --build
```

## Backup
Use `scripts/backup-postgres.sh` para gerar `pg_dump` periodico.

Exemplo:
```bash
chmod +x scripts/backup-postgres.sh
APP_DIR=/opt/polymarket-bot ./scripts/backup-postgres.sh
```

## Ordem recomendada de ativacao
1. `SMOKE_TEST_MODE=true`, `LIVE_TRADING=false`
2. `SMOKE_TEST_MODE=false`, `LIVE_TRADING=false`
3. Validar mercado real em leitura
4. So depois `LIVE_TRADING=true`

## Observacoes Hetzner
- Se voce usar **Volumes** separados da Hetzner para dados, backups/snapshots do servidor **nao** incluem esses Volumes.
- Se quiser usar Volume separado para banco, precisa de estrategia de backup propria adicional.

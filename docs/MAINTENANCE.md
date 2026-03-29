# Manutencao Operacional

Guia rapido para deploy, operacao, diagnostico e manutencao do `bot_polymarket`.

Este documento foi escrito para uso pratico em manutencao do ambiente local e da VPS de producao.

## Visao Geral

O projeto roda em `paper trading` ou `live trading` com esta topologia:

- `postgres`: persistencia principal
- `redis`: streams, heartbeats e runtime overrides
- `api`: FastAPI com endpoints de operacao e metrics
- `agents`: scanner, reviewer e executor
- `dashboard`: painel web
- `caddy`: proxy reverso e TLS

## Caminhos Importantes

- Projeto local: [C:\Projetos\bot_polymarket](C:\Projetos\bot_polymarket)
- Config de agentes: [C:\Projetos\bot_polymarket\config\agents.yaml](C:\Projetos\bot_polymarket\config\agents.yaml)
- Config de risco: [C:\Projetos\bot_polymarket\config\risk.yaml](C:\Projetos\bot_polymarket\config\risk.yaml)
- Config de cripto: [C:\Projetos\bot_polymarket\config\crypto.yaml](C:\Projetos\bot_polymarket\config\crypto.yaml)
- Compose de producao: [C:\Projetos\bot_polymarket\docker-compose.prod.yml](C:\Projetos\bot_polymarket\docker-compose.prod.yml)
- Dockerfile de producao: [C:\Projetos\bot_polymarket\Dockerfile.prod](C:\Projetos\bot_polymarket\Dockerfile.prod)
- Script de deploy VPS: [C:\Projetos\bot_polymarket\scripts\deploy-vps.sh](C:\Projetos\bot_polymarket\scripts\deploy-vps.sh)
- Script de validacao pos-deploy: [C:\Projetos\bot_polymarket\scripts\post-deploy-check.sh](C:\Projetos\bot_polymarket\scripts\post-deploy-check.sh)
- Script de replay: [C:\Projetos\bot_polymarket\scripts\replay_history.py](C:\Projetos\bot_polymarket\scripts\replay_history.py)
- Caddyfile: [C:\Projetos\bot_polymarket\ops\Caddyfile](C:\Projetos\bot_polymarket\ops\Caddyfile)

Na VPS, o diretorio esperado e:

- `/opt/polymarket-bot`

## Modos de Operacao

### Smoke test

Usado para validacao basica do pipeline sem depender do fluxo real.

```env
SMOKE_TEST_MODE=true
LIVE_TRADING=false
NEWS_VALIDATION_ENABLED=true
```

### Paper trading com mercado real

Modo recomendado para teste operacional.

```env
SMOKE_TEST_MODE=false
LIVE_TRADING=false
NEWS_VALIDATION_ENABLED=true
```

Requisitos:

- `OPENAI_API_KEY` preenchida se agentes estiverem em OpenAI
- `ANTHROPIC_API_KEY` preenchida se algum agente usar Anthropic
- `MARKETAUX_API_KEY` preenchida para a fonte principal de noticias
- `ALPHAVANTAGE_API_KEY` recomendada como fallback automatico quando a primaria bater limite ou falhar

### Live trading

So habilitar depois de validar `paper trading`.

```env
SMOKE_TEST_MODE=false
LIVE_TRADING=true
NEWS_VALIDATION_ENABLED=true
```

Requisitos adicionais:

- `POLYMARKET_PRIVATE_KEY`
- `POLYMARKET_API_KEY`
- `POLYMARKET_API_SECRET`
- `POLYMARKET_API_PASSPHRASE`
- `POLYMARKET_FUNDER`

## Variaveis Criticas do .env

Base recomendada:

```env
ENVIRONMENT=production
DOMAIN=bot.codifica.tec.br

POSTGRES_DB=trading
POSTGRES_USER=trading
POSTGRES_PASSWORD=troque_esta_senha
DATABASE_URL=postgresql://trading:troque_esta_senha@postgres:5432/trading

REDIS_URL=redis://redis:6379/0

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
NEWS_VALIDATION_ENABLED=true
NEWS_PROVIDER_PRIMARY=marketaux
NEWS_PROVIDER_FALLBACK=alphavantage
NEWS_LOOKBACK_HOURS=24
NEWS_HTTP_TIMEOUT_SECONDS=15
NEWS_FALLBACK_ON_QUOTA=true
NEWS_FALLBACK_ON_RATE_LIMIT=true
NEWS_FALLBACK_ON_UPSTREAM_ERROR=true
NEWS_FALLBACK_ON_EMPTY_RESULT=false
MARKETAUX_API_KEY=
MARKETAUX_BASE_URL=https://api.marketaux.com/v1/news/all
MARKETAUX_LANGUAGE=en
MARKETAUX_LIMIT_PER_REQUEST=3
ALPHAVANTAGE_API_KEY=
ALPHAVANTAGE_BASE_URL=https://www.alphavantage.co/query
ALPHAVANTAGE_NEWS_LIMIT=50

LIVE_TRADING=false
SMOKE_TEST_MODE=false

MAX_DAILY_SPEND_USD=25.00
MAX_SINGLE_POSITION_USD=100.00
PAPER_BANKROLL_USD=1000.00
AGENT_HEARTBEAT_TTL_SECONDS=45
```

Observacao:

- `MAX_DAILY_SPEND_USD` continua sendo um limite operacional de gasto para o fluxo de trading/risk;
- `MAX_DAILY_SPEND_USD=0` desabilita esse teto e libera operacoes sem limite diario;
- ele nao deve ser interpretado como bloqueio do reviewer/LLM;
- o custo de LLM que voce acompanha no dashboard vem de `llm_calls` e do campo `llm_cost_usd` nas metricas.

Arquivo base de exemplo:

- [.env.production.example](C:\Projetos\bot_polymarket\.env.production.example)

Para gerar `.env` inicial:

```bash
./scripts/prepare-prod-env.sh
```

Notas operacionais importantes:

- o `.env` real da VPS fica fora do Git e continua preservado em `git pull`
- atualizar `.env.production.example` nao altera o `.env` real ja existente
- `scripts/prepare-prod-env.sh` nao sobrescreve `.env` existente
- `scripts/deploy-vps.sh` agora aborta se `.env` estiver ausente, para evitar subir o stack sem configuracao valida

Toggle de noticias:

- `NEWS_VALIDATION_ENABLED=true`: fluxo completo `claude -> news_validator -> codex -> claw`
- `NEWS_VALIDATION_ENABLED=false`: bypass da etapa de noticias e fluxo `claude -> codex -> claw`
- com o toggle desligado, o agente `news_validator` nao sobe e `agents/status` nao deve listar esse agente

Toggle de momentum:

- `MOMENTUM_ENABLED=true`: habilita a estrategia direcional `momentum_15m`
- `MOMENTUM_MARKETS=BTC,ETH`: define os ativos 15m monitorados pelo motor de momentum
- `MOMENTUM_SIGNAL_CONFIDENCE_THRESHOLD`: filtro minimo de confianca para publicacao do sinal
- `MOMENTUM_COOLDOWN_MINUTES`: evita republicacao imediata de sinais equivalentes

## Configuracoes de Estrategia

### Agentes

Arquivo:

- [config/agents.yaml](C:\Projetos\bot_polymarket\config\agents.yaml)

Campos mais importantes:

- `model`
- `provider`
- `fallback_model`
- `daily_cost_limit_usd`

Agentes esperados em runtime:

- `claude`: scanner cripto
- `news_validator`: valida contexto de noticias quando `NEWS_VALIDATION_ENABLED=true`
- `codex`: revisao operacional
- `claw`: executor paper/live

### Cripto

Arquivo:

- [config/crypto.yaml](C:\Projetos\bot_polymarket\config\crypto.yaml)

Parametros mais sensiveis:

- `enabled`
- `direct_coin_only`
- `major_assets`
- `scan_priority`
- `btc.*`
- `major.*`
- `small_cap.*`

Escopo operacional atual:

- com `enabled=true` e `direct_coin_only=true`, o scanner tenta operar apenas mercados de cripto direta
- posicoes antigas sem `asset_symbol` ou `crypto_tier` no dashboard sao legado do banco e podem continuar visiveis ate serem encerradas ou limpas

### Risco

Arquivo:

- [config/risk.yaml](C:\Projetos\bot_polymarket\config\risk.yaml)

Parametros mais sensiveis:

- `min_edge`
- `min_confidence`
- `max_single_position_usd`
- `max_total_exposure_usd`
- `max_open_positions`
- `max_spread_bps`
- `max_slippage_bps`
- `max_order_price`
- `min_market_volume_24h`

## Rotina de Deploy na VPS

### Deploy padrao

```bash
cd /opt/polymarket-bot
bash scripts/deploy-vps.sh
```

O script:

- faz `git fetch`
- faz `git checkout` da branch
- faz `git pull --ff-only`
- exige que o arquivo `.env` ja exista no host e nao recria esse arquivo
- executa `docker compose -f docker-compose.prod.yml build --pull`
- executa `docker compose -f docker-compose.prod.yml up -d`

### Validacao pos-deploy

```bash
cd /opt/polymarket-bot
DOMAIN=bot.codifica.tec.br bash scripts/post-deploy-check.sh
```

### Validacao manual

```bash
cd /opt/polymarket-bot
docker compose -f docker-compose.prod.yml ps
curl -s https://bot.codifica.tec.br/api/healthz
curl -s https://bot.codifica.tec.br/api/agents/status
curl -s https://bot.codifica.tec.br/api/metrics/overview
curl -s https://bot.codifica.tec.br/api/metrics/performance?hours=24
```

## Comandos de Operacao

### Ver status dos containers

```bash
docker compose -f docker-compose.prod.yml ps
```

### Logs

```bash
docker compose -f docker-compose.prod.yml logs --tail=200 api
docker compose -f docker-compose.prod.yml logs --tail=200 agents
docker compose -f docker-compose.prod.yml logs --tail=200 dashboard
docker compose -f docker-compose.prod.yml logs --tail=200 caddy
```

### Reiniciar servico especifico

```bash
docker compose -f docker-compose.prod.yml restart api
docker compose -f docker-compose.prod.yml restart agents
docker compose -f docker-compose.prod.yml restart dashboard
docker compose -f docker-compose.prod.yml restart caddy
```

### Rebuild forcado

```bash
docker compose -f docker-compose.prod.yml build --no-cache api agents dashboard
docker compose -f docker-compose.prod.yml up -d api agents dashboard
```

## Endpoints Operacionais

### Saude e status

- `GET /api/healthz`
- `GET /api/agents/status`

### Observabilidade

- `GET /api/metrics/overview`
- `GET /api/metrics/performance?hours=24`
- `GET /api/costs/daily`
- `GET /api/risk-events/recent`

### Fluxo de trading

- `GET /api/signals/recent`
- `GET /api/decisions/recent`
- `GET /api/orders/recent`

### Portfolio

- `GET /api/portfolio/summary`
- `GET /api/portfolio/positions`
- `GET /api/portfolio/equity-history?limit=100`

Observacao importante:

- snapshots em `.tmp/api-snap` podem vir de execucoes de validacao/smoke e nao representam o estado atual da VPS;
- para diagnostico da producao, prefira sempre os endpoints ao vivo acima;
- se houver divergencia entre um snapshot local e o dashboard, valide primeiro a data de captura do snapshot.

## Replay e Backtest Observado

O projeto possui replay historico sobre snapshots persistidos pelo proprio bot. Nao e um backtest externo completo do Polymarket; e um replay da operacao observada.

### Rodar replay das ultimas 24h

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/replay_history.py --hours 24
```

### Exportar JSON e CSV

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/replay_history.py --hours 24 --export-json /tmp/replay.json --export-csv /tmp/replay.csv
```

### Replay por janela

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/replay_history.py --start 2026-03-18T00:00:00Z --end 2026-03-18T23:59:59Z
```

### Replay por mercado

```bash
docker compose -f docker-compose.prod.yml exec api python scripts/replay_history.py --market-id 540817 --hours 24
```

## Consultas Uteis no Postgres

### Contadores basicos

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select count(*) as signals from signals;"
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select count(*) as decisions from agent_decisions;"
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select count(*) as orders from paper_orders;"
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select reason, count(*) from risk_events group by reason order by count(*) desc;"
```

### Ultimos bloqueios de risco

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select created_at, reason, payload from risk_events order by created_at desc limit 20;"
```

### Ultimas ordens simuladas

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select created_at, market_id, status, payload from paper_orders order by created_at desc limit 20;"
```

### Ultimos snapshots de equity

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U trading -d trading -c "select created_at, total_equity, total_pnl, available_balance from equity_snapshots order by created_at desc limit 20;"
```

## Hot Swap de Modelos

O sistema suporta troca de modelo em runtime pela API.

### Trocar um agente para OpenAI

```bash
curl -X POST https://bot.codifica.tec.br/api/agents/swap-model \
  -H "Content-Type: application/json" \
  -d '{"agent":"claude","model":"openai/gpt-4o-mini"}'
```

### Ver runtime efetivo

```bash
curl -s https://bot.codifica.tec.br/api/agents/status
```

### Observacao importante

O compose de producao monta:

- `./config:/app/config`

Entao as trocas persistem no host e passam a sobreviver a restart dos containers.

## Recuperacao de Runtime Inconsistente

Se `agents/status` mostrar combinacoes invalidas como `anthropic/gpt-4o-mini` ou o YAML do host estiver correto mas os agents estiverem presos em estado antigo:

```bash
cd /opt/polymarket-bot
docker compose -f docker-compose.prod.yml exec redis redis-cli DEL runtime:agents:config runtime:agents:models runtime:agents:version
docker compose -f docker-compose.prod.yml restart api agents
sleep 15
curl -s https://bot.codifica.tec.br/api/agents/status
```

Depois disso, reaplique o `swap-model` se necessario.

## Problemas Comuns

### `502` no dominio logo apos deploy

Se `caddy` responder `502` imediatamente apos `up -d`, normalmente `api` ou `dashboard` ainda estao em `health: starting`.

Validacao:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=100 caddy
```

### `python: can't open file '/app/scripts/replay_history.py'`

Causa historica:

- `scripts/` fora do contexto Docker

Situacao atual:

- corrigido no `Dockerfile.prod`
- corrigido no `.dockerignore`

Se reaparecer, faca rebuild forcado do `api`.

### `ModuleNotFoundError: No module named 'core'` no replay

Causa historica:

- `scripts/replay_history.py` sem ajuste de `sys.path`

Situacao atual:

- corrigido no proprio script

### `could not convert string to float: '['`

Causa historica:

- resposta do Polymarket com arrays serializados em string

Situacao atual:

- corrigido no conector de mercado

### Erros de Anthropic

Exemplo:

- `Your credit balance is too low to access the Anthropic API`

Acao:

- recarregar saldo na Anthropic
- ou mover agentes para OpenAI via `swap-model`

### Ha sinais, mas poucas ordens

Se `signals` sobe mas `orders` fica baixo, normalmente o bloqueio e estrategico, nao tecnico.

Verifique:

- `GET /api/risk-events/recent`
- `config/risk.yaml`

Razoes comuns:

- `portfolio exposure exceeds max_total_exposure_usd`
- `edge below minimum`
- preco acima do limite corrigido

## Checklist de Manutencao

### Diario

- conferir `healthz`
- conferir `agents/status`
- conferir `metrics/overview`
- conferir `risk-events/recent`
- conferir `costs/daily`

### Antes de alterar risco

- exportar replay 24h
- salvar `metrics/performance?hours=24`
- revisar `open_positions`
- revisar principais razoes em `risk_events`

### Antes de habilitar live trading

- confirmar `paper trading` estavel por periodo relevante
- revisar logs de `agents` sem excecoes repetidas
- validar heartbeats dos 3 agentes
- revisar limites em `risk.yaml`
- garantir credenciais Polymarket completas

## Validacao Local de Desenvolvimento

### Testes Python

```powershell
pytest -q
python -m compileall api core tests scripts run_agents.py
```

### Build do dashboard

```powershell
cd C:\Projetos\bot_polymarket\dashboard
npm run build
```

## Seguranca

- nunca commitar `.env`
- se uma chave aparecer em terminal, chat ou log, gire a credencial
- mantenha `LIVE_TRADING=false` ate ter confianca operacional
- prefira `paper trading` para calibrar risco e custo

## Arquivos Mais Importantes para Suporte

- [api/main.py](C:\Projetos\bot_polymarket\api\main.py)
- [core/database.py](C:\Projetos\bot_polymarket\core\database.py)
- [core/model_provider.py](C:\Projetos\bot_polymarket\core\model_provider.py)
- [core/market_connector.py](C:\Projetos\bot_polymarket\core\market_connector.py)
- [core/redis_bus.py](C:\Projetos\bot_polymarket\core\redis_bus.py)
- [agents/claude_agent.py](C:\Projetos\bot_polymarket\agents\claude_agent.py)
- [agents/codex_agent.py](C:\Projetos\bot_polymarket\agents\codex_agent.py)
- [agents/claw_agent.py](C:\Projetos\bot_polymarket\agents\claw_agent.py)
- [dashboard/components/dashboard-client.tsx](C:\Projetos\bot_polymarket\dashboard\components\dashboard-client.tsx)
- [dashboard/components/charts.tsx](C:\Projetos\bot_polymarket\dashboard\components\charts.tsx)

## Resumo Operacional

Para colocar de pe:

```bash
cd /opt/polymarket-bot
bash scripts/deploy-vps.sh
DOMAIN=bot.codifica.tec.br bash scripts/post-deploy-check.sh
```

Para diagnosticar:

```bash
docker compose -f docker-compose.prod.yml logs --tail=200 agents
curl -s https://bot.codifica.tec.br/api/metrics/overview
curl -s https://bot.codifica.tec.br/api/risk-events/recent
```

Para medir:

```bash
curl -s https://bot.codifica.tec.br/api/metrics/performance?hours=24
docker compose -f docker-compose.prod.yml exec api python scripts/replay_history.py --hours 24
```

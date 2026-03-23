---
name: vps-maintenance-bot-polymarket
description: Operar e manter a VPS do projeto bot_polymarket com SSH de forma segura e repetível. Use quando precisar conectar na VPS, checar saúde de containers Docker, analisar logs (api/agents/postgres/redis/caddy), medir performance, validar flags de execução (LIVE_TRADING/SMOKE_TEST_MODE), fazer deploy/rollback e executar troubleshooting operacional sem perder contexto.
---

# VPS Maintenance Bot Polymarket

Executar manutenção operacional da VPS usando o alias SSH `bot-polymarket-vps` e o diretório remoto `/opt/polymarket-bot`.

## Executar Fluxo Rápido

1. Verificar conectividade:
   - `ssh bot-polymarket-vps "echo SSH_OK && whoami && hostname"`
2. Verificar stack:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && docker compose -f docker-compose.prod.yml ps"`
3. Coletar consumo:
   - `ssh bot-polymarket-vps "docker stats --no-stream"`
4. Checar logs recentes:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && docker compose -f docker-compose.prod.yml logs --since=2h --tail=200 api agents"`

## Diagnosticar Performance

Executar, nesta ordem:

1. Estado do host:
   - `ssh bot-polymarket-vps "date; uptime; free -h; df -h /"`
2. Estado dos serviços:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && docker compose -f docker-compose.prod.yml ps"`
3. Métricas de API via domínio local com SNI:
   - `ssh bot-polymarket-vps "curl -ksS --resolve bot.codifica.tec.br:443:127.0.0.1 https://bot.codifica.tec.br/api/healthz"`
   - `ssh bot-polymarket-vps "curl -ksS --resolve bot.codifica.tec.br:443:127.0.0.1 'https://bot.codifica.tec.br/api/metrics/performance?hours=24'"`
   - `ssh bot-polymarket-vps "curl -ksS --resolve bot.codifica.tec.br:443:127.0.0.1 https://bot.codifica.tec.br/api/metrics/overview"`
4. Erros recorrentes:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && docker compose -f docker-compose.prod.yml logs --since=24h agents | grep -ci Unclosed"`
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && docker compose -f docker-compose.prod.yml logs --since=24h api | grep -ci ERROR"`

## Aplicar Manutenção Segura

1. Atualizar código:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && git fetch origin && git checkout main && git pull --ff-only origin main"`
2. Fazer deploy:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && APP_DIR=/opt/polymarket-bot BRANCH=main ./scripts/deploy-vps.sh"`
3. Validar pós-deploy:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && DOMAIN=bot.codifica.tec.br ./scripts/post-deploy-check.sh"`

## Executar Rollback

1. Selecionar commit/tag estável.
2. Aplicar rollback:
   - `ssh bot-polymarket-vps "cd /opt/polymarket-bot && git checkout <tag-ou-commit> && docker compose -f docker-compose.prod.yml up -d --build"`
3. Validar saúde e logs.

## Verificar Guardrails Operacionais

Sempre checar no `.env` remoto antes de concluir diagnóstico de ordens:

- `LIVE_TRADING`
- `SMOKE_TEST_MODE`

Comando:

- `ssh bot-polymarket-vps "grep -E '^(LIVE_TRADING|SMOKE_TEST_MODE)=' /opt/polymarket-bot/.env || true"`

## Usar Referência

Ler [references/vps-runbook.md](references/vps-runbook.md) quando precisar de checklist detalhado de análise e resposta a incidentes.

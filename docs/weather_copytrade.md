# Weather Copytrade

Documento técnico do módulo `weather_copytrade`, cobrindo a implementação, o fluxo operacional, as correções aplicadas durante a validação e o comportamento esperado em produção.

Data de referência: `2026-04-05`

---

## 1. Objetivo Do Módulo

O módulo `weather_copytrade` foi criado para:

1. Vasculhar traders públicos da Polymarket com histórico no mercado de clima.
2. Aplicar filtros conservadores de consistência, drawdown, lucro e volume mínimo.
3. Selecionar um único trader candidato com melhor relação de risco/retorno.
4. Gerar um relatório curto via LLM barato apenas para apoio explicativo.
5. Exigir aprovação manual antes de ativar a cópia automática.
6. Copiar as operações do trader aprovado com capital baixo e regras de liquidez.
7. Exibir no dashboard a análise, a shortlist, o selecionado, o relatório e as métricas copiadas.

O foco inicial é somente a categoria `WEATHER`.

---

## 2. Arquitetura Geral

### 2.1 Componentes Principais

- `core/weather_copytrade_service.py`
  - Implementa a análise, a aprovação, o pause/resume e o sync de trades.
- `agents/weather_copytrade_agent.py`
  - Orquestra o ciclo do módulo em background.
- `core/database.py`
  - Persiste run, candidatos, estado ativo e agrega o summary retornado pela API.
- `api/main.py`
  - Expõe endpoints HTTP para análise, aprovação, pausa, sync, resumo e métricas.
- `dashboard/components/dashboard-client.tsx`
  - Renderiza a nova seção do dashboard e envia as ações do usuário.

### 2.2 Fluxo Macro

1. O agente executa `run_analysis()` periodicamente.
2. A análise busca o leaderboard da Polymarket e enriquece os candidatos.
3. A shortlist é pontuada e o melhor trader é destacado.
4. O estado é persistido no banco.
5. O dashboard mostra o resumo e permite aprovar manualmente.
6. Após aprovação, o agente faz polling de trades do usuário aprovado.
7. Cada trade novo passa por guardrails de mercado, spread, notional e deduplicação.
8. Se elegível, o trade é copiado com ordem limitada e capital baixo.

---

## 3. Estrutura De Dados

### 3.1 Tabelas

As tabelas do módulo estão em `core/database.py`:

- `weather_copytrade_runs`
  - Guarda cada varredura feita no leaderboard.
  - Colunas relevantes:
    - `run_id`
    - `category`
    - `leaderboard_limit`
    - `universe_count`
    - `shortlisted_count`
    - `selected_count`
    - `selected_proxy_wallet`
    - `selected_user_name`
    - `candidate_count`
    - `stage_counts`
    - `rejected_breakdown`
    - `model_summary`
    - `selection_summary`
    - `scan_stats`
    - `metadata`
    - `created_at`

- `weather_copytrade_candidates`
  - Guarda cada candidato da shortlist com seus metadados.
  - Colunas relevantes:
    - `run_id`
    - `rank`
    - `proxy_wallet`
    - `user_name`
    - `verified_badge`
    - `profile`
    - `metrics`
    - `score`
    - `rationale`
    - `selected`
    - `created_at`

- `weather_copytrade_state`
  - Mantém o estado ativo do copytrade.
  - Colunas relevantes:
    - `category`
    - `run_id`
    - `selected_proxy_wallet`
    - `selected_user_name`
    - `selected_profile`
    - `selection`
    - `report`
    - `approved`
    - `active`
    - `paused`
    - `approved_at`
    - `activated_at`
    - `last_trade_seen_at`
    - `last_trade_seen_hash`
    - `processed_trade_hashes`
    - `metadata`
    - `created_at`
    - `updated_at`

### 3.2 Contratos Retornados Pela API

O endpoint `/weather-copytrade/summary` agora retorna:

- `run`: objeto normalizado ou `null`
- `candidates`: lista de objetos normalizados
- `state`: objeto normalizado ou `null`
- `report`: relatório curto normalizado
- `selection_summary`: objeto com os principais dados do selecionado
- `scan_stats`: objeto com estatísticas da varredura
- `metadata`: objeto com metadados úteis, incluindo `copy_trade_fraction`

Isso foi necessário porque alguns campos chegavam como JSON stringificado, o que fazia o dashboard exibir `0` ou placeholders.

---

## 4. Lógica De Seleção

### 4.1 Fonte De Dados

A análise do módulo usa dados públicos da Polymarket:

- leaderboard de traders por categoria `WEATHER`
- public profile
- posições atuais
- posições fechadas
- trades recentes

### 4.2 Filtros Conservadores

Os filtros padrão atuais são:

- categoria obrigatória: `WEATHER`
- janela mínima de histórico: `30d`
- validação adicional em `7d` e `ALL`
- mínimo de trades em `30d`
- mínimo de trades em `7d`
- mínimo de posições fechadas relevantes
- mínimo de semanas positivas em 4 semanas
- `max_drawdown` limitado
- `profit_factor` mínimo
- `win_rate` mínimo
- penalização de concentração de lucro
- somente perfis públicos minimamente identificáveis

### 4.3 Score

O score é composto por:

- PnL de 30d e All Time
- volume de trades no período
- consistência semanal
- drawdown
- profit factor
- concentração de lucro
- bônus por badge verificado

O score final é usado para ordenar candidatos, mas a aprovação ainda depende do usuário.

### 4.4 Short Report Do Modelo

O relatório curto é gerado em `core/weather_copytrade_service.py`:

- quando há candidato selecionado:
  - o sistema tenta usar o LLM barato configurado
  - se falhar, usa fallback determinístico
- quando não há candidato:
  - o relatório explica que nenhum trader atingiu os thresholds conservadores

Campos do report:

- `summary`
- `why`
- `risks`
- `selection_reason`
- `selected_proxy_wallet`
- `selected_user_name`
- `model`
- `provider`
- `fallback_used`

---

## 5. Aprovação E Ativação

### 5.1 Aprovação Manual

O trader selecionado só entra em cópia depois que o usuário aprova explicitamente no dashboard.

### 5.2 Persistência Do Estado

O estado aprovado é salvo em `weather_copytrade_state`.

Campos importantes:

- `approved = true`
- `active = true`
- `paused = false`
- `approved_at` preenchido
- `activated_at` preenchido
- `selected_proxy_wallet` persistido
- `selected_user_name` persistido
- `selection` persistido
- `report` persistido

### 5.3 Correção Crítica Aplicada

Durante a validação, foi encontrado um bug importante:

- toda nova análise sobrescrevia o estado com:
  - `approved = false`
  - `active = false`
  - `paused = true`

Isso fazia o dashboard voltar para `false` depois de um tempo mesmo com o trader aprovado.

### 5.4 Fix Aplicado

Em `core/weather_copytrade_service.py`:

- `run_analysis()` passou a ler o estado atual antes de escrever um novo run.
- `_merge_state_from_run()` passou a preservar o lifecycle quando já existe um estado aprovado/ativo.
- Uma nova análise agora atualiza:
  - `run_id`
  - `report`
  - `metadata`
  - `scan_stats`
  - `selection_summary`
  sem desfazer a aprovação.

### 5.5 Regra Prática

Nova análise não significa desativação.

Se o trader já estiver aprovado:

- ele continua aprovado
- continua ativo
- continua não pausado

---

## 6. Sync De Trades

### 6.1 Entrada

`sync_mirror_trades()` é executado pelo agente quando:

- existe estado
- `active = true`
- `paused = false`

### 6.2 Processo

1. Busca o estado atual.
2. Lê o `selected_proxy_wallet`.
3. Busca trades do trader selecionado.
4. Ordena por timestamp.
5. Elimina duplicados usando:
   - `processed_trade_hashes`
   - `last_trade_seen_at`
6. Ignora trades não relacionados a clima.
7. Respeita limites de liquidez e spread.
8. Calcula o notional de cópia com base em `copy_trade_fraction`.
9. Limita o tamanho entre `min_notional_usd` e `max_notional_usd`.
10. Coloca a ordem via connector.
11. Registra a ordem em `paper_orders`.

### 6.3 Guardrails De Capital Baixo

Regras importantes:

- `min_notional_usd`
- `max_notional_usd`
- `max_spread_bps`
- `max_open_copied_positions`
- só mercados `WEATHER`
- skip de mercado sem orderbook consistente
- skip de trade com preço ou tamanho inválidos

### 6.4 Persistência Pós-Sync

Depois do sync, o módulo atualiza:

- `last_trade_seen_at`
- `last_trade_seen_hash`
- `processed_trade_hashes`
- `metadata.last_sync_at`

---

## 7. Agente Em Background

### 7.1 Arquivo

- `agents/weather_copytrade_agent.py`

### 7.2 Responsabilidade

O agente executa:

- análise periódica
- sync periódico
- telemetry do scan
- telemetry do sync
- telemetry de idle

### 7.3 Correção Aplicada No Agente

Foi encontrado outro bug de estado:

- `metadata` às vezes chegava como string JSON
- o tick tentava ler como dict puro
- isso podia quebrar a lógica de `last_run_at`

Fix aplicado:

- helper `_metadata_map(...)`
- leitura robusta de `metadata`
- parsing seguro de `last_run_at`

---

## 8. Normalização De Payload

### 8.1 Problema Original

O dashboard recebia campos como:

- `run`
- `state`
- `report`
- `selection_summary`
- `scan_stats`
- `metadata`

às vezes como string JSON, e não como objeto.

Isso fazia a UI mostrar:

- placeholders
- `0.00`
- campos vazios que pareciam dados reais

### 8.2 Solução No Backend

Em `core/database.py` foi criado o helper:

- `_normalize_weather_copytrade_payload(...)`

E os métodos do módulo passaram a usá-lo:

- `record_weather_copytrade_run`
- `upsert_weather_copytrade_state`
- `get_weather_copytrade_state`
- `get_latest_weather_copytrade_summary`
- `get_recent_weather_copytrade_runs`

### 8.3 Solução No Dashboard

Em `dashboard/components/dashboard-client.tsx` foram adicionados normalizadores:

- `normalizeRecord`
- `normalizeObjectRecord`
- `normalizeNumberRecord`
- `normalizeWeatherCopytradeReport`
- `normalizeWeatherCopytradeCandidate`
- `normalizeWeatherCopytradeRun`
- `normalizeWeatherCopytradeState`
- `normalizeWeatherCopytradeSummary`

E o estado da tela passou a ser montado com esses normalizadores.

### 8.4 Ajuste De Exibição

O dashboard agora:

- mostra `copy_trade_fraction` corretamente
- mostra `report.summary`, `why`, `risks`, `selection_reason`
- mostra métricas dos candidatos sem converter strings em zero
- mostra `-` quando não existe dado real

---

## 9. Dashboard

### 9.1 Nova Seção

Foi adicionada a seção `WEATHER_COPYTRADE`, com:

- status atual
- botões de ação
- última análise
- shortlist
- selecionado
- resumo do modelo
- operação copiada

### 9.2 Botões

- `Nova análise`
- `Aprovar e ativar`
- `Pausar` ou `Retomar`

### 9.3 Cards

#### Última análise

Mostra:

- `run_id`
- universo
- shortlist
- selecionado
- model
- resumo do modelo
- flags de aprovação, active e paused

#### Candidatos

Mostra por candidato:

- rank
- wallet
- score
- rationale
- `pnl_30d`
- `max_drawdown`
- `profit_factor`
- `trades_30d`

#### Operação copiada

Mostra:

- orders
- signals
- execution rate
- win rate
- PnL
- drawdown
- risk events
- approval rate
- selected wallet
- `copy_trade_fraction`

---

## 10. Debug E Validações Feitas

### 10.1 Monitoria Do Estado

Foi observado em produção que o módulo podia voltar de `approved = true` para `false` depois de um tempo.

Esse problema foi reproduzido e corrigido.

### 10.2 Validação Do Lifecycle

Foi feito um monitoramento por alguns ciclos da VPS:

- o estado permaneceu aprovado
- o estado permaneceu ativo
- o estado permaneceu não pausado
- uma nova análise não derrubou a aprovação

### 10.3 Debug Da Cópia

Também foi validado o caminho de cópia:

- em produção, naquele momento, não havia trade novo elegível
- o sync retornou `copied = 0`
- em teste sintético dentro do container, o fluxo `run_analysis -> approve_selection -> sync_mirror_trades` gerou:
  - `copied = 1`
  - ordem registrada

### 10.4 Conclusão Do Debug

O mecanismo de cópia está correto.

Quando há trade novo elegível:

- ele é detectado
- é filtrado
- é convertido em ordem
- é persistido

Quando não há trade novo elegível:

- o sync fica em `0`
- sem erro
- sem sobrescrever o estado

---

## 11. Endpoints Disponíveis

### 11.1 Resumo

- `GET /weather-copytrade/summary?limit=12`

### 11.2 Execução Manual Da Análise

- `POST /weather-copytrade/run`

### 11.3 Aprovação

- `POST /weather-copytrade/approve`

### 11.4 Pausa / Retomada

- `POST /weather-copytrade/pause`

### 11.5 Sync Manual

- `POST /weather-copytrade/sync`

### 11.6 Métricas

- `GET /weather-copytrade/metrics?hours=720`

---

## 12. Configurações Relevantes

Essas configurações vêm de `WeatherCopytradeSettings`:

- `leaderboard_limit`
- `shortlist_limit`
- `min_trades_30d`
- `min_trades_7d`
- `min_closed_positions_30d`
- `min_positive_weeks_4`
- `min_pnl_7d`
- `min_pnl_30d`
- `min_pnl_all`
- `max_drawdown`
- `min_profit_factor`
- `min_win_rate`
- `max_pnl_concentration`
- `max_spread_bps`
- `min_notional_usd`
- `max_notional_usd`
- `copy_trade_fraction`
- `max_open_copied_positions`
- `scan_interval_minutes`
- `poll_interval_seconds`
- `trade_lookback_days`

Essas opções controlam o conservadorismo da seleção e o tamanho da cópia.

---

## 13. Testes Adicionados / Ajustados

### 13.1 Backend

Arquivo:

- `tests/test_database.py`

Cobertura adicionada:

- normalização do summary com JSON aninhado
- `run`, `candidates`, `state` e `report` como objetos
- `copy_trade_fraction` vindo do metadata normalizado

### 13.2 Serviço

Arquivo:

- `tests/test_weather_copytrade_service.py`

Cobertura existente e reforçada:

- seleção consistente
- aprovação
- pause/resume
- sync
- preservação do estado aprovado após nova análise

### 13.3 Validações Executadas

Durante a implementação e o debug:

- `python -m py_compile` nos módulos alterados
- `npx -p typescript@5.8.3 tsc -p dashboard/tsconfig.json --noEmit --pretty false`
- deploy na VPS
- post-deploy check
- monitoria do estado por vários ciclos
- teste sintético end-to-end do fluxo de cópia

---

## 14. Resultados Observados Em Produção

Após as correções:

- o dashboard passa a refletir corretamente o report real
- `copy_trade_fraction` deixa de aparecer vazio
- os candidatos deixam de mostrar zeros falsos
- o estado aprovado não cai quando uma nova análise roda
- o agente continua fazendo scan e sync sem quebrar o lifecycle

No debug real da VPS:

- o estado ficou estável em `approved = true`
- o sync real encontrou `0` trades elegíveis naquele instante
- o fluxo sintético confirmou que a cópia funciona quando há trade novo

---

## 15. Observações Importantes

1. O copytrade só copia trades do trader aprovado.
2. A seleção não é automática sem aprovação do usuário.
3. O módulo foi desenhado para capital baixo e regras conservadoras.
4. Uma nova análise não deve desfazer a aprovação manual.
5. Se o trader não gerar trade novo, o sync naturalmente fica em `0`.
6. A interpretação correta de JSON normalizado foi essencial para o dashboard ficar confiável.

---

## 16. Resumo Final

O módulo `weather_copytrade` agora cobre todo o ciclo:

1. descobre traders públicos de clima
2. filtra consistência
3. escolhe um candidato
4. gera report curto
5. expõe o resultado no dashboard
6. permite aprovação manual
7. persiste o estado ativo
8. copia trades elegíveis
9. preserva a aprovação entre novas análises
10. registra telemetry e métricas do processo

Em outras palavras: o módulo saiu de uma prova de conceito para um fluxo operacional completo, monitorável e resiliente.

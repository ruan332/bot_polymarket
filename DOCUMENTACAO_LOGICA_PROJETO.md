# Documentação Técnica da Lógica do Projeto

## 1. Visão Geral
Este projeto é um bot de trading para Polymarket focado em mercados binários de 15 minutos (formato `up/down`).
A execução principal está em `src/index.ts`, com estratégia operacional em `src/order-builder/copytrade.ts`.

Fluxo macro:
1. Carrega configuração (`.env`) e valida chave privada.
2. Gera/garante credenciais de API da CLOB (`src/data/credential.json`).
3. Inicializa cliente CLOB autenticado.
4. Aprova allowances on-chain (USDC + ERC1155 approvals).
5. Aguarda saldo mínimo USDC.
6. Inicializa bot de estratégia (WebSocket + preditor + execução de ordens).
7. Em paralelo, scripts opcionais fazem redeem automático de mercados resolvidos.

---

## 2. Arquitetura por Módulo

### 2.1 Entrada e Orquestração
- **Arquivo:** `src/index.ts`
- Responsabilidades:
1. `validatePrivateKey()` garante chave válida e encerra processo em caso de erro.
2. `createCredential()` deriva/cria API key da Polymarket se necessário.
3. `getClobClient()` inicializa cliente autenticado e cacheado.
4. `approveUSDCAllowance()` + `updateClobBalanceAllowance()` sincronizam permissões.
5. `waitForMinimumUsdcBalance()` bloqueia início até carteira ter saldo/allowance suficiente.
6. Instancia `CopytradeArbBot.fromEnv(clobClient)` e chama `start()`.
7. Registra handlers de `SIGINT`/`SIGTERM` para shutdown limpo com sumários finais.

### 2.2 Configuração
- **Arquivo:** `src/config/index.ts`
- Centraliza leitura de variáveis de ambiente com parsing tipado:
- `envString`, `envNumber`, `envBool`, `envCsvLower`, `requireEnv`.
- Exporta objeto `config` com seções:
- rede (`chainId`, `clobApiUrl`, `rpcUrl`, `rpcToken`)
- wallet (`privateKey`, `useProxyWallet`, `proxyWalletAddress`)
- bot (`minUsdcBalance`, `waitForNextMarketStart`)
- logging
- copytrade (markets, shares, tickSize, buffers, limites por lado)
- redeem (`conditionId`, `indexSets`)

### 2.3 Cliente CLOB
- **Arquivo:** `src/providers/clobclient.ts`
- Garante presença de credenciais em disco (`ensureCredential`).
- Converte `secret` de base64url para base64 padrão (compatibilidade com SDK).
- Cria singleton cacheado de `ClobClient` (evita reinit repetida).
- Suporta assinatura EOA (`signatureType=0`) ou proxy wallet (`signatureType=2`).

### 2.4 Feed em Tempo Real
- **Arquivo:** `src/providers/websocketOrderbook.ts`
- Conecta em `wss://ws-subscriptions-clob.polymarket.com/ws/market`.
- Usa mensagens `best_bid_ask` como fonte principal (fallback para `l2_orderbook`).
- Mantém cache de preços por token (`bestBid`, `bestAsk`, `mid`, timestamp).
- Possui reconexão automática + resubscribe de tokens previamente ativos.
- Faz ping/pong para keepalive.

### 2.5 Segurança / On-chain allowances
- **Arquivo:** `src/security/allowance.ts`
- Aprova `MaxUint256` para:
- USDC -> ConditionalTokens
- USDC -> Exchange
- `setApprovalForAll` (ERC1155) -> Exchange
- Se `NEG_RISK=true`, também aprova contratos `negRiskAdapter` e `negRiskExchange`.
- Seleciona RPC funcional por fallback de endpoints.

### 2.6 Estratégia de Trading
- **Arquivo:** `src/order-builder/copytrade.ts`
- É o coração do bot:
- resolve slug atual de 15m (`{market}-updown-15m-{unix}`)
- busca token IDs no Gamma API
- assina callbacks de preço (UP e DOWN)
- usa `AdaptivePricePredictor` para gerar sinais
- executa primeira perna e ordem limite da segunda perna
- controla limites por lado (`COPYTRADE_MAX_BUY_COUNTS_PER_SIDE`)
- persiste estado em `src/data/copytrade-state.json`
- gera sumários de desempenho por ciclo

### 2.7 Preditor
- **Arquivo:** `src/utils/pricePredictor.ts`
- Modelo linear adaptativo com aprendizado online (gradient descent).
- Só gera predição em valores de "pole" (picos/vales locais).
- Filtra ruído com limiar de variação `< 0.02`.
- Features principais: momentum, volatilidade, trend + lags de preço.
- Saída: `predictedPrice`, `confidence`, `direction`, `signal`.

### 2.8 Holdings e Redeem
- **Arquivos:**
- `src/utils/holdings.ts`
- `src/utils/redeem.ts`
- `src/auto-redeem.ts`
- `src/redeem-holdings.ts`
- `src/redeem-proxy.ts`
- Mantém mapa `conditionId -> tokenId -> quantidade` em `src/data/token-holding.json`.
- Faz checagem de resolução no CTF (`payoutDenominator > 0`).
- Calcula outcomes vencedores via `payoutNumerators`.
- Redime apenas outcomes vencedores que o usuário realmente possui.
- Suporta modo por holdings local e modo discovery via API (`/positions`).

---

## 3. Fluxo de Execução Detalhado

## 3.1 Bootstrap (`npm start`)
1. `setupConsoleFileLogging(...)` intercepta stdout/stderr e grava logs em arquivo diário.
2. `validatePrivateKey()` valida formato da chave.
3. `createCredential()` tenta criar credenciais CLOB se ainda não existir arquivo.
4. `getClobClient()` monta cliente autenticado.
5. `approveUSDCAllowance()` realiza aprovações on-chain.
6. `updateClobBalanceAllowance()` sincroniza allowance on-chain na CLOB API.
7. `waitForMinimumUsdcBalance(...)` bloqueia execução até saldo disponível >= mínimo.
8. Cria `CopytradeArbBot` e chama `start()`.

## 3.2 Inicialização de mercados (`CopytradeArbBot.start`)
Para cada mercado em `COPYTRADE_MARKETS`:
1. Gera slug de 15 min atual.
2. Resolve token IDs Up/Down via Gamma API.
3. Faz subscribe WebSocket nesses token IDs.
4. Registra callback de atualização de preço para ambos lados.

## 3.3 Loop de preço
Em cada update:
1. Valida presença de `bestAsk` dos dois lados.
2. Debounce: ignora mudança de preço muito pequena (`< 0.0001` no UP ask).
3. Detecta troca de ciclo 15m (slug mudou):
- fecha summary do ciclo anterior
- limpa contadores/pausa do ciclo antigo
- re-resolve novos token IDs
- reseta preditor
4. Alimenta `AdaptivePricePredictor` com `upAsk`.
5. Só continua se houver predição (somente em polo).
6. Avalia acerto da predição anterior e atualiza score.
7. Se confiança/sinal passarem filtros, executa trade.

## 3.4 Execução da estratégia de ordem
Quando sinal é válido:
1. Escolhe lado principal:
- direção `up` => compra token UP
- direção `down` => compra token DOWN
2. Primeira ordem:
- tipo `GTC`
- preço limite = `askPrice + 0.01`
- tamanho = `COPYTRADE_SHARES`
3. Segunda ordem limite (lado oposto):
- preço = `0.98 - firstSidePrice`
- também `GTC`
4. Rastreamento da segunda ordem:
- polling com backoff (até ~30 tentativas)
- ao preencher, incrementa contadores/custos do lado
5. Pausa do mercado se atingir limite por lado (UP e DOWN no máximo configurado).

Exemplo numérico:
- `COPYTRADE_SHARES=5`
- `UP ask = 0.57`
- primeira ordem UP: preço limite `0.58`, custo nominal `2.90 USDC`
- segunda ordem DOWN: `0.98 - 0.57 = 0.41`, custo nominal `2.05 USDC`

---

## 4. Lógica do Preditor (AdaptivePricePredictor)

## 4.1 Regras de entrada
- Ignora preços fora do intervalo `[0.003, 0.97]`.
- Ignora atualização com variação `< 0.02` (filtro de ruído).
- Exige histórico mínimo antes de operar.

## 4.2 Pole detection
Predição só ocorre quando o preço atual se comporta como:
- pico local (maior que anteriores), ou
- vale local (menor que anteriores)
E com mudança relevante frente ao último polo.

## 4.3 Features e regressão
Features combinadas:
- `priceLag1/2/3`
- `momentum`
- `volatility`
- `trend` (com EMA curta/longa + componentes adicionais)

Predição linear:
`pred = intercept + w1*lag1 + w2*lag2 + w3*lag3 + wM*momentum + wV*volatility + wT*trend`

## 4.4 Aprendizado online
Após nova observação real:
- calcula erro (`actual - predicted`)
- ajusta learning rate conforme magnitude do erro e direção errada/certa
- atualiza pesos com gradient descent e decay
- mantém estatísticas de acurácia global e janela recente

## 4.5 Confidence e sinal
`confidence` considera:
- volatilidade (penaliza alta vol)
- força de tendência/momentum
- alinhamento entre direção prevista e features
- acurácia histórica/recente
- penalização de overconfidence

Sinal final:
- `BUY_UP` / `BUY_DOWN` / `HOLD`
- exige thresholds adaptativos de confiança + alinhamento de features.

Observação importante de integração:
- Mesmo que o preditor tenha múltiplos thresholds internos, o bot ainda impõe filtro adicional: `confidence >= 0.50` e `signal !== HOLD` antes de executar ordem.

---

## 5. Persistência e Estado

## 5.1 `src/data/credential.json`
- Credenciais API CLOB derivadas da wallet.
- Criado automaticamente na primeira execução.

## 5.2 `src/data/copytrade-state.json`
- Estado por slug/ciclo.
- Campos rastreados: `previousUpPrice`, `conditionId`, `slug`, `market`, `upIdx`, `downIdx`, `lastUpdatedIso`.
- Escrita com debounce de 500ms.

## 5.3 `src/data/token-holding.json`
- Ledger local de tokens comprados para facilitar redeem posterior.
- Estrutura:
```json
{
  "<conditionId>": {
    "<tokenId>": 12.5
  }
}
```

## 5.4 `logs/*.log` e `logs/pnl.log`
- `console-file.ts` espelha stdout/stderr para arquivo diário.
- `redeem-holdings.ts` e `redeem.ts` adicionam linhas em `pnl.log` com custo/payout/pnl quando possível.

---

## 6. Redeem: Lógica Técnica

## 6.1 Checagem de resolução
Em `checkConditionResolution`:
1. `getOutcomeSlotCount(conditionId)`
2. `payoutDenominator(conditionId)`
- `0` => não resolvido
- `>0` => resolvido
3. varre `payoutNumerators(conditionId, i)` para descobrir outcomes vencedores.

## 6.2 Redenção segura (`redeemMarket`)
1. Descobre outcomes vencedores.
2. Consulta saldos do usuário por `indexSet` (`getUserTokenBalances`).
3. Interseca vencedores com posições realmente detidas.
4. Chama `redeemPositions` apenas para indexSets redimíveis.
5. Retry com backoff em erros transitórios de rede/RPC.

## 6.3 Modos de execução
- `src/redeem.ts`: redeem manual por `conditionId`.
- `src/auto-redeem.ts`: varre holdings local ou API (`--api`).
- `src/redeem-holdings.ts`: worker em loop, pensado para rodar separado do bot.
- `src/redeem-proxy.ts`: redeem via Proxy Factory (transação encapsulada).

Exemplo de dry-run:
```bash
npm run redeem:holdings -- --dry-run
```

Exemplo API discovery:
```bash
ts-node src/auto-redeem.ts --api --dry-run --max 500
```

---

## 7. Variáveis de Ambiente Mais Relevantes

Obrigatórias:
- `PRIVATE_KEY`

Trading:
- `COPYTRADE_MARKETS` (ex.: `btc,eth`)
- `COPYTRADE_SHARES`
- `COPYTRADE_TICK_SIZE`
- `COPYTRADE_MAX_BUY_COUNTS_PER_SIDE`
- `COPYTRADE_WAIT_FOR_NEXT_MARKET_START`

Infra:
- `CHAIN_ID` (default 137)
- `CLOB_API_URL`
- `RPC_URL` e/ou `RPC_TOKEN`

Risco:
- `BOT_MIN_USDC_BALANCE`
- `NEG_RISK`

Logs:
- `LOG_DIR`, `LOG_FILE_PREFIX`, `LOG_FILE_PATH`

---

## 8. Sequência Técnica de um Trade (Exemplo completo)
1. WebSocket recebe `best_bid_ask` dos tokens UP e DOWN.
2. Bot lê `upAsk` e `downAsk` do cache.
3. Preditor identifica polo e retorna:
- direção `up`
- confiança `0.67`
- sinal `BUY_UP`
4. Bot valida limites por lado e estado de pausa.
5. Bot envia ordem 1 (UP) `GTC` em `upAsk + 0.01`.
6. Bot envia ordem 2 (DOWN) `GTC` em `0.98 - upAsk`.
7. Bot monitora preenchimento da ordem 2 e atualiza contadores/custos.
8. No próximo polo, avalia se predição anterior foi correta e alimenta score.
9. Ao trocar ciclo 15m, fecha summary e reinicializa mercado/token IDs.

---

## 9. Comportamentos Operacionais Importantes
- O bot depende de WebSocket para decisão em baixa latência.
- Há detecção ativa de virada de ciclo mesmo sem ticks (checagem a cada 10s).
- Estado é persistido para retomada/resiliência.
- Allowances são tentadas no início, mas o bot continua mesmo se essa etapa falhar (ordens podem falhar depois).
- Redeem pode ser desacoplado em processo separado (`redeem-holdings.ts`).

---

## 10. Pontos de Atenção Técnicos (baseado no código atual)
1. `src/providers/rpcProvider.ts` está vazio (não utilizado).
2. Em `copytrade.ts`, comentário cita fórmula `0.99 - firstSidePrice`, mas implementação usa `0.98 - firstSidePrice`.
3. Método `trackOrderAsync` existe, porém não é chamado no fluxo atual de compra da primeira perna.
4. Há mix de `logger` de `pretty-ts-logger` e `src/utils/logger.ts` em módulos diferentes (formatos de log distintos).

---

## 11. Comandos Operacionais
- Rodar bot principal:
```bash
npm start
```

- Redeem por holdings:
```bash
npm run redeem:holdings
```

- Redeem específico:
```bash
npm run redeem -- <conditionId>
```

- Auto redeem via API (descoberta de posições):
```bash
ts-node src/auto-redeem.ts --api
```

---

## 12. Resumo Executivo
Este bot implementa um motor de trading orientado a eventos (WebSocket), com predição adaptativa e execução em duas pernas por sinal. A estratégia opera por ciclos de 15 minutos, faz rotação automática de mercado/slug e mantém trilha de estado e holdings para liquidação posterior. O subsistema de redeem usa verificação on-chain de resolução + saldos efetivos, permitindo automação de fechamento de posições vencedoras com retry e limpeza de estado.

# Bot Polymarket

Projeto Python com API FastAPI, agentes de trading e dashboard Next.js.

## Fluxo local rapido

### 1) Preparar o ambiente

```powershell
.\scripts\Create-LocalEnv.ps1
```

Se for rodar o dashboard fora do Docker, copie:

```powershell
Copy-Item .\dashboard\.env.local.example .\dashboard\.env.local
```

### 2) Subir tudo

```powershell
.\scripts\Start-LocalStack.ps1
```

Isso sobe:

- Postgres local
- Redis local
- API em `http://127.0.0.1:8000`
- Agents
- Dashboard em `http://127.0.0.1:3000`

### 3) Validar alteracoes

```powershell
.\scripts\Run-LocalValidation.ps1
```

Para subir e validar o stack inteiro em um unico comando:

```powershell
.\scripts\Run-LocalValidation.ps1 -WithLocalStack
```

### 4) Derrubar a stack

```powershell
.\scripts\Stop-LocalStack.ps1
```

## Comandos uteis

```powershell
.\scripts\Run-LocalSmoke.ps1 -StartServices
pytest -q
cd .\dashboard; npm run build
```

## Requisitos

- Python 3.11+ com `.venv`
- Node.js 20+
- Postgres local ou `PgBinDir` apontando para os binarios
- Redis local ou `RedisServerPath` apontando para o executavel

## Observacoes

- O modo local usa `LIVE_TRADING=false` por padrao.
- Os logs da stack ficam em `.tmp\local-stack`.
- Para producao na VPS, continue usando `docker-compose.prod.yml`.

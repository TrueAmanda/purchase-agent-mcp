# purchase-agent-mcp

Estrutura inicial de um agente de compras com FastAPI, MCP e React. OpenRouter e PostgreSQL estão previstos para etapas futuras.

## Organização

- `backend/app/`: FastAPI com `api/routes`, `services`, `models` e `schemas`.
- `mcp-server/purchase_mcp/`: servidor MCP independente com `tools`, `services` e `schemas`.
- `frontend/src/`: React e TypeScript com `components`, `pages`, `services` e `types`.
- Cada componente possui dependências, Dockerfile e pasta `tests/` próprios.

Pastas vazias ficam reservadas para etapas futuras. Ainda não há lógica de agente, ferramentas, persistência ou comunicação entre componentes. Os nomes públicos reservados das ferramentas MCP são `buscar_produtos` e `detalhar_produto`; suas assinaturas serão definidas depois.

## Requisitos

Python 3.12, Node.js 22.12+ ou 24+ e npm. Docker é opcional.

## Configuração

Copie `.env.example` para `.env` na raiz. As variáveis apenas documentam integrações futuras e ainda não são lidas pelos serviços. Preencha as credenciais somente quando essas integrações forem implementadas. Variáveis `VITE_` são públicas e nunca devem conter segredos.

## Execução local

Abra um terminal separado para cada componente. Exemplos em PowerShell, partindo da raiz:

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Saúde: `http://localhost:8000/health`. Documentação: `http://localhost:8000/docs`.

### Servidor MCP

```powershell
cd mcp-server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m purchase_mcp.server
```

Endpoint Streamable HTTP: `http://localhost:8001/mcp`. O servidor inicia sem ferramentas. Esse endpoint recebe o protocolo MCP, não uma página web.

### Frontend

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Acesse `http://localhost:5173`. Execute `npm.cmd run build` para verificar os tipos e gerar o build. Em outros shells, use `npm` em vez de `npm.cmd`.

## Docker

Na raiz:

```powershell
docker build -t purchase-backend ./backend
docker build -t purchase-mcp ./mcp-server
docker build -t purchase-frontend ./frontend
```

Execute cada comando abaixo em um terminal separado:

```powershell
docker run --rm -p 8000:8000 purchase-backend
docker run --rm -p 8001:8001 purchase-mcp
docker run --rm -p 5173:5173 purchase-frontend
```

A imagem do frontend usa o servidor de desenvolvimento do Vite e serve apenas para esta estrutura inicial. Não há banco nem orquestração nesta etapa.

## Testes

As pastas `tests/` estão reservadas. Ainda não há testes automatizados ou dependências de teste. Valide inicialmente a rota de saúde, a inicialização do MCP e o build do frontend.

## Referências

- [Vite](https://vite.dev/guide/)
- [SDK Python MCP v1](https://py.sdk.modelcontextprotocol.io/v1/): dependência limitada à linha 1 para manter a API utilizada.

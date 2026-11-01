# purchase-agent-mcp

## Visão geral

Assistente de compras em linguagem natural com catálogo DummyJSON e requisições no PostgreSQL. O chat mostra produtos, valores, registros confirmados e ferramentas realmente chamadas pelo agente.

## Tecnologias

Python 3.12, FastAPI, MCP oficial/Streamable HTTP, OpenRouter, HTTPX, Pydantic, SQLAlchemy e PostgreSQL. Frontend React/TypeScript/Vite com Nginx. Docker Compose, Pytest, Vitest e Ruff.

## Como executar

Requer Docker com containers Linux e Compose v2: Desktop/WSL 2 no Windows, Desktop no macOS ou Engine/Compose no Linux. Execute na raiz do projeto.

### Windows (PowerShell)

Crie a configuração caso ainda não exista:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### Linux / macOS

```sh
[ -f .env ] || cp .env.example .env
```

Edite `.env`: configure `OPENROUTER_API_KEY` com uma chave ativa e orçamento disponível. `OPENROUTER_MODEL` usa `openai/gpt-4.1-mini`. Banco, portas, timeouts e limites estão em [.env.example](.env.example). A senha padrão é para uso local; segredos ficam fora do Git.

Inicie os quatro serviços e confira os healthchecks:

```sh
docker compose up --build -d --wait
docker compose ps
```

As tabelas são inicializadas automaticamente em volume persistente. Pare com `docker compose down`. **Não use `down -v`: ele apaga o volume.**

## Como utilizar

Abra o [chat](http://localhost:5173) ou a [API/Swagger](http://localhost:8000/docs). As portas são configuradas por `FRONTEND_PORT`, `BACKEND_PORT` e `MCP_PORT`, publicadas em `127.0.0.1`.

Exemplo de pedido:

> Preciso comprar 3 laptops para o time de vendas, até US$ 2.000 cada. Pesquise o catálogo e, se atender às condições, crie uma requisição em nome de (seu nome ou nome da Empresa), com o título “Laptops para vendas”.

O catálogo pode não atender ao pedido. A moeda é USD (US$). Use **Nova conversa** para outra compra; confira **Requisições salvas** antes de repetir uma operação incerta.

`POST /requisicoes` recebe `{"message":"..."}`; `GET /requisicoes` lista os registros persistidos. O chat usa o proxy `/api/`. O endpoint MCP padrão é `http://localhost:8001/mcp`.

## Ferramentas MCP

| Ferramenta | Responsabilidade |
| --- | --- |
| `buscar_produtos(termo)` | Consulta produtos e categorias na DummyJSON. |
| `detalhar_produto(id)` | Obtém a ficha real de um produto pelo ID. |
| `criar_requisicao(request)` | Valida itens e restrições, consulta preços atuais e persiste a requisição. |

## Decisões técnicas

- Backend e MCP independentes, modelos em `shared/purchase_storage` e separação de rotas, serviços e schemas.
- Schemas descobertos via MCP; o LLM escolhe ferramentas com `tool_choice: auto`, sem sequência fixa. O ciclo aceita múltiplas chamadas e tem limites de rodadas e tempo.
- Gravação exclusivamente em `criar_requisicao`, com preços externos, `Decimal`, transação e idempotência. O POST confirma no banco; o GET lista dados persistidos.
- Uma intenção por conversa, com contexto em memória. Autorização, restrições reconhecidas e concorrência são controladas no backend. Respostas comerciais usam dados confirmados; ambiguidades exigem esclarecimento.

Detalhes: [proteções e limitações](docs/security-fixes.md), [testes de integração](docs/integration-testing.md) e [validação do case](docs/case-validation.md).

## Testes

Todos os testes Python, incluindo integração com PostgreSQL isolado, em qualquer sistema:

```sh
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from mcp-tests
docker compose -f compose.test.yaml down
```

Testes com HTTP simulado e banco isolado, sem chave LLM nem alterações no banco de desenvolvimento. O build precisa de internet.

Para testes e build do frontend no host, use Node.js 22.22.2 ou superior da série 22. **No PowerShell, utilize `npm.cmd` no lugar de `npm`:**

```sh
cd frontend
npm ci
npm test
npm run build
cd ..
```

Execução Python local e lint: [roteiro de testes](docs/integration-testing.md). A CI executa Ruff/Pytest em push/PR para `main`. A [avaliação opcional de cinco pedidos](docs/agent-evaluation.md) usa serviços reais, consome créditos e pode gravar compras; fica fora da suíte automática. Resultados históricos estão em `docs/`.

## Possíveis evoluções

Propostas futuras, ainda não implementadas:

- Redis para cache de catálogo e estado compartilhado entre processos.
- Observabilidade com métricas e tracing distribuído, complementando os logs existentes.
- Autenticação e autorização por usuário.
- RAG para consultar políticas de compras da organização.
- Processamento assíncrono com filas e workers para pedidos demorados.

## Uso de IA

O Codex auxiliou na implementação, depuração, testes e documentação. As verificações e limitações estão documentadas.

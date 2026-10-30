# purchase-agent-mcp

Agente de compras em desenvolvimento com FastAPI, servidor MCP independente, PostgreSQL e React/TypeScript. A ferramenta MCP `buscar_produtos(termo)` consulta a API DummyJSON. O agente, a lógica de compras, a integração com OpenRouter e a ferramenta `detalhar_produto` ainda não estão implementados.

## Iniciar com Docker Compose

Requisitos: Docker Engine/Desktop com containers Linux e Docker Compose v2.

Na raiz do repositório:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

A cópia do `.env` é opcional: `docker compose up --build` funciona com os valores locais padrão. O Compose lê `.env` automaticamente. Para iniciar em segundo plano e aguardar os healthchecks:

```powershell
docker compose up --build -d --wait --wait-timeout 240
docker compose ps
```

Os quatro serviços devem aparecer como `healthy`. Na primeira execução, o download das imagens e dependências pode demorar.

| Serviço | Endereço no host | Endereço na rede do Compose |
| --- | --- | --- |
| Frontend | http://localhost:5173 | `frontend:80` |
| Backend | http://localhost:8000 | `backend:8000` |
| MCP | http://localhost:8001/mcp | `mcp-server:8001/mcp` |
| PostgreSQL | Sem porta publicada | `postgres:5432` |

A rede padrão do Compose permite resolver os nomes dos serviços por DNS. `localhost` dentro de um container aponta para esse próprio container. O navegador usa `/api/` no frontend; o Nginx encaminha esse caminho ao backend pela rede interna, removendo o prefixo. Exemplo: `http://localhost:5173/api/health` chega a `http://backend:8000/health`. O frontend continua estático e ainda não faz chamadas de compras.

## Configuração

- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`: banco, usuário e senha.
- `BACKEND_PORT`, `MCP_PORT`, `FRONTEND_PORT`: portas publicadas no host. As portas internas permanecem fixas no Compose.
- `DB_STARTUP_ATTEMPTS`, `DB_STARTUP_INTERVAL_SECONDS`: limite e intervalo das tentativas de conexão do backend.
- `MCP_SERVER_URL`: o Compose fornece `http://mcp-server:8001/mcp` ao backend, reservado para a futura integração.
- `PRODUCT_API_TIMEOUT_SECONDS`: timeout positivo e finito do HTTPX na consulta à DummyJSON, padrão `10` segundos para cada fase de I/O (conexão, leitura, escrita e espera pelo pool). Valores inválidos impedem a inicialização do servidor MCP.

Os valores padrão são destinados ao desenvolvimento local: banco e usuário `purchase_agent`, senha de exemplo `purchase_local_only`. Substitua a senha em ambientes compartilhados. O `.env.example` não contém credenciais reais. Mantenha `.env` fora do Git. Valores vazios no `.env` usam os padrões definidos no Compose.

No Compose, o backend recebe os mesmos campos de conexão do PostgreSQL; a URL é montada com `URL.create`, aceitando caracteres especiais na senha. `DATABASE_URL` é uma alternativa apenas para executar o backend fora do Compose, usando o formato `postgresql+psycopg://...`. Nesse caso, forneça as variáveis ao processo: o backend não carrega `.env` automaticamente e o PostgreSQL precisa estar acessível fora da rede Docker.

`OPENROUTER_API_KEY`, `OPENROUTER_MODEL` e `VITE_API_BASE_URL` continuam reservados para etapas futuras. Nenhum segredo é enviado ao build do frontend. O proxy `/api/` já está configurado no Nginx.

## Inicialização e tratamento de falhas

1. O PostgreSQL inicia e seu healthcheck executa `pg_isready` via TCP.
2. O MCP inicia, expõe `/health` e registra somente `buscar_produtos` via Streamable HTTP em `/mcp`.
3. O backend aguarda ambos ficarem saudáveis. No lifespan do FastAPI, tenta conectar ao banco com limite de tentativas e timeout por conexão; ao esgotar as tentativas, termina com erro explícito.
4. O SQLAlchemy importa os modelos e executa `Base.metadata.create_all()` antes de aceitar requisições. Erros de criação das tabelas impedem a inicialização.
5. O frontend inicia após o backend ficar saudável e serve o build estático com Nginx.

O backend usa `pool_pre_ping` para descartar conexões antigas. `/health` consulta o banco e retorna HTTP 503 se ele ficar indisponível, sem expor credenciais ou detalhes da exceção. Logs de inicialização também não imprimem a URL do banco. Nenhum processo executa tentativas infinitas.

`create_all()` é idempotente para tabelas existentes, mas não altera suas colunas. Migrações de alterações futuras de esquema ficam para outra etapa. Execute apenas uma instância do backend durante esta inicialização simples.

## Validar os serviços

Em PowerShell, com as portas padrão:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8001/health
Invoke-RestMethod http://localhost:5173/health
Invoke-RestMethod http://localhost:5173/api/health
docker compose logs backend mcp-server postgres frontend
```

O backend retorna `{"status":"ok","database":"ok"}`; MCP e frontend retornam `{"status":"ok"}`. A documentação FastAPI está em `http://localhost:8000/docs`. O endpoint `/mcp` recebe o protocolo MCP, não uma página web.

## Buscar produtos por MCP, sem LLM

A implementação usa o SDK oficial Python `mcp==1.30.0`, com Streamable HTTP, respostas JSON e transporte sem estado. A assinatura pública é `buscar_produtos(termo: str)`. O parâmetro de contexto do SDK é interno e não aparece na descoberta da ferramenta.

O cliente HTTPX fica em `mcp-server/purchase_mcp/services/product_client.py`, separado da definição da ferramenta em `tools/product_search.py`. O SDK cria e fecha o cliente HTTPX no ciclo de vida de cada requisição MCP. A busca faz `GET https://dummyjson.com/products/search` com `q` enviado por `params`, sem concatenar o termo à URL. Espaços externos são removidos; termos vazios ou compostos apenas de espaços são rejeitados antes de qualquer chamada à API.

Para iniciar ou atualizar somente o MCP, preservando os outros serviços e volumes:

```powershell
docker compose up --build -d --no-deps --wait mcp-server
```

O cliente de teste incluído utiliza `ClientSession` e `streamable_http_client` do SDK oficial. Ele executa a negociação `initialize`, descobre as ferramentas com `tools/list` e chama `tools/call`, sem LLM:

```powershell
docker compose exec mcp-server python -m purchase_mcp.client phone --url http://mcp-server:8001/mcp
```

Esse comando testa o protocolo MCP real via HTTP e o nome interno do serviço na rede Docker. Também é possível executar o cliente no host, com as dependências do MCP instaladas:

```powershell
cd mcp-server
python -m purchase_mcp.client phone --url http://localhost:8001/mcp
```

A descoberta deve listar somente `buscar_produtos`. O resultado contém `isError` e `structuredContent`. Em caso de erro da ferramenta ou falha na conexão, o cliente termina com código de saída `1`.

### Contrato de saída

| Campo | Significado |
| --- | --- |
| `status` | `success` quando existem produtos; `no_results` quando a API respondeu validamente sem resultados |
| `query`, `source` | Termo normalizado e origem `dummyjson` |
| `products` | Produtos retornados pela API, sem produtos locais de substituição |
| `total`, `skip`, `limit` | Metadados de paginação fornecidos pela DummyJSON; a busca mantém a primeira página e o limite padrão da API |

Cada produto contém `id`, `name` (campo `title` da API), `price` e `description`. Quando disponíveis, também são retornados categoria, marca, SKU, estoque, avaliação, desconto, disponibilidade, quantidade mínima, garantia, entrega, política de devolução, thumbnail, imagens e tags. Campos opcionais ausentes são `null`. Avaliações individuais e e-mails de avaliadores não são retornados.

O preço é convertido diretamente do número JSON para `Decimal`, sem cálculos com `float`, e serializado como string decimal em `structuredContent`. Não há conversão nem suposição de moeda: a resposta não informa uma moeda. A DummyJSON fornece um catálogo de demonstração; os dados exibidos são consultados na API, sem geração de produtos pela aplicação.

### Erros e logs

| Situação | Resultado MCP |
| --- | --- |
| Busca válida sem resultados | `isError=false`, `status=no_results`, `products=[]`, `total=0` |
| Termo vazio ou apenas espaços | `isError=true`, texto com código `invalid_term` |
| Timeout da API | `isError=true`, código `api_timeout` |
| Falha de rede ou protocolo HTTP | `isError=true`, código `api_unavailable` |
| Resposta HTTP fora de 2xx, inclusive 429 e 5xx | `isError=true`, código `api_http_error` e status HTTP |
| Content-Type, JSON, produto ou paginação inválidos | `isError=true`, código `invalid_response` |

Falhas nunca são convertidas em listas vazias nem recebem produtos de substituição. Não há retentativas automáticas. Os logs da aplicação incluem status, contagem, duração e categoria de falha; não incluem termos de busca, corpos de resposta, credenciais ou URLs com parâmetros. Os logs detalhados de HTTPX e HTTPCore são limitados a WARNING.

### JSON-RPC manual em PowerShell

Para testar diretamente as mensagens do protocolo, sem usar o cliente Python, execute na ordem abaixo:

```powershell
$mcpUrl = 'http://localhost:8001/mcp'
$headers = @{ Accept = 'application/json, text/event-stream' }

$initialize = @{
    jsonrpc = '2.0'; id = 1; method = 'initialize'
    params = @{
        protocolVersion = '2025-11-25'
        capabilities = @{}
        clientInfo = @{ name = 'manual-test'; version = '1.0' }
    }
}
$handshake = Invoke-RestMethod -Uri $mcpUrl -Method Post -Headers $headers -ContentType 'application/json' -Body ($initialize | ConvertTo-Json -Depth 10)
$headers['MCP-Protocol-Version'] = $handshake.result.protocolVersion

$initialized = @{ jsonrpc = '2.0'; method = 'notifications/initialized' }
Invoke-RestMethod -Uri $mcpUrl -Method Post -Headers $headers -ContentType 'application/json' -Body ($initialized | ConvertTo-Json)

$list = @{ jsonrpc = '2.0'; id = 2; method = 'tools/list'; params = @{} }
Invoke-RestMethod -Uri $mcpUrl -Method Post -Headers $headers -ContentType 'application/json' -Body ($list | ConvertTo-Json)

$call = @{
    jsonrpc = '2.0'; id = 3; method = 'tools/call'
    params = @{ name = 'buscar_produtos'; arguments = @{ termo = 'phone' } }
}
$response = Invoke-RestMethod -Uri $mcpUrl -Method Post -Headers $headers -ContentType 'application/json' -Body ($call | ConvertTo-Json -Depth 10)
$response.result | ConvertTo-Json -Depth 20
```

Como o transporte é sem estado, não é necessário guardar `Mcp-Session-Id`. O status HTTP 200 do envelope não garante sucesso da ferramenta: confira `result.isError`. Para testar ausência de resultados, use um termo inexistente; para validar a entrada, use `termo = '   '`. Ajuste a porta se `MCP_PORT` for alterada.

## Validar a criação das tabelas

Com os valores padrão, execute os comandos abaixo. Caso tenha alterado `POSTGRES_USER` ou `POSTGRES_DB`, substitua os argumentos `-U` e `-d` pelos valores configurados.

```powershell
docker compose exec postgres psql -U purchase_agent -d purchase_agent -c '\dt'
docker compose exec postgres psql -U purchase_agent -d purchase_agent -c '\d purchase_requests'
docker compose exec postgres psql -U purchase_agent -d purchase_agent -c '\d purchase_request_items'
```

Para consultar diretamente o catálogo do PostgreSQL em PowerShell, com `POSTGRES_USER=purchase_agent` e `POSTGRES_DB=purchase_agent`:

```powershell
$catalogSql = @'
SELECT schemaname, tablename
FROM pg_catalog.pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('purchase_requests', 'purchase_request_items')
ORDER BY tablename;
'@
$catalogSql | docker compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U purchase_agent -d purchase_agent
```

A consulta deve retornar exatamente as duas tabelas esperadas no schema `public`. Os argumentos `-U` e `-d` devem acompanhar os valores de `POSTGRES_USER` e `POSTGRES_DB` se forem alterados. Essa validação apenas lê o catálogo e não modifica tabelas ou volumes.

Devem existir:

- `purchase_requests`: `id`, `title`, `requester_name`, `currency` e `created_at` com fuso horário.
- `purchase_request_items`: `id`, `purchase_request_id`, `product_id` opcional, `description`, `quantity` e `unit_price`.

Confira que `unit_price` é `numeric(14,2)`, que a chave estrangeira aponta para `purchase_requests(id)` com `ON DELETE CASCADE` e que existem restrições para quantidade positiva e preço não negativo. No Python, o preço usa `Decimal`; não há cálculos monetários com `float`. Os modelos apenas definem a estrutura de armazenamento, sem operações de compra ou endpoints CRUD.

## Persistência e reinicialização

O volume nomeado `postgres-data` guarda os dados em `/var/lib/postgresql/data` (PostgreSQL 17). Para verificar a reinicialização preservando o volume:

```powershell
docker compose down
docker compose up -d --wait --wait-timeout 240
```

Repita os comandos de inspeção das tabelas. `docker compose down` preserva o volume. Não utilize o parâmetro `-v` nos testes: ele remove os volumes e apaga os dados locais. As variáveis `POSTGRES_*` inicializam um volume vazio; alterar usuário, senha ou nome do banco no `.env` não modifica um volume já inicializado. Para problemas de autenticação após mudanças, restaure os valores originais ou ajuste as credenciais no banco existente.

## Organização e dependências

- `compose.yaml`: quatro serviços e volume persistente na rede padrão.
- `backend/app/core/`: configuração por ambiente.
- `backend/app/database.py`: engine, inicialização e sessões por requisição.
- `backend/app/models/`: modelos SQLAlchemy 2 e registro dos metadados.
- `backend/app/api/`, `services/`, `schemas/`: rotas e camadas de aplicação.
- `mcp-server/purchase_mcp/`: transporte MCP, healthcheck, cliente HTTPX, schemas, ferramenta de busca e cliente MCP de teste.
- `frontend/src/`: React, TypeScript e Vite; `frontend/nginx.conf`: arquivos estáticos, healthcheck e proxy.
- Cada componente tem seu Dockerfile e arquivo de dependências. O frontend usa `package-lock.json` e `npm ci` em build de múltiplos estágios.

## Verificações de desenvolvimento

O backend utiliza `unittest` e `httpx` apenas nas dependências de teste. A partir da raiz, com Python 3.12:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-test.txt
cd backend
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

Esses testes validam configuração, tentativas de conexão, criação idempotente, relacionamentos, restrições, declaração PostgreSQL de `NUMERIC` e falhas do healthcheck. Os testes de armazenamento usam SQLite em memória; não substituem a validação real do PostgreSQL via Compose descrita acima.

Para o MCP, instale suas dependências em um ambiente próprio e execute `python -m unittest discover -s tests -v` dentro de `mcp-server`.

Os testes do MCP usam HTTPX MockTransport para simular falhas e validar o contrato, sem depender da disponibilidade da DummyJSON. A descoberta e as chamadas são testadas com o cliente oficial MCP via ASGI/Streamable HTTP. Para validar a consulta externa real e a rede TCP, execute o cliente de teste contra o container conforme os comandos acima.

Para o frontend: `cd frontend`, `npm.cmd ci` e `npm.cmd run build`. No Linux/macOS, use `npm`. Node.js 22.12+ ou 24+ é necessário. Para desenvolvimento local do frontend, `npm.cmd run dev`; o proxy Nginx está disponível na versão Docker.

## Referências

- [Docker Compose: ordem de inicialização](https://docs.docker.com/compose/how-tos/startup-order/)
- [SQLAlchemy 2: modelos declarativos](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html)
- [FastAPI: lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [SDK oficial MCP para Python, linha v1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
- [DummyJSON: produtos e busca](https://dummyjson.com/docs/products)
- [HTTPX: timeouts](https://www.python-httpx.org/advanced/timeouts/)

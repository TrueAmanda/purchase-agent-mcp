# Testes de integração

Os testes executam as rotas FastAPI, o serviço de agente, a descoberta e as chamadas MCP pelo SDK oficial, os serviços/repositório reais e a leitura SQL pelo GET. As respostas HTTP de OpenRouter e DummyJSON são simuladas com HTTPX. O modelo configurado nos testes e o token de teste são fictícios; nenhuma chave real é necessária.

O transporte entre backend e MCP é ASGI em memória, com mensagens reais do protocolo Streamable HTTP. Os testes TCP existentes do MCP continuam verificando uma conexão HTTP local real. Isso testa a orquestração e os contratos; respostas simuladas de um LLM não demonstram sua capacidade real de interpretar qualquer pedido.

## Executar

Na raiz, instale as dependências no ambiente virtual, sem precisar ativá-lo no PowerShell:

```powershell
python -m venv mcp-server/.venv
.\mcp-server\.venv\Scripts\python.exe -m pip install -r mcp-server/requirements-ci.txt -r backend/requirements-test.txt -e ./shared
```

Todos os testes Python, com SQLite temporário:

```powershell
.\mcp-server\.venv\Scripts\python.exe -m pytest -q
```

Apenas a nova integração HTTP:

```powershell
.\mcp-server\.venv\Scripts\python.exe -m pytest backend/tests/integration -v
```

Todos os testes Python com PostgreSQL real e isolado:

```powershell
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from mcp-tests
```

O serviço histórico `mcp-tests` agora executa as suítes do backend e do MCP. O código de saída é o do Pytest. Não há serviços de desenvolvimento, portas publicadas, arquivos `.env`, credenciais reais ou volumes locais montados nessa execução. A rede de teste é interna e o PostgreSQL usa `tmpfs`. O build inicial precisa de internet para instalar dependências; os testes não acessam serviços externos.

As integrações novas, os testes do adaptador e os testes MCP utilizam o PostgreSQL nesse modo. Alguns testes unitários antigos de rotas/configuração continuam criando seu SQLite isolado; não são apresentados como testes do dialeto PostgreSQL.

Cada teste recebe um banco SQLite temporário ou um schema PostgreSQL novo. A fixture compartilhada em `test_support/database.py` aceita somente `TEST_DATABASE_URL` com driver `postgresql+psycopg` e banco `purchase_mcp_test`; nunca usa `DATABASE_URL` ou `POSTGRES_*` da aplicação. Remove apenas o schema aleatório criado para o teste. Fixtures bloqueiam HTTP não simulado. Violações SQL de CHECK/uniqueness nos logs do PostgreSQL são esperadas nos testes que forçam rollback e concorrência.

Ao terminar, remova somente os containers e a rede de testes:

```powershell
docker compose -f compose.test.yaml down
```

Não utilize `down -v`. O Compose normal e seu volume PostgreSQL são preservados.

Lint e frontend:

```powershell
.\mcp-server\.venv\Scripts\python.exe -m ruff check backend mcp-server shared conftest.py test_support tests/manual
cd frontend
npm.cmd ci
npm.cmd test
npm.cmd run build
cd ..
```

O frontend Docker é Nginx e não contém Node/npm. Valide a imagem com `docker compose build frontend`, em vez de `docker compose exec frontend npm run build`.

## Cobertura

Resultados desta etapa:

| Verificação | Resultado |
| --- | --- |
| Python local, incluindo 36 novos testes de integração | 440 aprovados, 0 reprovados, 1 pulado (migração específica de PostgreSQL) |
| Python no Compose com PostgreSQL isolado | 441 aprovados, 0 reprovados, nenhum pulado |
| MCP executado pelo comando isolado anterior | 167 aprovados, 0 reprovados, 1 pulado |
| Frontend | 95 aprovados; build TypeScript/Vite aprovado |
| Ruff | Aprovado, incluindo fixtures e utilitários novos |
| Compose principal | Quatro serviços `healthy`; proxy, GET e tabelas conferidos |

Essas execuções se sobrepõem e seus totais não devem ser somados. O banco de desenvolvimento permaneceu com seis requisições antes e depois; nenhuma compra real foi criada pela suíte. Os containers/rede de testes foram removidos com `docker compose -f compose.test.yaml down`, sem remoção de volumes. A configuração do override de falha foi validada e seu cliente simulado foi testado automaticamente; os cenários A/B/C completos com LLM real não foram executados nesta etapa.

| Fluxo | Evidência automatizada |
| --- | --- |
| POST válido e GET | Criação por MCP, resposta 201, consulta em outra sessão SQL e igualdade com o GET |
| Valores reais da integração | Produtos vêm do mock HTTP da DummyJSON; nomes, preços e IDs são comparados à resposta externa |
| Totais | Valores JSON decimais, subtotal = quantidade × preço e total = soma dos subtotais |
| Ausência de opções adequadas | Catálogo vazio ou candidatos rejeitados pelo LLM simulado; nenhuma gravação automática |
| Entradas inválidas | 422 antes de qualquer chamada LLM/MCP ou gravação |
| DummyJSON indisponível | Timeout, erro de rede e HTTP 503, na busca e durante a validação do segundo item |
| MCP indisponível | Falha na descoberta ou no `tools/call`, timeout e mensagem HTTP segura |
| OpenRouter | HTTP 401/429/503, timeout e JSON inválido; chamadas já feitas continuam no histórico |
| Consistência SQL | Falha após INSERT de item e antes do COMMIT desfaz cabeçalho e itens |
| Confirmação obrigatória | Erro de leitura depois de uma escrita MCP bem-sucedida não retorna status `created` |
| Erro depois do commit | O POST pode falhar com registro completo existente; GET o recupera e a mesma chave retorna replay sem duplicar |
| Produto inexistente | 404 externo vira erro controlado; nenhum item ou cabeçalho parcial |
| Preço arbitrário do LLM | Schema rejeita antes de chegar ao servidor MCP |
| Decisão dinâmica | Criação direta, busca/detalhamento/criação, detalhamento/busca/criação e busca repetida |
| Histórico de ferramentas | Ordem e status comparados aos RPCs enviados; repetições preservadas e argumentos sensíveis ausentes |
| Criação automática de tabelas | Startup real do backend e do MCP sobre armazenamento vazio; healthcheck após a inicialização |

Uma falha não significa sempre rollback: se o COMMIT terminou e o provedor falhou depois, o registro está completo e deve permanecer. O teste diferencia essa situação de falhas dentro da transação. O endpoint não insere registros para compensar uma resposta do LLM.

As fixtures de protocolo em `backend/tests/conftest.py` são compartilhadas com os testes antigos do adaptador. Os testes originais de rotas, agente, servidor MCP, migração, idempotência e frontend continuam ativos. O comando isolado `python -m pytest mcp-server -q` também continua suportado.

## Preparar os cenários manuais

Configure a chave OpenRouter somente no `.env`, sem copiá-la para mensagens, prints ou Git. Inicie a aplicação:

```powershell
docker compose up --build -d --wait --wait-timeout 240
docker compose ps
```

Os quatro serviços devem estar `healthy`. Endereços padrão: chat em http://localhost:5173, OpenAPI em http://localhost:8000/docs e endpoints em http://localhost:8000/requisicoes. Se alterou as portas no `.env`, use as portas correspondentes. No chat, use **Nova conversa** antes de cada cenário. Cada chamada OpenRouter real pode consumir créditos.

No Postman, use `POST http://localhost:8000/requisicoes`, `Content-Type: application/json`, campo `message` e `include_tool_trace: true`. `conversation_id: null` inicia contexto; para continuar, envie o UUID retornado. Para consultar registros, use `GET http://localhost:8000/requisicoes?limit=10&offset=0`.

### A. Pedido válido com criação e persistência

Cole o texto de `message` no chat, ou envie no Postman:

```json
{
  "message": "Quero comprar 3 laptops para o time de vendas, até US$ 2.000 por unidade. Pesquise as opções e, se atenderem às condições, crie uma requisição em nome de Amanda com o título Validação de integração A.",
  "include_tool_trace": true,
  "conversation_id": null
}
```

Esse cenário pode criar um registro real. Considere aprovado somente se houver `status: created`, requisição estruturada e confirmação no PostgreSQL. Compare número, quantidade, preço, subtotais, total e moeda com o GET e com a consulta SQL abaixo. Nova criação retorna 201; replay retorna 200. Não há quantidade fixa de chamadas esperada: o modelo escolhe sua sequência.

Os comandos abaixo correspondem aos padrões `POSTGRES_USER=purchase_agent` e `POSTGRES_DB=purchase_agent` do `.env.example`. Se escolheu outros valores, substitua-os nos argumentos `-U` e `-d`.

```powershell
@'
SELECT tablename
FROM pg_catalog.pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('purchase_requests', 'purchase_request_items');

SELECT r.id, r.request_number, r.currency, r.total_amount,
       COUNT(i.id) AS item_count,
       SUM(i.subtotal) AS item_total,
       SUM(i.quantity * i.unit_price) AS calculated_total
FROM purchase_requests r
LEFT JOIN purchase_request_items i ON i.purchase_request_id = r.id
GROUP BY r.id
ORDER BY r.id DESC
LIMIT 10;
'@ | docker compose exec -T postgres psql -U purchase_agent -d purchase_agent
```

Para uma requisição nova, `total_amount`, `item_total` e `calculated_total` devem coincidir. Compare o UUID com o retornado pelo POST, sem assumir um ID fixo. Registros antigos podem ter campos nulos e não devem ser reescritos para o teste.

Se faltarem produtos adequados no catálogo atual, a recusa é correta e não valida a criação. Se o modelo pedir de novo uma autorização já fornecida, registre isso como limitação de comportamento, não como teste aprovado. Não repita uma compra com uma nova chave depois de timeout; consulte primeiro o GET.

### B. Pedido impossível sem produtos inventados

```json
{
  "message": "Preciso de 2 unidades exclusivamente do modelo INEXISTENTE-QX-987654321, até US$ 1 por unidade. Não substitua o modelo. Se encontrar exatamente esse produto dentro das condições, registre em nome de Amanda, título Validação de integração B.",
  "include_tool_trace": true,
  "conversation_id": null
}
```

Esperado: explicação de ausência de correspondência ou impossibilidade, `requisitions: []`, nenhum cartão de registro confirmado e nenhuma nova linha correspondente no GET/SQL. O painel deve mostrar somente as ferramentas realmente chamadas. Uma busca textual vazia não permite declarar que uma categoria inteira não existe. Não use uma categoria real apenas com um sinônimo não reconhecido como prova de impossibilidade.

### C. DummyJSON com HTTP 503 e mensagem clara no chat

O override de teste mantém MCP, backend e PostgreSQL reais, mas substitui apenas o cliente HTTP da DummyJSON por HTTPX MockTransport com HTTP 503. O script fica montado somente para leitura; não é parte da imagem de execução. Ative explicitamente:

```powershell
docker compose -f compose.yaml -f compose.dummyjson-failure.yaml up -d --no-deps --force-recreate --wait --wait-timeout 180 mcp-server
```

Use **Nova conversa** e envie:

```text
Pesquise agora no catálogo opções de máscara de cílios por até US$ 30 por unidade, para eu avaliar.
```

Esperado: POST com HTTP 502 e `detail.code: api_http_error`, texto claro sobre falha da DummyJSON e chamada `buscar_produtos` marcada como falha. Não deve aparecer “nenhum produto encontrado” como explicação de uma API indisponível, nem confirmação de compra. O healthcheck pode continuar saudável, pois verifica o processo e seu banco, não a disponibilidade externa do catálogo.

Restaure a configuração normal depois do cenário, inclusive se o teste falhar:

```powershell
docker compose -f compose.yaml up -d --no-deps --force-recreate --wait --wait-timeout 180 mcp-server
docker compose ps
```

Abra outra conversa e repita a pesquisa para conferir a recuperação. Não deixe o override ativo para os demais testes. Nenhum desses comandos remove volumes.

## Pendências da validação do case

O documento original foi localizado em Downloads e conferido. A matriz por requisito e página está em [case-validation.md](case-validation.md). O PDF não foi copiado para o projeto, pois contém uma credencial.

Permanecem fora da comprovação automatizada:

- Decisões semânticas do OpenRouter real: tradução de termos, associação de sinônimos a categorias, orçamento, quantidade, recusa de escopo e autorização sem confirmação redundante. A suíte comprova execução das decisões simuladas, não sua qualidade em todos os pedidos.
- Os três cenários reais A/B/C desta seção exigem execução manual com o modelo e catálogo do momento. Não são executados pela CI e não foram apresentados como aprovados por testes simulados.
- Validação visual da responsividade e do chat em navegador/dispositivo real. Testes DOM e build não substituem essa inspeção.
- Disponibilidade futura das APIs externas, carga, falhas do host e auditoria completa do histórico Git. Nenhuma dessas propriedades é garantida por esta suíte.

Os problemas já observados de `perfumes` versus categoria `fragrances`, confirmação redundante ocasional e narrativa imprecisa do LLM após erro precisam ser considerados nos testes reais. Não foram escondidos mudando asserções ou adicionando uma sequência fixa de ferramentas.

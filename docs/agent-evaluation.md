# Avaliação real com cinco pedidos

`backend/evals/purchase_evaluation.py` é um script opcional, separado da suíte unitária. Faz cinco POSTs independentes ao backend em execução, com OpenRouter, MCP, DummyJSON e PostgreSQL reais. Não simula decisões do modelo, não muda o agente e não determina sua sequência de ferramentas.

**Consome créditos e pode criar registros de teste no banco local.** O acesso SQL do avaliador usa transações somente de leitura; todas as gravações passam pelo POST e pela ferramenta MCP. Execute sem outras pessoas/processos fazendo compras simultaneamente: registros novos concorrentes são considerados evidência ambígua e fazem o cenário falhar. Nenhum registro é apagado pelo script.

## Executar no PowerShell

Configure a chave e o modelo no `.env` local, sem versionar a chave. Na raiz do projeto:

```powershell
docker compose up --build -d --wait --wait-timeout 180
New-Item -ItemType Directory -Force .agent-evaluation | Out-Null
docker compose run --rm --no-deps --volume "${PWD}/backend/evals:/evaluation:ro" --volume "${PWD}/.agent-evaluation:/evaluation-data" backend python /evaluation/purchase_evaluation.py --execute --state /evaluation-data/run.json
```

O container descartável usa as dependências existentes e as configurações de banco do backend. Envia o pedido ao backend principal em `http://backend:8000`, que acessa o MCP pela rede interna. O avaliador consulta a DummyJSON diretamente como fonte independente e o PostgreSQL por SQLAlchemy, sem criar tabelas nem executar migrações. Não instala pacotes adicionais. O arquivo de resultados fica em `.agent-evaluation/run.json`, ignorado pelo Git, sem chave, configuração de conexão ou payload bruto de erros do provedor.

Fora do Docker, instale as dependências existentes do backend e o pacote `shared`, forneça as variáveis `POSTGRES_*` ou `DATABASE_URL` para o **mesmo banco usado pela API** e execute:

```powershell
.\mcp-server\.venv\Scripts\python.exe backend/evals/purchase_evaluation.py --execute --api-url http://localhost:8000 --state .agent-evaluation/run.json
```

O PostgreSQL do Compose não publica porta no host; prefira o primeiro comando. O script não carrega `.env` sozinho e não precisa receber uma chave LLM via argumentos: quem chama a OpenRouter é o backend. `OPENROUTER_MODEL`, quando disponível no processo avaliador, é registrado como metadado da configuração, não como prova do modelo realmente servido pelo provedor.

## Critérios objetivos

Antes de enviar pedidos, o avaliador consulta integralmente as categorias `laptops` e `smartphones` da DummyJSON. Seleciona uma referência de menor preço com estoque/pedido mínimo confirmados para calcular quantidade viável e orçamento total com 10% de margem. Essa referência não força o produto a ser escolhido pelo agente: qualquer produto da categoria que cumpra as restrições pode passar. Preços são lidos como Decimal, sem aritmética monetária em ponto flutuante. A moeda USD segue a política do projeto.

| Cenário | Evidências exigidas |
| --- | --- |
| Laptops com orçamento | POST confirma exatamente uma criação; SQL confirma número, categoria real, quantidade exata, estoque, mínimo, preços externos, subtotais e total dentro do orçamento; GET reproduz os itens persistidos; histórico mostra busca e uma criação reais |
| Smartphones | Mesmos critérios, para a categoria smartphones e o orçamento/quantidade calculados a partir dela |
| Produto inexistente | Modelo exato único, previamente consultado na API externa; busca MCP sem resultados, resposta sem criação e nenhuma nova gravação; sem aceitar substitutos |
| Quantidade inválida | Pedido de zero unidades, sem alterar a quantidade; explicação sobre quantidade e nenhuma gravação ou chamada de criação |
| Orçamento impossível | Um laptop por no máximo US$ 0,01; pré-verificação de que nenhum laptop do catálogo cabe; consulta MCP, explicação do orçamento e nenhuma gravação |

Para cada chamada, confere nomes permitidos, ordem, status e duração do `tool_trace` produzido pelo executor. Não exige `detalhar_produto` nem uma sequência fixa. Chamadas repetidas permanecem distintas. O trace é evidência do backend; o texto do LLM nunca é convertido em atividades fictícias.

Antes/depois de cada POST, o avaliador lê os IDs SQL e confere os novos registros e seus itens relacionados, além da ausência de itens órfãos. Para compras registradas, busca cada produto pelo ID diretamente na DummyJSON e compara preço/nome/categoria, quantidade, subtotais, total SQL, resposta POST e listagem GET. Mudança real de preço entre a criação e a conferência causa FAIL por divergência, sem tolerância silenciosa. Há limite de paginação do GET e timeouts de HTTP/conexão/consulta SQL.

PASS exige todos os critérios do cenário; HTTP 200/201 ou uma frase de sucesso isolada não bastam. HTTP de erro, ferramenta com falha, evidência incompleta, gravação adicional e orçamento excedido são FAIL. Ao final imprime cada resultado, motivos, ferramentas, números persistidos, quantidade de acertos e porcentagem. Saídas: `0` todos passaram; `1` algum cenário falhou/não foi concluído; `2` pré-condição, concorrência de execução ou falha que interrompeu a avaliação.

## Proteção contra duplicação

O diário durável é gravado **antes** de cada POST; um arquivo `.lock` impede dois processos de usar o mesmo diário. Nenhum POST é repetido automaticamente. Uma falha de transporte/contrato pode deixar uma gravação incerta: a avaliação para e marca os demais casos como não executados. Uma falha SQL também interrompe a execução, pois não há como confirmar novas compras com segurança.

Executar novamente com o mesmo `--state` **apenas imprime o relatório salvo**, mesmo se a execução anterior tiver sido interrompida. Não retoma nem repete os cenários. Preserve esse arquivo: apagá-lo remove a proteção entre execuções. Após encerramento forçado pode restar um `.lock`; confirme que não há processo ativo e consulte SQL/GET antes de removê-lo. Não remova um lock de avaliação ainda em andamento.

Uma nova avaliação paga, deliberada, usa outro arquivo, por exemplo `--state /evaluation-data/run-02.json`. Ela representa uma nova intenção de teste, com títulos únicos, e pode criar novas requisições. Não use novo diário para repetir uma compra com resultado incerto. A idempotência interna continua sendo responsabilidade da ferramenta existente; o avaliador detecta gravações duplicadas, mas não altera as decisões do LLM nem desfaz compras já confirmadas. Não use `docker compose down -v`.

## Resultado original em 01/11/2026

Após investigar e corrigir proteções financeiras, busca por categoria e tratamento de argumentos, uma nova rodada ficou em **4/5 (80%)**. O histórico original abaixo permanece intacto. Veja [causas, alterações, rodadas intermediárias e resultado atualizado](evaluation-fixes.md).

Modelo configurado: `openai/gpt-4.1-mini`. Execução `22eb1a77-eda2-4b2d-9ea7-db0978781099`, cinco POSTs, sem repetição para melhorar a pontuação:

| Cenário | Resultado | Evidência e causa provável |
| --- | --- | --- |
| Laptops, 2 unidades, teto total US$ 2.419,98 | **FAIL** | Busca → criação, HTTP 201, SQL/GET confirmados. Gravou 2 Huawei Matebook X Pro a US$ 1.399,99: total US$ 2.799,98, **US$ 380 acima do teto**. A fonte/preços/aritmética estavam corretos, mas o modelo não respeitou o orçamento; a gravação não teve uma proteção determinística contra essa restrição do pedido |
| Smartphones, 3 unidades, teto total US$ 494,97 | **FAIL** | HTTP 200, somente busca (2 produtos), nenhuma gravação. O catálogo completo tinha Realme C35 a US$ 149,99, mínimo 3 e estoque 48, viável por US$ 449,97. A execução não concluiu a pesquisa/seleção/criação; o trace público não permite afirmar o termo exato nem o motivo textual do modelo |
| Produto inexistente | **FAIL** | HTTP 502, nenhuma gravação e nenhuma invocação MCP no histórico público. Logs do backend mostraram tentativa local de `detalhar_produto` rejeitada por `invalid_tool_arguments`; a busca necessária não ocorreu. Validação impediu a execução inválida, mas o pedido não terminou como ausência de produto bem tratada |
| Quantidade zero | **PASS** | HTTP 200, explicou a quantidade inválida, nenhuma ferramenta e nenhuma nova requisição |
| Orçamento de US$ 0,01 | **PASS** | HTTP 200, busca real com 5 candidatos, explicou a restrição e não gravou |

**Total: 2/5 acertos — 40%.** A contagem do banco passou de 6 para 7; foi preservada a requisição de teste `250c4e84-699b-472a-8a5f-01e4a30ff5ad` (ID 7), inclusive por ter revelado a falha de orçamento. Não foi corrigida a lógica do agente para fazer a avaliação passar. O relatório local conserva os resultados originais; melhorias posteriores nas mensagens do avaliador não reexecutam nem reclassificam esse histórico.

Esses cinco pedidos não comprovam conformidade geral do LLM, qualidade de toda frase da resposta, resistência a toda injeção nem a UI no navegador. Em particular, a semântica dos dois cenários positivos falhou nesta execução. Os testes determinísticos do avaliador verificam os critérios e as proteções, não substituem os resultados reais acima.

## CI e testes offline do avaliador

O script tem guarda `if __name__ == "__main__"`, exige `--execute` para uma nova avaliação e não fica nos caminhos `testpaths` do Pytest. Não foi adicionado à CI. A suíte normal testa somente seu verificador, diário e transporte simulado:

```powershell
.\mcp-server\.venv\Scripts\python.exe -m pytest backend/tests/test_purchase_evaluation.py -q
.\mcp-server\.venv\Scripts\python.exe -m pytest -q
```

Os testes recusam falsos sucessos por ausência/duplicação SQL, GET divergente, número inventado, categoria/quantidade/preço/subtotal/total errados, orçamento excedido, estoque/mínimo violados, trace falso/falho e itens parciais/órfãos. Também verificam lock, gravação do diário antes do POST, cinco envios independentes, interrupção por timeout e reexecução sem chamadas adicionais. Não usam chave LLM nem serviços externos.

Validação desta implementação: **37 testes offline do avaliador aprovados; suíte completa com 477 aprovados, 1 pulado e 3 subtestes aprovados; Ruff sem erros**. O teste pulado é a migração PostgreSQL que exige o Compose isolado; não foi reexecutada nesta etapa. Os quatro serviços principais permaneceram saudáveis. A repetição real do comando com o mesmo diário apenas exibiu os 2/5 originais, sem novas chamadas ao agente.

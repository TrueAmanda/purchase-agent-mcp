# Correções investigadas a partir da avaliação real

## Investigação antes das alterações

O diário original (`.agent-evaluation/run.json`, execução `22eb1a77-eda2-4b2d-9ea7-db0978781099`) foi comparado aos logs do executor, às implementações do backend/MCP, ao registro SQL 7 e a consultas de leitura da DummyJSON. Os cinco cenários e os dados de referência do avaliador foram mantidos.

| Falha | Evidência | Causa |
| --- | --- | --- |
| Laptops excederam o teto | O texto dizia **orçamento TOTAL de US$ 2419.98**, para 2 unidades. SQL registrou 2 × 1399.99 = 2799.98 do produto 80. O catálogo também tinha uma opção de 1099.99 com estoque suficiente | O LLM escolheu acima do teto. A aritmética e a persistência eram corretas, mas o contrato de criação não carregava limites: a ferramenta verificava preços/precisão, sem comparar o total ao orçamento |
| Smartphones não foram gravados | O trace tinha uma busca com 2 resultados. Na investigação, `search?q=smartphones` retornou Selfie Stick Monopod e Oppo K1; a categoria `smartphones` tinha 16 produtos, incluindo Realme C35 a 149.99, mínimo 3, estoque 48 | A consulta por categoria só acontecia quando a busca textual era vazia. Uma correspondência parcial não vazia ocultava opções viáveis. O termo exato da execução original não foi salvo; a reprodução é compatível com a contagem, sem provar o argumento original do LLM |
| Produto inexistente retornou 502 | Logs: `detalhar_produto outcome=invalid_tool_arguments`, seguido de resposta final; nenhum RPC de ferramenta no trace | O modelo tentou usar a ferramenta de ID com argumentos rejeitados. O feedback era genérico, centrado em UUID de idempotência. O backend mapeava a rejeição local para erro genérico 502, inclusive quando o modelo poderia corrigi-la em outra rodada |

Não houve indício de arredondamento binário ou erro de soma no registro de laptops. O preço unitário gravado era real; **ser real não tornava a seleção compatível com o orçamento**. Também não havia falta de produtos viáveis na DummyJSON para os dois pedidos positivos.

Os critérios financeiros e de persistência do avaliador estavam corretos: orçamento total, quantidade exata, preço externo, soma de itens, SQL e GET. A pontuação é deliberadamente estrita: exige HTTP 200/201, todas as invocações MCP bem-sucedidas e uma única chamada de criação nos positivos. Assim, uma rejeição segura com 422, ou uma tentativa rejeitada seguida de correção, pode receber FAIL mesmo sem gravação indevida. Esses critérios não foram relaxados para aumentar a nota.

## Proteção antes da gravação

`backend/app/services/purchase_policy.py` extrai limites das mensagens **do usuário** mantidas pelo servidor. Textos do LLM, descrições e resultados de ferramentas não estabelecem orçamento. Reconhece formas explícitas em português/inglês, como `orçamento total de US$ 500`, `US$ 200 por unidade`, `USD 100 each`, quantidades numéricas e categoria explícita. Valores com centavos e agrupamentos de milhares em português/inglês são convertidos em Decimal.

O adaptador MCP sobrescreve os limites propostos pelo modelo com os limites derivados desse contexto. O modelo não consegue omitir o teto, aumentá-lo ou transformar um teto total em unitário. Valores/escopos ambíguos bloqueiam **escritas** e exigem esclarecimento; pesquisar continua permitido. O prompt recebe também o teto unitário calculado para comprar toda a quantidade de um único produto.

O argumento `request` de `criar_requisicao` ganhou `constraints` opcional:

```json
{
  "constraints": {
    "max_total": "500.00",
    "max_unit_price": "200.00",
    "quantity": 3,
    "category": "example-category"
  }
}
```

Esse fragmento ilustra limites, não preços de produtos. `max_total` limita a soma de todos os itens; `max_unit_price` limita cada unidade. Ambos podem ser usados juntos. `quantity` é a quantidade total autorizada; `category` deve corresponder ao catálogo. `product_name`, quando informado para um pedido de modelo/produto exato, deve corresponder ao nome ou SKU real, sem substitutos.

Dentro do MCP, a criação consulta novamente cada produto e calcula preços/subtotais/total com Decimal e `ROUND_HALF_UP`. Antes da transação, valida limites financeiros, quantidade, categoria/nome exato, estoque e pedido mínimo quando estes últimos estão disponíveis. Preço ausente/inválido continua sendo erro; campos opcionais ausentes não são preenchidos com disponibilidade inventada. Uma violação gera `budget_exceeded` ou `purchase_constraints_violated`, sem cabeçalho nem itens parciais.

Os limites entram no fingerprint da intenção; a mesma chave com limites diferentes gera conflito. Limites monetários equivalentes são normalizados em centavos. Replays retornam o snapshot confirmado, sem nova cotação/escrita. A omissão dos campos opcionais preserva os fingerprints antigos; registros anteriores não são alterados. Um cliente MCP direto precisa informar os limites que deseja impor, pois o servidor MCP não recebe o texto da conversa. Pelo agente, o backend aplica os limites derivados do usuário.

Os resultados de busca/detalhamento recebidos pelo modelo também podem incluir `budget_checks`, calculados pelo backend a partir dos preços reais da ferramenta. Cada linha mostra ID, quantidade, subtotal e se aquele subtotal cabe nos limites. O cenário calculado é comprar toda a quantidade daquele único produto. Não escolhe produtos, não reserva estoque e não afirma viabilidade geral; combinações de itens podem exigir outra análise. A criação sempre revalida preços atuais. Esses detalhes não são expostos como argumentos privados no histórico público do chat.

## Busca e tool calling

`buscar_produtos` continua fazendo busca textual e agora confere o termo na lista oficial de categorias **mesmo quando há resultados textuais**. Para uma categoria exata, lê `/products/category/{slug}?limit=0`, valida categoria e completude da resposta e devolve `match_type: "category"`. Não há lista hardcoded de categorias/produtos nem equivalência semântica automática entre quaisquer sinônimos. Erros/timeout dessas consultas continuam sendo erros, nunca catálogo vazio.

`detalhar_produto` explica que seu argumento é um ID inteiro do catálogo. A rejeição local de um nome/modelo como ID devolve feedback específico ao modelo, correlacionado ao `tool_call_id`. O modelo pode corrigir o argumento ou escolher outra ferramenta dentro do mesmo limite de iterações. O executor não insere uma busca automática.

Uma rejeição de argumentos não corrigida retorna HTTP **422** com `invalid_tool_arguments`; não vira indisponibilidade externa. Uma chamada posterior de catálogo bem-sucedida permite recuperar uma rejeição local anterior. Falhas reais de MCP, DummyJSON ou persistência mantêm seus códigos e prioridade. Chamadas bloqueadas antes do RPC não aparecem como ferramentas executadas no chat. Rejeições financeiras e condições não atendidas também usam 422, com mensagens claras em português no frontend.

O backend continua sem lógica de INSERT. A ordem das ferramentas permanece determinada por `tool_choice: auto`; testes mantêm criação direta, busca/detalhamento em ordens diferentes e múltiplas chamadas. Nenhuma regra depende dos IDs, preços ou títulos dos cinco pedidos de avaliação.

## Primeira rodada após as correções

Execução `6e8b775d-2b8c-4c81-95a9-bff63ca2cb56`, diário `after-fixes.json`: **3/5 (60%)**.

- Laptops: PASS, 2 Lenovo Yoga 920 por 2199.98, abaixo de 2419.98; registro 8, número `4b38bdd2-d347-4976-a414-4e3bffe3267b`.
- Smartphones: FAIL, busca com 16 candidatos; o modelo tentou uma seleção acima do teto e a ferramenta a bloqueou com `budget_exceeded`, HTTP 422, nenhuma gravação.
- Produto inexistente: PASS, busca vazia, HTTP 200, nenhuma gravação.
- Quantidade zero: FAIL pelo critério HTTP do avaliador; o modelo tentou argumentos inválidos de criação, rejeitados localmente com 422. Nenhuma ferramenta foi invocada nem requisição gravada.
- Orçamento impossível: PASS, busca real e explicação, nenhuma gravação.

Essa rodada motivou os subtotais `budget_checks` e a orientação explícita para esclarecer quantidade zero. Não foram alterados o pedido, os limites, a referência de catálogo ou a pontuação do avaliador. O relatório intermediário foi preservado.

## Resultado final da rodada real

Execução `e15af186-563f-4f85-8bca-e0ad6da5ea6c`, diário `.agent-evaluation/after-budget-checks.json`, modelo configurado `openai/gpt-4.1-mini`: **4/5 (80%)**, contra **2/5 (40%)** originalmente. Os cenários, quantidades, limites e critérios do script não foram modificados.

| Cenário | Antes | Depois | Evidência final |
| --- | --- | --- | --- |
| Laptops | FAIL | **PASS** | Busca com 5 candidatos → criação; 2 Lenovo Yoga 920 × US$ 1.099,99 = **US$ 2.199,98**, abaixo do teto total de US$ 2.419,98; HTTP 201, SQL/GET confirmados |
| Smartphones | FAIL | **PASS** | Busca com 16 candidatos → criação; 3 Realme C35 × US$ 149,99 = **US$ 449,97**, abaixo do teto total de US$ 494,97; HTTP 201, SQL/GET confirmados |
| Produto inexistente | FAIL | **FAIL** | HTTP **422**, `invalid_tool_arguments`, nenhuma invocação MCP e nenhuma gravação. O modelo não concluiu uma busca válida; não há evidência para culpar a DummyJSON |
| Quantidade zero | PASS | **PASS** | HTTP 200, nenhuma ferramenta e nenhuma gravação |
| Orçamento impossível | PASS | **PASS** | HTTP 200, busca com 5 candidatos, nenhuma gravação |

A falha restante foi tratada com código controlado e sem dados fictícios, mas **o fluxo conversacional ainda não se recuperou nessa execução real**. O feedback permite corrigir argumentos e os testes simulados demonstram essa recuperação; isso não prova que o modelo sempre a escolherá. Não foi inventada uma busca nem acrescentada uma sequência fixa para obter PASS. Os argumentos brutos não são registrados; o diário final comprova o código de erro, mas não o campo exato rejeitado nessa última rodada. A causa original de 502 foi verificada nos logs disponíveis durante a investigação; os containers principais foram recriados posteriormente.

### Conferência independente no PostgreSQL

Após reiniciar os serviços, uma transação SQL somente de leitura confirmou:

- Registro **9**, número `6e038f37-15f2-458f-a5d0-d92791978036`: quantidade 2, soma dos itens/total **2199.98**.
- Registro **10**, número `705012c3-523d-4a68-ba8f-02c73f39ede4`: quantidade 3, soma dos itens/total **449.97**.
- Exatamente dois registros dessa rodada; nenhum dos três cenários negativos gerou registro.
- Nenhum título duplicado nessa rodada, nenhuma chave de idempotência duplicada, nenhum item órfão e nenhum cabeçalho sem itens no banco consultado.

As duas rodadas corrigidas representam intenções de teste distintas: a primeira criou o registro 8; a segunda criou 9 e 10. Não são retentativas da mesma compra. O registro antigo 7, acima do orçamento, foi preservado como evidência; não foi apagado nem alterado. Reexecutar com o mesmo diário apenas imprimiu o relatório salvo, sem reenviar os cinco pedidos.

### Validação automatizada

- Python local: **555 aprovados, 1 pulado, 3 subtestes aprovados**. O teste pulado exige PostgreSQL.
- Compose de teste isolado com PostgreSQL: **556 aprovados e 3 subtestes aprovados**, saída 0.
- Frontend: **104 testes aprovados**; TypeScript e build Vite aprovados.
- Ruff e `git diff --check`: sem erros.
- Quatro serviços principais iniciados com `docker compose up -d --wait`, todos saudáveis. Nenhum volume de desenvolvimento foi removido.

Regressões cobrem orçamento total versus unitário, Decimal, limite combinado, preço atualizado pela fonte, omissão/aumento de limites pelo modelo, quantidade/categoria/modelo exato, estoque/mínimo, bloqueio antes da gravação, idempotência, recuperação de argumentos e preservação de falhas externas. O executor continua aceitando ordens diferentes e criação direta. Testes unitários/integração não usam chave LLM nem chamadas reais aos provedores; as avaliações pagas permanecem separadas da CI.

## Limites da proteção

O parser de restrições tem escopo limitado, não interpreta toda linguagem natural. Formatos não reconhecidos, escopos conflitantes e múltiplas quantidades/categorias que não cabem no contrato simples podem exigir esclarecimento. Não trata de orçamentos separados por centros de custo, frete, impostos, conversão ou descontos negociados. A validação de criação cobre as restrições reconhecidas/fornecidas e os dados disponíveis na fonte; não representa uma garantia universal sobre qualquer pedido arbitrário.

Restrições das mensagens anteriores permanecem no contexto recente; uma nova conversa isola uma nova intenção. Expiração/evicção do histórico mantém os limites de memória já documentados. Ainda é necessário testar interpretações semânticas diversas do LLM, além destes cinco casos. Preço e disponibilidade podem mudar entre consultas; a requisição é um snapshot, não uma reserva de estoque.

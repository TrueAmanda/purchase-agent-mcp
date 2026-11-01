# Correções de segurança e confiabilidade

## Fronteira de escrita

O modelo continua escolhendo as ferramentas com `tool_choice: auto`. O backend não insere chamadas de busca, detalhamento ou criação. A autorização limita o que pode ser executado, sem escolher a sequência.

`PurchasePolicy` reconhece comandos explícitos de criação em português/inglês e proibições como `não crie`, `sem criar`, `do not create` e `only research`. Uma explicação sobre como criar não autoriza a gravação. A ausência de autorização bloqueia a chamada antes do RPC, com `purchase_not_authorized`/422. Chamar o MCP diretamente continua sendo uma operação técnica explícita do cliente; a política conversacional pertence ao backend.

Uma escrita exige quantidade numérica fornecida pelo usuário e alvo reconhecido: categoria explícita, modelo/nome exato ou IDs de catálogo. Há aliases de tipos comuns (por exemplo, celulares → smartphones), sem IDs/preços hardcoded. IDs explícitos não podem ser substituídos. Tipos não reconhecidos exigem esclarecer a categoria oficial ou IDs, em vez de aceitar um produto escolhido arbitrariamente pelo modelo. Ambiguidades de dinheiro, quantidade e categoria são independentes: atualizar o orçamento não resolve uma quantidade ambígua.

Os limites seguem até a ferramenta MCP, que consulta novamente os produtos e valida quantidades, categoria/nome exato, estoque/mínimo conhecido e orçamento total/unitário com Decimal antes da transação de escrita. O POST continua sem implementar uma segunda gravação de compras.

## Uma intenção, uma chave

Cada conversa representa **uma intenção de compra**. `PurchaseIntent` conserva política, chave e evidência da tentativa separadamente do texto recente. Descartar mensagens antigas não apaga esses controles. Após criação ou resultado incerto, dados diferentes não podem iniciar uma segunda gravação nessa intenção. Uma repetição confirmada em memória retorna o recibo real como replay, sem RPC inventado no histórico.

O backend substitui a chave proposta pelo LLM. O cabeçalho opcional `Idempotency-Key`, um UUID, identifica a intenção entre retentativas. O chat mantém essa chave na memória e no `sessionStorage` da aba, inclusive após recarregar a página; **Nova conversa** limpa a chave para uma intenção deliberadamente nova. Não são armazenados ali mensagens, nomes, preços ou credenciais.

Sem o cabeçalho, mensagens/contextos de usuário exatamente iguais derivam a mesma chave UUID5, inclusive quando a primeira resposta com o identificador da conversa se perde. Um hash incremental preserva essa identidade sem guardar uma cópia ilimitada do texto. A chave fica congelada na primeira tentativa real de escrita. Esse fallback não identifica paráfrases: clientes devem reutilizar o cabeçalho ao repetir uma compra. Mensagens diferentes com chaves diferentes representam intenções distintas; não é possível deduplicar toda semelhança semântica sem arriscar bloquear compras legítimas.

O índice único e o fingerprint existentes no PostgreSQL continuam sendo a proteção durável contra concorrência e reinício dos processos. A mesma chave não aceita outro título, solicitante, itens ou limites. Uma falha comprovadamente anterior à transação permite corrigir a seleção mantendo a chave; timeout de comunicação ou resultado desconhecido preserva os dados da tentativa. Não há retentativa automática de escrita. Após incerteza, consulte GET antes de alterar a compra ou gerar outra chave.

TTL, evicção da conversa inteira e reinício do backend ainda expiram o contexto com 410. A chave SQL continua existindo; o cliente deve preservá-la, conferir GET e fornecer novamente os dados completos ao recuperar uma operação. O estado de conversa não é um armazenamento distribuído.

## Respostas verificáveis e validação

O texto comercial do POST e da CLI é composto a partir de evidências. Registro usa número/total lidos do PostgreSQL; catálogo usa nomes/preços/campos retornados pelas ferramentas, até seis produtos, respeitando filtros monetários reconhecidos. Dados anteriores são identificados como consultados anteriormente. Falta de resultados não é tratada como indisponibilidade. Busca com candidatos acima do teto produz explicação de orçamento insuficiente, sem gravar.

Declarações livres do LLM não se tornam ofertas, números ou confirmações no chat. O modelo pode indicar quais campos faltam, mas as perguntas de planejamento são renderizadas por templates sem valores, identificadores ou afirmações embutidas. Nos demais casos sem evidência, a resposta é uma orientação de planejamento. As imagens vêm dos campos reais do catálogo e continuam sujeitas à validação de host/caminho do React. A composição do texto não executa ferramentas adicionais.

A validação JSON Schema local usa inteiros estritos: `id: 1.0`, booleanos e strings não passam como IDs inteiros. Erros locais recebem 422/`invalid_tool_arguments` antes do RPC, com feedback para o modelo corrigir a chamada. Campos extras de preço na criação permanecem rejeitados.

## Exposição e capacidade

Frontend, backend e MCP são publicados somente em `127.0.0.1`; o PostgreSQL continua sem porta no host. A comunicação entre containers usa a rede interna. A aplicação permanece destinada ao uso local, sem autenticação.

`AGENT_MAX_CONCURRENT_RUNS` limita execuções simultâneas por processo/app, padrão **4**, aceitando 1–32. Excesso retorna 503/`agent_busy`, sem chamada ao provedor. O slot é liberado inclusive em erro/cancelamento. Timeouts e limites de rodadas/chamadas continuam ativos. Uma implantação com múltiplos workers precisaria de admissão compartilhada; este limite não é uma quota distribuída.

## Validação e limites

As regressões reproduzem bloqueio de escrita proibida, mudança de título/chave, replay com novo adaptador HTTP, perda da primeira resposta da conversa, descarte de histórico, atualização parcial de restrições, categoria/quantidade incorreta, moeda não suportada, ID decimal, narrativa inventada, filtros monetários e limite de concorrência. As sequências anteriores de ferramentas, inclusive criação direta com alvo/quantidade explícitos, permanecem testadas. Fixtures antigas de compra genérica receberam quantidade e IDs explícitos para realmente alcançar as verificações de transação/erro que pretendiam testar; seus critérios de persistência não foram enfraquecidos.

O parser é conservador e limitado, não uma interpretação universal de linguagem natural. Pedidos complexos, várias categorias/quantidades, preferências subjetivas ou formatos não reconhecidos podem precisar de esclarecimento. Não há garantia universal sobre toda paráfrase adversarial ou qualidade de seleção do LLM. Estes controles também não reservam estoque nem adicionam pagamentos/autenticação. Os relatórios de avaliações anteriores foram preservados; nenhum dado de desenvolvimento foi apagado.

### Resultado da validação em 01/11/2026

- **587 testes Python locais aprovados**, 1 pulado (migração que requer PostgreSQL), 3 subtestes aprovados.
- **588 testes aprovados com PostgreSQL isolado**, 3 subtestes aprovados, saída 0.
- **105 testes frontend aprovados**, TypeScript e build Vite aprovados; Ruff e verificação de whitespace aprovados.
- Builds Docker concluídos; quatro serviços saudáveis, proxy `/api/health` funcionando e cabeçalho documentado no OpenAPI. Portas HTTP verificadas em `127.0.0.1`.
- Avaliação real `53a0b451-536e-4c1a-b8ee-9892bed5327a`, diário local `.agent-evaluation/security-fixes.json`: **0/5 (0%)**. Todos os cinco pedidos receberam 503, originalmente classificados como `llm_authentication`; a descoberta MCP confirmou três ferramentas, mas nenhuma chamada de catálogo/criação foi executada. Nenhuma nova requisição foi salva. Não é evidência de aprovação nem de regressão das decisões do modelo: elas não chegaram a ser exercitadas.

Não foi lido nem alterado o segredo local para tentar contornar a falha, e não houve repetição da avaliação para melhorar a nota. Depois de resolver o bloqueio do provedor, use um **novo diário** para uma nova avaliação deliberada. Reabrir `security-fixes.json` apenas mostra esse resultado original. O script e os cinco cenários/critérios não foram alterados.

### Diagnóstico posterior do bloqueio OpenRouter

A consulta autenticada `GET /api/v1/key` retornou **200**, demonstrando que a chave configurada é reconhecida. Uma geração mínima retornou **403**, com limite total de **US$ 20,00 consumido integralmente**, sem renovação automática. O projeto mapeava todos os 403 para autenticação: essa classificação estava incorreta. O diário original foi preservado.

O adaptador agora distingue 401 (`llm_authentication`), 402 (`llm_credits`), 403 de orçamento (`llm_budget_exceeded`) e outros 403 (`llm_forbidden`). Orçamento é identificado por `metadata.limit_source` conhecido ou prefixos fixos de erro, sem encaminhar corpos externos, identificadores de chave ou valores da conta aos logs/API. O frontend apresenta mensagens distintas. A documentação da [OpenRouter sobre erros de orçamento](https://openrouter.ai/docs/api_reference/errors-and-debugging#budget-errors) confirma que esses limites geram 403 antes da chamada ao provedor.

O limite financeiro externo não foi alterado. Para liberar gerações, o responsável deve revisar o orçamento dessa chave/workspace no painel; trocar a chave no código não resolve o limite por si só. Nenhuma compra de teste foi executada durante esse diagnóstico.

Regressão da classificação: **127 testes do adaptador/endpoints aprovados**, **107 testes frontend aprovados**, Ruff e build aprovados. Testes simulados distinguem orçamento, permissões, autenticação e respostas 403 malformadas, preservando uma única tentativa e a omissão do corpo externo nos logs.

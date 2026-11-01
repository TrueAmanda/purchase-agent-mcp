# Validação do case

Referência: `Case-FDE.pdf`, nove páginas, fornecido pelo usuário em Downloads. A leitura incluiu as páginas de requisitos, diferenciais, entrega e avaliação. O arquivo original contém uma chave de API e não foi copiado nem versionado neste projeto. Esta matriz não considera um teste com respostas simuladas como prova de uma decisão semântica do LLM real.

## Requisitos obrigatórios

| Página | Requisito | Evidência e situação |
| --- | --- | --- |
| 3 | Buscar e escolher produtos para o pedido em linguagem natural | Catálogo, argumentos e preços reais da integração são exercitados com HTTP simulado; repetir o pedido real de 3 laptops até US$ 2.000 por unidade segue no roteiro A |
| 3 | Salvar e responder com itens, quantidades, preços e total | POST → agente → MCP → SQL → GET coberto; Decimal e soma dos itens conferidos; sucesso exige consulta de confirmação no banco |
| 3 | Explicar impossibilidade sem inventar | Catálogo vazio, candidatos inadequados e produto inexistente não geram registros; qualidade da explicação e da seleção pelo modelo real exige roteiro B |
| 4 | MCP com `buscar_produtos(termo)` e `detalhar_produto(id)` chamando a DummyJSON | Ferramentas, schemas, endpoints e erros testados pelo SDK oficial, com mocks externos; há teste TCP local real |
| 4 | Backend POST/GET integrado à OpenRouter, MCP e PostgreSQL | Fluxo real dos serviços e protocolo testado com respostas LLM simuladas e PostgreSQL isolado; chamadas OpenRouter reais não fazem parte da suíte determinística |
| 4 | PostgreSQL guarda requisições e itens | Relações, valores, commit, rollback e idempotência exercitados em PostgreSQL real de teste |
| 4 | Chat React envia pedido e exibe resposta e número criado | Testes DOM com fetch simulado e build aprovados; walkthrough visual real de A/B/C não foi repetido nesta etapa |
| 5 | Quatro serviços sobem com Compose | `docker compose up --build -d --wait` concluído neste ambiente; quatro serviços saudáveis, proxy HTTP e catálogo SQL conferidos; não equivale a testar a máquina do avaliador |
| 5 | Tabelas criadas automaticamente no startup | Testes de inicialização de ambos os processos partindo de tabelas ausentes, com SQLite e PostgreSQL isolados; migração legada também coberta |
| 5 | Modelo decide ferramentas, sem fluxo fixo no código | Quatro sequências distintas solicitadas pelo LLM simulado, inclusive criação direta e busca repetida, atravessam o protocolo MCP; `tool_choice: auto` conferido. Comportamento do modelo real continua sujeito à avaliação manual |
| 5 | Falha/lentidão da DummyJSON gera mensagem clara no chat | Erros e timeouts passam pela integração até o contrato HTTP; frontend cobre mensagens e estados; simulação reproduzível disponível no roteiro C, com restauração explícita |
| 5 | Teste para cada ferramenta sem chave LLM | Suíte MCP preservada e executada isoladamente; suíte combinada também executada, sem acesso a provedores externos |
| 5 | README curto explicando execução e decisões | Há quickstart inicial, arquitetura e comandos. O README permanece extenso: uma revisão editorial antes da entrega é recomendada para cumprir a intenção de “poucas linhas” |
| 7 | Declarar uso de IA no README | Declaração adicionada ao início, com atividades assistidas pelo Codex e limites das validações |
| 7 | ZIP com a pasta `.git` | Pendente da entrega pelo usuário; nenhum ZIP, commit ou merge foi realizado nesta etapa. A preparação precisa excluir credenciais, `.env`, ambientes locais e arquivos temporários, preservando `.git` |

## Diferenciais opcionais (página 6)

| Diferencial | Situação |
| --- | --- |
| Exibir ferramentas usadas no chat | Eventos de execução e painel existentes, com testes de ordem, falha e repetição; inspeção visual real não repetida nesta etapa |
| Script com cinco pedidos e quantidade de acertos | Entregue em `backend/evals/purchase_evaluation.py`, com cruzamento API/MCP/SQL/GET/DummyJSON, proteção contra repetição e relatório PASS/FAIL. Execução real: **2/5 (40%)**; falhas de orçamento, conclusão da compra e argumentos do modelo preservadas. Ver [agent-evaluation.md](agent-evaluation.md) |
| CI com lint e testes | Workflow existente atualizado para conferir também os utilitários novos; comandos equivalentes executados localmente. Execução remota desta branch no GitHub Actions não comprovada nesta etapa |
| Terceira ferramenta `criar_requisicao`, com decisão do agente | Gravação exclusivamente na ferramenta MCP; testes confirmam criação, idempotência e rollback sem criar uma segunda gravação no POST |

## Limites e problemas conhecidos

- O uso de sinônimos na busca ainda pode falhar: `perfumes` não equivale automaticamente ao slug `fragrances`. A suíte de integração não corrige nem oculta essa limitação.
- Avaliações anteriores identificaram confirmação redundante ocasional e narrativa do LLM confundindo erro com busca vazia. Os testes atuais verificam que o backend classifica eventos de erro sem usar essa narrativa como confirmação ou ausência de produtos.
- As quatro sequências simuladas demonstram que o executor aceita decisões diferentes; não demonstram que o modelo real escolhe sempre a sequência correta ou respeita todas as restrições em linguagem natural.
- O exemplo do PDF, impossibilidade e falha da DummyJSON no chat possuem instruções em [integration-testing.md](integration-testing.md). A execução desses três cenários com o modelo real é uma validação manual separada.
- Permanecem pendentes a revisão visual em desktop/mobile, uma reprodução fora desta máquina, a inspeção final do ZIP/histórico e a avaliação humana da clareza da documentação e das decisões.

Os critérios de avaliação da página 8 incluem funcionamento (35%), agente/MCP (25%), integração/ambiente (25%) e comunicação (15%). Não foi atribuída uma nota automática: testes aprovados não comprovam sozinhos todos esses critérios.

import type { ToolName, ToolTraceEntry } from '../types/purchases';

export const toolLabels: Record<ToolName, string> = {
  buscar_produtos: 'Buscar produtos', detalhar_produto: 'Detalhar produto', criar_requisicao: 'Criar requisição',
};
export const toolErrorLabels: Record<string, string> = {
  api_timeout: 'O catálogo demorou para responder.',
  api_unavailable: 'O catálogo de produtos está indisponível.',
  api_http_error: 'O catálogo retornou um erro HTTP.',
  invalid_response: 'O catálogo retornou dados inválidos.',
  product_not_found: 'O produto não existe no catálogo.',
  database_unavailable: 'O banco de requisições está indisponível.',
  database_error: 'O registro da compra não foi confirmado.',
  idempotency_conflict: 'Os dados conflitam com uma tentativa anterior.',
  invalid_amount: 'O valor ultrapassa o limite suportado.',
  unknown_tool: 'A ferramenta não está disponível.',
  invalid_tool_arguments: 'Os argumentos não foram aceitos.',
  budget_exceeded: 'O orçamento foi excedido; nenhuma gravação foi realizada.',
  purchase_constraints_violated: 'Os itens não atendem às condições da compra; nenhuma gravação foi realizada.',
  purchase_constraints_unclear: 'As condições de compra precisam ser esclarecidas.',
  purchase_not_authorized: 'A criação não foi autorizada; nenhuma gravação foi realizada.',
  tool_timeout: 'O tempo limite terminou; a execução não foi confirmada.',
  tool_cancelled: 'A chamada foi interrompida; a execução não foi confirmada.',
  tool_execution_failed: 'A ferramenta não confirmou um resultado válido.',
  mcp_unavailable: 'A comunicação com a ferramenta falhou; a execução não foi confirmada.',
  invalid_tool_result: 'A ferramenta retornou dados fora do contrato esperado.',
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Missing or malformed evidence is unknown, never an invented empty execution list. */
export function decodeToolTrace(value: unknown): ToolTraceEntry[] | null {
  if (!Array.isArray(value)) return null;
  const orders = new Set<number>();
  const entries: ToolTraceEntry[] = [];
  for (const entry of value) {
    if (!object(entry) || typeof entry.order !== 'number' || !Number.isSafeInteger(entry.order) || entry.order < 1
      || orders.has(entry.order) || typeof entry.name !== 'string' || !Object.hasOwn(toolLabels, entry.name)
      || !['success', 'error', 'timeout'].includes(String(entry.status))
      || (entry.duration_ms != null && (typeof entry.duration_ms !== 'number' || !Number.isFinite(entry.duration_ms) || entry.duration_ms < 0))) return null;
    orders.add(entry.order);
    const summary: NonNullable<ToolTraceEntry['result']> = {};
    if (object(entry.result)) {
      for (const key of ['product_count', 'item_count'] as const) {
        const count = entry.result[key];
        if (typeof count === 'number' && Number.isSafeInteger(count) && count >= 0) summary[key] = count;
      }
      if (typeof entry.result.replayed === 'boolean') summary.replayed = entry.result.replayed;
    }
    entries.push({
      order: entry.order, name: entry.name as ToolName, status: entry.status as ToolTraceEntry['status'],
      duration_ms: typeof entry.duration_ms === 'number' ? entry.duration_ms : null,
      code: entry.status === 'success' ? null : (typeof entry.code === 'string' && Object.hasOwn(toolErrorLabels, entry.code)
        ? entry.code : 'tool_execution_failed'),
      result: entry.status === 'success' && Object.keys(summary).length > 0 ? summary : null,
    });
  }
  return entries.sort((left, right) => left.order - right.order);
}

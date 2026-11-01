import type { PurchaseListResponse, PurchaseRequisition, PurchaseResponse, ToolTraceEntry } from '../types/purchases';
import { decodeToolTrace } from './toolTrace';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const POST_TIMEOUT_MS = 180_000;
const DECIMAL = /^\d+(?:\.\d+)?$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class PurchaseApiError extends Error {
  constructor(
    message: string, public readonly code: string, public readonly uncertain = true,
    public readonly toolTrace: ToolTraceEntry[] | null = null,
    public readonly conversationId: string | null = null,
  ) {
    super(message);
    this.name = 'PurchaseApiError';
  }
}

const errorMessages: Record<string, string> = {
  api_unavailable: 'O catálogo de produtos está indisponível. Tente consultar novamente mais tarde.',
  api_timeout: 'O catálogo de produtos demorou para responder. A consulta não foi concluída.',
  api_http_error: 'O catálogo de produtos retornou um erro. Não foi possível concluir a consulta.',
  invalid_response: 'O catálogo retornou dados inválidos. Os produtos não puderam ser confirmados.',
  product_not_found: 'Um dos produtos solicitados não existe no catálogo.',
  mcp_unavailable: 'O serviço de ferramentas está indisponível. Não foi possível concluir seu pedido.',
  tool_catalog_error: 'Não foi possível carregar as ferramentas do assistente.',
  llm_unavailable: 'O assistente está indisponível no momento. Tente novamente mais tarde.',
  llm_timeout: 'O assistente demorou para responder. A operação não foi confirmada.',
  llm_rate_limit: 'O assistente está recebendo muitos pedidos. Aguarde antes de tentar novamente.',
  llm_authentication: 'O assistente não conseguiu acessar o provedor. Verifique a configuração do serviço.',
  llm_credits: 'O provedor do assistente está sem saldo disponível.',
  llm_budget_exceeded: 'O limite de gastos da chave ou do workspace na OpenRouter foi atingido. Verifique o orçamento no painel da OpenRouter.',
  llm_forbidden: 'A OpenRouter bloqueou esta solicitação por permissões ou políticas. Confira as regras da chave e do workspace.',
  invalid_configuration: 'O assistente precisa ser configurado para receber pedidos.',
  invalid_mcp_configuration: 'A conexão com o serviço de ferramentas precisa ser configurada.',
  invalid_llm_response: 'O assistente retornou uma resposta inválida. A operação não foi confirmada.',
  llm_incomplete: 'A resposta do assistente foi interrompida. A operação não foi confirmada.',
  iteration_limit: 'O assistente atingiu o limite de etapas e não conseguiu concluir seu pedido.',
  tool_call_limit: 'O assistente atingiu o limite de chamadas e não conseguiu concluir seu pedido.',
  agent_timeout: 'O tempo de processamento terminou. A operação não foi confirmada.',
  tool_timeout: 'Uma ferramenta demorou para responder. A operação não foi confirmada.',
  database_unavailable: 'O banco de requisições está indisponível. Não foi possível confirmar o registro.',
  database_error: 'Não foi possível consultar os registros de compra.',
  creation_unconfirmed: 'Não foi possível confirmar a gravação da requisição.',
  idempotency_conflict: 'Este pedido entrou em conflito com uma tentativa anterior. Consulte os registros antes de reenviar.',
  invalid_amount: 'O valor retornado pelo catálogo ultrapassa o limite suportado.',
  invalid_tool_arguments: 'O assistente enviou argumentos inválidos à ferramenta. Essa chamada foi bloqueada antes da execução.',
  budget_exceeded: 'Os produtos selecionados ultrapassam seu orçamento total ou por unidade. Nenhuma requisição foi gravada.',
  purchase_constraints_violated: 'Os itens não atendem às quantidades, categoria, estoque ou pedido mínimo. Nenhuma requisição foi gravada.',
  purchase_constraints_unclear: 'Informe claramente o orçamento em US$ (total ou por unidade) e as quantidades antes de criar a requisição.',
  purchase_not_authorized: 'A criação não foi autorizada. Nenhuma gravação foi realizada.',
  agent_busy: 'O assistente está processando o limite de pedidos simultâneos. Aguarde antes de tentar novamente.',
  conversation_expired: 'Esta conversa expirou ou o serviço reiniciou. Clique em Nova conversa e informe novamente os detalhes do pedido.',
  conversation_busy: 'Outra mensagem desta conversa está sendo processada. Aguarde a resposta antes de continuar.',
  conversation_capacity: 'O serviço de conversas está ocupado. Aguarde antes de tentar novamente.',
  invalid_conversation_configuration: 'A memória de conversa precisa ser configurada no serviço.',
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function nullableString(value: unknown) { return value === null || typeof value === 'string'; }
function amount(value: unknown, nullable = false) {
  return (nullable && value === null) || (typeof value === 'string' && DECIMAL.test(value));
}
function positiveInteger(value: unknown) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}
function conversationId(value: unknown): string | null {
  return typeof value === 'string' && UUID.test(value) ? value : null;
}
function requisition(value: unknown): value is PurchaseRequisition {
  return object(value) && positiveInteger(value.id) && value.status === 'registered'
    && (value.request_number === null || (typeof value.request_number === 'string' && UUID.test(value.request_number)))
    && typeof value.title === 'string' && typeof value.requester_name === 'string'
    && typeof value.created_at === 'string' && Number.isFinite(Date.parse(value.created_at))
    && nullableString(value.currency) && amount(value.total, true) && Array.isArray(value.items)
    && value.items.every((item: unknown) => object(item) && positiveInteger(item.id)
      && nullableString(item.product_id) && nullableString(item.name) && typeof item.description === 'string'
      && positiveInteger(item.quantity) && amount(item.unit_price) && amount(item.subtotal, true));
}
function purchaseResponse(value: unknown): value is PurchaseResponse {
  if (!object(value) || !['created', 'no_results', 'response'].includes(String(value.status))
    || typeof value.message !== 'string' || !Array.isArray(value.requisitions)) return false;
  if (value.status !== 'created') return value.requisitions.length === 0;
  return value.requisitions.length > 0 && value.requisitions.every((record: unknown) => requisition(record)
    && record.request_number !== null && record.total !== null && record.items.length > 0
    && typeof (record as unknown as Record<string, unknown>).replayed === 'boolean');
}

async function request(path: string, init: RequestInit, timeoutMs = POST_TIMEOUT_MS): Promise<unknown> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal, credentials: 'omit' });
    let payload: unknown;
    try { payload = await response.json(); } catch (error) {
      if (controller.signal.aborted) throw error;
      if (response.ok) {
        throw new PurchaseApiError('O serviço retornou uma resposta inválida. A operação não foi confirmada.', 'invalid_response_body');
      }
      payload = null;
    }
    if (!response.ok) {
      const detail = object(payload) && object(payload.detail) ? payload.detail : null;
      const code = typeof detail?.code === 'string' ? detail.code : `http_${response.status}`;
      const message = errorMessages[code] || (response.status === 422
        ? 'Confira o pedido enviado. Informe uma mensagem válida com até 20.000 caracteres.'
        : 'Não foi possível concluir a operação. Tente consultar os registros antes de reenviar uma compra.');
      throw new PurchaseApiError(message, code, true, object(payload) ? decodeToolTrace(payload.tool_trace) : null,
        object(payload) ? conversationId(payload.conversation_id) : null);
    }
    return payload;
  } catch (error) {
    if (error instanceof PurchaseApiError) throw error;
    if (controller.signal.aborted) {
      throw new PurchaseApiError('A resposta demorou mais que o esperado. A operação não foi confirmada.', 'client_timeout');
    }
    throw new PurchaseApiError('Não foi possível conectar ao serviço. Verifique sua conexão e consulte os registros antes de reenviar uma compra.', 'connection_error');
  } finally { clearTimeout(timeout); }
}

export async function submitPurchase(message: string, currentConversationId: string | null = null, intentKey?: string): Promise<PurchaseResponse> {
  const trimmed = message.trim();
  if (!trimmed || trimmed.length > 20_000) {
    throw new PurchaseApiError('Escreva um pedido com até 20.000 caracteres.', 'invalid_message', false);
  }
  const payload = await request('/requisicoes', {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...(intentKey ? { 'Idempotency-Key': intentKey } : {}) },
    body: JSON.stringify({ message: trimmed, include_tool_trace: true, conversation_id: currentConversationId }),
  });
  if (!purchaseResponse(payload)) {
    throw new PurchaseApiError('A resposta recebida não confirma uma requisição válida. Consulte os registros antes de reenviar.', 'invalid_contract');
  }
  return { ...payload, tool_trace: decodeToolTrace(payload.tool_trace), conversation_id: conversationId(payload.conversation_id) };
}

export async function listPurchases(): Promise<PurchaseListResponse> {
  const payload = await request('/requisicoes?limit=10&offset=0', { method: 'GET' }, 30_000);
  if (!object(payload) || !Array.isArray(payload.requisitions) || !payload.requisitions.every(requisition)
    || !positiveInteger(payload.limit) || typeof payload.offset !== 'number'
    || !Number.isSafeInteger(payload.offset) || payload.offset < 0 || typeof payload.has_more !== 'boolean') {
    throw new PurchaseApiError('Não foi possível interpretar os registros retornados pelo serviço.', 'invalid_contract', false);
  }
  return payload as unknown as PurchaseListResponse;
}

/** Monetary amounts remain decimal strings from the API. */
export interface PurchaseItem {
  id: number;
  product_id: string | null;
  name: string | null;
  description: string;
  quantity: number;
  unit_price: string;
  subtotal: string | null;
}

export interface PurchaseRequisition {
  id: number;
  request_number: string | null;
  status: 'registered';
  title: string;
  requester_name: string;
  created_at: string;
  currency: string | null;
  total: string | null;
  items: PurchaseItem[];
}

export interface ConfirmedRequisition extends PurchaseRequisition {
  request_number: string;
  total: string;
  replayed: boolean;
}

export interface PurchaseResponse {
  status: 'created' | 'no_results' | 'response';
  message: string;
  requisitions: ConfirmedRequisition[];
  tool_trace?: ToolTraceEntry[] | null;
  conversation_id?: string | null;
}

export type ToolName = 'buscar_produtos' | 'detalhar_produto' | 'criar_requisicao';
export interface ToolTraceEntry {
  order: number;
  name: ToolName;
  status: 'success' | 'error' | 'timeout';
  duration_ms: number | null;
  code: string | null;
  result: { product_count?: number; item_count?: number; replayed?: boolean } | null;
}

export interface PurchaseListResponse {
  requisitions: PurchaseRequisition[];
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface ChatEntry {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  response?: PurchaseResponse;
  toolTrace?: ToolTraceEntry[] | null;
  error?: { message: string; uncertain: boolean };
}

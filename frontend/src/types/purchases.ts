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
  error?: { message: string; uncertain: boolean };
}

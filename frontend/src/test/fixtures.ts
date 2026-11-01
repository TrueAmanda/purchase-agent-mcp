import type { ConfirmedRequisition, PurchaseResponse } from '../types/purchases';

/** Synthetic fixtures only, never imported by production components. */
export const requisition: ConfirmedRequisition = {
  id: 3, request_number: '999b34d1-eda5-4b6b-a1df-5ec7db4eebd6', status: 'registered',
  title: 'Compra de teste', requester_name: 'Pessoa de teste', created_at: '2026-10-31T23:48:56Z',
  currency: 'USD', total: '5399.97', replayed: false,
  items: [{ id: 3, product_id: '79', name: 'Notebook de teste', description: 'Descrição sintética apenas para os testes.',
    quantity: 3, unit_price: '1799.99', subtotal: '5399.97' }],
};
export const created: PurchaseResponse = { status: 'created', message: 'Registro confirmado pelo serviço.', requisitions: [requisition] };
export const answer: PurchaseResponse = { status: 'response', message: 'Quais equipamentos você precisa?', requisitions: [] };
export function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
}

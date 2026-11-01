import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listPurchases, PurchaseApiError, submitPurchase } from './purchaseApi';
import { answer, created, jsonResponse, requisition } from '../test/fixtures';

const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });

describe('purchase API', () => {
  it('posts the actual contract, without requesting tool traces', async () => {
    fetchMock.mockResolvedValue(jsonResponse(answer));
    expect(await submitPurchase('  Preciso comprar equipamentos  ')).toEqual(answer);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/requisicoes');
    expect(options?.method).toBe('POST');
    expect(JSON.parse(options?.body as string)).toEqual({ message: 'Preciso comprar equipamentos', include_tool_trace: false });
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    expect(options?.credentials).toBe('omit');
  });

  it('accepts a confirmed receipt and preserves decimal strings', async () => {
    fetchMock.mockResolvedValue(jsonResponse(created, 201));
    const result = await submitPurchase('Crie a requisição');
    expect(result.requisitions[0].total).toBe('5399.97');
    expect(typeof result.requisitions[0].items[0].unit_price).toBe('string');
  });

  it.each(['', '  ', 'x'.repeat(20_001)])('rejects invalid input before HTTP', async (input) => {
    await expect(submitPurchase(input)).rejects.toMatchObject({ code: 'invalid_message', uncertain: false });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    { ...created, requisitions: [] },
    { ...answer, requisitions: [requisition] },
    { ...created, requisitions: [{ ...requisition, request_number: null }] },
    { ...created, requisitions: [{ ...requisition, request_number: 'invented-number' }] },
    { ...created, requisitions: [{ ...requisition, items: [] }] },
    { ...created, requisitions: [{ ...requisition, total: 5399.97 }] },
    { ...created, requisitions: [{ ...requisition, total: null }] },
    { ...created, requisitions: [{ ...requisition, replayed: undefined }] },
    { status: 'unknown', message: 'Unexpected', requisitions: [] },
  ])('rejects inconsistent confirmation contracts', async (payload) => {
    fetchMock.mockResolvedValue(jsonResponse(payload));
    await expect(submitPurchase('Teste')).rejects.toMatchObject({ code: 'invalid_contract', uncertain: true });
  });

  it.each([
    [503, 'api_unavailable', 'O catálogo de produtos está indisponível'],
    [504, 'api_timeout', 'O catálogo de produtos demorou'],
    [502, 'api_http_error', 'O catálogo de produtos retornou um erro'],
    [503, 'mcp_unavailable', 'O serviço de ferramentas está indisponível'],
    [503, 'llm_unavailable', 'O assistente está indisponível'],
    [502, 'creation_unconfirmed', 'Não foi possível confirmar a gravação'],
    [409, 'idempotency_conflict', 'Este pedido entrou em conflito'],
  ])('maps HTTP %s/%s to safe Portuguese messages', async (status, code, text) => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: { code, message: 'private-provider-body' } }, Number(status)));
    await expect(submitPurchase('Teste')).rejects.toThrow(String(text));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('handles FastAPI validation and omits raw input/error payloads', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: [{ input: 'private-input', msg: 'private-error' }] }, 422));
    await expect(submitPurchase('Teste')).rejects.toThrow('Confira o pedido enviado');
  });

  it('handles network failures without retries or definitive write failure claims', async () => {
    fetchMock.mockRejectedValue(new TypeError('private-network-details'));
    await expect(submitPurchase('Crie uma compra')).rejects.toMatchObject({ code: 'connection_error', uncertain: true });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('bounds the client wait and aborts an unresponsive request', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    const result = submitPurchase('Teste');
    const assertion = expect(result).rejects.toMatchObject({ code: 'client_timeout', uncertain: true });
    await vi.advanceTimersByTimeAsync(180_000);
    await assertion;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('rejects an HTML body instead of displaying it', async () => {
    fetchMock.mockResolvedValue(new Response('<html>private upstream body</html>'));
    await expect(submitPurchase('Teste')).rejects.toBeInstanceOf(PurchaseApiError);
  });

  it('reads persisted records and preserves null legacy fields', async () => {
    const legacy = { ...requisition, currency: null, request_number: null, total: null,
      items: [{ ...requisition.items[0], name: null, subtotal: null }] };
    fetchMock.mockResolvedValue(jsonResponse({ requisitions: [legacy], limit: 10, offset: 0, has_more: false }));
    const result = await listPurchases();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/requisicoes?limit=10&offset=0');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('GET');
    expect(result.requisitions[0].currency).toBeNull();
    expect(result.requisitions[0].total).toBeNull();
  });
});

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../App';
import { answer, created, jsonResponse, requisition, trace, conversationId } from '../test/fixtures';

const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => { fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock); });
const input = () => screen.getByRole('textbox', { name: 'Seu pedido de compra' }) as HTMLTextAreaElement;
function send(text = 'Preciso de notebooks') {
  fireEvent.change(input(), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Enviar pedido' }));
}

describe('purchase chat', () => {
  it('starts without fabricated messages or catalog data and suggestions only fill the draft', () => {
    render(<App />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('comprar hoje?');
    expect((screen.getByRole('button', { name: 'Enviar pedido' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Encontrar produtos/ }));
    expect(input().value).toContain('Busque notebooks');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('displays the user message, locks duplicate sends, then displays the actual agent reply', async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<App />);
    send('  Preciso de notebooks  ');
    expect(screen.getByLabelText('Sua mensagem').textContent).toContain('Preciso de notebooks');
    expect(screen.getByRole('status').textContent).toContain('Processando seu pedido');
    expect(input().disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Enviar pedido' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByRole('form', { name: 'Enviar pedido de compra' }));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { resolve(jsonResponse(answer)); });
    expect(await screen.findByText(answer.message)).toBeTruthy();
    expect(input().disabled).toBe(false);
    expect(screen.queryByText('Registro confirmado')).toBeNull();
    expect(screen.getAllByRole('article')).toHaveLength(2);
  });

  it('renders a confirmed requisition with the returned number, amounts, and quantities', async () => {
    fetchMock.mockResolvedValue(jsonResponse(created, 201));
    render(<App />); send('Crie uma requisição de 3 notebooks');
    expect(await screen.findByText(requisition.request_number)).toBeTruthy();
    expect(screen.getByText('Registro confirmado')).toBeTruthy();
    expect(screen.getByText('Notebook de teste')).toBeTruthy();
    expect(screen.getByText('3', { selector: 'dd' })).toBeTruthy();
    expect(screen.getByText(/1\.799,99/, { selector: 'dd' })).toBeTruthy();
    expect(screen.getByText(/5\.399,97/, { selector: '.purchase-total strong' })).toBeTruthy();
    expect(screen.getByText('Ferramentas utilizadas')).toBeTruthy();
    expect(screen.queryByText(/duration_ms|tool_trace/)).toBeNull();
  });

  it('distinguishes idempotent replay from a newly created record', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...created, requisitions: [{ ...requisition, replayed: true }] }));
    render(<App />); send();
    expect(await screen.findByText('Registro existente')).toBeTruthy();
    expect(screen.queryByText('Registro confirmado')).toBeNull();
  });

  it('handles an empty search without a purchase card or an API failure', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: 'no_results', message: 'Não encontrei esse produto.', requisitions: [] }));
    render(<App />); send('Busque um produto inexistente');
    expect(await screen.findByText('Nenhum resultado nas buscas realizadas')).toBeTruthy();
    expect(screen.getByText('Não encontrei esse produto.')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText('Registro confirmado')).toBeNull();
  });

  it('rejects a fake created payload with no confirmed record', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...created, requisitions: [] }));
    render(<App />); send();
    expect((await screen.findByRole('alert')).textContent).toContain('não confirma uma requisição válida');
    expect(screen.queryByText('Registro confirmado')).toBeNull();
    expect(screen.queryByText(created.message)).toBeNull();
  });

  it.each([
    ['api_unavailable', 'catálogo de produtos está indisponível'],
    ['mcp_unavailable', 'serviço de ferramentas está indisponível'],
    ['llm_unavailable', 'assistente está indisponível'],
  ])('presents a controlled %s failure and allows read-only record verification', async (code, message) => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { code, message: 'private-provider-body' } }, 503))
      .mockResolvedValueOnce(jsonResponse({ requisitions: [], limit: 10, offset: 0, has_more: false }));
    render(<App />); send();
    expect((await screen.findByRole('alert')).textContent).toContain(message);
    expect(screen.queryByText(/private-provider-body/)).toBeNull();
    expect(input().disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: /Consultar registros/ }));
    expect(await screen.findByText('Nenhuma requisição registrada')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1]?.method).toBe('GET');
    fireEvent.click(screen.getByRole('button', { name: 'Fechar registros' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('preserves the conversation after a network error and does not retry automatically', async () => {
    fetchMock.mockRejectedValue(new TypeError('private-network-details'));
    render(<App />); send('Meu pedido de teste');
    expect((await screen.findByRole('alert')).textContent).toContain('Não foi possível conectar');
    expect(screen.getByText('Meu pedido de teste')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('validates whitespace and excessive input and preserves Shift+Enter/IME', () => {
    render(<App />);
    fireEvent.change(input(), { target: { value: '   ' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Enviar pedido de compra' }));
    fireEvent.change(input(), { target: { value: 'x'.repeat(20_001) } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(screen.getByText('O pedido deve ter no máximo 20.000 caracteres.')).toBeTruthy();
    fireEvent.change(input(), { target: { value: 'Produto' } });
    fireEvent.keyDown(input(), { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(input(), { key: 'Enter', isComposing: true });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends with Enter and can clear the local conversation without deleting persisted records', async () => {
    fetchMock.mockResolvedValue(jsonResponse(answer));
    render(<App />);
    fireEvent.change(input(), { target: { value: 'Meu pedido' } });
    fireEvent.keyDown(input(), { key: 'Enter' });
    await screen.findByText(answer.message);
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: 'Nova conversa' }));
    expect(screen.queryByRole('log')).toBeNull();
    expect(input().value).toBe('');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('safely displays text without executing user/catalog markup', async () => {
    const markup = '<img src=x onerror=alert(1)>';
    fetchMock.mockResolvedValue(jsonResponse({ ...answer, message: markup }));
    render(<App />); send(markup);
    await waitFor(() => expect(screen.getAllByText(markup)).toHaveLength(2));
    expect(document.querySelector('img')).toBeNull();
  });

  it('loads only real saved records and displays unknown legacy fields without assuming USD', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ requisitions: [{ ...requisition, currency: null, total: null,
      items: [{ ...requisition.items[0], subtotal: null }] }], limit: 10, offset: 0, has_more: false }));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Requisições salvas' }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(requisition.request_number)).toBeTruthy();
    expect(within(dialog).getAllByText('Não informado')).toHaveLength(2);
    expect(within(dialog).getByText(/moeda não informada/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not infer tool usage from assistant text and keeps each request history separate', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...answer, message: 'I claim buscar_produtos ran.', tool_trace: [] }))
      .mockResolvedValueOnce(jsonResponse(created, 201));
    render(<App />); send('Primeiro pedido');
    await screen.findByText('I claim buscar_produtos ran.');
    send('Segundo pedido');
    await screen.findByText(requisition.request_number);
    const replies = screen.getAllByRole('article', { name: 'Resposta do assistente' });
    expect(within(replies[0]).getByText('Nenhuma chamada')).toBeTruthy();
    expect(replies[0].querySelectorAll('.tool-event')).toHaveLength(0);
    expect(replies[1].querySelectorAll('.tool-event')).toHaveLength(2);
    expect(within(replies[1]).getByText('2 chamadas')).toBeTruthy();
  });

  it('shows a failed MCP call on an HTTP error without a purchase confirmation', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: { code: 'product_not_found', message: 'private-body' },
      tool_trace: [{ ...trace[0], name: 'detalhar_produto', status: 'error', code: 'product_not_found', result: null }] }, 422));
    render(<App />); send('Detalhe um produto inexistente');
    await screen.findByRole('alert');
    expect(screen.getByText('1 chamada')).toBeTruthy();
    expect(screen.getByText('detalhar_produto')).toBeTruthy();
    expect(screen.getByText('Falhou')).toBeTruthy();
    expect(screen.queryByText('Registro confirmado')).toBeNull();
    expect(screen.queryByText(/private-body/)).toBeNull();
  });

  it('shows history as unavailable after a connection error, not as zero calls', async () => {
    fetchMock.mockRejectedValue(new TypeError('Connection closed'));
    render(<App />); send('Teste');
    await screen.findByRole('alert');
    expect(screen.getByText('Histórico indisponível')).toBeTruthy();
    expect(screen.queryByText('Nenhuma chamada')).toBeNull();
  });

  it('continues the same server conversation and resets it only on Nova conversa', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ...answer, message: 'Encontrei um pó compacto.' }))
      .mockResolvedValueOnce(jsonResponse({ ...answer, message: 'Detalhes do pó compacto.' }))
      .mockResolvedValueOnce(jsonResponse({ ...answer, message: 'Outra conversa.' }));
    render(<App />);
    send('Procure opções de maquiagem');
    await screen.findByText('Encontrei um pó compacto.');
    send('Quero mais detalhes do pó compacto');
    await screen.findByText('Detalhes do pó compacto.');
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string).conversation_id).toBeNull();
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string)).toEqual({
      message: 'Quero mais detalhes do pó compacto', include_tool_trace: true, conversation_id: conversationId,
    });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: 'Nova conversa' }));
    send('Outro assunto de compras');
    await screen.findByText('Outra conversa.');
    expect(JSON.parse(fetchMock.mock.calls[2][1]?.body as string).conversation_id).toBeNull();
    expect(screen.queryByText('Encontrei um pó compacto.')).toBeNull();
  });

  it('keeps the conversation identifier returned with an error for a later follow-up', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: { code: 'llm_unavailable', message: 'Provider failure' },
      conversation_id: conversationId, tool_trace: [] }, 503))
      .mockResolvedValueOnce(jsonResponse({ ...answer, message: 'Resultado da consulta anterior.' }));
    render(<App />); send('Procure maquiagem');
    await screen.findByRole('alert');
    send('O que você conseguiu consultar?');
    await screen.findByText('Resultado da consulta anterior.');
    expect(JSON.parse(fetchMock.mock.calls[1][1]?.body as string).conversation_id).toBe(conversationId);
  });

  it('informs expired or missing context without inventing a continuation', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(answer))
      .mockResolvedValueOnce(jsonResponse({ detail: { code: 'conversation_expired', message: 'Expired' } }, 410));
    render(<App />); send('Procure maquiagem');
    await screen.findByText(answer.message);
    send('Quero detalhes');
    expect((await screen.findByRole('alert')).textContent).toContain('Nova conversa');
    expect(screen.getByRole('status').textContent).toContain('O contexto desta conversa expirou');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { usePurchaseChat } from './usePurchaseChat';
import { answer, jsonResponse } from '../test/fixtures';

it('blocks synchronous duplicate requests before React renders pending state', async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = vi.fn<typeof fetch>().mockReturnValue(new Promise((done) => { resolve = done; }));
  vi.stubGlobal('fetch', fetchMock);
  const { result } = renderHook(usePurchaseChat);
  let first!: Promise<boolean>;
  let duplicate!: Promise<boolean>;
  act(() => {
    first = result.current.send('Meu pedido');
    duplicate = result.current.send('Meu pedido');
    result.current.clear();
  });
  expect(await duplicate).toBe(false);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(result.current.messages).toHaveLength(1);
  await act(async () => { resolve(jsonResponse(answer)); await first; });
  expect(result.current.messages).toHaveLength(2);
  expect(result.current.pending).toBe(false);
});

it('keeps the intent key on retries and remounts, and resets it only for a new conversation', async () => {
  sessionStorage.clear();
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(answer));
  vi.stubGlobal('fetch', fetchMock);
  const first = renderHook(usePurchaseChat);
  await act(async () => { await first.result.current.send('Meu pedido'); });
  const key = (fetchMock.mock.calls[0][1]?.headers as Record<string, string>)['Idempotency-Key'];
  expect(key).toMatch(/^[0-9a-f-]{36}$/i);
  await act(async () => { await first.result.current.send('Tente novamente'); });
  expect((fetchMock.mock.calls[1][1]?.headers as Record<string, string>)['Idempotency-Key']).toBe(key);
  first.unmount();
  const second = renderHook(usePurchaseChat);
  await act(async () => { await second.result.current.send('Meu pedido'); });
  expect((fetchMock.mock.calls[2][1]?.headers as Record<string, string>)['Idempotency-Key']).toBe(key);
  act(() => second.result.current.clear());
  await act(async () => { await second.result.current.send('Nova compra'); });
  expect((fetchMock.mock.calls[3][1]?.headers as Record<string, string>)['Idempotency-Key']).not.toBe(key);
  sessionStorage.clear();
});

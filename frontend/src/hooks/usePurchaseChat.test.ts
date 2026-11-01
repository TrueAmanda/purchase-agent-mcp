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

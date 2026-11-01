import { describe, expect, it } from 'vitest';
import { decodeToolTrace } from './toolTrace';
import { trace } from '../test/fixtures';

describe('public trace decoding', () => {
  it('uses only the explicit execution order and preserves repeated names', () => {
    const events = [{ ...trace[0], order: 3 }, { ...trace[0], order: 1 }, { ...trace[1], order: 2 }];
    const result = decodeToolTrace(events)!;
    expect(result.map((event) => event.order)).toEqual([1, 2, 3]);
    expect(result.map((event) => event.name)).toEqual(['buscar_produtos', 'criar_requisicao', 'buscar_produtos']);
  });

  it.each([
    undefined, null, 'buscar_produtos', {},
    [{ ...trace[0], order: 0 }], [{ ...trace[0], order: 1.5 }], [trace[0], trace[0]],
    [{ ...trace[0], name: 'private-tool-name' }], [{ ...trace[0], status: 'invented' }],
    [{ ...trace[0], duration_ms: -1 }], [{ ...trace[0], duration_ms: Infinity }],
  ])('marks invalid or missing evidence unavailable instead of inventing calls', (input) => {
    expect(decodeToolTrace(input)).toBeNull();
  });

  it('keeps safe counts and removes raw arguments, data, and unknown error strings', () => {
    const result = decodeToolTrace([{ ...trace[0], arguments: 'private-arguments',
      result: { product_count: 5, requester_name: 'private-person', api_key: 'private-key' } },
    { ...trace[1], status: 'error', code: 'private-error-body', result: { item_count: 999 } }])!;
    expect(result[0].result).toEqual({ product_count: 5 });
    expect(result[1].code).toBe('tool_execution_failed');
    expect(result[1].result).toBeNull();
    expect(JSON.stringify(result)).not.toContain('private');
  });

  it('does not fabricate a duration when it is missing', () => {
    expect(decodeToolTrace([{ ...trace[0], duration_ms: undefined }])![0].duration_ms).toBeNull();
  });
});

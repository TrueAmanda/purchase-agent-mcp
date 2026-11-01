import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ToolTraceEntry } from '../types/purchases';
import { trace } from '../test/fixtures';
import { ToolHistory } from './ToolHistory';

describe('structured MCP history', () => {
  it('starts collapsed and exposes real order, statuses, and elapsed durations when expanded', () => {
    const events: ToolTraceEntry[] = [trace[0],
      { order: 2, name: 'detalhar_produto', status: 'error', code: 'product_not_found', duration_ms: 1200, result: null },
      { ...trace[0], order: 3, status: 'timeout', code: 'api_timeout', duration_ms: null, result: null },
    ];
    const { container } = render(<ToolHistory events={events} />);
    const details = container.querySelector('details')!;
    expect(details.open).toBe(false);
    expect(screen.getByText('3 chamadas')).toBeTruthy();
    fireEvent.click(container.querySelector('summary')!);
    expect(details.open).toBe(true);
    const rows = container.querySelectorAll('li');
    expect([...rows].map((row) => row.value)).toEqual([1, 2, 3]);
    expect(rows[0].textContent).toContain('buscar_produtos');
    expect(rows[0].textContent).toContain('Concluída');
    expect(rows[0].textContent).toContain('388,9 ms');
    expect(rows[0].textContent).toContain('Produtos retornados: 5');
    expect(rows[1].textContent).toContain('Falhou');
    expect(rows[1].textContent).toContain('1,2 s');
    expect(rows[1].textContent).toContain('não existe no catálogo');
    expect(rows[2].textContent).toContain('Tempo esgotado');
    expect(rows[2].textContent).toContain('Duração não informada');
    fireEvent.click(container.querySelector('summary')!);
    expect(details.open).toBe(false);
  });

  it('distinguishes no invocations from unavailable evidence', () => {
    const { rerender } = render(<ToolHistory events={[]} />);
    expect(screen.getByText('Nenhuma chamada')).toBeTruthy();
    expect(screen.getByText('Nenhuma ferramenta foi chamada neste pedido.')).toBeTruthy();
    rerender(<ToolHistory events={null} />);
    expect(screen.getByText('Histórico indisponível')).toBeTruthy();
    expect(screen.queryByText('Nenhuma chamada')).toBeNull();
    expect(screen.getByText(/Isso não confirma a ausência de chamadas/)).toBeTruthy();
  });

  it('does not treat a successful creation tool as confirmation of a persisted purchase', () => {
    const { container } = render(<ToolHistory events={[{ ...trace[1], order: 1 }]} />);
    expect(container.textContent).toContain('Itens retornados: 1');
    expect(screen.queryByText('Registro confirmado')).toBeNull();
    expect(screen.queryByText('Requisição criada')).toBeNull();
  });
});

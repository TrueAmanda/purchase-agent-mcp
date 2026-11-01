import type { ToolTraceEntry } from '../types/purchases';
import { toolErrorLabels, toolLabels } from '../services/toolTrace';
import { Icon } from './Icon';

const statusLabels = { success: 'Concluída', error: 'Falhou', timeout: 'Tempo esgotado' };
const number = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });

function duration(value: number | null) {
  if (value === null) return 'Duração não informada';
  return value >= 1000 ? `${number.format(value / 1000)} s` : `${number.format(value)} ms`;
}

function resultSummary(entry: ToolTraceEntry) {
  if (entry.status !== 'success') return toolErrorLabels[entry.code ?? 'tool_execution_failed'] ?? toolErrorLabels.tool_execution_failed;
  const result = entry.result;
  const labels = [];
  if (result?.product_count !== undefined) labels.push(`Produtos retornados: ${result.product_count}`);
  if (result?.item_count !== undefined) labels.push(`Itens retornados: ${result.item_count}`);
  if (result?.replayed === true) labels.push('Resultado de uma tentativa repetida');
  return labels.join(' · ');
}

/** Only structured executor evidence is displayed; assistant prose is never inspected. */
export function ToolHistory({ events }: { events: ToolTraceEntry[] | null }) {
  const count = events?.length;
  return <details className="tool-history">
    <summary><span className="tool-history-heading"><Icon name="box" size={15} /> Ferramentas utilizadas</span>
      <span className="tool-history-count">{count === undefined ? 'Histórico indisponível'
        : count === 0 ? 'Nenhuma chamada' : `${count} ${count === 1 ? 'chamada' : 'chamadas'}`}</span>
      <Icon name="chevron" size={14} /></summary>
    {events === null ? <p className="tool-history-note">O serviço não enviou um histórico verificável. Isso não confirma a ausência de chamadas.</p>
      : events.length === 0 ? <p className="tool-history-note">Nenhuma ferramenta foi chamada neste pedido.</p>
        : <ol className="tool-history-list">{events.map((entry) => <li key={entry.order} value={entry.order} className={`tool-event tool-event-${entry.status}`}>
          <span className="tool-event-order" aria-label={`Chamada ${entry.order}`}>{entry.order}</span>
          <div className="tool-event-content"><div className="tool-event-heading"><strong>{toolLabels[entry.name]}</strong>
            <span className={`tool-event-status tool-event-status-${entry.status}`}><Icon name={entry.status === 'success' ? 'check' : 'alert'} size={12} />{statusLabels[entry.status]}</span></div>
            <div className="tool-event-meta"><code>{entry.name}</code><span>{duration(entry.duration_ms)}</span></div>
            {resultSummary(entry) && <p className="tool-event-result">{resultSummary(entry)}</p>}
          </div>
        </li>)}</ol>}
  </details>;
}

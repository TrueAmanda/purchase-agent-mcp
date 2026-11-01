import type { ChatEntry } from '../types/purchases';
import { Icon } from './Icon';
import { RequisitionCard } from './RequisitionCard';

export function ChatMessage({ entry, onViewRecords }: { entry: ChatEntry; onViewRecords: () => void }) {
  const user = entry.role === 'user';
  return <article className={`chat-message ${user ? 'user-message' : 'assistant-message'}`} aria-label={user ? 'Sua mensagem' : 'Resposta do assistente'}>
    {!user && <div className="agent-avatar"><Icon name="sparkles" size={18} /></div>}
    <div className="message-content"><div className="message-author">{user ? 'Você' : 'Assistente de compras'}</div>
      {entry.error ? <div className="error-message" role="alert">
        <div className="error-title"><Icon name="alert" size={17} /><strong>Não foi possível concluir</strong></div>
        <p>{entry.error.message}</p>
        {entry.error.uncertain && <p className="uncertainty-note">Se você pediu uma criação, ela pode ter sido registrada. Confira os registros antes de reenviar.</p>}
        <button className="text-button" type="button" onClick={onViewRecords}>Consultar registros <Icon name="chevron" size={14} /></button>
      </div> : <>
        {entry.response?.status === 'no_results' && <div className="empty-result-label"><Icon name="search" size={15} /> Nenhum produto encontrado</div>}
        <p className="message-text">{entry.content}</p>
        {entry.response?.status === 'created' && entry.response.requisitions.map((requisition) => <RequisitionCard key={requisition.id} requisition={requisition} />)}
      </>}
    </div>
  </article>;
}

export function PendingMessage() {
  return <div className="chat-message assistant-message pending-message" role="status">
    <div className="agent-avatar"><Icon name="sparkles" size={18} /></div>
    <div><div className="message-author">Assistente de compras</div>
      <div className="pending-copy"><span className="typing-dots" aria-hidden="true"><i /><i /><i /></span> Processando seu pedido…</div></div>
  </div>;
}

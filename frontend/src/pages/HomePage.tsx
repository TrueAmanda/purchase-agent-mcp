import { useEffect, useRef, useState } from 'react';
import { ChatMessage, PendingMessage } from '../components/ChatMessage';
import { Composer } from '../components/Composer';
import { Icon } from '../components/Icon';
import { SavedRequests } from '../components/SavedRequests';
import { Welcome } from '../components/Welcome';
import { usePurchaseChat } from '../hooks/usePurchaseChat';

export function HomePage() {
  const { messages, pending, send, clear, contextNotice } = usePurchaseChat();
  const [suggestion, setSuggestion] = useState<{ text: string; revision: number } | null>(null);
  const [showRecords, setShowRecords] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const revision = useRef(0);
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, pending]);

  function newConversation() {
    if (pending) return;
    clear();
    setSuggestion({ text: '', revision: ++revision.current });
  }
  return <div className="app-shell">
    <aside className="sidebar" aria-label="Navegação principal">
      <a className="brand" href="#main-content" aria-label="Purchase Agent, assistente de compras"><span className="brand-icon"><Icon name="sparkles" size={22} /></span>
        <span>purchase<span className="brand-accent">agent</span><small>COMPRAS COM INTELIGÊNCIA</small></span></a>
      <button type="button" className="new-chat" onClick={newConversation} disabled={pending}><Icon name="plus" size={18} /> Nova conversa</button>
      <span className="nav-label">ESPAÇO DE TRABALHO</span>
      <nav><div className="nav-current" aria-current="page"><Icon name="chat" size={18} /> Assistente <span className="nav-dot" /></div>
        <button type="button" className="nav-link" onClick={() => setShowRecords(true)}><Icon name="box" size={18} /> Requisições salvas</button></nav>
      <div className="sidebar-note"><span className="sidebar-note-icon"><Icon name="sparkles" size={19} /></span>
        <strong>Uma conversa que<br />acompanha sua compra.</strong><p>Peça mais detalhes de uma opção já mostrada. Use Nova conversa para começar outro assunto.</p></div>
      <div className="sidebar-footer"><div className="currency-symbol">$</div><div><strong>Valores em US$</strong><span>Exclusivamente dólares americanos</span></div></div>
    </aside>
    <main id="main-content" className="chat-main">
      <header className="topbar"><div className="breadcrumb"><span>Compras</span><Icon name="chevron" size={14} /><strong>Assistente</strong></div>
        <div className="topbar-actions"><span className="usd-pill">USD <span>·</span> US$</span>
          <button type="button" className="icon-button mobile-records" aria-label="Consultar requisições salvas" onClick={() => setShowRecords(true)}><Icon name="box" size={19} /></button>
          <button type="button" className="icon-button" aria-label="Nova conversa" onClick={newConversation} disabled={pending}><Icon name="plus" size={19} /></button></div></header>
      <div className={`conversation${messages.length === 0 ? ' conversation-empty' : ''}`}>
        {messages.length === 0 ? <Welcome onSuggestion={(text) => setSuggestion({ text, revision: ++revision.current })} />
          : <div className="message-list" role="log" aria-label="Conversa de compras" aria-live="polite" aria-relevant="additions">
            {messages.map((entry) => <ChatMessage key={entry.id} entry={entry} onViewRecords={() => setShowRecords(true)} />)}
            {pending && <PendingMessage />}<div ref={end} />
          </div>}
      </div>
      <div className="composer-area"><Composer pending={pending} suggestion={suggestion} onSend={send} />
        <p className={`app-footnote${contextNotice ? ' input-error' : ''}`} role={contextNotice ? 'status' : undefined}>
          {contextNotice ?? 'O assistente considera o contexto recente desta conversa.'}</p></div>
    </main>
    {showRecords && <SavedRequests onClose={() => setShowRecords(false)} />}
  </div>;
}

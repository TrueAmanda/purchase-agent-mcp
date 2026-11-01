import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Icon } from './Icon';

interface ComposerProps {
  pending: boolean;
  suggestion: { text: string; revision: number } | null;
  onSend: (message: string) => Promise<boolean>;
}

export function Composer({ pending, suggestion, onSend }: ComposerProps) {
  const [draft, setDraft] = useState('');
  const input = useRef<HTMLTextAreaElement>(null);
  const tooLong = draft.trim().length > 20_000;
  useEffect(() => { if (suggestion) { setDraft(suggestion.text); input.current?.focus(); } }, [suggestion]);
  useEffect(() => { if (!pending) input.current?.focus(); }, [pending]);
  useEffect(() => {
    if (input.current) {
      input.current.style.height = 'auto';
      input.current.style.height = `${Math.min(Math.max(input.current.scrollHeight, 52), 180)}px`;
    }
  }, [draft]);

  function submit() {
    if (pending || tooLong || !draft.trim()) return;
    const message = draft;
    setDraft('');
    void onSend(message);
  }
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); submit();
    }
  }
  return <form className="composer" onSubmit={(event) => { event.preventDefault(); submit(); }} aria-label="Enviar pedido de compra">
    <div className={`composer-box${tooLong ? ' composer-invalid' : ''}`}>
      <label className="sr-only" htmlFor="purchase-message">Seu pedido de compra</label>
      <textarea ref={input} id="purchase-message" placeholder="Descreva o que você precisa comprar…" value={draft}
        onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} disabled={pending}
        aria-describedby="composer-help" aria-invalid={tooLong} rows={2} />
      <div className="composer-toolbar"><span><Icon name="sparkles" size={14} /> {pending ? 'Aguarde a resposta do assistente' : 'Pedidos em linguagem natural'}</span>
        <button className="send-button" type="submit" disabled={pending || !draft.trim() || tooLong} aria-label="Enviar pedido">
          {pending ? <span className="spinner" aria-hidden="true" /> : <Icon name="arrow" size={19} />}
        </button>
      </div>
    </div>
    <div id="composer-help" className={`composer-help${tooLong ? ' input-error' : ''}`}>
      {tooLong ? 'O pedido deve ter no máximo 20.000 caracteres.' : <>Inclua produto, quantidade e orçamento em US$. <span>Enter envia · Shift + Enter quebra a linha</span></>}
    </div>
  </form>;
}

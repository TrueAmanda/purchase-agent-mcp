import { useCallback, useRef, useState } from 'react';
import { PurchaseApiError, submitPurchase } from '../services/purchaseApi';
import type { ChatEntry } from '../types/purchases';

export function usePurchaseChat() {
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const nextId = useRef(0);
  const conversationId = useRef<string | null>(null);
  const [contextNotice, setContextNotice] = useState<string | null>(null);

  const send = useCallback(async (message: string): Promise<boolean> => {
    const content = message.trim();
    // A synchronous lock prevents duplicate submissions before disabled controls render.
    if (inFlight.current || !content || content.length > 20_000) return false;
    inFlight.current = true;
    setPending(true);
    const id = String(++nextId.current);
    setMessages((previous) => [...previous, { id: `user-${id}`, role: 'user', content }]);
    try {
      const response = await submitPurchase(content, conversationId.current);
      conversationId.current = response.conversation_id ?? null;
      setContextNotice(conversationId.current ? null
        : 'O contexto não foi confirmado pelo serviço. Use Nova conversa e inclua os detalhes completos.');
      setMessages((previous) => [...previous, { id: `assistant-${id}`, role: 'assistant', content: response.message,
        response, toolTrace: response.tool_trace ?? null }]);
    } catch (error) {
      const failure = error instanceof PurchaseApiError ? error : new PurchaseApiError('Não foi possível confirmar a resposta do serviço.', 'unknown');
      if (failure.conversationId) conversationId.current = failure.conversationId;
      if (failure.code === 'conversation_expired') {
        setContextNotice('O contexto desta conversa expirou. Clique em Nova conversa para continuar.');
      }
      setMessages((previous) => [...previous, { id: `assistant-${id}`, role: 'assistant', content: failure.message,
        error: { message: failure.message, uncertain: failure.uncertain }, toolTrace: failure.toolTrace }]);
    } finally { inFlight.current = false; setPending(false); }
    return true;
  }, []);

  const clear = useCallback(() => {
    if (!inFlight.current) { setMessages([]); conversationId.current = null; setContextNotice(null); }
  }, []);
  return { messages, pending, send, clear, contextNotice };
}

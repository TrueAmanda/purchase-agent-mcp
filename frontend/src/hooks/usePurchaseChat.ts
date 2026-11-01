import { useCallback, useRef, useState } from 'react';
import { PurchaseApiError, submitPurchase } from '../services/purchaseApi';
import type { ChatEntry } from '../types/purchases';

const INTENT_STORAGE_KEY = 'purchase-agent-intent';

function storedIntentKey(): string {
  try {
    const previous = sessionStorage.getItem(INTENT_STORAGE_KEY);
    if (previous && /^[0-9a-f-]{36}$/i.test(previous)) return previous;
  } catch { /* Storage can be disabled; the in-memory key still protects retries. */ }
  const key = crypto.randomUUID();
  try { sessionStorage.setItem(INTENT_STORAGE_KEY, key); } catch { /* No automatic retries. */ }
  return key;
}

export function usePurchaseChat() {
  const [messages, setMessages] = useState<ChatEntry[]>([]);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const nextId = useRef(0);
  const conversationId = useRef<string | null>(null);
  const intentKey = useRef<string | null>(null);
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
      intentKey.current ??= storedIntentKey();
      const response = await submitPurchase(content, conversationId.current, intentKey.current);
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
    if (!inFlight.current) {
      setMessages([]); conversationId.current = null; intentKey.current = null; setContextNotice(null);
      try { sessionStorage.removeItem(INTENT_STORAGE_KEY); } catch { /* Storage can be disabled. */ }
    }
  }, []);
  return { messages, pending, send, clear, contextNotice };
}

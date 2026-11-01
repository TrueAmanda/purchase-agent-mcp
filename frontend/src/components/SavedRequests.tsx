import { useEffect, useRef, useState } from 'react';
import { listPurchases, PurchaseApiError } from '../services/purchaseApi';
import type { PurchaseListResponse } from '../types/purchases';
import { Icon } from './Icon';
import { RequisitionCard } from './RequisitionCard';

export function SavedRequests({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [result, setResult] = useState<PurchaseListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    dialog.current?.showModal();
    let active = true;
    void listPurchases().then((data) => { if (active) setResult(data); }).catch((failure: unknown) => {
      if (active) setError(failure instanceof PurchaseApiError ? failure.message : 'Não foi possível consultar os registros.');
    });
    return () => { active = false; dialog.current?.close(); };
  }, []);
  return <dialog ref={dialog} className="records-dialog" onCancel={onClose} aria-labelledby="records-title">
    <div className="records-header"><div><span className="eyebrow">REGISTROS DE COMPRA</span><h2 id="records-title">Requisições salvas</h2></div>
      <button type="button" className="icon-button" aria-label="Fechar registros" onClick={onClose}><Icon name="close" /></button></div>
    <div className="records-content">
      <p className="records-description">Até 10 registros mais recentes, consultados diretamente no banco. Abrir esta lista não repete seu pedido.</p>
      {!result && !error && <p className="records-loading" role="status"><span className="spinner" /> Consultando registros…</p>}
      {error && <p className="records-error" role="alert">{error}</p>}
      {result?.requisitions.length === 0 && <div className="records-empty"><Icon name="box" size={30} /><h3>Nenhuma requisição registrada</h3><p>Os registros confirmados aparecerão aqui.</p></div>}
      {result?.requisitions.map((requisition) => <RequisitionCard key={requisition.id} requisition={requisition} />)}
      {result?.has_more && <p className="records-description">Há registros anteriores aos exibidos nesta lista.</p>}
    </div>
  </dialog>;
}

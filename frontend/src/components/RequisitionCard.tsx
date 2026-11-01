import type { ConfirmedRequisition, PurchaseRequisition } from '../types/purchases';
import { formatDate, formatMoney } from '../utils/format';
import { Icon } from './Icon';

export function RequisitionCard({ requisition }: { requisition: PurchaseRequisition | ConfirmedRequisition }) {
  const replayed = 'replayed' in requisition && requisition.replayed;
  return <section className="requisition-card" aria-label={`Registro: ${requisition.title}`}>
    <div className="requisition-heading">
      <span className="confirmed-icon"><Icon name="check" size={17} /></span>
      <div><span className="eyebrow success-text">{replayed ? 'Registro existente' : 'Registro confirmado'}</span><h3>{requisition.title}</h3></div>
      {requisition.currency && <span className="currency-tag">{requisition.currency}</span>}
    </div>
    <div className="request-number"><span>Número da requisição</span><code>{requisition.request_number ?? 'Não informado'}</code></div>
    <ul className="purchase-items">{requisition.items.map((item) => <li key={item.id}>
      <div className="item-description"><strong>{item.name ?? 'Nome não informado'}</strong>
        {item.product_id && <span>Produto #{item.product_id}</span>}<p>{item.description}</p></div>
      <dl className="item-values"><div><dt>Quantidade</dt><dd>{item.quantity}</dd></div>
        <div><dt>Valor unitário</dt><dd>{formatMoney(item.unit_price, requisition.currency)}</dd></div>
        <div><dt>Subtotal</dt><dd>{formatMoney(item.subtotal, requisition.currency)}</dd></div></dl>
    </li>)}</ul>
    <div className="purchase-total"><span>Total da requisição</span><strong>{formatMoney(requisition.total, requisition.currency)}</strong></div>
    <footer className="requisition-footer"><span>Solicitante: {requisition.requester_name}</span>
      <time dateTime={requisition.created_at}>{formatDate(requisition.created_at)}</time></footer>
  </section>;
}

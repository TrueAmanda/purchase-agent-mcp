import { Icon } from './Icon';

const suggestions = [
  { icon: 'search' as const, title: 'Encontrar produtos', description: 'Explore opções para o que você precisa.', prompt: 'Busque notebooks para um time de vendas, até US$ 2.000 por unidade. Não crie uma requisição ainda.' },
  { icon: 'box' as const, title: 'Criar uma requisição', description: 'Informe os itens, a quantidade e o orçamento.', prompt: 'Quero criar uma requisição de 1 laptop, até US$ 2.000. Quais dados você precisa para registrar o pedido?' },
  { icon: 'chat' as const, title: 'Planejar uma compra', description: 'Organize os detalhes antes de escolher.', prompt: 'Quais informações preciso definir para planejar a compra de equipamentos para meu escritório?' },
];

export function Welcome({ onSuggestion }: { onSuggestion: (text: string) => void }) {
  return <section className="welcome" aria-labelledby="welcome-title">
    <div className="welcome-symbol"><Icon name="sparkles" size={30} /></div>
    <span className="welcome-eyebrow">SEU ASSISTENTE DE COMPRAS</span>
    <h1 id="welcome-title">O que vamos<br /><span>comprar hoje?</span></h1>
    <p className="welcome-copy">Da primeira ideia à requisição registrada.<br />Conte o que precisa e vamos encontrar as opções.</p>
    <div className="suggestions">{suggestions.map((suggestion) => <button key={suggestion.title} type="button" className="suggestion" onClick={() => onSuggestion(suggestion.prompt)}>
      <span className="suggestion-icon"><Icon name={suggestion.icon} size={20} /></span>
      <strong>{suggestion.title}</strong><span>{suggestion.description}</span><Icon name="chevron" size={16} />
    </button>)}</div>
  </section>;
}

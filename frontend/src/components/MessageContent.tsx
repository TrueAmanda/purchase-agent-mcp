import { useState } from 'react';
import { Icon } from './Icon';

const IMAGE_MARKDOWN = /!\[([^\]\r\n]{0,200})\]\(([^\s)]+)\)/g;
const MAX_IMAGES = 6;

/** Only known catalog assets may produce external browser requests. */
export function catalogImageUrl(candidate: string): string | null {
  if (candidate.length > 2048 || candidate.includes('\\')) return null;
  try {
    const url = new URL(candidate);
    if (url.protocol !== 'https:' || url.hostname !== 'cdn.dummyjson.com' || url.port
      || url.username || url.password || url.search || url.hash
      || !/^\/product-images\/[a-z0-9_/-]+\.(webp|png|jpe?g|avif)$/i.test(url.pathname)) return null;
    return url.href;
  } catch { return null; }
}

function CatalogImage({ url, alt }: { url: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  const description = alt.trim() && alt.trim().toLowerCase() !== 'imagem' ? alt.trim() : 'Imagem do produto';
  return <figure className="catalog-image">
    {failed ? <div className="catalog-image-unavailable" role="status"><Icon name="box" size={25} /><span>Imagem indisponível</span></div>
      : <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Abrir imagem: ${description}`}>
        <img src={url} alt={description} loading="lazy" decoding="async" referrerPolicy="no-referrer"
          width={320} height={240} onError={() => setFailed(true)} />
      </a>}
    <figcaption>{description}</figcaption>
  </figure>;
}

/** Focused support for Markdown images; all other content remains escaped React text. */
export function MessageContent({ text }: { text: string }) {
  const parts = [];
  let cursor = 0;
  let imageCount = 0;
  for (const match of text.matchAll(IMAGE_MARKDOWN)) {
    const url = catalogImageUrl(match[2]);
    if (!url || imageCount >= MAX_IMAGES) continue;
    const preceding = text.slice(cursor, match.index);
    if (preceding.trim()) parts.push(<p className="message-text" key={`text-${cursor}`}>{preceding}</p>);
    parts.push(<CatalogImage key={`image-${match.index}-${url}`} url={url} alt={match[1]} />);
    cursor = match.index + match[0].length;
    imageCount += 1;
  }
  const rest = text.slice(cursor);
  if (rest.trim() || parts.length === 0) parts.push(<p className="message-text" key={`text-${cursor}`}>{rest}</p>);
  return <div className="message-body">{parts}</div>;
}

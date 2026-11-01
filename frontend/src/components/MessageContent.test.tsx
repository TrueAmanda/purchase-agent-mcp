import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MessageContent } from './MessageContent';
import { ChatMessage } from './ChatMessage';

const thumbnail = 'https://cdn.dummyjson.com/product-images/beauty/eyeshadow-palette-with-mirror/thumbnail.webp';

describe('catalog images in assistant messages', () => {
  it.each(["dior-j'adore", 'dior-j%27adore'])('renders legitimate product paths containing an apostrophe: %s', (slug) => {
    const url = `https://cdn.dummyjson.com/product-images/fragrances/${slug}/thumbnail.webp`;
    render(<MessageContent text={`Dior J'adore - US$ 89,99.\n![Dior J'adore](${url})`} />);
    expect(screen.getByRole('img', { name: "Dior J'adore" }).getAttribute('src')).toBe(url);
    expect(screen.getByRole('link', { name: "Abrir imagem: Dior J'adore" }).getAttribute('href')).toBe(url);
  });

  it('renders the returned Markdown thumbnail while preserving surrounding text', () => {
    render(<MessageContent text={`Produto encontrado.\nImagem: ![imagem](${thumbnail})\nPreço: US$ 19,99.`} />);
    const image = screen.getByRole('img', { name: 'Imagem do produto' });
    expect(image.getAttribute('src')).toBe(thumbnail);
    expect(image.getAttribute('loading')).toBe('lazy');
    expect(image.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(screen.getByText(/Produto encontrado/)).toBeTruthy();
    expect(screen.getByText(/Preço: US\$ 19,99/)).toBeTruthy();
    const link = screen.getByRole('link', { name: 'Abrir imagem: Imagem do produto' });
    expect(link.getAttribute('href')).toBe(thumbnail);
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('shows an honest unavailable state after an image error without substituting a photo', () => {
    const { container, rerender } = render(<MessageContent text={`![Paleta](${thumbnail})`} />);
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByRole('status').textContent).toContain('Imagem indisponível');
    expect(screen.queryByRole('img')).toBeNull();
    expect(container.querySelectorAll('img')).toHaveLength(0);
    const another = thumbnail.replace('thumbnail.webp', '1.webp');
    rerender(<MessageContent text={`![Outra imagem](${another})`} />);
    expect(screen.getByRole('img').getAttribute('src')).toBe(another);
  });

  it.each([
    'http://cdn.dummyjson.com/product-images/beauty/palette/thumbnail.webp',
    'https://example.com/thumbnail.webp',
    'https://cdn.dummyjson.com.evil.test/product-images/beauty/palette/thumbnail.webp',
    'https://user:secret@cdn.dummyjson.com/product-images/beauty/palette/thumbnail.webp',
    `${thumbnail}?secret=value`, `${thumbnail}#secret`,
    'https://cdn.dummyjson.com:8443/product-images/beauty/palette/thumbnail.webp',
    'https://cdn.dummyjson.com/private/thumbnail.webp',
    'https://cdn.dummyjson.com/product-images/beauty/palette/thumbnail.svg',
    'https://cdn.dummyjson.com/product-images/fragrances/dior-j%2527adore/thumbnail.webp',
    'data:image/png;base64,AAAA', 'javascript:alert(1)', '/thumbnail.webp',
  ])('keeps unsupported image URLs as inert text: %s', (url) => {
    const text = `![imagem](${url})`;
    render(<MessageContent text={text} />);
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByText(text)).toBeTruthy();
  });

  it('does not interpret arbitrary HTML, including image alternative text', () => {
    const markup = '<img src=x onerror=alert(1)><script>alert(1)</script>';
    const { container } = render(<MessageContent text={`${markup}\n![<b>Paleta</b>](${thumbnail})`} />);
    expect(screen.getByText(markup)).toBeTruthy();
    expect(screen.getByRole('img').getAttribute('alt')).toBe('<b>Paleta</b>');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelectorAll('img')).toHaveLength(1);
  });

  it('bounds image loads and preserves additional references as text', () => {
    const markdown = Array.from({ length: 7 }, (_, index) => `![Produto ${index}](${thumbnail.replace('thumbnail.webp', `${index}.webp`)})`);
    render(<MessageContent text={markdown.join('\n')} />);
    expect(screen.getAllByRole('img')).toHaveLength(6);
    expect(screen.getByText(markdown[6])).toBeTruthy();
  });

  it('keeps image syntax in user messages literal', () => {
    const text = `![imagem](${thumbnail})`;
    render(<ChatMessage entry={{ id: 'user-1', role: 'user', content: text }} onViewRecords={vi.fn()} />);
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.getByText(text)).toBeTruthy();
  });
});

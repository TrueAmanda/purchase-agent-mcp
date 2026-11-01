import { describe, expect, it } from 'vitest';
import { formatMoney } from './format';

const clean = (value: string) => value.replace(/\s/g, ' ');
describe('authoritative monetary display', () => {
  it.each([
    ['5399.97', 'USD', 'US$ 5.399,97'],
    ['9.99', 'USD', 'US$ 9,99'],
    ['0.00', 'USD', 'US$ 0,00'],
    ['100', 'USD', 'US$ 100,00'],
    ['29.97', 'BRL', 'R$ 29,97'],
    ['10.00', 'EUR', '€ 10,00'],
    ['999999999999999999.99', 'USD', 'US$ 999.999.999.999.999.999,99'],
    ['1.999', 'USD', 'US$ 2,00'],
    ['10.50', 'JPY', 'JP¥ 11'],
  ])('formats %s in %s without binary monetary arithmetic', (value, currency, expected) => {
    expect(clean(formatMoney(value, currency))).toBe(expected);
  });
  it('does not substitute zero or USD for absent fields', () => {
    expect(formatMoney(null, 'USD')).toBe('Não informado');
    expect(formatMoney('29.97', null)).toBe('29,97 (moeda não informada)');
    expect(formatMoney('29.97', '???')).toContain('moeda não informada');
    expect(formatMoney('NaN', 'USD')).toBe('Valor indisponível');
  });
});

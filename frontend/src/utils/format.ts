/** Decimal strings are formatted without losing cents above Number.MAX_SAFE_INTEGER. */
export function formatMoney(value: string | null, currency: string | null): string {
  if (value === null) return 'Não informado';
  if (!/^\d+(?:\.\d+)?$/.test(value)) return 'Valor indisponível';
  const [integer, decimals = ''] = value.split('.');
  if (!currency || !/^[A-Z]{3}$/.test(currency)) {
    return `${BigInt(integer).toLocaleString('pt-BR')},${decimals.padEnd(2, '0')} (moeda não informada)`;
  }
  try {
    const formatter = new Intl.NumberFormat('pt-BR', { style: 'currency', currency });
    const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
    const scale = 10n ** BigInt(digits);
    const padded = decimals.padEnd(digits + 1, '0');
    const rounded = BigInt(integer) * scale + BigInt(padded.slice(0, digits) || '0')
      + (Number(padded[digits]) >= 5 ? 1n : 0n);
    const fraction = (rounded % scale).toString().padStart(digits, '0');
    return formatter.formatToParts(rounded / scale)
      .map((part) => part.type === 'fraction' ? fraction : part.value).join('');
  } catch { return `${value} (moeda não informada)`; }
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
}

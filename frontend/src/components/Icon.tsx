type IconName = 'sparkles' | 'arrow' | 'plus' | 'chat' | 'box' | 'check' | 'search' | 'alert' | 'close' | 'chevron';
const paths: Record<IconName, string> = {
  sparkles: 'm12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6L12 3ZM20 2v4m-2-2h4',
  arrow: 'M12 19V5m-6 6 6-6 6 6',
  plus: 'M12 5v14M5 12h14',
  chat: 'M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 4 11.5a8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z',
  box: 'm12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V8m-9 5v10M7.5 5.5l9 5',
  check: 'm5 12 4 4L19 6',
  search: 'M21 21l-5-5m2-6a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z',
  alert: 'M12 8v4m0 4h.01M10.3 3.9 1.8 18.1A2 2 0 0 0 3.5 21h17a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0Z',
  close: 'm6 6 12 12M6 18 18 6',
  chevron: 'm9 5 7 7-7 7',
};

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

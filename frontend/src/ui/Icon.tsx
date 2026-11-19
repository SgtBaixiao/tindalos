import type { SVGProps } from 'react';

export type IconName =
  | 'compass'
  | 'grid'
  | 'book'
  | 'search'
  | 'history'
  | 'check'
  | 'memory'
  | 'arrow'
  | 'sun'
  | 'moon';
const paths: Record<IconName, string> = {
  compass: 'M12 2 22 12 12 22 2 12Z M8 16l3-7 5-1-3 7Z',
  grid: 'M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z',
  book: 'M4 3h6a3 3 0 0 1 3 3v15a4 4 0 0 0-4-2H4Z M13 6a3 3 0 0 1 3-3h5v16h-5a3 3 0 0 0-3 2',
  search: 'M17 17l5 5 M19 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  history: 'M3 8a9 9 0 1 1 0 9 M3 3v5h5 M12 7v6l4 2',
  check: 'M7 3h10l4 5v13H3V8Z M7 12l3 3 7-7',
  memory: 'M4 5h16v14H4Z M8 9h8 M8 13h8 M8 17h4 M8 2v3 M16 2v3',
  arrow: 'M4 12h16 M14 6l6 6-6 6',
  sun: 'M12 3V1 M12 23v-2 M3 12H1 M23 12h-2 M5 5 3 3 M21 21l-2-2 M5 19l-2 2 M21 3l-2 2 M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0',
  moon: 'M21 14a9 9 0 1 1-11-11 7 7 0 0 0 11 11',
};
export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}

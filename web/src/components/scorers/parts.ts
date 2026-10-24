import type { Scorer } from '@/lib/family';

export const partId = (scorer: Scorer, part: string) => `${scorer}-${part}`;

import type { Scorer } from '@/lib/family';

/** The id of a part's heading in a scorer's methodology section. */
export const partId = (scorer: Scorer, part: string) => `${scorer}-${part}`;

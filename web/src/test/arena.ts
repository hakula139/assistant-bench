import type { FamilyView, PairState } from '@/lib/arena';
import type { Defect } from '@/lib/defects';

export const VOTE_FAMILY: FamilyView = {
  family: 'fixture',
  aspect: 'writing',
  display_name: 'Chinese',
};

export const WRITING_DEFECTS: Defect[] = [
  'scope',
  'facts',
  'clarity',
  'tone',
  'mechanics',
  'formulaic',
  'length',
];

/** A pair ID, which the API requires to be a UUID, numbered for readable tests. */
export const pairId = (index: number) =>
  `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;

export function requestBody(init?: RequestInit): unknown {
  if (typeof init?.body !== 'string') {
    throw new Error('Expected a JSON request body');
  }
  return JSON.parse(init.body);
}

export function makeState(overrides: Partial<PairState> = {}): PairState {
  return {
    pair: {
      id: pairId(1),
      instruction: 'Compare the two outputs.',
      checklist: ['Use the requested tone.'],
      category: 'message',
      render: 'text',
      a: 'Output A',
      b: 'Output B',
      defects: WRITING_DEFECTS,
    },
    votes: 0,
    remaining: 2,
    undo: false,
    ...overrides,
  };
}

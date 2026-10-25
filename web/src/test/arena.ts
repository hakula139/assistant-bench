import type { FamilyView, SessionView } from '@/lib/arena';

export const COMMAND_ID = '00000000-0000-4000-8000-000000000001';

export const VOTE_FAMILY: FamilyView = {
  name: 'fixture',
  aspect: 'writing',
  display_name: 'Chinese',
  defects: ['scope', 'facts', 'clarity', 'tone', 'mechanics', 'formulaic', 'length'],
};

export function requestBody(init?: RequestInit): unknown {
  if (typeof init?.body !== 'string') {
    throw new Error('Expected a JSON request body');
  }
  return JSON.parse(init.body);
}

export function makeSession(overrides: Partial<SessionView> = {}): SessionView {
  return {
    id: 's1',
    family: structuredClone(VOTE_FAMILY),
    votes: 0,
    remaining: 2,
    undo_acknowledgment: null,
    pair: {
      id: 'lease-1',
      capability: 'private-pair-capability',
      instruction: 'Compare the two outputs.',
      checklist: ['Use the requested tone.'],
      a: { render: 'text', text: 'Output A' },
      b: { render: 'text', text: 'Output B' },
    },
    ...overrides,
  };
}

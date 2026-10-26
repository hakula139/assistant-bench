export const defects = [
  'facts',
  'mechanics',
  'tone',
  'formulaic',
  'length',
  'scope',
  'conventions',
  'complexity',
  'testing',
  'clarity',
] as const;

export type Defect = (typeof defects)[number];

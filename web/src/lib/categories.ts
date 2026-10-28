export const categories = [
  'bug-fix',
  'docs',
  'essay',
  'feature',
  'fiction',
  'message',
  'refactor',
  'reply',
  'review',
  'smoke',
] as const;

export type Category = (typeof categories)[number];

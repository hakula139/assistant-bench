import { renderToString } from 'react-dom/server';
import { describe, expect, test } from 'vitest';

import { TestsBoard } from '@/components/boards/TestsBoard';
import type { TestsRound } from '@/lib/reports';
import { FIXTURE_NAMES } from '@/test/fixture';

const standing = (system: string, passRate: number) => ({
  system,
  fingerprint: null,
  trials: 4,
  errored: 0,
  pass_rate: passRate,
  mean_cost_usd: null,
  p50_latency_sec: null,
});

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('TestsBoard', () => {
  test("lists the latest round's systems by pass rate, best first", () => {
    const rounds: TestsRound[] = [
      { round: '2026-10-07', results: { scorer: 'tests', standings: [standing('acme-tiny', 1)] } },
      {
        round: '2026-10-21',
        results: {
          scorer: 'tests',
          standings: [standing('acme-small', 0.25), standing('acme-pro', 0.75)],
        },
      },
    ];

    const html = renderToString(<TestsBoard rounds={rounds} names={FIXTURE_NAMES} />);

    expect(text(html)).toMatch(/Round 2026-10-21 .* Acme Pro 75% 4 0 – – Acme Small 25% 4 0 – – $/);
  });
});

// @vitest-environment jsdom
import { cleanup, render as renderInDom, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test } from 'vitest';

import { TestsBoard } from '@/components/boards/TestsBoard';
import { terms } from '@/lib/glossary';
import type { TestsRound } from '@/lib/reports';
import { FIXTURE_NAMES } from '@/test/fixture';

const standing = (system: string, passRate: number) => ({
  system,
  trials: 4,
  errored: 0,
  pass_rate: passRate,
  mean_cost_usd: null,
  p50_latency_sec: null,
});

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

const round: TestsRound = {
  round: '2026-10-21',
  closed_at: '2026-10-21T18:00:00Z',
  systems: Object.fromEntries(
    ['acme-small', 'acme-pro'].map((system) => [
      system,
      { display_name: FIXTURE_NAMES[system], fingerprint: null },
    ]),
  ),
  results: {
    scorer: 'tests',
    standings: [standing('acme-small', 0.25), standing('acme-pro', 0.75)],
  },
};

const rounds = [{ round: round.round, href: '/coding/smoke/' }];

describe('TestsBoard', () => {
  afterEach(cleanup);

  test("lists the round's systems by pass rate, best first", () => {
    const html = renderToString(<TestsBoard round={round} rounds={rounds} />);

    expect(text(html)).toMatch(/Round 2026-10-21 .* Acme Pro 75% 4 0 – – Acme Small 25% 4 0 – – $/);
  });

  test.each([
    ['Pass rate', terms.passRate],
    ['Errored', terms.errored],
    ['Cost / trial', terms.testsCostLatency],
    ['p50 latency', terms.testsCostLatency],
  ])("opens the %s column's definition from its header", async (header, term) => {
    renderInDom(<TestsBoard round={round} rounds={rounds} />);

    await userEvent.setup().click(screen.getByRole('button', { name: header }));

    expect((await screen.findByRole('dialog', { name: term.term })).textContent).toContain(
      term.definition,
    );
  });
});

// @vitest-environment jsdom
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, test } from 'vitest';

import { CheckersBoard } from '@/components/boards/CheckersBoard';
import { readReport } from '@/test/fixture';
import { stubPlotFrame } from '@/test/plot';

beforeEach(() => {
  stubPlotFrame();
  window.history.replaceState(null, '', '/');
});
afterEach(cleanup);

test('keeps strict ties and descriptive effects separate from applicable-rule denominators', async () => {
  const round = await readReport('report-checkers', 'checkers');

  render(<CheckersBoard round={round} />);

  const board = screen.getAllByRole('table')[0];
  expect(
    within(board)
      .getAllByRole('row')
      .map((row) => row.textContent),
  ).toEqual([
    'SystemPass rate95% intervalCases',
    'Acme75%19–99%4',
    'Acme · Instructions75%19–99%4',
  ]);
  expect(screen.getByText(/cannot separate it from Acme · Instructions/)).toBeTruthy();
  expect(screen.getByText(/do not establish significance/)).toBeTruthy();
  const details = screen.getByText('Acme', { selector: 'summary' }).parentElement;
  expect(details).not.toBeNull();
  await userEvent.setup().click(screen.getByText('Acme', { selector: 'summary' }));
  expect(details?.textContent).toContain('Straight quotes1 / 11');
});

test('filters strict scores by category without changing the paired overall effects', async () => {
  render(<CheckersBoard round={await readReport('report-checkers', 'checkers')} />);

  await userEvent.setup().selectOptions(screen.getByRole('combobox'), 'message');

  const board = screen.getAllByRole('table')[0];
  expect(
    within(board)
      .getAllByRole('row')
      .map((row) => row.textContent),
  ).toEqual([
    'SystemPass rate95% intervalCases',
    'Acme100%16–100%2',
    'Acme · Instructions50%1–99%2',
  ]);
  expect(window.location.search).toContain('category=message');
  expect(screen.getByRole('heading', { name: 'Prompt effect' })).toBeTruthy();
});

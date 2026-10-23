// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Hint } from '@/components/site/Term';
import { terms } from '@/lib/glossary';

afterEach(cleanup);

describe('Hint', () => {
  test('opens the definition on tap with its last words kept on one line', async () => {
    const user = userEvent.setup();
    render(<Hint term={terms.separatedPairs} />);

    await user.click(screen.getByRole('button', { name: 'Separated pairs' }));

    const definition = screen.getByText(/^The share of system pairs/);
    expect(definition.textContent).toBe(terms.separatedPairs.definition);
    expect(definition.querySelector('.text-nowrap')?.textContent).toBe('not overlap.');
  });
});

// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { Hint } from '@/components/site/Term';
import { terms } from '@/lib/glossary';

afterEach(cleanup);

describe('Hint', () => {
  test('opens a dialog described by the definition, with its last words kept on one line', async () => {
    const user = userEvent.setup();
    render(<Hint term={terms.decisive} />);

    await user.click(screen.getByRole('button', { name: 'Decisive' }));

    const dialog = screen.getByRole('dialog', {
      name: 'Decisive',
      description: terms.decisive.definition,
    });
    expect(dialog.querySelector('.text-nowrap')?.textContent).toBe('bad votes.');
  });
});

// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { VoteApp } from '@/components/vote/VoteApp';
import type { SessionView } from '@/lib/arena';

interface Cast {
  token: string;
  choice: string;
  flags: string[];
  note: string | null;
}

/** A stand-in for the arena API that serves numbered pairs and records each vote and undo. */
function stubApi() {
  const casts: Cast[] = [];
  let undos = 0;
  let served = 0;
  const view = (): SessionView => {
    served += 1;
    return {
      id: 's1',
      family: 'prose-zh',
      votes: casts.length - undos,
      size: 30,
      pair: {
        token: `t${served}`,
        instruction: `Instruction ${served}`,
        checklist: [],
        a: { render: 'text', text: `Output A${served}` },
        b: { render: 'text', text: `Output B${served}` },
      },
    };
  };
  const respond = (value: unknown, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(value), { status }));

  vi.stubGlobal('fetch', (path: string, init?: RequestInit) => {
    if (path === '/api/families') {
      return respond(['prose-zh']);
    }
    if (path === '/api/sessions') {
      return respond(view(), 201);
    }
    if (path === '/api/sessions/s1/undo') {
      undos += 1;
      return respond(view());
    }
    const vote = /^\/api\/pairs\/([^/]+)\/vote$/.exec(path);
    if (vote) {
      casts.push({ token: vote[1], ...(JSON.parse(init?.body as string) as Omit<Cast, 'token'>) });
      return respond(view());
    }
    return respond({ detail: `unexpected ${path}` }, 404);
  });
  return { casts, undos: () => undos };
}

async function startSession() {
  const user = userEvent.setup();
  render(<VoteApp />);
  await user.click(await screen.findByRole('button', { name: 'prose-zh' }));
  await screen.findByText('Output A1');
  return user;
}

describe('VoteApp', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test('submits from the note by chord, then takes the next key as a shortcut', async () => {
    const api = stubApi();
    const user = await startSession();

    await user.keyboard('a{n}');
    await user.keyboard('第一行{Enter}第二行');
    await user.keyboard('{Control>}{Enter}{/Control}');
    await screen.findByText('Output A2');
    await user.keyboard('b');

    expect(api.casts).toEqual([{ token: 't1', choice: 'a', flags: [], note: '第一行\n第二行' }]);
    expect(screen.getByRole('radio', { name: 'B is better' })).toHaveProperty(
      'ariaChecked',
      'true',
    );
    expect(screen.getByRole('textbox')).toHaveProperty('value', '');
  });

  test('submits on Enter after a click on Undo instead of undoing again', async () => {
    const api = stubApi();
    const user = await startSession();
    await user.keyboard('a{Enter}');
    await screen.findByText('Output A2');

    await user.click(screen.getByRole('button', { name: 'Undo' }));
    await screen.findByText('Output A3');
    await user.keyboard('b{Enter}');
    await screen.findByText('Output A4');

    expect(api.undos()).toBe(1);
    expect(api.casts.map((cast) => cast.choice)).toEqual(['a', 'b']);
  });

  test('shows an error from starting a session on the start screen', async () => {
    vi.stubGlobal('fetch', (path: string) =>
      Promise.resolve(
        path === '/api/families'
          ? new Response(JSON.stringify(['prose-zh']))
          : new Response(JSON.stringify({ detail: 'no outputs to pair' }), { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    render(<VoteApp />);

    await user.click(await screen.findByRole('button', { name: 'prose-zh' }));

    expect((await screen.findByRole('alert')).textContent).toBe('no outputs to pair');
  });
});

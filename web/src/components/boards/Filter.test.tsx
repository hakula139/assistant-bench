// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { Filter } from '@/components/boards/Filter';

describe('Filter', () => {
  afterEach(cleanup);

  test('labels its select and reports the chosen option', () => {
    const onChange = vi.fn();
    render(
      <Filter
        label="Category"
        value="all"
        options={[
          { value: 'all', label: 'All categories' },
          { value: 'message', label: 'message' },
        ]}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'message' } });

    expect(onChange.mock.calls).toEqual([['message']]);
  });
});

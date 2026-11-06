import type { Category } from '@/lib/categories';
import { categoryTerm } from '@/lib/glossary';
import { useSearchParam } from '@/lib/use-search-param';

const ALL = 'aggregate';

/**
 * Reads and writes a board's category filter through the `category` query parameter. Returns the
 * selected category, or null for all categories and for a category the board does not show, with
 * the props of the `Filter` that sets it.
 */
export function useCategory(categories: readonly Category[]) {
  const [param, setParam] = useSearchParam('category', null);
  const category = categories.find((item) => item === param) ?? null;
  const filter = {
    label: 'Category',
    value: category ?? ALL,
    options: [
      { value: ALL, label: 'All categories' },
      ...categories.map((item) => ({ value: item, label: categoryTerm(item).term })),
    ],
    onChange: (value: string) => {
      setParam(value === ALL ? null : value);
    },
  };

  return [category, filter] as const;
}

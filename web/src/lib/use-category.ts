import type { Category } from '@/lib/categories';
import { categoryTerms } from '@/lib/glossary';
import { useSearchParam } from '@/lib/use-search-param';

const ALL = 'aggregate';

/**
 * Reads and writes a board's category filter through the `category` query parameter. Returns the
 * selected category, or null for all categories and for a category the board does not show, with
 * the props of the `Filter` that sets it, or null where the board has one category or none and
 * shows no filter.
 */
export function useCategory(categories: readonly Category[]) {
  const [param, setParam] = useSearchParam('category', null);
  if (categories.length < 2) {
    return [null, null] as const;
  }

  const category = categories.find((item) => item === param) ?? null;
  const filter = {
    label: 'Category',
    value: category ?? ALL,
    options: [
      { value: ALL, label: 'All categories' },
      ...categories.map((item) => ({ value: item, label: categoryTerms[item].term })),
    ],
    onChange: (value: string) => {
      setParam(value === ALL ? null : value);
    },
  };

  return [category, filter] as const;
}

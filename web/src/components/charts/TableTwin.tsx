import type { ReactNode } from 'react';

import { Disclosure } from '@/components/site/Disclosure';

export function TableTwin({ children }: { children: ReactNode }) {
  return <Disclosure summary="Show the data as a table">{children}</Disclosure>;
}

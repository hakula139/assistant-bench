import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';

/** A chart's data as a table, behind a disclosure below the chart that works without scripts. */
export function TableTwin({ children }: { children: ReactNode }) {
  return (
    <details className="group mt-2">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-sm text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronDown
          className="size-4 -rotate-90 transition-transform group-open:rotate-0"
          aria-hidden="true"
        />
        Show the data as a table
      </summary>
      <div className="pt-2">{children}</div>
    </details>
  );
}

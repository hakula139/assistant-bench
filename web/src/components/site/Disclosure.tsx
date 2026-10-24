import { ChevronDown } from 'lucide-react';
import type { ReactNode } from 'react';

export function Disclosure({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <details className="group">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-sm text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
        <ChevronDown
          className="size-4 -rotate-90 transition-transform group-open:rotate-0"
          aria-hidden="true"
        />
        {summary}
      </summary>
      <div className="pt-2">{children}</div>
    </details>
  );
}

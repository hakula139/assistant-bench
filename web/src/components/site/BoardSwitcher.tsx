import { cn } from 'cn';

import { buttonVariants } from '@/components/ui/button';
import { aspects, type Aspect } from '@/lib/aspects';
import type { BoardLink } from '@/lib/reports';

/** Links between an aspect's boards by their short names, drawn once the aspect has two. */
export function BoardSwitcher({ aspect, boards }: { aspect: Aspect; boards: BoardLink[] }) {
  if (boards.length < 2) {
    return null;
  }
  return (
    <nav aria-label={`${aspects[aspect].title} boards`}>
      <ul className="flex flex-wrap gap-2">
        {boards.map(({ href, label, current }) => (
          <li key={href}>
            <a
              href={href}
              aria-current={current ? 'page' : undefined}
              className={cn(
                buttonVariants({ variant: current ? 'secondary' : 'outline', size: 'sm' }),
                !current && 'text-foreground',
              )}
            >
              {label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

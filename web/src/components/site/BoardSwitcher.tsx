import { cn } from 'cn';

import { Disclosure } from '@/components/site/Disclosure';
import { buttonVariants } from '@/components/ui/button';
import { aspects, type Aspect } from '@/lib/aspects';
import { formatCountOf } from '@/lib/format';
import type { BoardLink } from '@/lib/reports';

export function BoardSwitcher({ aspect, boards }: { aspect: Aspect; boards: BoardLink[] }) {
  if (boards.length < 2) {
    return null;
  }

  const { title } = aspects[aspect];

  return (
    <nav aria-label={`${title} boards`}>
      <Disclosure summary={formatCountOf(boards.length, `${title} board`)}>
        <ul className="flex flex-wrap gap-2">
          {boards.map(({ href, label, current }) => (
            <li key={href}>
              <a
                href={href}
                aria-current={current ? 'page' : undefined}
                className={cn(
                  buttonVariants({ variant: 'outline', size: 'sm' }),
                  'text-foreground aria-[current=page]:border-primary aria-[current=page]:bg-primary/10',
                )}
              >
                {label}
              </a>
            </li>
          ))}
        </ul>
      </Disclosure>
    </nav>
  );
}

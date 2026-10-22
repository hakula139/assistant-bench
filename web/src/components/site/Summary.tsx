import type { ReactNode } from 'react';

/** The classes of a titled part of a board or system page, ruled off from the part above it. */
export const section = { root: 'space-y-3 border-t pt-6', title: 'text-lg font-semibold' };

/** A titled part of a board, for React callers of `section`. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={section.root}>
      <h2 className={section.title}>{title}</h2>
      {children}
    </section>
  );
}

/** A row of labelled figures, for use inside a `<dl>` of `Stat`s. */
export const statList = 'grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:flex sm:flex-wrap';

/** One figure in a summary: its label, its value, and an optional qualifier after the value. */
export function Stat({
  label,
  detail,
  children,
}: {
  label: string;
  detail?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">
        {children}
        {detail !== undefined && (
          <>
            {' '}
            <span className="font-normal text-muted-foreground">{detail}</span>
          </>
        )}
      </dd>
    </div>
  );
}

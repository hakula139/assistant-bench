import type { ReactNode } from 'react';

export const container =
  'mx-auto w-full max-w-6xl px-(--gutter) [--gutter:--spacing(4)] sm:[--gutter:--spacing(6)]';

/** The vote page's column and header, wide enough at `xl` for the brief beside both outputs. */
export const wideContainer = `${container} xl:max-w-7xl`;

/**
 * The classes that pull a block out over its `container`'s gutter while keeping its content in
 * line, so a bar's background runs to the screen's edges.
 */
export const bleed = '-mx-(--gutter) px-(--gutter)';

/** The widest a line of running text grows, so wide screens use their width without long lines. */
export const measure = 'max-w-measure';

export const stack = 'flex flex-col gap-10';

export const pageTitle = 'text-title leading-tight font-semibold text-balance';

const heading = 'scroll-mt-6 font-semibold text-balance target:text-link';

export const sectionTitle = `${heading} text-lg`;

export const groupTitle = `${heading} text-2xl`;

export const intro = `${measure} text-lg text-muted-foreground`;

export const list = 'flex flex-col gap-2 pl-6 marker:text-muted-foreground';

export const note = `${measure} text-sm text-muted-foreground`;

export function PageHeader({
  title,
  context,
  status,
  children,
}: {
  title: string;
  context?: ReactNode;
  status?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4">
      {context}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className={pageTitle}>{title}</h1>
        {status}
      </div>
      {children}
    </header>
  );
}

export function Section({
  title,
  id,
  children,
}: {
  title: string;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-4 border-t pt-10">
      <h2 id={id} className={sectionTitle}>
        {title}
      </h2>
      {children}
    </section>
  );
}

export function Part({ title, id, children }: { title: string; id: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4 pt-4">
      <h3 id={id} className={heading}>
        {title}
      </h3>
      {children}
    </section>
  );
}

/** A row of labelled figures, for use inside a `<dl>` of `Stat`s. */
export const statList = 'grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:flex sm:flex-wrap';

export function Stat({
  label,
  detail,
  children,
}: {
  label: ReactNode;
  detail?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div>
      <dt className="text-balance text-muted-foreground">{label}</dt>
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

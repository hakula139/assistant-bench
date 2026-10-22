import type { ReactNode } from 'react';

/** The classes of the column that the header, every page, and the footer share. */
export const container = 'mx-auto w-full max-w-6xl px-4 sm:px-6';

/** The column of the vote page and its header, wide enough at `xl` for the brief beside both outputs. */
export const wideContainer = `${container} xl:max-w-7xl`;

/** The widest a line of running text grows, so wide screens use their width without long lines. */
export const measure = 'max-w-measure';

/** The gap between a page's blocks, and between a board's sections. */
export const stack = 'space-y-10';

/** The classes of a page's `<h1>`, except the home page's hero. */
export const pageTitle = 'text-title leading-tight font-semibold text-balance';

const heading = 'scroll-mt-6 font-semibold text-balance target:text-link';

/** The classes of a section's heading, which turns the link color while a link targets it. */
export const sectionTitle = `${heading} text-lg`;

/** The classes of the heading over a group of sections, such as an aspect's families. */
export const groupTitle = `${heading} text-2xl`;

/** The classes of the paragraph that introduces a page, under its title. */
export const intro = `${measure} text-lg text-muted-foreground`;

/** The classes of a numbered or bulleted list in running text. */
export const list = 'space-y-2 pl-6 marker:text-muted-foreground';

/** The classes of a muted note that explains the figure or table beside it. */
export const note = `${measure} text-sm text-muted-foreground`;

/**
 * A page's title, with `context` such as a breadcrumb above it, `status` beside it, and `children`
 * such as an introduction or stats below it.
 */
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
    <header className="space-y-4">
      {context}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className={pageTitle}>{title}</h1>
        {status}
      </div>
      {children}
    </header>
  );
}

/** A titled part of a page or board, ruled off from the part above it by the same gap as `stack`. */
export function Section({
  title,
  id,
  children,
}: {
  title: string;
  /** The heading's id, for a page whose links target its sections. */
  id?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4 border-t pt-10">
      <h2 id={id} className={sectionTitle}>
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A titled part of a `Section`, set off from the block above it without a rule. */
export function Part({ title, id, children }: { title: string; id: string; children: ReactNode }) {
  return (
    <section className="space-y-4 pt-4">
      <h3 id={id} className={heading}>
        {title}
      </h3>
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

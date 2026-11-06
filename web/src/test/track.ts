/** How a table row's `ScoreTrack` draws its system, read from the classes that style each mark. */
export interface TrackDrawing {
  dot: 'filled' | 'hollow' | 'none';
  /** The sides of the axis where an arrowhead marks a score the axis cuts off. */
  arrows: ('start' | 'end')[];
  /** The dot's, the anchor rule's, and each gridline's positions on the axis, as `left` styles. */
  dotAt: string | null;
  ruleAt: string | null;
  gridAt: string[];
  /** The score interval's start and width on the axis, as `left` and `width` styles. */
  interval: [string, string] | null;
}

export function trackDrawing(row: Element): TrackDrawing {
  const track = row.querySelector('[data-slot="score-track"]');
  if (track === null) {
    throw new Error(`the row "${row.textContent}" draws no score track`);
  }

  const dot = track.querySelector<HTMLElement>('.rounded-full:not([data-slot="score-interval"])');
  const interval = track.querySelector<HTMLElement>('[data-slot="score-interval"]');
  const arrows = [...track.querySelectorAll('svg')].map((svg) =>
    svg.classList.contains('left-0') ? 'start' : 'end',
  );

  return {
    dot:
      dot === null
        ? 'none'
        : dot.classList.contains('bg-background') && dot.classList.contains('border-chart-series')
          ? 'hollow'
          : dot.classList.contains('bg-chart-series')
            ? 'filled'
            : 'none',
    arrows,
    dotAt: dot?.style.left ?? null,
    ruleAt: track.querySelector<HTMLElement>('.bg-chart-reference')?.style.left ?? null,
    gridAt: [...track.querySelectorAll<HTMLElement>('.bg-chart-grid')].map(
      (line) => line.style.left,
    ),
    interval: interval && [interval.style.left, interval.style.width],
  };
}

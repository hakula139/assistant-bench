/** How a table row's `ScoreTrack` draws its system, read from the classes that give each mark its look. */
export interface TrackDrawing {
  dot: 'filled' | 'hollow' | 'none';
  /** The sides of the axis where an arrowhead marks a score the axis cuts off. */
  arrows: ('start' | 'end')[];
  /** The dot's and the anchor rule's positions on the axis, as their `left` styles. */
  dotAt: string | null;
  ruleAt: string | null;
}

export function trackDrawing(row: Element): TrackDrawing {
  const track = row.querySelector('[data-slot="score-track"]');
  if (track === null) {
    throw new Error(`the row "${row.textContent}" draws no score track`);
  }

  const dot = track.querySelector<HTMLElement>('.rounded-full');
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
  };
}

/** The fewest words the last line of a block may hold. */
const TAIL_WORDS = 2;
/**
 * The fewest characters the last line of a block may hold. A line break may fall between any two
 * Chinese characters, so a word count alone would still strand a short phrase.
 */
const TAIL_CHARS = 6;
/** The longest tail kept on one line, beyond which the tail could overflow a narrow column. */
const TAIL_LIMIT = 24;

/** Keeps a tail on one line, leaving the spaces of preformatted text as written. */
export const TAIL_CLASS = 'text-nowrap';

const segmenter = new Intl.Segmenter(undefined, { granularity: 'word' });

/** The end of a line, measured backwards until it holds the words a last line must hold. */
export class Tail {
  private chars = 0;
  private words = 0;

  get full(): boolean {
    return this.words >= TAIL_WORDS && this.chars >= TAIL_CHARS;
  }

  get fits(): boolean {
    return this.chars <= TAIL_LIMIT;
  }

  /** Takes `text` from its end until the tail is full, and returns where the tail starts in it. */
  take(text: string, whole = false): number {
    const segments = [...segmenter.segment(text)];
    let at = text.length;
    for (let i = segments.length - 1; i >= 0 && (whole || !this.full); i -= 1) {
      at = segments[i].index;
      this.chars += segments[i].segment.length;
      this.words += segments[i].isWordLike ? 1 : 0;
    }
    return at;
  }
}

/**
 * Splits `text` before the words its last line must hold, leaving the tail empty when those words
 * run past `TAIL_LIMIT`.
 */
export function splitTail(text: string): [string, string] {
  const tail = new Tail();
  const at = tail.take(text);
  return tail.fits ? [text.slice(0, at), text.slice(at)] : [text, ''];
}

/** The classes of a vote card, whose bands and content take the card's own padding. */
export const panel = 'gap-0 py-0';

/**
 * The classes of a vote card's title band, which shares a grid row with the other cards' bands
 * from `xl`, where the brief sits beside the outputs and their rules align. Its first row stays at
 * the top when another band wraps and stretches it, so the titles align too.
 */
export const band = 'flex flex-wrap content-start items-center gap-x-4 gap-y-2 p-(--card-spacing)';

/** The classes of a vote card's text, which scrolls within the height its band leaves. */
export const scroll = 'min-h-0 flex-1 overflow-y-auto py-(--card-spacing)';

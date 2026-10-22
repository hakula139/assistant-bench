/** The classes of a vote card, whose bands and content take the card's own padding. */
export const panel = 'gap-0 py-0';

/**
 * The classes of a vote card's title band, as tall as the brief's from `xl`, where the brief sits
 * beside the outputs and their rules align.
 */
export const band = 'flex flex-wrap items-center gap-x-4 gap-y-2 p-(--card-spacing) xl:min-h-13';

/** The classes of a vote card's text, which scrolls within the height its band leaves. */
export const scroll = 'min-h-0 flex-1 overflow-y-auto py-(--card-spacing)';

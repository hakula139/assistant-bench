const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;

export const ACTORS_PER_TAG = 5;
export const WINDOW_MS = DAY_MS;

/** How long a vote stays undoable, equal to `UNDO_WINDOW_MS` in `arena/votes.py` for cuts. */
export const UNDO_MS = 10 * MINUTE_MS;

/** An actor cookie's lifetime, the longest a browser keeps one. */
export const ACTOR_MAX_AGE_MS = 400 * DAY_MS;

export const TAG_RETENTION_MS = 60 * DAY_MS;
export const PAIR_RETENTION_MS = 30 * DAY_MS;
export const BODY_LIMIT_BYTES = 16 * 1024;

const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;

/** New browsers one IP tag may admit within `WINDOW_MS`. */
export const ACTORS_PER_TAG = 5;

/** The sliding window for admitting browsers from one network. */
export const WINDOW_MS = DAY_MS;

/** How long a vote stays undoable, equal to `UNDO_WINDOW_MS` in `arena/votes.py` for cuts. */
export const UNDO_MS = 10 * MINUTE_MS;

/** An actor cookie's lifetime, the longest a browser keeps one. */
export const ACTOR_MAX_AGE_MS = 400 * DAY_MS;

/** How long actors and votes keep their IP tag. */
export const TAG_RETENTION_MS = 60 * DAY_MS;

/** How long a pending pair survives after its issue. */
export const PAIR_RETENTION_MS = 30 * DAY_MS;

/** The largest request body any route accepts. */
export const BODY_LIMIT_BYTES = 16 * 1024;

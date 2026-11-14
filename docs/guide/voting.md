# Voting

Open the website link in the repository's About section to vote on blind pairs from a Mac, phone, or another device.

## Opening the Vote Page

Follow the site's Vote link and pick a family under its aspect. The first visit verifies your browser, showing a challenge only when interaction is needed, and your first pair opens after it. The page remembers your browser in a cookie, so voting needs cookies allowed for the site.

For private cases, open the Access-protected private vote page and sign in with an allowed identity. It uses the same voting flow and stores votes in private D1, as [Running](running.md#private-voting) describes.

Each pair compares two systems' different, passing outputs on one case, and you vote at most once on each comparison. A family ends with No pairs left once you have voted on every comparison it offers. Tap the vote count in the Prompt bar to see how many pairs remain.

The server holds your current pair, so a reload or a second tab shows the same one. Reloading preserves your draft without submitting it. If the pair changed meanwhile, the page says when it cleared an unsent choice.

If a vote fails to send, Retry sends the same vote again, and the server records it once. If another tab already voted on the pair, the page shows the current pair. Skip shows another case for the same two systems, and says so when no other case is left. Undo withdraws your last vote within 10 minutes of casting it and shows its pair again, and each further Undo goes one vote further back.

## Casting a Vote

Judge outputs A and B against the prompt and What to check, whose criteria the model never saw. The page hides system identities. Choose a verdict, optionally tag issues, then submit:

| Key        | Action                                                                               |
| ---------- | ------------------------------------------------------------------------------------ |
| `a` / `b`  | A or B is better                                                                     |
| `t`        | Tie                                                                                  |
| `x`        | Both bad                                                                             |
| `1` to `5` | Toggle the first five issues on output A, in the order the Issues popover lists them |
| `6` to `0` | Toggle the first five issues on output B                                             |
| `Enter`    | Submit from the pair background, or activate the focused control                     |
| `s`        | Skip to another case for the same two systems                                        |
| `u`        | Undo the last vote and show its pair again                                           |

Letter and digit shortcuts work while the voting panel has focus. Each new pair focuses the panel. Header controls and open popovers keep their own keyboard behavior.

An issue counts against the output you tag, whatever your verdict. Each family's Issues menu explains its available tags.

Outputs appear exactly as written. The Render Markdown toggle renders both text panes as Markdown, with raw HTML shown as text and images left unloaded.

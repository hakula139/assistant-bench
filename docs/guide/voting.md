# Voting

Vote on blind pairs at [bench.hakula.xyz/vote/](https://bench.hakula.xyz/vote/), using the same service on a Mac, phone, or another device.

## Opening the Vote Page

Follow the site's Vote link and pick a family under its aspect. The first visit verifies your browser, showing a challenge only when interaction is needed, and your first pair opens after it. The page remembers your browser in a cookie, so voting needs cookies allowed for the site. All real votes go to the production D1 database. The staging deployment has its own database for validation.

Each pair compares two systems' different, passing outputs on one case, and you vote at most once on each comparison. A family ends with No pairs left once you have voted on every comparison it offers. Tap the vote count in the Prompt bar to see how many pairs remain.

The server holds your current pair, so a reload or a second tab shows the same one. A reload also keeps your unsent choice, issues, Markdown toggle, collapsed prompt and scroll position, and never sends a vote. If the pair changed meanwhile, the page shows the new one and says so when it cleared an unsent choice.

If a vote fails to send, Retry sends the same vote again, and the server records it once. If another tab already voted on the pair, the page shows the current pair. Skip shows another case for the same two systems, and says so when no other case is left. Undo withdraws your last vote while it is under 10 minutes old and shows its pair again, and each further Undo goes one vote further back.

## Casting a Vote

Each pair shows the case's prompt above two outputs, or, for text outputs, beside them on a wide screen, with A on the left and B on the right, and never names their systems. Under the prompt, What to check lists the points to judge by, which the model never saw. A prompt you collapse stays collapsed for the following pairs from the same case. Nothing is selected until you choose:

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

An issue counts against the output you tag, whatever your verdict. Each family lists its own issues, such as wrong facts or a wrong tone on writing and off-scope changes or weak tests on code. Each output's Issues button explains and toggles them, including on phones. A family that lists none shows no Issues button.

Outputs appear exactly as written. The Markdown toggle renders both text panes as Markdown, with raw HTML shown as text and images left unloaded.

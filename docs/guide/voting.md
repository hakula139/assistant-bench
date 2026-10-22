# Voting

Learn how to vote on pairs by keyboard, read outputs as Markdown, and keep HTML outputs contained, once [Running](running.md#voting) has started the arena.

## Opening the Vote Page

`bench serve` builds the site, so run `pnpm install` once in the checkout first. Open `http://127.0.0.1:8790/vote/`, or follow the Vote link in the header of the local site, and pick a family to start a session. A reload resumes the session in the same tab.

## Casting a Vote

Each pair shows the case's brief and checklist above two outputs, or, for text outputs, beside them on a wide screen, with A on the left and B on the right, and never names their systems. Nothing is selected until you choose:

| Key        | Action                                                                                                 |
| ---------- | ------------------------------------------------------------------------------------------------------ |
| `a` / `b`  | A or B is better                                                                                       |
| `t`        | Tie                                                                                                    |
| `x`        | Both bad                                                                                               |
| `1` to `5` | Toggle a flag: meaning changed, punctuation, register, wrong script, too long                          |
| `n`        | Write a note, where `Enter` starts a new line, `⌘↵` or `Ctrl+↵` submits, and `Escape` leaves the field |
| `Enter`    | Submit the vote and load the next pair, even with a vote control focused, which `Space` still presses  |
| `u`        | Undo the last vote and show its pair again                                                             |

Flags count against the side you did not prefer, or against both sides of a tie or a both-bad vote. Outputs appear exactly as written, and the Markdown toggle renders both panes as Markdown, with raw HTML shown as text and images left unloaded.

## HTML Outputs

A family that renders HTML loads each output in a sandboxed frame from a second origin, under a policy that blocks network requests. The viewport presets of 375, 768, and 1280 px lay out both frames at that width, scaled down to fit the pane.

Keys pressed inside a frame stay in the frame, so the page shows a hint while one has focus. Click outside the frames before voting by keyboard.

The frame's policy cannot block WebRTC, so vote on HTML outputs of private cases in a browser profile without network access.

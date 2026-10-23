# Running

Learn how to point the bench at a model endpoint, check and run a family, collect its outputs, vote on them, and close a round, with the setup covered in [Quickstart](quickstart.md).

## The Model Endpoint

Single-call systems send OpenAI chat-completions requests to an endpoint configured in `.env`, which `bench` loads from the working directory or a parent. Copy [`.env.example`](../../.env.example) and fill in:

| Variable                  | Value                                                                                                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BENCH_API_BASE_URL`      | The endpoint's base URL, including `/v1`                                                                                                                               |
| `BENCH_API_KEY_FILE`      | A file holding the API key, so the key is never copied into the checkout                                                                                               |
| `BENCH_API_CA_FILE`       | Optional. A CA bundle for an endpoint whose certificate the system store does not trust                                                                                |
| `BENCH_API_HEADERS`       | Optional. A JSON object of extra request headers, such as `{"x-example": "value"}`                                                                                     |
| `BENCH_API_LOCAL_ADDRESS` | Optional. A local IP address to bind outgoing connections to, such as `0.0.0.0` to keep them on IPv4. A bound client connects directly and ignores the proxy variables |

Without `BENCH_API_LOCAL_ADDRESS`, the client honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY`.

Each system's route is the model ID the endpoint expects, and every trial sends one request with the case instruction as the user message, the system's prompt as the system message when it has one, the family's `max_output_tokens` as `max_completion_tokens`, and the system's effort, when it resolves to one, as `reasoning_effort`.

## Checking a Family

```bash
uv run bench check smoke-text
uv run bench check smoke
```

`bench check` validates `family.toml` and its systems. For a `cases.jsonl` family, it validates the cases and renders them into Harbor tasks to name any that changed since a trial ran them, which needs neither Docker nor an endpoint. For a `tasks/` family, it runs Harbor's oracle on every task and fails unless each reference solution earns reward 1, which needs Docker but no endpoint.

## Running a Family

```bash
uv run bench run smoke-text
```

`bench run` renders each case into a Harbor task, writes a job with one agent for each system listed in `family.toml`, and runs it through the Harbor CLI. Each case and system pair runs `samples` times, retrying a failed trial once, and systems that share a route share one concurrency limit. A reply cut off by the token limit counts as an output, while an HTTP error fails the trial.

Because a rerun generates only the trials that no earlier job of the family finished, adding a system to `family.toml` and rerunning sends requests for that system alone. To regenerate a case, give it a new ID.

To run only some of the listed systems, name each with `--system`, as in `uv run bench run prose-zh --system kimi-k3 --system opus-5.5`, and a later run without the option generates the other systems' trials alone. Voting can start once two systems have outputs, and those votes keep counting after the rest join. `bench close` anchors the board at the family's reference when the round ranks it, and otherwise at the most-voted system.

## Collecting Outputs

```bash
uv run bench ingest smoke-text
```

`bench ingest` reads the family's trials into `outputs/<family>.jsonl`, keeping at most `samples` records per case, system, and fingerprint and preferring successful trials, so a rerun replaces an earlier error. It prints the count, the errors by type, the gate failures by gate for a `votes` family, and the mean reward when the family has one. Each record holds the text (or the verifier's reward for a `tests` family), the [gates](../design/families/prose.md#gate-verdicts) a voted output failed, the error type of a failed trial, token counts including reasoning tokens where the endpoint reports them, the cost at catalog prices, the end-to-end latency, the response model, the request ID, and the system's fingerprint and nixos-config rev. A model whose catalog channel has no price reports a null cost.

## Voting

```bash
uv run bench serve
```

`bench serve` builds the vote page and serves it at `http://127.0.0.1:8790/vote/` for every family scored by votes that has outputs, and it runs until interrupted. Open that address exactly, since the API admits no other host. A session holds up to 30 votes, each recorded as soon as it is cast. [Voting](voting.md) covers the keys and flags, and [Arena](../design/arena.md) covers how pairs are chosen.

## Closing a Round

```bash
uv run bench close prose-zh
```

`bench close` fits every vote of the family, or aggregates a `tests` family's rewards, and writes `reports/<family>/<round>.json`, dated by the UTC day. Commit the report through a PR. [Reports](../design/reports.md) describes its contents.

## The Data Directory

Everything a run produces stays out of Git in the data directory, which `BENCH_DATA_DIR` names and which defaults to `$XDG_DATA_HOME/assistant-bench/`, or `~/.local/share/assistant-bench/`:

```text
assistant-bench/
├── cases/<family>.jsonl                    # Local cases, appended to the committed ones
├── checks/<family>/<job>/                  # Oracle jobs from `bench check`
├── jobs/<family>/<job>/                    # Harbor job directories, each with its config.json and one directory per trial
├── outputs/<family>.jsonl                  # Records written by `bench ingest`
├── tasks/<family>/<case>/                  # Harbor tasks rendered from the cases
└── votes.sqlite                            # Sessions, votes, and undos from `bench serve`
```

Run `harbor view` on a family's job directory to browse its trials.

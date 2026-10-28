# pipeline

`pipeline` builds a project's files through steps that are plain Python functions. A project is a directory with a `pipeline.toml` that declares each step's function, the files it reads and the files it writes.

```toml
[steps.clean]
run = "steps:clean"
inputs = ["raw/*.csv"]
outputs = ["build/clean.csv"]
params = { delimiter = ";" }

[steps.report]
run = "steps:report"
inputs = ["build/clean.csv", "templates/*.txt"]
outputs = ["build/report.txt", "build/summary.json"]
```

## Steps

- `run` names a function as `module:function`, imported with the project directory on the module path.
- `inputs` are glob patterns relative to the project directory, in the syntax of `pathlib.Path.glob`. A step depends on every step that declares an output one of its patterns matches, and steps may not depend on each other in a cycle.
- `outputs` are paths relative to the project directory. No two steps may declare the same output, and a step must write every output it declares.
- `params` is an optional table passed through to the function.

The function receives a `pipeline.StepContext`. Its `inputs` are the existing files the patterns match, sorted, its `outputs` are the declared outputs in order, with their parent directories created, and its `params` and `root` are the step's parameters and the project directory. An exception from the function fails the step.

A step must be deterministic: its outputs may depend only on the paths and contents of its inputs, the paths of its outputs, its params and the module that defines its function, and not on file metadata, environment variables, the clock, other modules or files, or what an earlier run wrote.

## Command line

```bash
python -m pipeline [--project DIR] build [--target STEP]
python -m pipeline [--project DIR] clean
python -m pipeline [--project DIR] order
```

The project defaults to the current directory. `build` runs every step after the steps it depends on, or with `--target` only that step and its dependencies, and prints `ran STEP` for each step it runs and `up to date STEP` for each step it skips. A failed step stops the build, which prints the error and exits with status 1. `clean` deletes every declared output and the build records, and `order` prints the build order.

## Incremental builds

`build` skips a step that is up to date, so it leaves the same outputs as a build from a clean project. A step is up to date when its last run succeeded with the same `run`, `inputs`, `params` and `outputs`, the same source in the module that defines its function, and the same paths and contents of the files its patterns match, and when its outputs still hold what that run wrote. The module that defines the function may differ from the one `run` names, which can re-export it. Modification times play no part, so touching a file reruns nothing, and a step whose inputs come out of a rerun unchanged is skipped.

The records live in `.pipeline/state.json` under the project directory, which input patterns never match and no step may declare as an output. A step's record is removed before it runs and written once it succeeds, so a failed or interrupted step runs again in the next build.

## Python API

`pipeline.load(root)` reads a project into a `Pipeline`, raising `ConfigError` for an invalid one. `pipeline.build(pipeline, target=None, *, log=print)` builds it and returns a `BuildReport`, whose `ran` and `skipped` list step names in build order, and raises `StepFailed` when a step fails. A step module edited since a build in the same process ran it runs again from its new source. Step modules are imported by name, so one process can build several projects only when their step modules' names differ. `pipeline.clean(pipeline)` deletes the outputs and returns the ones that existed.

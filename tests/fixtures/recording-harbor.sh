#!/bin/sh
printf '%s\n' "$@" >"$0.args"
cp "$3" "$0.json"
exit "$STUB_HARBOR_EXIT_CODE"

FROM debian:trixie-slim@sha256:4876fdea29cac1a33a3195f1eb1952feb1fca7dd6dd00be3e2f1879cf20d34d2

RUN apt-get update \
  && apt-get install -y --no-install-recommends tmux \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

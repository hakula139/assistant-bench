FROM debian:trixie-20261020-slim

# terminus-2 drives its shell through tmux.
RUN apt-get update \
  && apt-get install -y --no-install-recommends tmux \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

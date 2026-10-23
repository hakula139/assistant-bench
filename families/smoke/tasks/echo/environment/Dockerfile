FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad

# terminus-2 drives its shell through tmux.
RUN apt-get update \
  && apt-get install -y --no-install-recommends tmux \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

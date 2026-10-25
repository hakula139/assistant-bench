FROM python:3.14-slim@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8

ENV TZ=UTC \
  LANG=C.UTF-8 \
  GIT_AUTHOR_DATE=2026-01-01T00:00:00Z \
  GIT_COMMITTER_DATE=2026-01-01T00:00:00Z

# terminus-2 drives its shell through tmux.
RUN apt-get update \
  && apt-get install -y --no-install-recommends git tmux \
  && rm -rf /var/lib/apt/lists/* \
  && git config --global user.name bench \
  && git config --global user.email bench@example.invalid \
  && git config --global init.defaultBranch main

COPY app/ /app/
WORKDIR /app
RUN git init -q \
  && git add -A \
  && git commit -q -m "chore: import project"

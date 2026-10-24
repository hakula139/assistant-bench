from collections.abc import Mapping


class FakeTransport:
    """Record posts and answer with a fixed status code."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.posts: list[tuple[str, str, dict[str, str]]] = []

    def post(self, url: str, body: str, headers: Mapping[str, str]) -> int:
        self.posts.append((url, body, dict(headers)))
        return self.status

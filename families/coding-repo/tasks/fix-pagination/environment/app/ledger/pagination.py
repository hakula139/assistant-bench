"""Pagination of ledger listings."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    """One page of a listing. ``number`` is 1-based."""

    items: list[T]
    number: int
    total_pages: int

    @property
    def has_next(self) -> bool:
        """Whether a page follows this one."""
        return self.number <= self.total_pages


def total_pages(count: int, per_page: int) -> int:
    """Return how many pages ``count`` items fill at ``per_page`` items per page.

    A partially filled last page counts as a page, and an empty listing still has one (empty)
    page. Raise ``ValueError`` when ``per_page`` is less than 1.
    """
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    return max(1, count // per_page)


def paginate(items: Sequence[T], number: int, per_page: int) -> Page[T]:
    """Return page ``number`` (1-based) of ``items``.

    Raise ``ValueError`` when ``number`` is outside ``1..total_pages``.
    """
    pages = total_pages(len(items), per_page)
    if not 1 <= number <= pages:
        raise ValueError(f"page {number} is out of range 1..{pages}")
    start = number * per_page
    return Page(list(items[start : start + per_page]), number, pages)

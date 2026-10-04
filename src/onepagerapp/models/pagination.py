"""Page arithmetic shared by the paginated query results."""


class Pagination:
    """Page numbers of a paginated result (``page`` is 1-indexed).

    Mixed into result dataclasses that have ``total_rows``, ``page`` and
    ``page_size`` fields.
    """

    total_rows: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        """Calculate total number of pages."""
        if self.page_size <= 0:
            return 0
        return (self.total_rows + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        """Whether there is a next page."""
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        """Whether there is a previous page."""
        return self.page > 1

    @property
    def offset(self) -> int:
        """Calculate SQL OFFSET for this page (0-indexed)."""
        return (self.page - 1) * self.page_size

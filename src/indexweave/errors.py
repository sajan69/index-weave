"""Public errors contain operational context, never document content."""


class StoreError(Exception):
    """Base error for storage operations."""


class InvalidInput(StoreError):
    pass


class InvalidWorkspace(StoreError):
    pass


class Busy(StoreError):
    pass


class Conflict(StoreError):
    pass


class MissingDocument(StoreError):
    pass


class Corruption(StoreError):
    def __init__(self, offset: int, reason: str):
        self.offset = offset
        super().__init__(f"Invalid log at byte {offset}: {reason}.")


class IncompleteTail(StoreError):
    pass


class CommitUnknown(StoreError):
    """A write may be present despite an unsuccessful acknowledgement."""


class UnusableWriter(StoreError):
    pass

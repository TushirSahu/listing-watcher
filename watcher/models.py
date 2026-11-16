from pydantic import BaseModel


class Listing(BaseModel):
    url: str
    title: str
    price: int | None
    why_matches: str
    matches: bool
    verified: bool
    item_key: str | None = None


class RunResult(BaseModel):
    searched: bool
    listings_seen: int
    blocked_or_lost: bool
    note: str
    listings: list[Listing]

    @property
    def failed(self) -> bool:
        return not self.searched or self.blocked_or_lost


def failed_result(note: str) -> RunResult:
    return RunResult(searched=False, listings_seen=0, blocked_or_lost=True, note=note, listings=[])

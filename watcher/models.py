from pydantic import BaseModel


class Listing(BaseModel):
    url: str
    title: str
    price: int | None
    why_matches: str
    matches: bool
    verified: bool
    item_key: str | None = None
    image_url: str | None = None
    shop: str | None = None              # shop chain, e.g. "Hard Off" (shop search only)
    branch: str | None = None            # branch holding the item
    branch_station: str | None = None    # where train times route to: branch's station or street address
    branch_url: str | None = None        # branch's own page (has its address)
    condition_grade: str | None = None   # shop grade, e.g. "A", "B"


class Reply(BaseModel):
    answer: str
    listings: list[Listing] = []

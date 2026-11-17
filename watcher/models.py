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


class Reply(BaseModel):
    answer: str
    listings: list[Listing] = []

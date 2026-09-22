from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Article:
    id: str
    legal_body_id: str
    number: str
    section: str
    text: str
    order: int

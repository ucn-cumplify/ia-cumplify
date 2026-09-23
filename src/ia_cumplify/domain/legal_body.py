from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LegalBody:
    id: str
    title: str
    summary: str
    type: str

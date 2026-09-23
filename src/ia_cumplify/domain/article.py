from dataclasses import dataclass

_SKIP_EXACT_NUMBERS = frozenset({"encabezado", "promulgación"})
_TITULO_PREFIX = "título"


@dataclass(frozen=True, slots=True)
class Article:
    id: str
    legal_body_id: str
    number: str
    section: str
    text: str
    order: int

    def should_classify(self) -> bool:
        number = self.number.strip().casefold()
        if number in _SKIP_EXACT_NUMBERS:
            return False
        return not number.startswith(_TITULO_PREFIX)

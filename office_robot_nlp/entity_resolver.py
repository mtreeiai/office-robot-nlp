"Deterministic normalized matching against names and aliases supplied by MapCatalog."

import string
import unicodedata

from office_robot_nlp.domain.map import MapCatalog
from office_robot_nlp.validation.intent_validator import (
    AmbiguousLocation, EntityResolutionResult, ResolvedLocation, UnknownLocation,
)


def normalize_location_text(text: str) -> str:
    """Normalize conservative Vietnamese location spelling for exact matching."""
    normalized = unicodedata.normalize("NFKD", text).casefold()
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    punctuation = string.punctuation + "。！？？，、"
    normalized = normalized.strip(punctuation + " \t\r\n")
    return " ".join(normalized.split())


def resolve_destination(destination_text: str | None, catalog: MapCatalog) -> EntityResolutionResult:
    '''Resolve a location by exact normalized name/alias; never synthesize IDs.

    Each matching location contributes one candidate, even if several of its
    names match. Candidate IDs follow catalog order.
    '''
    if not isinstance(destination_text, str):
        return UnknownLocation("NOT_FOUND")

    query = normalize_location_text(destination_text)
    if not query:
        return UnknownLocation("NOT_FOUND")

    candidates: list[str] = []
    for location in catalog.locations:
        if query in {normalize_location_text(name) for name in (location.name, *location.aliases)}:
            candidates.append(location.id)

    if len(candidates) == 1:
        return ResolvedLocation("RESOLVED", candidates[0])
    if candidates:
        return AmbiguousLocation("AMBIGUOUS", candidates)
    return UnknownLocation("NOT_FOUND")

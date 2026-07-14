"""Nature-inspired word list for readable workspace names.

Used to generate human-friendly display names like 'acme-finops-cedar'
by combining the profile name with a word from this list.

The word is selected deterministically from the workspace ID hash
so the same profile always gets the same readable name.
"""
from __future__ import annotations

# Nature-inspired words: trees, mountains, rivers, weather, terrain.
# Kept short (4-7 chars), easy to read, unambiguous over the phone.
WORDS: list[str] = [
    "alder",
    "aspen",
    "birch",
    "bluff",
    "briar",
    "brook",
    "butte",
    "cedar",
    "cliff",
    "cloud",
    "coral",
    "crest",
    "delta",
    "dune",
    "ember",
    "fern",
    "fjord",
    "flint",
    "frost",
    "glade",
    "gorge",
    "grove",
    "hazel",
    "heath",
    "holly",
    "inlet",
    "ivory",
    "jade",
    "lake",
    "larch",
    "ledge",
    "lotus",
    "maple",
    "marsh",
    "mesa",
    "mist",
    "moss",
    "north",
    "oak",
    "opal",
    "peak",
    "pine",
    "pond",
    "quartz",
    "rain",
    "reef",
    "ridge",
    "river",
    "sage",
    "shoal",
    "slate",
    "slope",
    "snow",
    "south",
    "spark",
    "stone",
    "storm",
    "thorn",
    "tide",
    "trail",
    "vale",
    "willow",
    "wind",
    "woods",
]


def pick_word(hash_bytes: bytes) -> str:
    """Select a word deterministically from hash bytes.

    Uses the first 4 bytes of the hash as an unsigned integer
    to index into the word list.

    Args:
        hash_bytes: Hash digest bytes (at least 4 bytes).

    Returns:
        A word from the word list.
    """
    index = int.from_bytes(hash_bytes[:4], "big") % len(WORDS)
    return WORDS[index]

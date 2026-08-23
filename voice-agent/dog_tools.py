"""Tools for the dog-adoption voice assistant ("Office Helper", a golden retriever).

The adoptable dogs are loaded from the shared web/dogs.json so the voice agent and
the website stay in sync. If that file can't be found, a small built-in list keeps
the demo working. Bookings are kept in memory for the session.
"""

from __future__ import annotations

import json
from pathlib import Path

from livekit.agents import function_tool, RunContext

# --------------------------------------------------------------------------- #
# Data — load the adoptable dogs from the shared web/dogs.json.
# --------------------------------------------------------------------------- #
# dog_tools.py lives in .../Dogathon/voice-agent, dogs.json in .../Dogathon/web.
_DOGS_JSON = Path(__file__).resolve().parent.parent / "web" / "dogs.json"

_FALLBACK_DOGS = [
    {
        "name": "Max", "breed": "Golden Retriever", "age": "3 years", "size": "large",
        "kids": True, "energy": "medium",
        "blurb": "A gentle, family-friendly gentleman who loves fetch and nap time.",
        "slots": ["Tuesday from 12 to 2 PM", "Thursday from 3 to 5 PM"],
    },
]


def _load_dogs() -> list[dict]:
    """Read dogs.json and normalize each record to the fields the tools use."""
    try:
        raw = json.loads(_DOGS_JSON.read_text())
    except (OSError, ValueError):
        raw = _FALLBACK_DOGS

    dogs = []
    for d in raw:
        slots = d.get("slots") or []
        dogs.append(
            {
                "name": d.get("name", "").strip(),
                "breed": d.get("breed", "").strip(),
                "age": d.get("age", "").strip(),
                "size": (d.get("size") or "").strip().lower(),      # small | medium | large
                "energy": (d.get("energy") or "").strip().lower(),  # low | medium | high
                # Accept either "kids" (json) or "good_with_kids" (legacy).
                "good_with_kids": bool(d.get("kids", d.get("good_with_kids", False))),
                "blurb": d.get("blurb", "").strip(),
                "slots": slots,
                "availability": ", or ".join(slots) if slots else "by appointment",
            }
        )
    return dogs


DOGS = _load_dogs()

# Bookings made this session (in-memory).
_BOOKINGS: list[dict] = []

# Spoken shorthands -> a word that appears in a real breed string.
_BREED_ALIASES = {
    "lab": "labrador",
    "golden": "retriever",
    "german": "shepherd",
    "husky": "siberian",
    "doodle": "poodle",
    "corgi": "corgi",
    "pup": "puppy",
    "puppy": "puppy",
}

# Words that map onto an energy/temperament level.
_TEMPERAMENT = {
    "low": ["calm", "mellow", "relaxed", "chill", "low energy", "low-energy", "lazy", "quiet", "gentle"],
    "high": ["active", "energetic", "playful", "high energy", "high-energy", "bouncy", "hyper"],
    "medium": ["medium energy", "moderate", "balanced"],
}


def _find_dog(name: str) -> dict | None:
    name = name.strip().lower()
    if not name:
        return None
    for d in DOGS:
        if d["name"].lower() == name or name in d["name"].lower():
            return d
    return None


def _matches_breed(dog: dict, query: str) -> bool:
    """True if the query refers to this dog's breed (by full string, any word,
    or a spoken alias like 'lab')."""
    q = query.strip().lower()
    if not q:
        return True
    breed = dog["breed"].lower()
    if q in breed:
        return True
    # any significant word of the breed the user said
    for word in breed.split():
        if len(word) > 3 and word != "mix" and word in q:
            return True
    # aliases
    for alias, real in _BREED_ALIASES.items():
        if alias in q and real in breed:
            return True
    return False


def _resolve_energy(temperament: str) -> str | None:
    """Turn a free-text temperament word into an energy level, or None."""
    t = temperament.strip().lower()
    if not t:
        return None
    if t in ("low", "medium", "high"):
        return t
    for level, words in _TEMPERAMENT.items():
        if any(w in t for w in words):
            return level
    return None


@function_tool
async def list_available_dogs(
    context: RunContext,
    good_with_kids: bool = False,
    breed: str = "",
    size: str = "",
    temperament: str = "",
    max_results: int = 6,
) -> str:
    """List adoptable dogs, optionally filtered. Use when the user asks what dogs
    you have, or is looking for a certain kind of dog. All filters are optional and
    combine (AND). Leave a filter empty to ignore it.

    Args:
        good_with_kids: True to only include dogs that are good with children.
        breed: A breed or breed word to match, e.g. "labrador", "poodle", "corgi".
            Accepts common shorthands like "lab" or "golden".
        size: One of "small", "medium", or "large".
        temperament: Desired energy/temperament, e.g. "calm", "low energy",
            "active", "playful". Maps onto the dog's energy level.
        max_results: Cap on how many dogs to read back (default 6).
    """
    dogs = list(DOGS)

    if good_with_kids:
        dogs = [d for d in dogs if d["good_with_kids"]]
    if breed.strip():
        dogs = [d for d in dogs if _matches_breed(d, breed)]
    if size.strip():
        want = size.strip().lower()
        # tolerate "big"/"little"
        want = {"big": "large", "little": "small", "tiny": "small"}.get(want, want)
        dogs = [d for d in dogs if d["size"] == want]
    if temperament.strip():
        energy = _resolve_energy(temperament)
        if energy:
            dogs = [d for d in dogs if d["energy"] == energy]

    if not dogs:
        return (
            "I don't have a dog matching all of that right now. "
            "Want me to loosen the search, or tell you everyone we have?"
        )

    shown = dogs[:max_results]
    lines = [f"{d['name']}, a {d['age']} {d['breed']} — {d['blurb']}" for d in shown]
    more = len(dogs) - len(shown)
    tail = f"\n(and {more} more)" if more > 0 else ""
    return "Here are the dogs that fit:\n- " + "\n- ".join(lines) + tail


@function_tool
async def get_dog_details(context: RunContext, name: str) -> str:
    """Get details about one specific dog by name (breed, age, temperament,
    whether it's good with kids). Use when the user asks about a particular dog.

    Args:
        name: The dog's name, e.g. "Max".
    """
    d = _find_dog(name)
    if not d:
        return f"I don't have a dog named {name}. Would you like to hear who is available?"
    kids = "great with kids" if d["good_with_kids"] else "better with older kids or adults"
    return (
        f"{d['name']} is a {d['age']} {d['breed']}, {d['size']} size, {d['energy']} energy, "
        f"and {kids}. {d['blurb']}"
    )


@function_tool
async def check_viewing_availability(context: RunContext, name: str) -> str:
    """Check when a specific dog is available for an in-person viewing. Use when
    the user wants to visit or schedule time to meet a dog.

    Args:
        name: The dog's name, e.g. "Max".
    """
    d = _find_dog(name)
    if not d:
        return f"I couldn't find a dog named {name}. Who did you want to visit?"
    return f"{d['name']} is available for a viewing {d['availability']}. Shall I book one of those for you?"


@function_tool
async def book_viewing(context: RunContext, name: str, day_and_time: str) -> str:
    """Book an in-person viewing for a dog at a chosen day and time. Only call
    this after the user has confirmed they want to book a specific slot.

    Args:
        name: The dog's name, e.g. "Max".
        day_and_time: The chosen slot in plain words, e.g. "Tuesday 12 to 2 PM".
    """
    d = _find_dog(name)
    if not d:
        return f"I couldn't find a dog named {name} to book."
    _BOOKINGS.append({"dog": d["name"], "when": day_and_time})
    return (
        f"You're booked to meet {d['name']} on {day_and_time}. "
        f"We'll see you at the shelter — {d['name']} can't wait!"
    )


ALL_TOOLS = [
    list_available_dogs,
    get_dog_details,
    check_viewing_availability,
    book_viewing,
]

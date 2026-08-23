"""Tools for the dog-adoption voice assistant ("Office Helper", a golden retriever).

Demo data is hardcoded — a real shelter would back these with a database and a
real calendar, but for the Dogathon this gives a fully working, self-contained
demo: the dog can list adoptable dogs, check a dog's viewing availability, and
"book" a viewing (kept in memory for the session).
"""

from __future__ import annotations

from livekit.agents import function_tool, RunContext

# --------------------------------------------------------------------------- #
# Demo data — the adoptable dogs and their viewing availability.
# --------------------------------------------------------------------------- #
DOGS = [
    {
        "name": "Max",
        "breed": "Golden Retriever",
        "age": "3 years",
        "size": "large",
        "good_with_kids": True,
        "energy": "medium",
        "blurb": "A gentle, family-friendly gentleman who loves fetch and nap time.",
        "availability": "Tuesday 12 to 2 PM, or Thursday 3 to 5 PM",
    },
    {
        "name": "Luna",
        "breed": "Border Collie",
        "age": "1 year",
        "size": "medium",
        "good_with_kids": True,
        "energy": "high",
        "blurb": "Brilliant and bouncy — needs an active home and loves to learn tricks.",
        "availability": "Wednesday 10 AM to noon, or Saturday 1 to 3 PM",
    },
    {
        "name": "Biscuit",
        "breed": "Beagle",
        "age": "5 years",
        "size": "small",
        "good_with_kids": True,
        "energy": "low",
        "blurb": "A mellow cuddle-bug, happiest on the couch. Great first dog.",
        "availability": "Monday 2 to 4 PM, or Friday 11 AM to 1 PM",
    },
    {
        "name": "Rocky",
        "breed": "Boxer mix",
        "age": "2 years",
        "size": "large",
        "good_with_kids": False,
        "energy": "high",
        "blurb": "Playful goofball, still learning manners — best with older kids or adults.",
        "availability": "Thursday 12 to 2 PM, or Sunday 10 AM to noon",
    },
]

# Bookings made this session (in-memory).
_BOOKINGS: list[dict] = []


def _find_dog(name: str) -> dict | None:
    name = name.strip().lower()
    for d in DOGS:
        if d["name"].lower() == name or name in d["name"].lower():
            return d
    return None


@function_tool
async def list_available_dogs(context: RunContext, good_with_kids: bool = False) -> str:
    """List the dogs currently available for adoption. Use when the user asks
    what dogs you have, or is looking for a certain kind of dog (e.g. a family
    dog). Set good_with_kids to True if they mention kids or a family.

    Args:
        good_with_kids: If True, only include dogs that are good with children.
    """
    dogs = [d for d in DOGS if d["good_with_kids"]] if good_with_kids else DOGS
    if not dogs:
        return "We don't have any matching dogs right now."
    lines = [
        f"{d['name']}, a {d['age']} {d['breed']} — {d['blurb']}"
        for d in dogs
    ]
    return "Here are the dogs available:\n- " + "\n- ".join(lines)


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

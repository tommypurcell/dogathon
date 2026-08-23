"""Function tools for the local voice assistant.

Each tool is a plain async function decorated with @function_tool. The LLM decides
when to call them based on the docstring/description, so keep descriptions clear
and concise (the docstring IS the tool description the model sees).

Kept deliberately small: for a voice agent, every extra tool adds latency and
context. Start here, grow later.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import subprocess
import time
from datetime import datetime

import aiohttp
from livekit.agents import function_tool, RunContext

# Local notes file lives next to this script.
_NOTES_PATH = pathlib.Path(__file__).parent / "notes.json"


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #
@function_tool
async def get_current_time(context: RunContext) -> str:
    """Get the current local date and time. Use whenever the user asks what
    time or day it is."""
    now = datetime.now()
    return now.strftime("It is %A, %B %d, %Y, at %I:%M %p.")


# --------------------------------------------------------------------------- #
# Timers
# --------------------------------------------------------------------------- #
@function_tool
async def set_timer(context: RunContext, seconds: int, label: str = "") -> str:
    """Set a countdown timer. When it finishes, the assistant announces it out loud.

    Args:
        seconds: How many seconds from now the timer should go off.
        label: Optional short name for the timer, e.g. "tea" or "laundry".
    """
    if seconds <= 0:
        return "I can only set a timer for a positive number of seconds."

    session = context.session

    async def _fire() -> None:
        await asyncio.sleep(seconds)
        name = f" for {label}" if label else ""
        # Speak the announcement into the room.
        await session.generate_reply(
            instructions=(
                f"The timer{name} just went off. Announce clearly and briefly "
                "that the timer is done."
            )
        )

    asyncio.create_task(_fire())
    name = f" for {label}" if label else ""
    mins = seconds // 60
    secs = seconds % 60
    pretty = f"{mins} minute(s) {secs} second(s)" if mins else f"{secs} second(s)"
    return f"Timer{name} set for {pretty}. I'll let you know when it's done."


# --------------------------------------------------------------------------- #
# Notes / memory (persisted to a local JSON file)
# --------------------------------------------------------------------------- #
def _load_notes() -> list[dict]:
    if _NOTES_PATH.exists():
        try:
            return json.loads(_NOTES_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_notes(notes: list[dict]) -> None:
    _NOTES_PATH.write_text(json.dumps(notes, indent=2))


def save_note(note: str) -> None:
    """Deterministically persist a note. Shared by the tool AND the safety-net
    hook in agent.py, so a note is saved even if the LLM forgets to call the tool.
    Skips exact duplicates so the hook + tool firing together doesn't double-save."""
    note = note.strip()
    if not note:
        return
    notes = _load_notes()
    if any(n.get("note", "").strip().lower() == note.lower() for n in notes):
        return
    notes.append({"note": note, "saved_at": datetime.now().isoformat(timespec="seconds")})
    _save_notes(notes)


@function_tool
async def remember_note(context: RunContext, note: str) -> str:
    """Save a note or fact the user wants remembered across conversations.
    Use when the user says things like "remember that..." or "make a note".

    Args:
        note: The exact thing to remember, in a clear standalone sentence.
    """
    save_note(note)
    return f"Got it. I'll remember: {note}"


@function_tool
async def recall_notes(context: RunContext, query: str = "") -> str:
    """Recall previously saved notes. Use when the user asks what you remember,
    or asks about something they told you earlier.

    Args:
        query: Optional keyword to filter notes; leave empty to list everything.
    """
    notes = _load_notes()
    if not notes:
        return "I don't have any notes saved yet."
    if query:
        q = query.lower()
        notes = [n for n in notes if q in n["note"].lower()]
        if not notes:
            return f"I don't have any notes matching '{query}'."
    lines = [f"- {n['note']}" for n in notes]
    return "Here's what I remember:\n" + "\n".join(lines)


# --------------------------------------------------------------------------- #
# Weather (wttr.in — real current conditions, no API key)
# --------------------------------------------------------------------------- #
@function_tool
async def get_weather(context: RunContext, location: str) -> str:
    """Get the CURRENT weather and temperature for a place. Use this (not
    web_search) whenever the user asks about weather, temperature, or how hot/cold
    it is somewhere.

    Args:
        location: City and optionally state/country, e.g. "Vallejo, California".
    """
    place = location.strip().replace(" ", "+")
    fmt = "%l:+%c+%t+(feels+like+%f),+%C,+wind+%w,+humidity+%h"
    url = f"https://wttr.in/{place}?format={fmt}"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "curl/8"}) as http:
            async with http.get(url) as resp:
                if resp.status != 200:
                    return f"I couldn't get the weather for {location} right now."
                text = (await resp.text()).strip()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return f"I couldn't reach the weather service for {location} just now."

    # wttr.in returns "Unknown location" text if it can't resolve the place.
    if not text or "Unknown location" in text or "Sorry" in text:
        return f"I couldn't find weather for '{location}'. Could you say the city again?"
    return text


# --------------------------------------------------------------------------- #
# Web search (live DuckDuckGo results scrape — no key, keeps us fully free)
# --------------------------------------------------------------------------- #
@function_tool
async def web_search(context: RunContext, query: str) -> str:
    """Search the live web for current information (prices, weather, news, recent
    events, anything you don't already know). Returns the top real search-result
    snippets. Use for anything time-sensitive.

    IMPORTANT: If this returns "NO_RESULTS", you MUST tell the user you couldn't
    find it. NEVER make up a number, price, or fact that isn't in these results.

    Args:
        query: What to search for.
    """
    # Scrape DuckDuckGo's HTML results endpoint — real live results, no API key.
    url = "https://html.duckduckgo.com/html/"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
            async with http.post(url, data={"q": query}) as resp:
                if resp.status != 200:
                    return "NO_RESULTS (search service returned an error)."
                html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return "NO_RESULTS (couldn't reach the web just now)."

    # Each result's snippet sits in <a class="result__snippet">...</a>.
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S | re.I
    )
    cleaned = []
    for s in snippets[:4]:
        text = _WS_RE.sub(" ", _ANGLE_RE.sub(" ", s)).strip()
        if text:
            cleaned.append(text)

    if not cleaned:
        return f"NO_RESULTS for '{query}'. Tell the user you couldn't find it; do not guess."

    return "Top web results:\n- " + "\n- ".join(cleaned)


# --------------------------------------------------------------------------- #
# Fetch a specific web page (the "run curl, but only to fetch a URL" tool)
# --------------------------------------------------------------------------- #
# SAFETY: this tool can ONLY perform an HTTP GET on a public http(s) URL. It
# cannot run shell commands, read local files, or reach private/localhost hosts.
# That containment is the whole point — the model gets web reach, not a terminal.
_BLOCKED_HOST_RE = re.compile(
    r"^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.0\.0\.0|\[?::1\]?)", re.I
)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANGLE_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _html_to_text(html: str, limit: int = 1500) -> str:
    html = _TAG_RE.sub(" ", html)          # drop script/style bodies
    text = _ANGLE_RE.sub(" ", html)        # drop remaining tags
    text = _WS_RE.sub(" ", text).strip()   # collapse whitespace
    return text[:limit]


@function_tool
async def fetch_url(context: RunContext, url: str) -> str:
    """Download a specific web page and return its readable text. Use when the
    user gives you a URL or when a web_search result points at a page you should
    read. Only works on public http(s) web addresses.

    Args:
        url: The full web address to fetch, e.g. "https://example.com/article".
    """
    if not url.lower().startswith(("http://", "https://")):
        return "I can only fetch normal web addresses starting with http or https."

    # Block private / loopback targets so the model can't poke local services.
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split(":")[0]
    if _BLOCKED_HOST_RE.match(host):
        return "I can't fetch local or private network addresses."

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"User-Agent": "LocalVoiceAssistant/1.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
            async with http.get(url) as resp:
                if resp.status != 200:
                    return f"That page returned an error (status {resp.status})."
                ctype = resp.headers.get("Content-Type", "")
                body = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return "I couldn't load that page just now."
    except UnicodeDecodeError:
        return "That page isn't readable text (it may be a file or image)."

    if "html" in ctype.lower():
        body = _html_to_text(body)
    else:
        body = _WS_RE.sub(" ", body).strip()[:1500]

    return body or "That page didn't have any readable text."


# --------------------------------------------------------------------------- #
# Local file search (SANDBOXED, read-only)
# --------------------------------------------------------------------------- #
# SAFETY MODEL — this is the only tool that touches your real files, so it is
# locked down hard:
#   * Confined to SANDBOX_DIRS below. A resolved path outside them is rejected,
#     so the model literally cannot read anything else on the Mac.
#   * Read-only: it finds/greps/reads. It never writes, moves, or deletes.
#   * No shell string interpolation: args are passed to subprocess as a list, so
#     a filename can't smuggle in a shell command.
_SANDBOX_DIRS = [
    (pathlib.Path.home() / "Documents").resolve(),
    (pathlib.Path.home() / "Downloads").resolve(),
]
_RG = "/opt/homebrew/bin/rg"      # ripgrep (Rust) — fast, full path to skip shell alias
_MDFIND = "/usr/bin/mdfind"       # Spotlight name search


def _in_sandbox(p: pathlib.Path) -> bool:
    """True only if p is inside one of the whitelisted sandbox dirs."""
    try:
        rp = p.resolve()
    except OSError:
        return False
    return any(rp == d or d in rp.parents for d in _SANDBOX_DIRS)


@function_tool
async def find_files(context: RunContext, name_query: str) -> str:
    """Find files on the user's Mac by NAME or keyword in the filename. Use when
    the user is looking for a file but may not remember its exact name, e.g.
    "find the file for my ASO keyword tool" or "where's my tax document". Only
    searches the user's Documents and Downloads folders.

    Args:
        name_query: Words likely in the file or folder name, e.g. "aso keyword".
    """
    results = []
    for d in _SANDBOX_DIRS:
        try:
            out = await asyncio.to_thread(
                subprocess.run,
                [_MDFIND, "-onlyin", str(d), name_query],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        for line in out.stdout.splitlines():
            p = pathlib.Path(line)
            if _in_sandbox(p):
                results.append(str(p))
    # Dedupe, keep it short for voice.
    seen, uniq = set(), []
    for r in results:
        if r not in seen:
            seen.add(r); uniq.append(r)
    if not uniq:
        return f"I couldn't find any files matching '{name_query}' in Documents or Downloads."
    top = uniq[:8]
    listing = "\n".join(f"- {p}" for p in top)
    more = f"\n(and {len(uniq)-len(top)} more)" if len(uniq) > len(top) else ""
    return f"Found {len(uniq)} match(es):\n{listing}{more}"


@function_tool
async def search_in_files(context: RunContext, text_query: str) -> str:
    """Search the CONTENTS of text/code files for a phrase, when the answer is
    inside a file rather than in its name (e.g. "which file mentions my API key"
    or "find where I wrote about the launch plan"). Searches Documents and
    Downloads. Returns matching files with a snippet.

    Args:
        text_query: The literal text/phrase to look for inside files.
    """
    hits = []
    for d in _SANDBOX_DIRS:
        try:
            out = await asyncio.to_thread(
                subprocess.run,
                # --fixed-strings: treat query literally; -i: case-insensitive;
                # -l-ish with -m1 snippet; cap results; ripgrep skips binaries.
                [_RG, "--fixed-strings", "-i", "--max-count", "1",
                 "--max-columns", "200", "--no-heading", "--with-filename",
                 "--line-number", text_query, str(d)],
                capture_output=True, text=True, timeout=20,
            )
        except (subprocess.SubprocessError, OSError):
            continue
        for line in out.stdout.splitlines():
            # format: path:line:snippet
            parts = line.split(":", 2)
            if len(parts) == 3 and _in_sandbox(pathlib.Path(parts[0])):
                hits.append(parts)
            if len(hits) >= 8:
                break
        if len(hits) >= 8:
            break
    if not hits:
        return f"I couldn't find '{text_query}' in any files in Documents or Downloads."
    lines = [f"- {p} (line {ln}): {snip.strip()[:120]}" for p, ln, snip in hits]
    return f"Found it in {len(hits)} file(s):\n" + "\n".join(lines)


@function_tool
async def read_file(context: RunContext, path: str) -> str:
    """Read the contents of a specific file (e.g. one that find_files or
    search_in_files located) so you can summarize or answer questions about it.
    Only files inside Documents or Downloads can be read.

    Args:
        path: Full path to the file, e.g. "/Users/you/Documents/notes.txt".
    """
    p = pathlib.Path(path).expanduser()
    if not _in_sandbox(p):
        return "I can only read files inside your Documents or Downloads folders."
    if not p.is_file():
        return f"I couldn't find a file at {path}."
    try:
        # Cap the read so a huge file doesn't blow up the voice context.
        data = await asyncio.to_thread(p.read_text, "utf-8", "replace")
    except (OSError, ValueError):
        return "I couldn't read that file (it may not be a text file)."
    data = data.strip()
    if not data:
        return "That file is empty."
    return data[:2500] + ("\n…(truncated)" if len(data) > 2500 else "")


# All tools, exported for the agent to register.
ALL_TOOLS = [
    get_current_time,
    set_timer,
    remember_note,
    recall_notes,
    get_weather,
    web_search,
    fetch_url,
    find_files,
    search_in_files,
    read_file,
]

"""Behavioral tests for the local voice assistant.

These use LiveKit's AgentSession.run() harness with text input (no audio), driven
by the SAME local Ollama LLM the real agent uses — so the tests are fully local
too. Verified against livekit-agents 1.7.0 and docs.livekit.io/agents/build/testing.

Run with:
    source .venv/bin/activate
    pytest -q

Note: these hit the local Ollama server, so `ollama serve` must be running and the
LLM_MODEL from .env must be pulled. They exercise real model behavior, which is the
point — prompt/tool-description changes that break behavior get caught here.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession
from livekit.plugins import openai

from tools import ALL_TOOLS, _load_notes, _NOTES_PATH
# Import the REAL prompt + Agent subclass so tests match production exactly.
from agent import SYSTEM_PROMPT, Assistant

load_dotenv()


def _llm():
    return openai.LLM.with_ollama(
        model=os.getenv("LLM_MODEL", "llama3.1:8b"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    )


def _agent() -> Assistant:
    return Assistant(instructions=SYSTEM_PROMPT, tools=ALL_TOOLS)


@pytest.mark.asyncio
async def test_greeting_is_friendly():
    """Basic conversation flow: the agent responds appropriately to a greeting."""
    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(_agent())
        result = await session.run(user_input="Hi there!")
        # A small local model may emit tool preamble; assert that SOMEWHERE in the
        # run it produces a friendly assistant reply, rather than pinning event[0].
        await (
            result.expect.next_event(type="message")
            .judge(
                llm,
                intent=(
                    "A friendly, conversational reply to a greeting — for example a "
                    "hello, asking how the user is, or offering to help. Any warm, "
                    "on-topic response to 'hi' satisfies this."
                ),
            )
        )


@pytest.mark.asyncio
async def test_asking_the_time_calls_the_time_tool():
    """Tool invocation: asking the time should call get_current_time."""
    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(_agent())
        result = await session.run(user_input="What time is it right now?")
        # Order-independent: the model may narrate before/after the call.
        result.expect.contains_function_call(name="get_current_time")


@pytest.fixture
def clean_notes():
    """Isolate the notes test from the user's real notes.json."""
    backup = _NOTES_PATH.read_bytes() if _NOTES_PATH.exists() else None
    if _NOTES_PATH.exists():
        _NOTES_PATH.unlink()
    yield
    if backup is not None:
        _NOTES_PATH.write_bytes(backup)
    elif _NOTES_PATH.exists():
        _NOTES_PATH.unlink()


async def test_remember_note_tool_persists(clean_notes):
    """The remember_note tool itself must persist to disk (deterministic)."""
    from tools import save_note
    save_note("my wifi password is sunflower42")
    saved = [n["note"].lower() for n in _load_notes()]
    assert any("sunflower42" in n for n in saved), f"not persisted; on disk={saved}"


@pytest.mark.asyncio
async def test_safety_net_saves_note_even_without_tool_call(clean_notes):
    """The on_user_turn_completed safety net must save a 'remember ...' phrase
    directly — this is what guarantees notes aren't lost when the local model
    forgets to call the tool. Verified without the LLM so it's deterministic."""
    from livekit.agents import llm as _lk_llm

    agent = _agent()
    msg = _lk_llm.ChatMessage(
        role="user", content=["Please remember that my flight is at 6pm."]
    )
    await agent.on_user_turn_completed(_lk_llm.ChatContext.empty(), msg)
    saved = [n["note"].lower() for n in _load_notes()]
    assert any("flight is at 6pm" in n for n in saved), f"safety net failed; on disk={saved}"


@pytest.mark.asyncio
async def test_live_question_triggers_web_search():
    """A current-info question (crypto price) should trigger web_search rather
    than the model answering a made-up number from memory."""
    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(_agent())
        result = await session.run(
            user_input="Search the web and tell me the current price of Ethereum."
        )
        result.expect.contains_function_call(name="web_search")


@pytest.mark.asyncio
async def test_weather_question_uses_weather_tool():
    """Weather questions must use get_weather (real data), not web_search."""
    async with _llm() as llm, AgentSession(llm=llm) as session:
        await session.start(_agent())
        result = await session.run(
            user_input="What's the temperature in Vallejo, California right now?"
        )
        result.expect.contains_function_call(name="get_weather")


def test_file_tools_sandbox_blocks_outside_paths():
    """SECURITY: the file tools must never allow reading outside Documents/Downloads.
    Verified directly (no LLM) so it's deterministic and can't silently regress."""
    import pathlib
    from tools import _in_sandbox

    home = pathlib.Path.home()
    # Allowed
    assert _in_sandbox(home / "Documents" / "x.txt")
    assert _in_sandbox(home / "Downloads" / "y.pdf")
    # Must be blocked
    assert not _in_sandbox(pathlib.Path("/etc/passwd"))
    assert not _in_sandbox(home / ".ssh" / "id_rsa")
    assert not _in_sandbox(home / "Documents" / ".." / ".." / "etc" / "passwd")

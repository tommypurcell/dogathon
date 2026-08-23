"""A fully-local voice assistant built on LiveKit Agents.

Everything runs on this machine:
  - STT:  faster-whisper           (local_stt.LocalWhisperSTT)
  - LLM:  Ollama via OpenAI-compat (openai.LLM.with_ollama)  <- verified in LiveKit docs
  - TTS:  kokoro-onnx              (local_tts.LocalKokoroTTS)
  - VAD:  Silero                   (livekit-plugins-silero)
  - Server: livekit-server --dev on ws://localhost:7880

Run it and talk to it straight from the terminal:
    python agent.py console

All LiveKit API usage verified against livekit-agents 1.7.0 installed source and
docs.livekit.io on 2026-08-21.
"""

from __future__ import annotations

import os
import pathlib
import re

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    inference,
    llm,
)
from livekit.plugins import openai, silero

from local_stt import LocalWhisperSTT
from local_tts import LocalKokoroTTS
from tools import ALL_TOOLS, save_note

load_dotenv()

_HERE = pathlib.Path(__file__).parent

SYSTEM_PROMPT = (
    "You are a friendly, concise personal voice assistant. Your brain is a local "
    "AI model running on the user's own Mac via Ollama; if asked what model you "
    "are or how you work, just say so plainly and briefly — don't be evasive or "
    "claim you have no model. You are speaking out loud in a live back-and-forth "
    "conversation, so be BRIEF: reply in ONE short sentence whenever possible, "
    "and never more than two. Get to the point fast — no preamble like 'sure, let "
    "me...'. Don't use markdown, bullet points, emoji, or special formatting; it "
    "will be read aloud. "
    "TOOL ROUTING (follow exactly): For ANYTHING about weather, temperature, how "
    "hot or cold it is, forecast, or conditions in a place, you MUST call "
    "get_weather — even if the user says 'search the web' or 'search the internet'. "
    "The word 'search' does NOT mean use web_search for weather; get_weather is the "
    "correct tool for weather, always. Use web_search only for non-weather live "
    "facts like news, prices, or events. NEVER use web_search or any tool to answer "
    "questions about yourself, what model or tools you use, or general knowledge you "
    "already have — just answer those directly. "
    "When you use web_search, base your answer ONLY on the results it returns. "
    "If it returns 'NO_RESULTS' or nothing useful, say you couldn't find it — "
    "NEVER invent a price, weather forecast, statistic, or fact that wasn't in the "
    "results. It is always better to say 'I couldn't find that' than to guess. "
    "When the user asks you to remember something, you MUST call the remember_note "
    "tool to actually save it — never just say 'I'll remember' without calling it, "
    "or it will be lost. To recall things, call recall_notes. When asked for the "
    "time or to set a timer, always call those tools rather than guessing. "
    "Do not call any tool for greetings, small talk, or questions about yourself — "
    "just reply out loud with a warm, brief response. Never stay silent and never "
    "say things like 'no response needed'; the user is talking to you and expects a reply."
)


# Matches "remember (that) X", "make a note (that) X", "don't forget (that) X".
_REMEMBER_RE = re.compile(
    r"\b(?:remember|make a note|note that|don'?t forget)\b(?:\s+that)?[\s,:]*(.+)",
    re.I,
)


class Assistant(Agent):
    """Agent subclass with a safety net: small local models sometimes SAY they
    saved a note without actually calling the tool. This hook fires on every
    completed user turn and force-saves anything phrased as 'remember ...', so a
    note is never silently lost regardless of whether the LLM called the tool.
    save_note() dedupes, so the tool + hook firing together won't double-save."""

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        text = (new_message.text_content or "").strip()
        m = _REMEMBER_RE.search(text)
        if m:
            fact = m.group(1).strip().rstrip(".")
            if fact:
                save_note(fact)


def prewarm(proc: JobProcess) -> None:
    """Load the Silero VAD model once per worker process and cache it, so each
    conversation starts fast instead of reloading the model.

    min_silence_duration is lowered from the 0.55s default to 0.35s so the agent
    decides you've finished talking sooner -> snappier, more conversational turns.
    (Trade-off: if you pause mid-sentence it may reply a touch early.)"""
    proc.userdata["vad"] = silero.VAD.load(min_silence_duration=0.35)

    # Pin the LLM in Ollama's memory so it never cold-starts mid-conversation.
    # Without this, Ollama unloads the model after ~5 min idle and the NEXT reply
    # pays a ~10s reload — that was the real cause of the choppy 11-22s stalls.
    # Only pin the local model if we're actually using it.
    if os.getenv("LLM_BACKEND", "local").lower() == "local":
        try:
            import urllib.request, json as _json
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").replace("/v1", "")
            body = _json.dumps({
                "model": os.getenv("LLM_MODEL", "qwen2.5:3b"),
                "keep_alive": -1,  # -1 = keep loaded indefinitely
                "prompt": "hi",
            }).encode()
            req = urllib.request.Request(base + "/api/generate", data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
        except Exception:
            pass  # non-fatal; first real reply will just be a touch slower


# MODE selects where speech-to-text and text-to-speech run:
#   "local"  -> faster-whisper + Kokoro on this Mac (private, free, slower on CPU)
#   "hybrid" -> LiveKit Inference (Deepgram) STT+TTS in the cloud (fast, accurate),
#               while the LLM stays LOCAL on Ollama. Requires LiveKit Cloud creds.
# The LLM and VAD are local in BOTH modes.
MODE = os.getenv("MODE", "local").lower()


def _build_stt():
    if MODE == "hybrid":
        # Cloud STT via LiveKit Inference — uses your LIVEKIT_API_KEY/SECRET.
        return inference.STT(model=os.getenv("STT_MODEL", "deepgram/nova-3"), language="en")
    return LocalWhisperSTT(
        model=os.getenv("WHISPER_MODEL", "base.en"),
        device=os.getenv("WHISPER_DEVICE", "cpu"),
        compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
    )


def _build_tts():
    if MODE == "hybrid":
        return inference.TTS(
            model=os.getenv("TTS_MODEL", "deepgram/aura-2"),
            voice=os.getenv("TTS_VOICE", "thalia"),
        )
    return LocalKokoroTTS(
        model_path=str(_HERE / "models" / "kokoro-v1.0.onnx"),
        voices_path=str(_HERE / "models" / "voices-v1.0.bin"),
        voice=os.getenv("KOKORO_VOICE", "af_heart"),
    )


# LLM_BACKEND chooses where the LLM runs, independently of MODE:
#   "local" -> Ollama on this Mac (private brain, but small local models can flub tools)
#   "cloud" -> LiveKit Inference (fast + smart tool use; conversation goes to cloud)
def _build_llm():
    backend = os.getenv("LLM_BACKEND", "local").lower()
    if backend == "cloud":
        return inference.LLM(model=os.getenv("CLOUD_LLM_MODEL", "google/gemma-4-31b-it"))
    return openai.LLM.with_ollama(
        model=os.getenv("LLM_MODEL", "qwen2.5:3b"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    )


async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        # STT: local Whisper or cloud Deepgram depending on MODE.
        stt=_build_stt(),
        # LLM: local Ollama or cloud LiveKit Inference depending on LLM_BACKEND.
        llm=_build_llm(),
        # TTS: local Kokoro or cloud Deepgram depending on MODE.
        tts=_build_tts(),
        # --- Local voice activity detection (cached from prewarm) ---
        vad=ctx.proc.userdata["vad"],
        # Use local VAD to decide when the user has finished talking. Without this,
        # the SDK tries a cloud turn-detection model — we want 100% local, so pin it.
        turn_detection="vad",
        # Respond sooner after you stop speaking (default is higher) -> feels live.
        min_endpointing_delay=0.4,
        # Stop it from cutting itself off when you pause or say "um"/"okay": a real
        # interruption now needs at least 2 words. Kills the choppy false-interrupts
        # while keeping genuine barge-in working.
        min_interruption_words=2,
    )

    await session.start(
        agent=Assistant(instructions=SYSTEM_PROMPT, tools=ALL_TOOLS),
        room=ctx.room,
    )

    # Greet the user first so it's obvious the assistant is listening.
    await session.generate_reply(
        instructions="Greet the user warmly in one short sentence and ask how you can help."
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )

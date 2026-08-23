# Local Voice Assistant (LiveKit)

A push-a-button-and-talk personal assistant that runs **100% on your Mac**. No cloud,
no API keys, nothing leaves your computer. Built to practice with the LiveKit Agents SDK.

## What's local

| Piece | What it uses | Runs where |
|-------|--------------|-----------|
| Realtime server | `livekit-server --dev` | your Mac (port 7880) |
| Speech-to-text | `faster-whisper` (via a custom adapter in `local_stt.py`) | your Mac |
| LLM (the brain) | Ollama (`llama3.1:8b` by default) | your Mac |
| Text-to-speech | `kokoro-onnx` (via a custom adapter in `local_tts.py`) | your Mac |
| Voice activity / turn-taking | Silero VAD | your Mac |

> LiveKit ships no official *local* STT/TTS plugin, so `local_stt.py` and `local_tts.py`
> are small custom adapters bridging faster-whisper and Kokoro into LiveKit's interfaces.
> All LiveKit API usage was verified against the installed SDK (v1.7.0) and docs.livekit.io.

## What it can do

- Just talk / chat
- Tell the time, set timers (announces out loud when done)
- Current weather / temperature for any place (wttr.in, no key)
- Search the live web (DuckDuckGo results, no key) + fetch a specific URL
- Remember & recall notes (saved to `notes.json`; a safety-net hook saves
  "remember ..." even if the model forgets to call the tool)

Tools live in `tools.py` — add more there.

## One-time setup (already done during build)

```bash
brew install livekit livekit-cli espeak-ng   # server, CLI, Kokoro phonemizer
# Ollama already installed; models already pulled (llama3.1:8b, gemma4, ...)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Kokoro model files downloaded into ./models/
```

## Run it — talk to your assistant

Open **two terminals**, both `cd`'d into this folder.

**Terminal 1 — start the local server (leave it running):**
```bash
livekit-server --dev
```

**Terminal 2 — start the assistant in console mode and talk:**
```bash
source .venv/bin/activate
python agent.py console
```
Then just start talking. It greets you first; speak, pause, and it replies through
your speakers. Press `Ctrl+C` to quit.

> The SDK prints that `python agent.py console` is deprecated in favor of
> `lk agent console`. Both work today; `python agent.py console` is simplest.

Make sure Ollama is running (`ollama serve` if it isn't) before starting.

## Tests

```bash
source .venv/bin/activate
pytest -q
```
These drive the real local llama3.1 model with text input and assert the greeting
and tool-calls behave. (Requires Ollama running.)

## Tweaking it — everything is in `.env`

```bash
LLM_MODEL=qwen2.5:7b       # fast + good tool use. try llama3.1:8b or gemma4:latest
WHISPER_MODEL=base.en      # tiny/base/small/medium (bigger = accurate but slower; base = fast)
KOKORO_VOICE=af_heart      # af_bella, am_michael, bf_emma, ...
```
No code change needed — edit `.env` and restart the agent.

## Files

- `agent.py` — wires STT + LLM + TTS + VAD into an `AgentSession`; the entrypoint.
- `local_stt.py` — faster-whisper → LiveKit STT adapter.
- `local_tts.py` — kokoro-onnx → LiveKit TTS adapter.
- `tools.py` — the function tools (time, timers, web search, notes).
- `tests/test_agent.py` — behavioral tests via `AgentSession.run()`.
- `models/` — Kokoro ONNX model + voices (git-ignored, large).

## The LiveKit skill

The `livekit-agents` skill is installed under `.agents/skills/`. Its golden rule:
**never trust model memory for LiveKit APIs — always verify against live docs.** A
`livekit-docs` MCP server is registered (`~/.claude.json`); restart Claude Code to
activate it for future sessions.

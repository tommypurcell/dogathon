# Paws & Co. — Dog Adoption Demo 🐶

A talking golden-retriever "office helper" that answers questions about adoptable
dogs and books viewings. Two pages:

- **index.html** — the shelter site (grid of dogs)
- **helper.html** — talk to Buddy, the voice assistant

## Brain: scripted + real AI

- Fast **scripted answers** for common asks (instant, offline).
- Anything off-script falls back to **Gemma 4 31B** via LiveKit Cloud, which does
  real **tool-calling** over the dog data (understands any phrasing, won't invent
  dogs). Requires the bridge server below.

## Voice
Uses the browser's best available neural voice (Siri/Google/Microsoft), tuned to
sound natural. No setup.

## Run it

**1. Start the AI bridge** (gives Buddy the smart brain):
```bash
cd ~/Documents/Dogathon/voice-agent && source .venv/bin/activate
pip install fastapi uvicorn openai livekit-api        # one-time
cd ~/Documents/Dogathon/web && python server.py
```
It reads your LiveKit creds from `../voice-agent/.env` and serves on :8000.
Check it: open http://localhost:8000/health → should say `{"ok": true}`.

**2. Open the page in Chrome** (needs the Web Speech API + mic):
```bash
open -a "Google Chrome" ~/Documents/Dogathon/web/helper.html
```
Click **Start talking**, allow the mic, and talk.

> If mic is blocked on `file://`, serve the page too:
> `cd ~/Documents/Dogathon/web && python3 -m http.server 8080` → http://localhost:8080/helper.html

## Add the dog picture
Save the golden-retriever image as `web/assets/dog.png` (helper avatar) — the page
shows a placeholder circle until you do.

## Try saying
- "I'm looking for a good family dog."
- "Do you have any golden retrievers?"  ← now works (breed match / Gemma)
- "I live in a tiny apartment and work long hours — which dog suits me?"  ← Gemma reasons → Biscuit
- "When can I meet Max?" → "The Tuesday one, book it."

If the bridge isn't running, Buddy still answers the scripted asks and degrades
gracefully (no crash).

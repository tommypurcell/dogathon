"""Tiny bridge server: the web page POSTs what the user said, this asks Gemma 31B
(via LiveKit Cloud Inference — OpenAI-compatible) with the dog tools, runs the
chosen tool over the mock data, and returns the dog's spoken reply.

Why a server: LiveKit Inference must be reached with your API secret (can't live
in a browser), and it's server-side only. This keeps the secret here.

Run:
    cd ~/Documents/Dogathon/voice-agent && source .venv/bin/activate
    pip install fastapi uvicorn openai livekit-api        # one-time
    cd ~/Documents/Dogathon/web
    python server.py
Then open index.html / helper.html — the page calls http://localhost:8000/chat.
Reads LIVEKIT_API_KEY / LIVEKIT_API_SECRET from ../voice-agent/.env.
"""

from __future__ import annotations

import json
import os
import pathlib

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# LiveKit token helper (same one the agent uses).
from livekit import api
import datetime

# --- credentials from the voice-agent's .env ---
_ENV = pathlib.Path(__file__).parent.parent / "voice-agent" / ".env"
load_dotenv(_ENV)
KEY = os.getenv("LIVEKIT_API_KEY", "")
SECRET = os.getenv("LIVEKIT_API_SECRET", "")
MODEL = os.getenv("CLOUD_LLM_MODEL", "google/gemma-4-31b-it")
BASE_URL = "https://agent-gateway.livekit.cloud/v1"

# --- the mock dog data (mirrors dogs.js) ---
DOGS = [
    {"name": "Max", "breed": "Golden Retriever", "age": "3 years", "size": "large",
     "kids": True, "energy": "medium",
     "blurb": "a gentle, family-friendly gentleman who loves fetch and nap time",
     "slots": ["Tuesday from 12 to 2 PM", "Thursday from 3 to 5 PM"]},
    {"name": "Scout", "breed": "Appenzeller Mix", "age": "2 years", "size": "medium",
     "kids": True, "energy": "high",
     "blurb": "a beach-loving adventurer, always up for a hike and a swim",
     "slots": ["Wednesday from 10 AM to noon", "Saturday from 1 to 3 PM"]},
    {"name": "Biscuit", "breed": "Boston Terrier Mix", "age": "5 years", "size": "small",
     "kids": True, "energy": "low",
     "blurb": "a mellow cuddle-bug, happiest on the couch — a great first dog",
     "slots": ["Monday from 2 to 4 PM", "Friday from 11 AM to 1 PM"]},
    {"name": "Rocky", "breed": "Labrador Puppy", "age": "5 months", "size": "medium",
     "kids": True, "energy": "high",
     "blurb": "a splashy little water pup, playful and eager to learn",
     "slots": ["Thursday from 12 to 2 PM", "Sunday from 10 AM to noon"]},
]


def _dog(name: str):
    name = (name or "").strip().lower()
    return next((d for d in DOGS if d["name"].lower() == name or name in d["name"].lower()), None)


# --- tool implementations (run over mock data) ---
def list_available_dogs(good_with_kids: bool = False):
    dogs = [d for d in DOGS if d["kids"]] if good_with_kids else DOGS
    return [{"name": d["name"], "breed": d["breed"], "age": d["age"], "blurb": d["blurb"],
             "size": d["size"], "energy": d["energy"]} for d in dogs]


def get_dog_details(name: str):
    d = _dog(name)
    return d or {"error": f"No dog named {name}."}


def check_viewing_availability(name: str):
    d = _dog(name)
    return {"name": d["name"], "slots": d["slots"]} if d else {"error": f"No dog named {name}."}


def book_viewing(name: str, day_and_time: str):
    d = _dog(name)
    if not d:
        return {"error": f"No dog named {name}."}
    return {"booked": True, "dog": d["name"], "when": day_and_time}


TOOLS_IMPL = {
    "list_available_dogs": list_available_dogs,
    "get_dog_details": get_dog_details,
    "check_viewing_availability": check_viewing_availability,
    "book_viewing": book_viewing,
}

# --- tool schemas for the LLM ---
TOOLS = [
    {"type": "function", "function": {
        "name": "list_available_dogs",
        "description": "List dogs available for adoption. Use for 'what dogs do you have', breeds, or 'a family dog'.",
        "parameters": {"type": "object", "properties": {
            "good_with_kids": {"type": "boolean", "description": "Only kid-friendly dogs."}}}}},
    {"type": "function", "function": {
        "name": "get_dog_details",
        "description": "Details about one dog by name.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "check_viewing_availability",
        "description": "Get a dog's viewing time slots. Use when the user wants to visit/schedule.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "book_viewing",
        "description": "Book a viewing after the user confirms a specific slot.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "day_and_time": {"type": "string"}},
            "required": ["name", "day_and_time"]}}},
]

SYSTEM = (
    "You are Buddy, a friendly golden-retriever 'office helper' at Paws & Co., a dog "
    "adoption shelter. You are speaking OUT LOUD, so keep replies short, warm, and "
    "conversational — one or two sentences, no lists or markdown. Use your tools to "
    "answer from real shelter data; never invent dogs, breeds, or times not returned "
    "by a tool. When a user wants to visit a dog, check availability and offer the "
    "slots, then book only after they confirm. A little dog personality is welcome."
)


def _token() -> str:
    grant = api.access_token.InferenceGrants(perform=True)
    return (api.AccessToken(KEY, SECRET).with_identity("dogathon-web")
            .with_inference_grants(grant)
            .with_ttl(datetime.timedelta(minutes=10)).to_jwt())


app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Turn(BaseModel):
    message: str
    history: list[dict] = []   # prior [{role, content}] for context


@app.post("/chat")
def chat(turn: Turn):
    client = OpenAI(api_key=_token(), base_url=BASE_URL)
    messages = [{"role": "system", "content": SYSTEM}, *turn.history,
                {"role": "user", "content": turn.message}]

    # First call — model may request a tool.
    resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
    msg = resp.choices[0].message

    if msg.tool_calls:
        messages.append(msg.model_dump(exclude_none=True))
        for tc in msg.tool_calls:
            fn = TOOLS_IMPL.get(tc.function.name)
            args = json.loads(tc.function.arguments or "{}")
            result = fn(**args) if fn else {"error": "unknown tool"}
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result)})
        # Second call — model turns tool results into a spoken reply.
        resp = client.chat.completions.create(model=MODEL, messages=messages)
        msg = resp.choices[0].message

    return {"reply": msg.content or "Woof! Could you say that again?"}


@app.get("/health")
def health():
    return {"ok": bool(KEY and SECRET), "model": MODEL}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

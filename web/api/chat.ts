// Vercel serverless function: the dog's "brain".
// The web page POSTs what the user said; this asks Gemma 4 31B (via LiveKit
// Cloud Inference — OpenAI-compatible) with the dog tools, runs the chosen tool
// over the mock data, and returns Buddy's spoken reply.
//
// Env vars required in Vercel (Project → Settings → Environment Variables):
//   LIVEKIT_API_KEY, LIVEKIT_API_SECRET
// Optional: CLOUD_LLM_MODEL (defaults to google/gemma-4-31b-it)

import { SignJWT } from "jose";

const BASE_URL = "https://agent-gateway.livekit.cloud/v1";

// --- mock dog data (mirrors dogs.json) ---
type Dog = {
  name: string; breed: string; age: string; size: string;
  kids: boolean; energy: string; blurb: string; slots: string[];
};

const DOGS: Dog[] = [
  { name: "Max", breed: "Golden Retriever", age: "3 years", size: "large",
    kids: true, energy: "medium",
    blurb: "a gentle, family-friendly gentleman who loves fetch and nap time",
    slots: ["Tuesday from 12 to 2 PM", "Thursday from 3 to 5 PM"] },
  { name: "Scout", breed: "Appenzeller Mix", age: "2 years", size: "medium",
    kids: true, energy: "high",
    blurb: "a beach-loving adventurer, always up for a hike and a swim",
    slots: ["Wednesday from 10 AM to noon", "Saturday from 1 to 3 PM"] },
  { name: "Biscuit", breed: "Boston Terrier Mix", age: "5 years", size: "small",
    kids: true, energy: "low",
    blurb: "a mellow cuddle-bug, happiest on the couch — a great first dog",
    slots: ["Monday from 2 to 4 PM", "Friday from 11 AM to 1 PM"] },
  { name: "Rocky", breed: "Labrador Puppy", age: "5 months", size: "medium",
    kids: true, energy: "high",
    blurb: "a splashy little water pup, playful and eager to learn",
    slots: ["Thursday from 12 to 2 PM", "Sunday from 10 AM to noon"] },
];

function findDog(name: string): Dog | undefined {
  const n = (name || "").trim().toLowerCase();
  return DOGS.find((d) => d.name.toLowerCase() === n || n.includes(d.name.toLowerCase()));
}

// --- tool implementations ---
const TOOLS_IMPL: Record<string, (args: any) => unknown> = {
  list_available_dogs: ({ good_with_kids = false }) =>
    (good_with_kids ? DOGS.filter((d) => d.kids) : DOGS).map((d) => ({
      name: d.name, breed: d.breed, age: d.age, blurb: d.blurb,
      size: d.size, energy: d.energy,
    })),
  get_dog_details: ({ name }) => findDog(name) ?? { error: `No dog named ${name}.` },
  check_viewing_availability: ({ name }) => {
    const d = findDog(name);
    return d ? { name: d.name, slots: d.slots } : { error: `No dog named ${name}.` };
  },
  book_viewing: ({ name, day_and_time }) => {
    const d = findDog(name);
    return d
      ? { booked: true, dog: d.name, when: day_and_time }
      : { error: `No dog named ${name}.` };
  },
};

// --- tool schemas for the LLM ---
const TOOLS = [
  { type: "function", function: {
    name: "list_available_dogs",
    description: "List dogs available for adoption. Use for 'what dogs do you have', breeds, or 'a family dog'.",
    parameters: { type: "object", properties: {
      good_with_kids: { type: "boolean", description: "Only kid-friendly dogs." } } } } },
  { type: "function", function: {
    name: "get_dog_details",
    description: "Details about one dog by name.",
    parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } } },
  { type: "function", function: {
    name: "check_viewing_availability",
    description: "Get a dog's viewing time slots. Use when the user wants to visit/schedule.",
    parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"] } } },
  { type: "function", function: {
    name: "book_viewing",
    description: "Book a viewing after the user confirms a specific slot.",
    parameters: { type: "object", properties: {
      name: { type: "string" }, day_and_time: { type: "string" } },
      required: ["name", "day_and_time"] } } },
];

const SYSTEM =
  "You are Buddy, a friendly golden-retriever 'office helper' at Paws & Co., a dog " +
  "adoption shelter. You are speaking OUT LOUD, so keep replies short, warm, and " +
  "conversational — one or two sentences, no lists or markdown. Use your tools to " +
  "answer from real shelter data; never invent dogs, breeds, or times not returned " +
  "by a tool. When a user wants to visit a dog, check availability and offer the " +
  "slots, then book only after they confirm. A little dog personality is welcome.";

// --- mint the LiveKit inference JWT (same claims the Python SDK produces) ---
async function inferenceToken(apiKey: string, apiSecret: string): Promise<string> {
  const secret = new TextEncoder().encode(apiSecret);
  return await new SignJWT({ inference: { perform: true } })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuer(apiKey)
    .setSubject("dogathon-web")
    .setNotBefore("0s")
    .setExpirationTime("10m")
    .sign(secret);
}

async function callInference(token: string, body: unknown) {
  const r = await fetch(`${BASE_URL}/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    throw new Error(`inference ${r.status}: ${await r.text()}`);
  }
  return (await r.json()) as any;
}

export const config = { runtime: "edge" };

export default async function handler(req: Request): Promise<Response> {
  // CORS (page may be served from a different origin / file://)
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "POST only" }), {
      status: 405, headers: { ...cors, "Content-Type": "application/json" } });
  }

  const KEY = process.env.LIVEKIT_API_KEY ?? "";
  const SECRET = process.env.LIVEKIT_API_SECRET ?? "";
  const MODEL = process.env.CLOUD_LLM_MODEL ?? "google/gemma-4-31b-it";
  if (!KEY || !SECRET) {
    return new Response(JSON.stringify({ error: "server missing LIVEKIT creds" }), {
      status: 500, headers: { ...cors, "Content-Type": "application/json" } });
  }

  try {
    const { message, history = [] } = (await req.json()) as {
      message: string; history?: { role: string; content: string }[];
    };
    const token = await inferenceToken(KEY, SECRET);

    const messages: any[] = [
      { role: "system", content: SYSTEM },
      ...history.slice(-8),
      { role: "user", content: message },
    ];

    // First call — model may request tool(s).
    let data = await callInference(token, { model: MODEL, messages, tools: TOOLS });
    let msg = data.choices[0].message;

    if (msg.tool_calls?.length) {
      messages.push(msg);
      for (const tc of msg.tool_calls) {
        const fn = TOOLS_IMPL[tc.function.name];
        const args = JSON.parse(tc.function.arguments || "{}");
        const result = fn ? fn(args) : { error: "unknown tool" };
        messages.push({ role: "tool", tool_call_id: tc.id, content: JSON.stringify(result) });
      }
      // Second call — turn tool results into a spoken reply.
      data = await callInference(token, { model: MODEL, messages });
      msg = data.choices[0].message;
    }

    return new Response(JSON.stringify({ reply: msg.content || "Woof! Could you say that again?" }), {
      status: 200, headers: { ...cors, "Content-Type": "application/json" } });
  } catch (e: any) {
    return new Response(JSON.stringify({ error: String(e?.message ?? e) }), {
      status: 500, headers: { ...cors, "Content-Type": "application/json" } });
  }
}

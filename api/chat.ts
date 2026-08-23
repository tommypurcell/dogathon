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
  kids: boolean; energy: string; blurb: string; slots: string[]; notes?: string;
};

const DOGS: Dog[] = [
  { name: "Max", breed: "Golden Retriever", age: "3 years", size: "large",
    kids: true, energy: "medium",
    blurb: "a gentle, family-friendly gentleman who loves fetch and nap time",
    slots: ["Tuesday from 12 to 2 PM", "Thursday from 3 to 5 PM"],
    notes: "Max is an absolute sweetheart. He's house-trained and crate-trained. Loves fetch but gets tired after about 20 mins of running. Prefers soft toys over hard ones. Gets along great with other dogs and kids. Slight separation anxiety if left alone for more than 3 hours—needs a buddy or regular check-ins. Loves swimming in the pool. Responds best to positive reinforcement and treats." },
  { name: "Scout", breed: "Appenzeller Mix", age: "2 years", size: "medium",
    kids: true, energy: "high",
    blurb: "a beach-loving adventurer, always up for a hike and a swim",
    slots: ["Wednesday from 10 AM to noon", "Saturday from 1 to 3 PM"],
    notes: "Scout is a bundle of pure energy! She needs at least an hour of serious exercise daily or she gets restless. Excellent swimmer and will jump in water at any opportunity. Very smart—already knows sit, stay, and come. Can be jumpy when excited, so she needs owners who are confident with high-energy dogs. Great with kids once she settles down. Eats quickly—use a slow feeder. Loves rope toys and tug-of-war. Leash-trained but pulls when excited." },
  { name: "Biscuit", breed: "Boston Terrier Mix", age: "5 years", size: "small",
    kids: true, energy: "low",
    blurb: "a mellow cuddle-bug, happiest on the couch — a great first dog",
    slots: ["Monday from 2 to 4 PM", "Friday from 11 AM to 1 PM"],
    notes: "Biscuit is the perfect apartment dog. Content with two short walks a day and plenty of couch time. Very well-behaved and doesn't bark much. Snores adorably. Gets cold easily, so he appreciates a sweater in winter. Loves treats and very food-motivated—easy to train. Gets along with everyone including cats (we've tested this!). Had a rough start before coming to us but is now a confident little guy. Needs regular ear cleanings due to breed tendency." },
  { name: "Rocky", breed: "Labrador Puppy", age: "5 months", size: "medium",
    kids: true, energy: "high",
    blurb: "a splashy little water pup, playful and eager to learn",
    slots: ["Thursday from 12 to 2 PM", "Sunday from 10 AM to noon"],
    notes: "Rocky is an absolute goofball! Super smart and picks up commands quickly. Still working on house-training (accidents happen, he's a pup). Mouths everything when teething—provide good chew toys. Will eat shoes if unsupervised. Needs TONS of exercise and playtime or he gets destructive. Water obsessed—splashes in his bowl and loves puddles. Responds amazingly to clicker training. Needs an owner ready for the puppy phase with patience and time. He'll be a magnificent dog once he grows up." },
  { name: "Daisy", breed: "Beagle", age: "4 years", size: "small",
    kids: true, energy: "medium",
    blurb: "a curious, floppy-eared sweetheart who follows her nose everywhere",
    slots: ["Monday from 10 AM to noon", "Wednesday from 2 to 4 PM"],
    notes: "Daisy is sweet but has a nose that runs the show! She will follow a scent and completely ignore you if not on a secure leash. Needs a fenced yard for safety. Howls instead of barks when excited—she's got serious beagle vocals. Food-motivated and smart, but gets distracted easily. Loves playing with other dogs. Can be stubborn during training but responds well to high-value treats. She's a counter-surfer so put food away! Great hiking and adventure companion if you keep her leashed." },
  { name: "Cooper", breed: "Corgi", age: "2 years", size: "small",
    kids: true, energy: "medium",
    blurb: "a stubby-legged charmer with a big personality and bigger ears",
    slots: ["Tuesday from 3 to 5 PM", "Saturday from 11 AM to 1 PM"],
    notes: "Cooper is an hilarious little guy with a huge personality. He herds everything including kids and other dogs by nipping at heels—classic Corgi behavior. Very loyal and loves being part of the family action. Barks at everything but it's more alert-barking than aggressive. Back issues are common in Corgis, so avoid too many stairs and jumps. Needs regular grooming—he sheds like crazy. Incredibly smart and food-motivated. Gets along great with kids and other pets. Loves play-fighting with appropriate toys. He'll keep you entertained and laughing daily." },
  { name: "Pepper", breed: "Dalmatian", age: "3 years", size: "large",
    kids: true, energy: "high",
    blurb: "a spotted bundle of energy who loves to run and show off",
    slots: ["Thursday from 10 AM to noon", "Sunday from 1 to 3 PM"],
    notes: "Pepper is a show-off and loves being the center of attention! Super friendly and playful. Needs serious daily exercise or she becomes a couch destroyer. Fast runner—she'll race anything. Gets along great with kids and other dogs, though she can be a bit much when she gets the zoomies. Sometimes thinks she's a lapdog despite her size. Responsive to training but gets bored easily—needs variety in her routine. Watch for ear infections given her floppy ears. She's a people-pleaser and thrives on interaction. Perfect for active families." },
  { name: "Koda", breed: "Siberian Husky", age: "4 years", size: "large",
    kids: false, energy: "high",
    blurb: "a talkative snow-lover who needs room to run — best with active adults",
    slots: ["Wednesday from 12 to 2 PM", "Saturday from 3 to 5 PM"],
    notes: "Koda is an escape artist and a talker—she 'woo woo woos' constantly! She needs experienced dog owners. Strong prey drive means she can't be trusted off-leash around small animals. Needs a SECURE fence—she's jumped 6-foot fences here. Requires 2+ hours of vigorous exercise daily or she's miserable. Loves snow and cold weather. Very pack-oriented and needs strong leadership. Can be stubborn but responds to consistent, firm training. Not suitable for apartments or families with young kids. She's a beautiful, athletic girl who will thrive with the right owner who can handle her intensity." },
  { name: "Bailey", breed: "Labrador Retriever", age: "6 years", size: "large",
    kids: true, energy: "low",
    blurb: "a calm, loyal senior who just wants a warm spot and a good belly rub",
    slots: ["Monday from 1 to 3 PM", "Friday from 10 AM to noon"],
    notes: "Bailey is a golden-hearted senior gentleman. He's house-trained, well-mannered, and just wants to be loved. Moves a bit slower these days and prefers short walks to long adventures. Has some arthritis—watch the stairs and jumping. Loves swimming as it's easier on his joints. Gets along perfectly with kids, other dogs, and cats. Very food-motivated but watch his weight. Needs a soft bed and regular exercise to stay comfortable. He's on a couple of supplements that his new family will need to manage. Bailey deserves a quiet, loving home for his golden years. He's the perfect match for someone who wants a gentle, devoted companion." },
  { name: "Coco", breed: "Toy Poodle", age: "1 year", size: "small",
    kids: true, energy: "medium",
    blurb: "a tiny, clever cloud of curls — smart, gentle, and hypoallergenic",
    slots: ["Tuesday from 10 AM to noon", "Thursday from 1 to 3 PM"],
    notes: "Coco is a little genius! She knows basic commands already and learns new tricks in minutes. Super sweet and gentle with kids. She's still a young girl so she has the zoomies sometimes, but nothing crazy. Needs grooming every 4-6 weeks—her curly coat doesn't shed but requires maintenance. Loves playing with small toys and cuddles. Gets a bit anxious if left alone too long—she's a companion dog. Good for apartment living. She jumped on the furniture when she first arrived but is learning boundaries. Coco would thrive with owners who appreciate a smart, affectionate little girl who's eager to please." },
  { name: "Otis", breed: "Pug", age: "5 years", size: "small",
    kids: true, energy: "low",
    blurb: "a wrinkly, snorty couch potato who lives for snacks and snuggles",
    slots: ["Wednesday from 3 to 5 PM", "Sunday from 11 AM to 1 PM"],
    notes: "Otis is the definition of a lap dog. He just wants to be with his people 24/7—no separation anxiety, just pure devotion. Very food motivated and a bit chubby (vet approved weight plan in progress). Snores and snorts adorably. Keep him cool in summer—Pugs overheat easily. Short walks are fine, no need for marathons. Gets along great with everyone including other dogs and cats. Watch his wrinkles and keep them clean to prevent infections. He's a little stubborn but responds well to treats and praise. Otis is perfect for someone who wants a devoted, cuddly companion who's happy to just exist by your side on the couch." },
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
      size: d.size, energy: d.energy, notes: d.notes || "",
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

/* Paws & Co. — talking Office Helper dog.
   Fully self-contained demo: browser speech-in, mock brain, browser speech-out.
   No backend, no API keys. Open index.html and talk. */

// ---------------------------------------------------------------------------
// Mock data — the adoptable dogs and their viewing availability.
// ---------------------------------------------------------------------------
// Adoptable dogs, loaded from dogs.json at startup (see init at bottom).
let DOGS = [];

function findDog(text) {
  const t = text.toLowerCase();
  return DOGS.find(d => t.includes(d.name.toLowerCase())) || null;
}

// Simple conversational memory so "book it" / "yes" knows which dog.
let context = { dog: null, offeredSlots: null };

// ---------------------------------------------------------------------------
// The "brain" — turn a user utterance into a spoken reply.
// ---------------------------------------------------------------------------
function respond(text) {
  const t = text.toLowerCase().trim();
  const dogInText = findDog(t);
  if (dogInText) context.dog = dogInText;

  // 1) Booking confirmation ("yes", "book it", "add me", "sounds good")
  if (/\b(yes|yeah|sure|book|schedule me|add me|sounds good|let's do|do it|first one|that works)\b/.test(t)
      && context.dog && context.offeredSlots) {
    // pick the slot they named (by day, time, or "first"/"second"), else the first offered
    const ordinals = ["first", "second", "third"];
    const ordinalIdx = ordinals.findIndex(w => t.includes(w));
    let slot = ordinalIdx >= 0 ? context.offeredSlots[ordinalIdx] : undefined;
    if (!slot) {
      slot = context.offeredSlots.find(s =>
        s.toLowerCase().split(/\s+/).some(word => word.length > 2 && t.includes(word)));
    }
    if (!slot) slot = context.offeredSlots[0];
    context.offeredSlots = null;
    return `You're all set! I've booked you to meet ${context.dog.name} on ${slot}. ` +
           `We'll see you at the shelter — ${context.dog.name} can't wait to meet you!`;
  }

  // 2) Scheduling / availability for a dog
  if (/\b(schedul|viewing|visit|come see|come in|meet|availab|times?|when can)\b/.test(t) && context.dog) {
    const d = context.dog;
    context.offeredSlots = d.slots;
    return `Great choice! ${d.name} is available ${d.slots[0]}, or ${d.slots[1]}. ` +
           `Would you like me to add one of those to the calendar?`;
  }

  // 3) Asking about a specific dog by name
  if (dogInText) {
    const d = dogInText;
    const kids = d.kids ? "great with kids" : "best with older kids or adults";
    return `${d.name} is a ${d.age} ${d.breed} — ${d.blurb}. ${cap(d.name)} is ${kids}. ` +
           `Would you like to schedule a time to meet ${d.name}?`;
  }

  // 6) Greetings only — everything else (breed/trait/family/"what dogs do you
  // have" questions) goes to the LLM brain, which has real tool access to the
  // same data and won't repeat the same canned sentence every time.
  if (/^\s*(hi|hello|hey|woof)\b/.test(t)) {
    return `Woof! Hi there, I'm Buddy, the office helper here at Paws and Co. ` +
           `Are you looking to adopt a furry friend today?`;
  }

  return null;  // nothing scripted matched -> caller falls back to the LLM brain
}

const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

// ---------------------------------------------------------------------------
// LLM fallback — ask Gemma 31B (via the local bridge) for anything not scripted.
// Keeps a short history so context carries across turns.
// ---------------------------------------------------------------------------
// On Vercel this hits the serverless function at /api/chat. For pure-local dev
// with the Python server, override by setting window.LLM_URL before app.js loads.
const LLM_URL = window.LLM_URL || "/api/chat";
let llmHistory = [];

async function askLLM(text) {
  // NOTE: does not mutate llmHistory itself — the caller (onresult) records
  // every turn uniformly, whether it was answered by the scripted brain or
  // the LLM, so context never gets dropped when the two hand off to each other.
  try {
    const r = await fetch(LLM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: llmHistory.slice(-12) }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const data = await r.json();
    return data.reply;
  } catch (e) {
    // Bridge not running — degrade gracefully to a helpful scripted line.
    return `I can tell you about our dogs or help you book a viewing. ` +
           `Try asking what dogs we have, or say a dog's name.`;
  }
}

// ---------------------------------------------------------------------------
// Speech synthesis (dog's voice)
// ---------------------------------------------------------------------------
function pickVoice() {
  const vs = speechSynthesis.getVoices();
  // Prefer high-quality neural voices in rough order of "sounds human".
  // macOS/iOS premium ("Siri"), then Google/Microsoft neural, then any en voice.
  const prefs = [
    /siri/i, /samantha \(enhanced\)/i, /ava \(premium\)|ava \(enhanced\)/i,
    /google us english/i, /microsoft .*(aria|jenny|guy).*online/i,
    /samantha/i, /allison|nicky|zoe/i,
  ];
  for (const re of prefs) {
    const v = vs.find(v => re.test(v.name) && v.lang.startsWith("en"));
    if (v) return v;
  }
  return vs.find(v => v.lang.startsWith("en-US")) || vs.find(v => v.lang.startsWith("en")) || null;
}

function speak(text, onEnd) {
  setState("speaking");
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1.0; u.pitch = 1.05;    // natural, a touch warm (not chipmunky)
  u.voice = pickVoice();
  u.onend = () => { onEnd && onEnd(); };
  speechSynthesis.cancel();
  speechSynthesis.speak(u);
}

// ---------------------------------------------------------------------------
// Speech recognition (user's mic) + conversation loop
// ---------------------------------------------------------------------------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null;
let running = false;
// Bumped every time listen() starts a new recognition session. Old sessions'
// onresult/onerror/onend are closures that can still fire asynchronously
// after being stopped/replaced (e.g. mute/interrupt racing a restart) — each
// callback checks it's still the current generation before acting, so a
// stale session can never double-process an utterance or spawn a duplicate.
let recogGen = 0;

const els = {
  body: document.body,
  status: document.getElementById("status"),
  btn: document.getElementById("talkBtn"),
  btnLabel: document.getElementById("btnLabel"),
  btnIcon: document.getElementById("btnIcon"),
  err: document.getElementById("err"),
  muteBtn: document.getElementById("muteBtn"),
  interruptBtn: document.getElementById("interruptBtn"),
};

let isMuted = false;

function setState(s, msg) {
  els.body.dataset.state = s;
  const labels = {
    idle: "Tap to start talking",
    listening: "Listening…",
    thinking: "Thinking…",
    speaking: "Speaking…",
  };
  els.status.textContent = msg || labels[s] || "";
}

function startConversation() {
  if (!SR) {
    els.err.textContent = "Speech recognition needs Chrome or Safari. Try opening this in Chrome.";
    return;
  }
  running = true;
  isMuted = false;
  els.btn.classList.add("end");
  els.btnLabel.textContent = "End";
  els.btnIcon.textContent = "⏹";
  els.muteBtn.disabled = false;
  els.muteBtn.classList.remove("active");
  els.muteBtn.textContent = "🔊 Mic On";
  els.interruptBtn.disabled = false;
  els.interruptBtn.textContent = "⏸️ Stop";
  // Greet first.
  speak("Woof! Hi there, I'm Buddy at Paws and Co. How can I help you find your new best friend?", listen);
}

function stopConversation() {
  running = false;
  isMuted = false;
  forceStopRecog();
  speechSynthesis.cancel();
  els.btn.classList.remove("end");
  els.btnLabel.textContent = "Start talking";
  els.btnIcon.textContent = "🎙️";
  els.muteBtn.disabled = true;
  els.muteBtn.classList.remove("active");
  els.muteBtn.textContent = "🔊 Mic On";
  els.interruptBtn.disabled = true;
  els.interruptBtn.textContent = "⏸️ Stop";
  setState("idle");
}

function listen() {
  if (!running || isMuted) return;
  // A prior session is still shutting down — let its onerror/onend chain
  // decide whether to restart, instead of stacking a second live recognizer.
  if (recog) return;
  const myGen = ++recogGen;
  setState("listening");
  recog = new SR();
  recog.lang = "en-US";
  recog.interimResults = false;
  recog.maxAlternatives = 1;

  recog.onresult = async (e) => {
    if (myGen !== recogGen) return;  // stale session — a newer one has taken over
    recog = null;
    const said = e.results[0][0].transcript;
    setState("thinking", `"${said}"`);
    // Try the instant scripted brain first; if nothing matches, ask Gemma 31B.
    let reply = respond(said);
    if (reply === null) {
      reply = await askLLM(said);
    }
    // Record EVERY turn (scripted or LLM) so context never drops when the two
    // brains hand off to each other.
    llmHistory.push({ role: "user", content: said });
    llmHistory.push({ role: "assistant", content: reply });
    if (!running) return;
    speak(reply, () => { if (running) listen(); });
  };
  recog.onerror = (e) => {
    if (myGen !== recogGen) return;  // stale session — ignore
    recog = null;
    if (!running) return;
    if (e.error === "no-speech" || e.error === "aborted") { listen(); return; }
    els.err.textContent = "Mic error: " + e.error;
  };
  recog.onend = () => {
    if (myGen !== recogGen) return;  // stale session — ignore
    recog = null;
  };

  try { recog.start(); } catch (e) { /* already started */ }
}

// Stops any in-flight recognition and immediately invalidates it, so its
// (still-pending, async) onresult/onerror/onend can never act — the caller
// is free to call listen() again right away without racing the old session.
function forceStopRecog() {
  recogGen++;
  const old = recog;
  recog = null;
  try { old && old.stop(); } catch (e) {}
}

els.btn.addEventListener("click", () => {
  if (!DOGS.length) return;  // wait until the dog data has loaded
  running ? stopConversation() : startConversation();
});

els.muteBtn.addEventListener("click", () => {
  if (!running) return;
  isMuted = !isMuted;
  if (isMuted) {
    forceStopRecog();
    els.muteBtn.classList.add("active");
    els.muteBtn.textContent = "🔇 Mic Muted";
    setState("idle", "Mic muted");
  } else {
    els.muteBtn.classList.remove("active");
    els.muteBtn.textContent = "🔊 Mic On";
    listen();
  }
});

els.interruptBtn.addEventListener("click", () => {
  if (!running) return;
  speechSynthesis.cancel();
  forceStopRecog();
  isMuted = false;
  els.muteBtn.classList.remove("active");
  els.muteBtn.textContent = "🔊 Mic On";
  setState("idle", "Interrupted—go ahead");
  listen();
});

// Some browsers load voices async.
speechSynthesis.onvoiceschanged = () => {};

// ---------------------------------------------------------------------------
// Load adoptable dogs from dogs.json, then enable the talk button.
// ---------------------------------------------------------------------------
els.btn.disabled = true;
fetch("dogs.json")
  .then((r) => r.json())
  .then((dogs) => {
    DOGS = dogs;
    els.btn.disabled = false;
    setState("idle");
  })
  .catch(() => {
    els.err.textContent = "Couldn't load our dogs. Please refresh.";
  });

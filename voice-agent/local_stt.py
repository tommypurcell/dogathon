"""Local speech-to-text adapter: faster-whisper bridged into LiveKit's STT interface.

LiveKit has no official on-device STT plugin, so we implement the base `stt.STT`
class ourselves. Verified against livekit-agents 1.7.0 installed source:
  - override `_recognize_impl(buffer, *, language, conn_options) -> SpeechEvent`
  - capabilities: non-streaming, offline
  - return SpeechEvent(FINAL_TRANSCRIPT, alternatives=[SpeechData(...)])

faster-whisper runs the Whisper model locally via CTranslate2. First run downloads
the model weights to a local cache (~/.cache/huggingface); after that it's offline.
"""

from __future__ import annotations

import asyncio

import numpy as np
from livekit.agents import stt, utils, APIConnectOptions
from livekit.agents.types import NOT_GIVEN, NotGivenOr


class LocalWhisperSTT(stt.STT):
    def __init__(
        self,
        *,
        model: str = "base.en",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "en",
    ) -> None:
        # Non-streaming: whisper transcribes a complete utterance at once.
        # The VAD upstream decides when an utterance is complete, then hands us
        # the buffered audio.
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        self._language = language

        # Import here so the module imports fast even if the model isn't cached yet.
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        # Combine the buffered frames into one, then to a float32 mono array
        # normalized to [-1, 1], which is what faster-whisper expects.
        frame = utils.combine_frames(buffer)
        samples = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0

        # faster-whisper resamples internally to 16k, but feeding 16k avoids extra
        # work. LiveKit frames carry their own sample_rate; whisper handles it.
        lang = language if language != NOT_GIVEN else self._language

        # WhisperModel.transcribe is blocking (CPU-bound) -> run off the event loop.
        def _transcribe() -> str:
            segments, _info = self._model.transcribe(
                samples,
                language=lang if lang else None,
                beam_size=5,          # a little search = far fewer misfires than greedy
                vad_filter=False,     # LiveKit's Silero VAD already gated this
                condition_on_previous_text=False,  # stop earlier garbage priming later garbage
            )
            # Confidence gate: drop segments Whisper isn't sure about. This is what
            # kills the "Frame." / "Um..." / hallucinated-fragment problem — those
            # come back with low avg_logprob or high no_speech_prob.
            kept = []
            for seg in segments:
                if getattr(seg, "no_speech_prob", 0.0) > 0.6:
                    continue  # probably silence/noise, not words
                if getattr(seg, "avg_logprob", 0.0) < -1.0:
                    continue  # low-confidence guess -> discard rather than send junk
                kept.append(seg.text)
            return "".join(kept).strip()

        text = await asyncio.get_event_loop().run_in_executor(None, _transcribe)

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(language=lang or "en", text=text)
            ],
        )

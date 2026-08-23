"""Local text-to-speech adapter: kokoro-onnx bridged into LiveKit's TTS interface.

LiveKit has no official on-device TTS plugin, so we implement the base `tts.TTS`
class ourselves. Verified against livekit-agents 1.7.0 installed source:
  - TTS.__init__ requires capabilities, sample_rate, num_channels
  - override synthesize(text) -> ChunkedStream
  - ChunkedStream._run(emitter) drives an AudioEmitter:
        emitter.initialize(request_id, sample_rate, num_channels, mime_type)
        emitter.push(pcm_int16_bytes)   # repeatedly
        emitter.flush()

Kokoro (verified): Kokoro(model_path, voices_path).create(text, voice) -> (float32, 24000)
and an async create_stream(...) generator we use for lower latency. Requires the
model + voices files on disk and `espeak-ng` installed for phonemization.
"""

from __future__ import annotations

import numpy as np
from livekit.agents import tts, utils, APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

KOKORO_SAMPLE_RATE = 24000
NUM_CHANNELS = 1


class LocalKokoroTTS(tts.TTS):
    def __init__(
        self,
        *,
        model_path: str,
        voices_path: str,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=KOKORO_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._voice = voice
        self._speed = speed
        self._lang = lang

        from kokoro_onnx import Kokoro

        self._kokoro = Kokoro(model_path, voices_path)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "_KokoroChunkedStream":
        return _KokoroChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _KokoroChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: LocalKokoroTTS, input_text: str, conn_options) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
        )

        # Stream sentence-by-sentence so audio starts playing before the whole
        # utterance is synthesized -> much lower perceived latency.
        stream = self._tts._kokoro.create_stream(
            self._input_text,
            voice=self._tts._voice,
            speed=self._tts._speed,
            lang=self._tts._lang,
        )
        async for samples, _sr in stream:
            # kokoro returns float32 in [-1, 1]; LiveKit wants int16 PCM bytes.
            pcm = np.clip(samples, -1.0, 1.0)
            pcm = (pcm * 32767.0).astype(np.int16)
            output_emitter.push(pcm.tobytes())

        output_emitter.flush()

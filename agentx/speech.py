"""
Voice intake — describing a problem out loud instead of typing it.

Typing a coherent account of what went wrong is the first barrier a consumer
complaint system puts in front of the person it claims to help, and it selects
hard: against people who are angry, in a hurry, on a phone, dyslexic, elderly, or
composing in a language they read better than they write. Speaking is what those
people can already do. So `/agentx` takes dictation, and the transcript enters the
existing intake path unchanged — the same understanding, the same evidence graph,
the same chain.

TWO TRANSCRIBERS, AND THE DEFAULT IS THE PRIVATE ONE

    device   the browser's own speech recognition. Audio never leaves the
             machine; only text reaches Agent X. No key, no cost, no network
             call from this server, and nothing for us to leak.
    live     a hosted speech-to-text API, registered ONLY when its key is
             configured — for browsers with no built-in recogniser.

This mirrors `execution/providers/live_providers.py` exactly: a live transcriber
self-registers when its own configuration is present and is otherwise honestly
absent, never a silent fallback. `mode` travels with every transcript so a caller
always knows whether audio left the device.

AUDIO IS NEVER STORED. ANYWHERE.

Not in the database, not on disk, not in a log, not as case evidence. It is held
in memory for the length of one request and discarded. This is not caution for its
own sake — a voice recording is biometric-adjacent personal data, and Agent X is a
product whose central claim is that it can prove what it erased. The only way to
make that claim about audio without building a whole second erasure path for it is
to never retain it. The TRANSCRIPT is what becomes case evidence, sealed under the
case's own key and crypto-shreddable like everything else.

WHAT THIS DOES NOT CLAIM

It does not claim multilingual understanding. A transcriber will happily return
fluent Tamil or Portuguese, and `understanding.py`'s catalogue is English lexical —
so a non-English transcript classifies badly. `language_note()` says so plainly
rather than letting a confident-looking misclassification through.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict

# Upper bound on a single dictation. Long enough for a rambling account of a
# dispute, short enough that one request cannot pin a worker or a paid API.
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 120

# Container types a browser's MediaRecorder actually produces.
ALLOWED_AUDIO = {
    "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg",
    "audio/wav", "audio/x-wav", "audio/m4a", "audio/flac", "",
}


@dataclass(frozen=True)
class Transcript:
    """What was said, and how it was heard."""
    text: str
    mode: str                    # device | live
    provider: str
    language: str | None = None
    note: str | None = None

    @property
    def audio_left_the_device(self) -> bool:
        return self.mode == "live"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["audio_left_the_device"] = self.audio_left_the_device
        d["audio_retained"] = False      # invariant, stated in the payload
        return d


class Transcriber:
    """One way of turning speech into text."""
    id = "transcriber"
    mode = "live"
    label = "Speech to text"

    @classmethod
    def configured(cls) -> bool:
        return False

    def transcribe(self, audio: bytes, *, media_type: str = "",
                   language: str | None = None) -> Transcript:
        raise NotImplementedError


class GroqWhisperTranscriber(Transcriber):
    """Hosted Whisper, for browsers with no recogniser of their own.

    Registered only when `AGENT_X_GROQ_API_KEY` is set. Audio is streamed to the
    API and dropped; nothing is written down at either end of this function.
    """
    id = "live:groq-whisper"
    mode = "live"
    label = "Whisper (hosted)"

    ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
    MODEL = os.environ.get("AGENT_X_SPEECH_MODEL", "whisper-large-v3-turbo")

    @classmethod
    def configured(cls) -> bool:
        return bool(os.environ.get("AGENT_X_GROQ_API_KEY"))

    def transcribe(self, audio: bytes, *, media_type: str = "",
                   language: str | None = None) -> Transcript:
        import httpx

        key = os.environ.get("AGENT_X_GROQ_API_KEY")
        if not key:
            raise RuntimeError("no speech provider is configured")

        files = {"file": ("dictation.webm", audio, media_type or "audio/webm")}
        data = {"model": self.MODEL, "response_format": "json"}
        if language:
            data["language"] = language

        with httpx.Client(timeout=60.0) as client:
            r = client.post(self.ENDPOINT, headers={"Authorization": f"Bearer {key}"},
                            files=files, data=data)
            r.raise_for_status()
            payload = r.json()

        return Transcript(text=(payload.get("text") or "").strip(),
                          mode=self.mode, provider=self.id,
                          language=language or payload.get("language"))


_TRANSCRIBERS: list[type[Transcriber]] = [GroqWhisperTranscriber]


def server_transcriber() -> Transcriber | None:
    """The configured server-side transcriber, or None. None is the normal case."""
    for cls in _TRANSCRIBERS:
        if cls.configured():
            return cls()
    return None


def availability() -> dict:
    """What voice intake can do on this deployment — for /health and the UI.

    The browser path is always reported as available because whether it works is
    a fact about the visitor's browser, not about this server. The UI feature-
    detects and falls back; this only says what the SERVER can offer.
    """
    server = server_transcriber()
    return {
        "voice_intake": True,
        "device_recognition": {
            "available": True,
            "detail": "The browser transcribes locally; audio never leaves the "
                      "device and this server never receives it.",
        },
        "server_transcription": {
            "available": server is not None,
            "provider": server.id if server else None,
            "detail": (f"{server.label} is configured. Audio is sent for "
                       f"transcription and never stored."
                       if server else
                       "No server-side transcriber is configured. Browsers "
                       "without built-in speech recognition cannot dictate; "
                       "typing always works."),
        },
        "audio_retained": False,
        "max_seconds": MAX_SECONDS,
    }


def transcribe(audio: bytes, *, media_type: str = "",
               language: str | None = None) -> Transcript:
    """Transcribe one dictation server-side. Raises if nothing is configured."""
    if not audio:
        raise ValueError("no audio was received")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError(f"audio exceeds {MAX_AUDIO_BYTES // (1024 * 1024)}MB")
    if media_type and media_type.split(";")[0].strip() not in ALLOWED_AUDIO:
        raise ValueError(f"unsupported audio type {media_type!r}")

    transcriber = server_transcriber()
    if transcriber is None:
        raise RuntimeError(
            "no server-side transcriber is configured on this deployment; "
            "use the browser's own speech recognition, or type instead")
    return transcriber.transcribe(audio, media_type=media_type, language=language)


# ─────────────────────────────────────────────────────────────────────────────
# honesty about language
# ─────────────────────────────────────────────────────────────────────────────
# Dictation makes it trivial to speak a language the classifier cannot read. The
# ontology is English lexical, so a Tamil or Portuguese transcript produces a
# confident-looking result built on almost no lexical evidence. Detecting the
# script is cheap and catches the clearest case; saying nothing would not.
_NON_LATIN = re.compile(
    r"[ऀ-ॿ"      # Devanagari
    r"஀-௿"       # Tamil
    r"ఀ-౿"       # Telugu
    r"ঀ-৿"       # Bengali
    r"؀-ۿ"       # Arabic
    r"一-鿿"       # CJK
    r"぀-ヿ"       # Kana
    r"가-힯]"      # Hangul
)


def language_note(text: str, language: str | None = None) -> str | None:
    """A warning when a transcript is in a language the catalogue cannot read.

    Returns None when there is nothing to say, which is the common case.
    """
    if not text.strip():
        return None
    non_latin = len(_NON_LATIN.findall(text))
    declared_other = bool(language and not language.lower().startswith("en"))
    if non_latin >= 4 or declared_other:
        return ("This was transcribed in a language Agent X's problem catalogue "
                "does not read — it matches problems by English wording. Your "
                "words are kept exactly as spoken and stored as evidence, but the "
                "problem type may be wrong. Describing it in English as well will "
                "give a better result.")
    return None

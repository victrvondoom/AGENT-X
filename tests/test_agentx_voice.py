"""
Voice intake — and the promise that nothing is kept.

Speaking a complaint is the accessibility win; not retaining the recording is the
part that has to be true for it to belong in this product at all. Agent X's whole
claim is that it can prove what it erased, and a voice clip is biometric-adjacent
personal data. The way to keep that claim without building a second erasure path
for audio is to never store audio — so the tests below assert absence, not
behaviour.
"""
from __future__ import annotations

import re

import pytest

from agentx import speech


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch):
    """Default posture: no hosted transcriber configured."""
    monkeypatch.delenv("AGENT_X_GROQ_API_KEY", raising=False)
    yield


# ─────────────────────────────────────────────────── availability
def test_device_recognition_is_always_offered():
    """Whether the browser can transcribe is a fact about the visitor, not this
    server, so the server never claims it is impossible."""
    a = speech.availability()
    assert a["voice_intake"] is True
    assert a["device_recognition"]["available"] is True


def test_server_transcription_absent_unless_configured():
    assert speech.server_transcriber() is None
    assert speech.availability()["server_transcription"]["available"] is False


def test_server_transcription_self_registers_when_configured(monkeypatch):
    """Same discipline as `live:smtp`: present and used, or absent and said so."""
    monkeypatch.setenv("AGENT_X_GROQ_API_KEY", "test-key")
    t = speech.server_transcriber()
    assert t is not None and t.mode == "live"
    assert speech.availability()["server_transcription"]["provider"] == t.id


def test_availability_states_that_audio_is_not_retained():
    assert speech.availability()["audio_retained"] is False


# ─────────────────────────────────────────────────── refusals
def test_transcribe_without_a_provider_refuses_rather_than_degrading():
    with pytest.raises(RuntimeError) as e:
        speech.transcribe(b"\x00\x01", media_type="audio/webm")
    assert "no server-side transcriber" in str(e.value)


def test_empty_audio_is_rejected():
    with pytest.raises(ValueError):
        speech.transcribe(b"", media_type="audio/webm")


def test_oversized_audio_is_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_X_GROQ_API_KEY", "k")
    with pytest.raises(ValueError) as e:
        speech.transcribe(b"x" * (speech.MAX_AUDIO_BYTES + 1), media_type="audio/webm")
    assert "exceeds" in str(e.value)


def test_unsupported_media_type_is_rejected(monkeypatch):
    monkeypatch.setenv("AGENT_X_GROQ_API_KEY", "k")
    with pytest.raises(ValueError):
        speech.transcribe(b"MZ\x90", media_type="application/x-msdownload")


def test_browser_recorder_content_types_are_accepted(monkeypatch):
    """MediaRecorder tags its output with a codec suffix; the check must not
    reject `audio/webm;codecs=opus` for having one."""
    monkeypatch.setenv("AGENT_X_GROQ_API_KEY", "k")
    sent = {}

    class Fake(speech.Transcriber):
        id, mode = "live:fake", "live"

        @classmethod
        def configured(cls):
            return True

        def transcribe(self, audio, *, media_type="", language=None):
            sent["type"] = media_type
            return speech.Transcript(text="ok", mode="live", provider=self.id)

    monkeypatch.setattr(speech, "_TRANSCRIBERS", [Fake])
    speech.transcribe(b"aa", media_type="audio/webm;codecs=opus")
    assert sent["type"] == "audio/webm;codecs=opus"


# ─────────────────────────────────────────────────── the retention promise
def test_transcript_reports_whether_audio_left_the_device():
    assert speech.Transcript("hi", "device", "browser").audio_left_the_device is False
    assert speech.Transcript("hi", "live", "x").audio_left_the_device is True


def test_transcript_payload_states_audio_is_not_retained():
    d = speech.Transcript("hi", "live", "x").as_dict()
    assert d["audio_retained"] is False


def test_no_module_path_writes_audio_anywhere():
    """A structural check on the promise.

    `speech.py` must contain no file-write, no database insert, and no storage
    call. Asserting the absence of the capability is stronger than asserting that
    one particular caller happens not to use it — a future contributor adding a
    "just cache the clip" line fails here.
    """
    import inspect
    src = inspect.getsource(speech)
    forbidden = (r"\bopen\s*\(", r"\.write\s*\(", r"INSERT\s+INTO",
                 r"\bstore\b", r"tempfile", r"\.save\s*\(")
    for pattern in forbidden:
        assert not re.search(pattern, src, re.I), (
            f"speech.py appears to persist audio ({pattern}); it must not")


# ─────────────────────────────────────────────────── language honesty
def test_english_transcript_gets_no_warning():
    assert speech.language_note("I was charged twice for the same order") is None


def test_non_latin_transcript_is_flagged():
    """A transcriber returns fluent Tamil; the catalogue reads English wording.
    Saying nothing would let a confident misclassification through."""
    note = speech.language_note("என் விமானம் ரத்து செய்யப்பட்டது மற்றும் பணம் திரும்ப மறுக்கிறார்கள்")
    assert note and "does not read" in note


def test_declared_non_english_language_is_flagged():
    assert speech.language_note("meu voo foi cancelado", language="pt-BR")


def test_empty_transcript_has_nothing_to_warn_about():
    assert speech.language_note("") is None
    assert speech.language_note("   ") is None


# ─────────────────────────────────────────────────── HTTP surface
def test_endpoint_reports_501_when_nothing_is_configured():
    from fastapi.testclient import TestClient
    from app.main import app

    import os
    token = os.environ.get("AGENT_X_AUTH_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = TestClient(app).post("/api/agentx/voice/transcribe",
                             files={"file": ("a.webm", b"xx", "audio/webm")},
                             headers=headers)
    assert r.status_code == 501
    assert r.json()["detail"]["error"] == "no_server_transcriber"


def test_health_publishes_voice_capability():
    from fastapi.testclient import TestClient
    from app.main import app

    v = TestClient(app).get("/api/agentx/health").json()["voice"]
    assert v["device_recognition"]["available"] is True
    assert v["audio_retained"] is False

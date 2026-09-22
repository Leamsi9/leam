"""Bounded local speech wire format; no audio, text or credentials are persisted."""

import base64
import binascii
import math
import struct
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .voice_catalog import STOCK_VOICES

SAMPLE_RATE = 16000
OUTPUT_RATE = 24000
MAX_SECONDS = 90
MAX_OUTPUT_SECONDS = 45


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Capture(Input):
    captureId: UUID
    sequence: int = Field(ge=0, le=180)
    pcm: str = Field(default="", max_length=85336)
    sampleRate: Literal[16000] = SAMPLE_RATE
    finish: bool = False
    language: str = Field(default="en-GB", min_length=2, max_length=40)

    @field_validator("language")
    @classmethod
    def english(cls, value):
        if value.split("-")[0].lower() != "en":
            raise ValueError("The installed local recognition model supports English")
        return value

    def samples(self):
        try:
            raw = base64.b64decode(self.pcm, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid PCM encoding") from None
        if len(raw) % 4 or len(raw) > SAMPLE_RATE * 4:
            raise ValueError("Each PCM chunk must contain at most one second")
        values = [v[0] for v in struct.iter_unpack("<f", raw)]
        if any(not math.isfinite(v) or abs(v) > 1 for v in values):
            raise ValueError("PCM samples must be finite and between -1 and 1")
        return values


class Cancel(Input):
    captureId: UUID


class Speak(Input):
    text: str = Field(min_length=1, max_length=300)
    voice: str = Field(default="alba", min_length=1, max_length=80)

    @field_validator("voice")
    @classmethod
    def stock_voice(cls, value):
        if value not in STOCK_VOICES:
            raise ValueError("Only installed stock voices are supported")
        return value

    language: str = Field(default="en-GB", min_length=2, max_length=40)

    @field_validator("language")
    @classmethod
    def english(cls, value):
        return Capture.english(value)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Speech text is empty")
        return value

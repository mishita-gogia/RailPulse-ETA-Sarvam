"""Pydantic schemas for Sarvam AI Assistant."""

from pydantic import BaseModel, Field
from typing import Optional


class SarvamChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="Natural language user query")
    language: Optional[str] = Field("auto", description="Preferred response language (e.g. 'auto', 'hi', 'en')")


class SarvamChatResponse(BaseModel):
    response: str = Field(..., description="Assistant natural language response")
    language: str = Field("auto", description="Response language detected/used")
    train_number: Optional[str] = Field(None, description="Identified train number if applicable")
    source: str = Field("railpulse", description="Authoritative data source (railpulse)")
    audio_base64: Optional[str] = Field(None, description="Optional base64-encoded audio for text-to-speech")
    localized_text: Optional[str] = Field(None, description="Optional localized/translated speech text")


class SarvamTTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1500, description="Text to synthesize to speech")
    language_code: Optional[str] = Field("hi-IN", description="Language code for speech (e.g. 'hi-IN', 'en-IN')")


class SarvamTTSResponse(BaseModel):
    audio_base64: str = Field(..., description="Base64 encoded audio string")
    format: str = Field("mp3", description="Audio format")
    localized_text: Optional[str] = Field(None, description="Optional localized/translated speech text")


class SarvamTranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=1500, description="Text to translate")
    target_language_code: str = Field(..., description="Target language BCP-47 code (e.g. 'hi-IN')")
    source_language_code: Optional[str] = Field("auto", description="Source language BCP-47 code or 'auto'")


class SarvamTranslateResponse(BaseModel):
    translated_text: str = Field(..., description="Translated text")
    target_language_code: str = Field(..., description="Target language BCP-47 code")

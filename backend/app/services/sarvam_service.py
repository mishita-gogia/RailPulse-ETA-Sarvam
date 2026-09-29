"""Sarvam AI Service - Natural Language & Voice Assistant for RailPulse ETA.

This service is the natural language / accessibility layer.
RailPulse backend and ETA engine remain the authoritative source of truth.
"""

import os
import re
import logging
from typing import Optional, Dict, Any, Tuple
from app.config import settings

logger = logging.getLogger(__name__)


class SarvamService:
    """Service wrapping the official Sarvam AI SDK for RailPulse."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key if api_key is not None else settings.SARVAM_API_KEY
        self.client = None
        self._initialize_client()

    def _initialize_client(self):
        """Safely initialize the SarvamAI client without exposing the key."""
        if not self._api_key:
            logger.warning("[SarvamService] SARVAM_API_KEY not found. Fallback mode will be used.")
            return

        try:
            from sarvamai import SarvamAI
            self.client = SarvamAI(api_subscription_key=self._api_key)
            logger.info("[SarvamService] Initialized SarvamAI official client.")
        except Exception as e:
            logger.error(f"[SarvamService] Failed to initialize SarvamAI client: {type(e).__name__}")
            self.client = None

    def is_available(self) -> bool:
        """Check whether the Sarvam AI client is configured and available."""
        return self.client is not None

    def extract_train_number(self, text: str) -> Optional[str]:
        """Extract a 5-digit Indian Railways train number from text if present."""
        match = re.search(r'\b(\d{5})\b', text)
        if match:
            return match.group(1)
        return None

    def generate_chat_response(
        self,
        user_message: str,
        railpulse_context: Dict[str, Any],
        language: str = "auto",
    ) -> Tuple[str, str]:
        """Generate a natural-language response using Sarvam chat completion grounded on RailPulse data.

        Returns (response_text, language_used).
        """
        # If Sarvam client is not available, immediately use factual template fallback
        if not self.is_available():
            fallback_text = self._build_template_fallback(user_message, railpulse_context)
            return fallback_text, "en"

        # Build grounded system prompt
        system_prompt = (
            "You are RailPulse Assistant, an AI railway assistant for Indian Railways. "
            "Your task is to answer passenger and staff inquiries clearly, politely, and factually.\n\n"
            "STRICT OPERATIONAL RULES:\n"
            "1. Answer ONLY using the factual RailPulse Ground Truth Data provided below.\n"
            "2. NEVER invent, hallucinate, or recalculate train locations, arrival times, delays, or speeds.\n"
            "3. If a train is not found or data is missing, politely say so based on the provided status.\n"
            "4. Respond in the same language or dialect as the user's inquiry (e.g. English, Hindi, or Hinglish).\n"
            "5. If telemetry is marked as simulated, do NOT claim it is an authorized live GPS feed.\n"
            "6. Keep answers concise, helpful, and natural (2 to 4 sentences)."
        )

        context_summary = self._format_context_for_prompt(railpulse_context)
        prompt_content = f"RailPulse Ground Truth Data:\n{context_summary}\n\nUser Question:\n{user_message}"

        try:
            response = self.client.chat.completions(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_content},
                ],
                model="sarvam-105b-conversations",
                temperature=0.2,
                max_tokens=400,
            )
            reply = response.choices[0].message.content.strip()
            return reply, language if language != "auto" else "multilingual"
        except Exception as e:
            logger.warning(f"[SarvamService] Chat completion call failed: {type(e).__name__}. Falling back to template.")
            fallback = self._build_template_fallback(user_message, railpulse_context)
            return fallback, "en"

    def translate_text(
        self,
        text: str,
        target_language_code: str,
        source_language_code: str = "auto",
    ) -> Optional[str]:
        """Translate text into target Indian language using Sarvam Translate.

        Preserves international numerals (train numbers, delays, times).
        """
        if not self.is_available():
            return None

        clean = text.strip()
        if not clean:
            return ""

        # English to English requires no translation
        if target_language_code in ("en-IN", "en"):
            return clean

        try:
            res = self.client.text.translate(
                input=clean[:1000],
                source_language_code=source_language_code,
                target_language_code=target_language_code,
                numerals_format="international",
            )
            if res and hasattr(res, "translated_text") and res.translated_text:
                return res.translated_text.strip()
            return None
        except Exception as e:
            logger.warning(f"[SarvamService] Translation failed: {type(e).__name__}: {e}")
            return None

    def synthesize_speech(
        self,
        text: str,
        language_code: str = "hi-IN",
    ) -> Optional[str]:
        """Convert text to speech using Sarvam Bulbul model.

        Returns base64 audio string or None if failed.
        """
        if not self.is_available():
            return None

        # Supported language codes: 'en-IN', 'hi-IN', 'bn-IN', 'ta-IN', 'te-IN', etc.
        valid_lang = language_code if language_code in [
            "en-IN", "hi-IN", "bn-IN", "ta-IN", "te-IN", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "od-IN"
        ] else "hi-IN"

        try:
            tts_res = self.client.text_to_speech.convert(
                text=text[:500],  # Bound text length for TTS safety
                language_code=valid_lang,
                model="bulbul:v3",
            )
            if tts_res and tts_res.audios:
                return tts_res.audios[0]
            return None
        except Exception as e:
            logger.warning(f"[SarvamService] Text-to-speech failed: {type(e).__name__}")
            return None

    def transcribe_audio(
        self,
        audio_file_or_bytes: Any,
        language_code: str = "unknown",
    ) -> Optional[str]:
        """Transcribe speech to text using Sarvam Saaras model.

        Returns transcribed text or None.
        """
        if not self.is_available():
            return None

        try:
            stt_res = self.client.speech_to_text.transcribe(
                file=audio_file_or_bytes,
                model="saaras:v3",
                language_code=language_code if language_code != "auto" else "unknown",
            )
            if stt_res and stt_res.transcript:
                return stt_res.transcript.strip()
            return None
        except Exception as e:
            logger.warning(f"[SarvamService] Speech-to-text failed: {type(e).__name__}")
            return None

    def _format_context_for_prompt(self, ctx: Dict[str, Any]) -> str:
        """Format the factual RailPulse context dictionary for the LLM prompt."""
        if not ctx:
            return "No specific train data queried. General railway assistant mode."

        if ctx.get("found") is False:
            train_num = ctx.get("train_number", "Unknown")
            return f"Status: Train {train_num} was NOT found in the RailPulse catalog or database."

        if "train" in ctx:
            t = ctx["train"]
            pos = ctx.get("position") or {}
            etas = ctx.get("etas") or []

            lines = [
                f"Train: {t.get('train_number')} - {t.get('train_name')} ({t.get('train_type', 'Express')})",
                f"Route: {t.get('source')} ({t.get('source_code')}) to {t.get('destination')} ({t.get('destination_code')})",
                f"Current Location: {pos.get('current_location') or pos.get('current_station_name') or 'En route'}",
                f"Status: {pos.get('status', 'Running')} | Delay: {pos.get('delay_minutes', 0)} minutes",
                f"Current Speed: {pos.get('speed_kmh', 0)} km/h",
                f"Telemetry Source: {pos.get('telemetry_source', 'Simulated Telemetry (No Authorized Live Feed)')}",
            ]

            if etas:
                next_stop = etas[0]
                lines.append(
                    f"Next Stop: {next_stop.get('station_name')} ({next_stop.get('station_code')}) - "
                    f"Scheduled: {next_stop.get('scheduled_arrival')}, Predicted: {next_stop.get('predicted_arrival')} "
                    f"(Predicted Delay: {next_stop.get('predicted_delay_minutes', 0)} mins, Confidence: {next_stop.get('confidence_level', 'High')})"
                )
                if len(etas) > 1:
                    later_stops = [
                        f"{s.get('station_name')}: {s.get('predicted_arrival')} (+{s.get('predicted_delay_minutes', 0)}m)"
                        for s in etas[1:4]
                    ]
                    lines.append(f"Upcoming Stops: {', '.join(later_stops)}")

            return "\n".join(lines)

        if "kpis" in ctx:
            k = ctx["kpis"]
            return (
                f"Network Overview: Active Trains: {k.get('active_trains', 0)}, "
                f"On Time: {k.get('on_time', 0)}, Delayed: {k.get('delayed', 0)}, "
                f"Critical Delay: {k.get('critical', 0)}, Average Delay: {k.get('avg_delay_minutes', 0)} mins, "
                f"Active Alerts: {k.get('active_alerts', 0)}."
            )

        return str(ctx)

    def _build_template_fallback(self, query: str, ctx: Dict[str, Any]) -> str:
        """Deterministic template-based response for when Sarvam API is unreachable or disabled."""
        if not ctx or ("train" not in ctx and "kpis" not in ctx and not ctx.get("train_number")):
            return (
                "Namaste! I am the RailPulse Assistant. You can ask me about train positions, delays, and ETAs. "
                "For example: 'Where is train 12951?' or '12951 kab aayegi?'"
            )

        if ctx.get("found") is False:
            train_num = ctx.get("train_number", "")
            return f"Train {train_num} was not found in the RailPulse catalog. Please check the 5-digit train number and try again."

        if "train" in ctx:
            t = ctx["train"]
            pos = ctx.get("position") or {}
            etas = ctx.get("etas") or []

            train_no = t.get("train_number")
            train_name = t.get("train_name")
            loc = pos.get("current_location") or pos.get("current_station_name") or "en route"
            delay = pos.get("delay_minutes", 0)
            status = pos.get("status", "Running")

            res = f"Train {train_no} ({train_name}) is currently at {loc}. Running status: {status} with a current delay of {delay} minutes."
            if etas:
                next_stop = etas[0]
                res += (
                    f" Next predicted arrival is at {next_stop.get('station_name')} at "
                    f"{next_stop.get('predicted_arrival')} (predicted delay: {next_stop.get('predicted_delay_minutes')} mins)."
                )
            return res

        if "kpis" in ctx:
            k = ctx["kpis"]
            return (
                f"RailPulse Network Status: {k.get('active_trains', 0)} active trains monitored. "
                f"{k.get('on_time', 0)} on time, {k.get('delayed', 0)} delayed. Average network delay is {k.get('avg_delay_minutes', 0)} minutes."
            )

        return "RailPulse train information retrieved successfully. Please refer to the Live Trains view for full route details."


# Singleton instance
sarvam_service = SarvamService()

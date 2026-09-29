"""Sarvam AI Assistant API Endpoints.

Provides authenticated natural-language and voice railway inquiry assistance.
All data is grounded strictly on RailPulse backend facts and ML ETAs.
Operational actions (simulation, event injection) are strictly forbidden here.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from typing import Optional, Dict, Any
from app.api.auth_deps import get_current_user
from app.models.user_model import User
from app.models.sarvam_schemas import (
    SarvamChatRequest,
    SarvamChatResponse,
    SarvamTTSRequest,
    SarvamTTSResponse,
    SarvamTranslateRequest,
    SarvamTranslateResponse,
)
from app.services.sarvam_service import sarvam_service
from app.services.train_service import train_service
from app.services.eta_service import eta_service

router = APIRouter()


async def _fetch_railpulse_context(query: str) -> Dict[str, Any]:
    """Retrieve ground-truth train facts from the authoritative RailPulse services."""
    ctx: Dict[str, Any] = {}

    # 1. Check for explicit 5-digit train number
    train_num = sarvam_service.extract_train_number(query)

    # 2. If no number, check if train name was mentioned (e.g. Rajdhani, Shatabdi)
    if not train_num:
        lower_q = query.lower()
        if any(w in lower_q for w in ["rajdhani", "shatabdi", "duronto", "tejas", "garib rath", "vande bharat", "express"]):
            # Search train service
            search_res = await train_service.search_trains(query, limit=1)
            if search_res:
                train_num = search_res[0].get("train_number")

    if train_num:
        train = await train_service.get_train(train_num)
        if train:
            pos = await train_service.get_train_position(train_num)
            etas = await eta_service.calculate_all_upcoming_etas(train_num)
            ctx["found"] = True
            ctx["train_number"] = train_num
            ctx["train"] = train
            ctx["position"] = pos
            ctx["etas"] = etas
        else:
            ctx["found"] = False
            ctx["train_number"] = train_num
        return ctx

    # 3. Check if user is asking for general system status, delays, or network overview
    lower_q = query.lower()
    if any(k in lower_q for k in ["delay", "status", "active", "how many", "network", "overview", "late", "kpi"]):
        kpis = await train_service.get_kpis()
        ctx["kpis"] = kpis
        return ctx

    return ctx


@router.post("/chat", response_model=SarvamChatResponse)
async def chat_with_assistant(
    request: SarvamChatRequest,
    current_user: User = Depends(get_current_user),
):
    """Interact with RailPulse Assistant using natural language (English, Hindi, Hinglish).

    Requires an authenticated PASSENGER or RAILWAY_STAFF session.
    Strictly informational; does not execute operational mutations.
    """
    clean_msg = request.message.strip()
    if not clean_msg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty",
        )

    # Gather ground-truth context from RailPulse backend
    context = await _fetch_railpulse_context(clean_msg)
    train_number = context.get("train_number")

    # Generate grounded natural-language response using Sarvam SDK
    reply_text, detected_lang = sarvam_service.generate_chat_response(
        user_message=clean_msg,
        railpulse_context=context,
        language=request.language or "auto",
    )

    return SarvamChatResponse(
        response=reply_text,
        language=detected_lang,
        train_number=train_number,
        source="railpulse",
    )


@router.post("/tts", response_model=SarvamTTSResponse)
async def text_to_speech(
    request: SarvamTTSRequest,
    current_user: User = Depends(get_current_user),
):
    """Generate audio speech (TTS) using Sarvam Bulbul model.

    Optional feature for accessibility; returns base64 encoded audio.
    Localizes/translates English factual text into selected regional language before TTS.
    """
    clean_text = request.text.strip()
    if not clean_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text cannot be empty",
        )

    target_lang = request.language_code or "hi-IN"

    # Regional language selection: translate/localize into target language before TTS
    speech_text = clean_text
    if target_lang != "en-IN":
        translated = sarvam_service.translate_text(
            text=clean_text,
            target_language_code=target_lang,
            source_language_code="auto",
        )
        if translated:
            speech_text = translated

    audio_b64 = sarvam_service.synthesize_speech(
        text=speech_text,
        language_code=target_lang,
    )

    if not audio_b64:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech synthesis is temporarily unavailable",
        )

    return SarvamTTSResponse(
        audio_base64=audio_b64,
        format="mp3",
        localized_text=speech_text if speech_text != clean_text else None,
    )


@router.post("/translate", response_model=SarvamTranslateResponse)
async def translate_text(
    request: SarvamTranslateRequest,
    current_user: User = Depends(get_current_user),
):
    """Translate text into selected Indian language using Sarvam Translate."""
    clean_text = request.text.strip()
    if not clean_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text cannot be empty",
        )

    translated = sarvam_service.translate_text(
        text=clean_text,
        target_language_code=request.target_language_code,
        source_language_code=request.source_language_code or "auto",
    )
    if not translated:
        translated = clean_text

    return SarvamTranslateResponse(
        translated_text=translated,
        target_language_code=request.target_language_code,
    )


@router.post("/stt", response_model=SarvamChatResponse)
async def speech_to_text_chat(
    file: UploadFile = File(...),
    language_code: Optional[str] = Form("unknown"),
    tts_language_code: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """Voice inquiry endpoint: Transcribes speech and answers railway queries.

    Accepts uploaded audio (wav/mp3/webm), runs Sarvam STT, queries RailPulse, and returns text answer.
    """
    try:
        audio_bytes = await file.read()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not read uploaded audio file",
        )

    if not audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio file is empty",
        )

    # Transcribe speech with Sarvam Saaras
    # Format as (filename, bytes) tuple expected by SDK
    transcription = sarvam_service.transcribe_audio(
        (file.filename or "audio.wav", audio_bytes),
        language_code=language_code or "unknown",
    )

    if not transcription:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech-to-text recognition could not transcribe audio",
        )

    # Process transcribed text query
    context = await _fetch_railpulse_context(transcription)
    train_number = context.get("train_number")

    reply_text, detected_lang = sarvam_service.generate_chat_response(
        user_message=transcription,
        railpulse_context=context,
        language="auto",
    )

    # Optional speech synthesis of response in selected voice language
    target_tts_lang = tts_language_code or "hi-IN"
    speech_reply_text = reply_text
    if target_tts_lang != "en-IN":
        translated = sarvam_service.translate_text(
            text=reply_text,
            target_language_code=target_tts_lang,
            source_language_code="auto",
        )
        if translated:
            speech_reply_text = translated

    audio_reply = sarvam_service.synthesize_speech(
        speech_reply_text,
        language_code=target_tts_lang,
    )

    return SarvamChatResponse(
        response=reply_text,
        language=detected_lang,
        train_number=train_number,
        source="railpulse",
        audio_base64=audio_reply,
        localized_text=speech_reply_text if speech_reply_text != reply_text else None,
    )

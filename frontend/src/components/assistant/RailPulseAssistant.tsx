import React, { useState, useRef, useEffect } from 'react';
import { Send, Mic, MicOff, Volume2, Bot, User as UserIcon, Loader2, Sparkles, AlertCircle } from 'lucide-react';
import { askSarvamAssistant, getSarvamTTS, sendSarvamAudioSTT } from '../../services/api';

interface Message {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  trainNumber?: string | null;
  audioBase64?: string | null;
  timestamp: string;
}

export const RailPulseAssistant: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      sender: 'assistant',
      text: 'Namaste! I am the RailPulse Assistant powered by Sarvam AI. Ask me about train locations, delays, or arrival times in English, Hindi, or Hinglish (e.g., "Where is train 12951?" or "12951 kaha pahunchi hai?").',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [audioPlayingId, setAudioPlayingId] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  const [language, setLanguage] = useState('auto');
  const [preferredTtsLanguage, setPreferredTtsLanguage] = useState('hi-IN');

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading]);

  useEffect(() => {
    return () => {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }
    };
  }, []);

  const speakAssistantMessage = async (msg: Message) => {
    try {
      if (!msg.text) return;

      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }

      let audioB64 = msg.audioBase64;

      if (!audioB64) {
        const ttsRes = await getSarvamTTS(msg.text, preferredTtsLanguage);
        audioB64 = ttsRes.audio_base64;
        msg.audioBase64 = audioB64;
      }

      if (!audioB64) return;

      const audio = new Audio(`data:audio/mp3;base64,${audioB64}`);
      currentAudioRef.current = audio;
      setAudioPlayingId(msg.id);

      audio.onended = () => {
        setAudioPlayingId(null);
        currentAudioRef.current = null;
      };

      audio.onerror = () => {
        setAudioPlayingId(null);
        currentAudioRef.current = null;
        setAudioError('Unable to play audio stream');
      };

      await audio.play();
    } catch {
      setAudioPlayingId(null);
      currentAudioRef.current = null;
      setAudioError('Text-to-speech is currently unavailable');
    }
  };

  const handleSend = async (textToSend?: string) => {
    const query = (textToSend || input).trim();
    if (!query || loading) return;

    const userMsg: Message = {
      id: `u-${Date.now()}`,
      sender: 'user',
      text: query,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages(prev => [...prev, userMsg]);
    if (!textToSend) setInput('');
    setLoading(true);
    setAudioError(null);

    try {
      const data = await askSarvamAssistant(query, language);
      const assistantMsg: Message = {
        id: `a-${Date.now()}`,
        sender: 'assistant',
        text: data.response,
        trainNumber: data.train_number,
        audioBase64: data.audio_base64 || null,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages(prev => [...prev, assistantMsg]);
      if (assistantMsg.audioBase64) {
        void speakAssistantMessage(assistantMsg);
      }
    } catch (err: any) {
      const errorMsg: Message = {
        id: `err-${Date.now()}`,
        sender: 'assistant',
        text: 'RailPulse Assistant is currently unable to reach the language service. Please verify your query or check train details directly in Live Trains.',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setMessages(prev => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handlePlayAudio = async (msg: Message) => {
    if (audioPlayingId === msg.id) {
      currentAudioRef.current?.pause();
      currentAudioRef.current = null;
      setAudioPlayingId(null);
      return;
    }

    await speakAssistantMessage(msg);
  };

  const startVoiceRecording = async () => {
    setAudioError(null);
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setAudioError('Microphone not supported in this browser.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunksRef.current = [];
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        stream.getTracks().forEach(track => track.stop());

        if (audioBlob.size > 0) {
          setLoading(true);
          try {
            const result = await sendSarvamAudioSTT(audioBlob);
            const assistantMsg: Message = {
              id: `a-${Date.now()}`,
              sender: 'assistant',
              text: result.response,
              trainNumber: result.train_number,
              audioBase64: result.audio_base64,
              timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            };
            setMessages(prev => [...prev, assistantMsg]);
            if (assistantMsg.audioBase64) {
              void speakAssistantMessage(assistantMsg);
            }
          } catch {
            setAudioError('Could not process speech. Please type your query.');
          } finally {
            setLoading(false);
          }
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err: any) {
      setAudioError('Microphone access denied or unavailable. You can continue typing your question.');
      setIsRecording(false);
    }
  };

  const stopVoiceRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const quickPrompts = [
    'Where is train 12951?',
    '12951 kaha pahunchi hai?',
    '12951 kab aayegi?',
    'Which trains are delayed?',
  ];

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm flex flex-col h-[580px] overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-red-50 border border-red-200 flex items-center justify-center text-red-600">
            <Bot className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-semibold text-slate-900 text-sm">RailPulse Assistant</h3>
              <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-red-100 text-red-700">
                <Sparkles className="w-3 h-3" /> Sarvam AI
              </span>
            </div>
            <p className="text-xs text-slate-500">Multilingual Voice & Text Grounded on RailPulse Data</p>
          </div>
        </div>
        <div className="text-right">
          <span className="text-[11px] text-slate-500 font-medium">Source: RailPulse Engine</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 p-4 overflow-y-auto space-y-4 bg-slate-50/50">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex gap-3 max-w-[85%] ${msg.sender === 'user' ? 'ml-auto flex-row-reverse' : ''}`}
          >
            <div
              className={`w-8 h-8 rounded-full shrink-0 flex items-center justify-center text-xs font-bold ${
                msg.sender === 'user'
                  ? 'bg-red-600 text-white'
                  : 'bg-white border border-slate-200 text-red-600 shadow-sm'
              }`}
            >
              {msg.sender === 'user' ? <UserIcon className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
            </div>

            <div className="flex flex-col">
              <div
                className={`p-3.5 rounded-2xl text-sm leading-relaxed shadow-sm ${
                  msg.sender === 'user'
                    ? 'bg-red-600 text-white rounded-tr-none'
                    : 'bg-white border border-slate-200 text-slate-800 rounded-tl-none'
                }`}
              >
                {msg.text}
              </div>

              <div className="flex items-center gap-2 mt-1 px-1">
                <span className="text-[10px] text-slate-400">{msg.timestamp}</span>

                {msg.sender === 'assistant' && (
                  <button
                    onClick={() => handlePlayAudio(msg)}
                    className={`inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded transition-colors ${
                      audioPlayingId === msg.id
                        ? 'bg-red-100 text-red-700'
                        : 'text-slate-500 hover:text-red-600 hover:bg-slate-100'
                    }`}
                    title="Listen to audio response"
                  >
                    <Volume2 className={`w-3 h-3 ${audioPlayingId === msg.id ? 'animate-pulse' : ''}`} />
                    <span>{audioPlayingId === msg.id ? 'Playing...' : 'Listen'}</span>
                  </button>
                )}

                {msg.trainNumber && (
                  <span className="text-[10px] font-semibold text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">
                    Train #{msg.trainNumber}
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex gap-3 max-w-[80%]">
            <div className="w-8 h-8 rounded-full bg-white border border-slate-200 text-red-600 flex items-center justify-center shrink-0">
              <Bot className="w-4 h-4" />
            </div>
            <div className="p-3 bg-white border border-slate-200 rounded-2xl rounded-tl-none text-sm text-slate-500 flex items-center gap-2 shadow-sm">
              <Loader2 className="w-4 h-4 animate-spin text-red-600" />
              <span>Checking RailPulse train data & generating answer...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Notification banner if microphone or audio error */}
      {audioError && (
        <div className="px-4 py-2 bg-amber-50 border-t border-amber-200 text-amber-800 text-xs flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0 text-amber-600" />
          <span>{audioError}</span>
        </div>
      )}

      {/* Quick Suggestion Chips */}
      <div className="px-4 py-2 border-t border-slate-100 bg-white flex gap-2 overflow-x-auto text-xs">
        <span className="text-slate-400 self-center shrink-0">Suggested:</span>
        {quickPrompts.map((q, idx) => (
          <button
            key={idx}
            onClick={() => handleSend(q)}
            disabled={loading}
            className="shrink-0 bg-slate-100 hover:bg-slate-200 text-slate-700 px-2.5 py-1 rounded-full transition-colors disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>

      {/* Input controls */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
        className="p-3 border-t border-slate-200 bg-white flex items-center gap-2"
      >
        <button
          type="button"
          onClick={isRecording ? stopVoiceRecording : startVoiceRecording}
          disabled={loading}
          className={`p-2.5 rounded-lg border transition-colors flex items-center justify-center shrink-0 ${
            isRecording
              ? 'bg-red-600 border-red-600 text-white animate-pulse'
              : 'border-slate-300 text-slate-600 hover:bg-slate-50 hover:text-red-600'
          }`}
          title={isRecording ? 'Stop Recording' : 'Speak into Microphone'}
        >
          {isRecording ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
        </button>

        <select
          value={language}
          onChange={(e) => {
            const nextLanguage = e.target.value;
            setLanguage(nextLanguage);
            setPreferredTtsLanguage(nextLanguage === 'auto' ? 'hi-IN' : nextLanguage);
          }}
          aria-label="Assistant language"
          className="h-9 w-[92px] shrink-0 rounded-lg border border-slate-300 bg-white px-2 text-xs font-medium text-slate-700 outline-none focus:border-red-500 focus:ring-1 focus:ring-red-500"
        >
          <option value="auto">Auto</option>
          <option value="en-IN">English</option>
          <option value="hi-IN">हिन्दी</option>
          <option value="bn-IN">বাংলা</option>
          <option value="gu-IN">ગુજરાતી</option>
          <option value="kn-IN">ಕನ್ನಡ</option>
          <option value="ml-IN">മലയാളം</option>
          <option value="mr-IN">मराठी</option>
          <option value="od-IN">ଓଡ଼ିଆ</option>
          <option value="pa-IN">ਪੰਜਾਬੀ</option>
          <option value="ta-IN">தமிழ்</option>
          <option value="te-IN">తెలుగు</option>
        </select>

        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={isRecording ? 'Listening to speech...' : 'Ask about your train (e.g. 12951 kaha pahunchi hai?)...'}
          disabled={loading || isRecording}
          className="flex-1 bg-slate-50 border border-slate-300 text-slate-900 px-3.5 py-2 rounded-lg text-sm focus:outline-none focus:border-red-500 focus:ring-1 focus:ring-red-500 transition-colors"
        />

        <button
          type="submit"
          disabled={loading || !input.trim() || isRecording}
          className="bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-colors shrink-0"
        >
          <Send className="w-4 h-4" />
          <span>Send</span>
        </button>
      </form>
    </div>
  );
};

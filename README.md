# RailPulse ETA 🚄

### AI-Powered Dynamic Train Arrival Forecasting & Multilingual Passenger Assistance

**Smart India Hackathon (SIH) 2026 · Problem Statement 26028 · Ministry of Railways**

RailPulse ETA is a railway operations and passenger information platform for **dynamic Expected Time of Arrival (ETA) forecasting**. Instead of simply carrying forward the current delay, RailPulse continuously estimates **additional expected delay** from changing operational conditions such as congestion, weather, speed restrictions, unscheduled halts, preceding-train delay, speed variation, and station dwell behavior.

The current application combines the ETA engine with a **Sarvam AI-powered multilingual voice and conversational assistant**, giving passengers and railway staff a natural-language way to query train status, location, delays and arrival forecasts.

> **Prototype / Demo Transparency**
>
> RailPulse uses authentic railway timetable and station master data from open sources, while **real-time operational telemetry is simulated** in the current prototype because authorized Indian Railways operational feeds are not publicly available to the project. The ML model and ETA engine operate on this simulated live state. This keeps the demonstration reproducible while clearly separating real master data from simulated telemetry.

---

## ✨ What RailPulse Does

### 🚆 Dynamic Railway ETA Platform
- **Dynamic ETA Prediction** — Continuously recalculates predicted arrival times as train state and operational conditions change.
- **ML-Based Delay Forecasting** — Uses a trained Gradient Boosting Regressor to predict additional expected delay rather than simply adding current delay to the timetable.
- **Live Simulation** — Simulates train movement, speed, delay progression, congestion, weather impact, speed restrictions, and unscheduled halts.
- **Operational Event Detection** — Inject and resolve disruptions and observe their effect on train state, ETA, and alerts.
- **Explainable Predictions** — Surfaces the operational factors contributing to an ETA change.
- **Real-Time Updates** — Uses REST APIs and WebSockets for continuously refreshed train and operational state.

### 👥 Two Role-Based Experiences
- **Passenger Experience** — Search by train number or name and view current position, next station, dynamic ETA, delay status and the full route timetable.
- **Control Room Experience** — Provides operations staff with network status, active trains, congestion, critical alerts, train telemetry, operational response controls and analytics.
- **Authentication & Role-Based Access** — Users sign in through the authentication layer, while the Control Room route is restricted to users with the railway-staff role.

### 🗣️ Sarvam AI Multilingual Assistant
- **Dedicated AI Assistant** — A separate assistant experience keeps conversational/voice interaction distinct from the main passenger train-tracking screen.
- **Grounded Railway Answers** — Assistant responses are generated from authoritative RailPulse train, position, KPI and ETA data; Sarvam is the language/voice layer, not the source of railway facts.
- **Natural-Language Train Queries** — Ask questions such as “Where is train 12951?”, “12951 kaha pahunchi hai?” or “12951 kab aayegi?”.
- **Speech-to-Text (Sarvam Saaras)** — Record a voice query through the microphone and convert it into a railway inquiry.
- **Text-to-Speech (Sarvam Bulbul)** — Assistant responses can be synthesized into spoken audio.
- **Automatic Voice Playback** — Generated assistant responses are automatically spoken without requiring the user to press Listen each time.
- **Regional Language Selection** — The chat area provides compact language selectors for the assistant voice language and speech-input language.
- **Indian Language Support** — The current voice/TTS selector includes Hindi, English, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil and Telugu, with auto-detect available for speech input.
- **Translation Support** — Sarvam Translate is used to localize responses before regional-language speech synthesis where required.
- **Safe Separation of Concerns** — The assistant is informational only and does not perform operational mutations such as simulation-event injection.

---

## 🧠 How the ETA Engine Works

RailPulse follows a continuous operational loop:

**Observe → Predict → Alert → Act → Recalculate**

The ETA prediction pipeline combines dynamic and contextual features including:

- Current delay and speed
- Average sectional speed
- Distance to the next station and destination
- Section congestion
- Weather severity
- Active speed restrictions
- Preceding-train delay
- Station dwell behavior
- Time-of-day and day-of-week effects
- Train type and route context

The prototype uses a trained **Gradient Boosting Regressor** with chronological holdout evaluation on synthetic operational observations.

**Model benchmark in this prototype:** approximately **3.95 minutes MAE** on the project's synthetic chronological test set. This is a project benchmark, **not a claim of live Indian Railways field accuracy**.

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Web Browser                             │
│                                                             │
│ Passenger View      AI Assistant      Control Room          │
└───────────────┬───────────────┬───────────────┬─────────────┘
                │               │               │
                │          Authenticated        │
                │           API Access          │
                └───────────────┬───────────────┘
                                │
                       REST API + WebSocket
                                │
┌───────────────────────────────▼───────────────────────────────┐
│                       FastAPI Backend                        │
│                                                             │
│ Train Service   ETA Service   Alert Service   Analytics      │
│       │              │             │              │          │
│       └──────────────┴─────────────┴──────────────┘          │
│                        Simulation Engine                     │
│          telemetry · speed · delay · events · state          │
│                                                             │
│  Authentication   Sarvam Chat   Sarvam STT   Sarvam TTS      │
└───────────────┬───────────────────────────────┬──────────────┘
                │                               │
         MongoDB Atlas                    Sarvam AI
                │                         Saaras / Bulbul
                │                         / Translate
                │
                └───────────────┐
                                ▼
                       ML ETA Predictor
                    Gradient Boosting Model
```

**Important:** Sarvam AI is an additional language and voice interaction layer around RailPulse. Train facts, positions and ETA forecasts originate from the RailPulse backend and ML pipeline.

---

## 💻 Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, Recharts, React-Leaflet, Lucide Icons, Axios |
| Backend | Python, FastAPI, Pydantic, Uvicorn, WebSockets |
| Machine Learning | Scikit-Learn, Gradient Boosting, Pandas, NumPy, Joblib |
| Database | MongoDB Atlas / PyMongo Async |
| Authentication | JWT-based application authentication with role checks |
| Mapping | Leaflet / OpenStreetMap |
| Voice & Language | Sarvam AI — Saaras, Bulbul, Translate |
| Deployment | Render |

---

## 🔐 Authentication & Access Model

RailPulse supports authenticated application access with role-aware routing.

```
Login / Register
      │
      ▼
Authenticated Session
      │
      ├── Passenger Experience
      │      ├── Passenger View
      │      └── AI Assistant
      │
      └── Railway Staff
             └── Control Room
```

The Control Room route is protected with a railway-staff role check, while the assistant and passenger experience remain informational and user-facing.

---

## 📂 Project Structure

```
RailPulse-ETA-Sarvam/
├── backend/
│   ├── app/
│   │   ├── api/             # REST, auth, Sarvam & WebSocket endpoints
│   │   ├── database/        # MongoDB connection & data helpers
│   │   ├── models/          # Runtime models & API schemas
│   │   ├── services/        # Train, ETA, Alert, Analytics & Sarvam services
│   │   ├── simulation/      # Simulation engine & telemetry logic
│   │   ├── config.py        # Environment configuration
│   │   └── main.py          # FastAPI application entrypoint
│   ├── tests/               # Automated backend tests
│   └── requirements.txt     # Backend dependencies
├── frontend/
│   ├── src/
│   │   ├── components/      # Maps, charts, controls, auth & assistant UI
│   │   ├── pages/           # Dashboard, Passenger, Assistant, Control Room, etc.
│   │   ├── hooks/           # Auth, WebSocket and application hooks
│   │   ├── services/        # API and WebSocket clients
│   │   ├── types/           # TypeScript types
│   │   └── App.tsx          # Application routing
│   ├── package.json
│   └── vite.config.ts
├── ml/
│   ├── generate_dataset.py
│   ├── feature_engineering.py
│   ├── train_model.py
│   ├── predict.py
│   ├── evaluate.py
│   └── model/               # Trained model artifacts and metrics
├── data/
│   ├── seed/                # Railway master/reference data
│   └── generated/           # Synthetic ML training/validation data
├── docs/
│   ├── architecture.md
│   └── demo-script.md
├── start-windows.bat
├── render.yaml
└── README.md
```

---

## 🚀 Quick Start

### Clone the current repository

```bash
git clone https://github.com/mishita-gogia/RailPulse-ETA-Sarvam.git
cd RailPulse-ETA-Sarvam
```

### Backend

```bash
cd backend
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Start FastAPI:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Backend:
- API: `http://localhost:8000`
- Swagger/OpenAPI: `http://localhost:8000/docs`

### Frontend

Open a second terminal:

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 3000
```

Frontend:

```
http://localhost:3000
```

### Environment

The deployed application expects a reachable **MongoDB Atlas** instance and the required Sarvam credentials configured through environment variables. The exact secrets are intentionally not committed to this repository.

---

## 🇮🇳 Railway Data Provenance

RailPulse separates **authentic master timetable data** from **simulated operational telemetry**.

### Open Railway Master Data

The project incorporates railway master/reference information from:

- [DataMeet Community Indian Railways Repository](https://github.com/datameet/railways)
- [Government Open Government Data Platform — Indian Railways Train Time Table](https://www.data.gov.in/catalog/indian-railways-train-time-table)

The repository preserves authentic station codes, timetable fields, route ordering, and available source distances rather than fabricating missing railway data.

### Representative Reference Trains

The project includes curated reference records such as:

- **20491 Jaisalmer – Sabarmati SF Express** — 23 authentic halts
- **20492 Sabarmati – Jaisalmer SF Express** — 23 authentic halts
- **22436 New Delhi – Varanasi Vande Bharat Express** — reference route with authentic stops

### Simulated Telemetry

Current demo telemetry includes:

- GPS / positional progression
- Current speed
- Train delay state
- Section congestion
- Weather impact
- Speed restrictions
- Unscheduled halt events

These are explicitly simulated for the prototype and are not presented as live Indian Railways telemetry.

---

## 🗣️ Sarvam AI Integration

Sarvam is integrated as the application's **multilingual voice and conversational interface**, while RailPulse remains the authoritative source for railway information.

### Text Query Flow

```
User question
      ↓
Sarvam-aware assistant endpoint
      ↓
RailPulse train / ETA / KPI context retrieval
      ↓
Grounded response generation
      ↓
Optional regional translation
      ↓
Sarvam TTS
      ↓
Automatic voice playback
```

### Voice Query Flow

```
Microphone
    ↓
Sarvam Saaras STT
    ↓
Transcribed railway question
    ↓
RailPulse train / ETA context
    ↓
Grounded response
    ↓
Sarvam translation (when needed)
    ↓
Sarvam Bulbul TTS
    ↓
Spoken response
```

### Supported interaction

The current assistant supports:

- Train location, status, delay and ETA questions
- English, Hindi and Hinglish conversational queries
- Voice input through the browser microphone
- Automatic spoken responses
- Regional-language voice selection
- Speech-input language selection / auto-detect
- Text translation into selected Indian languages
- Queries grounded in RailPulse train and ETA data

The assistant is **read-only**: it does not inject or resolve operational simulation events.

---

## 🎯 SIH Demo Flow

A typical 3–5 minute demonstration:

1. **Login** — Enter the application through the authenticated login flow.
2. **Dashboard** — Show active trains, delay KPIs and the live network map.
3. **Train Details** — Open a train such as **12951** and inspect station-by-station ETA predictions.
4. **Inject an Event** — Apply signal congestion, weather impact, speed restriction, or an unscheduled halt.
5. **Observe Dynamic Recalculation** — Watch delay/ETA values and operational alerts change.
6. **Passenger View** — Search for a train and view passenger-friendly status and the dynamic forecast.
7. **AI Assistant** — Ask a train-status question in text or by voice, select a regional language, and demonstrate automatic spoken playback.
8. **Control Room** — Switch to railway-staff access and review alerts, congestion, telemetry and operational response.
9. **Analytics** — Show the ML evaluation and delay analytics.

---

## 📊 ML Evaluation

The project evaluates the ETA model using MAE, RMSE and R².

Prototype benchmark:

| Model | MAE (minutes) | RMSE (minutes) | R² |
|---|---:|---:|---:|
| Naive baseline | 22.60 | 26.14 | -2.70 |
| Statistical operational-condition baseline | 6.29 | 8.24 | 0.63 |
| Random Forest | 4.72 | 6.06 | 0.80 |
| Gradient Boosting | **3.95** | **4.94** | **0.87** |

These metrics are from the project's **synthetic operational benchmark with chronological holdout evaluation**. They should not be interpreted as live field accuracy.

---

## 🧪 Testing

Run the backend test suite:

```bash
pytest backend/tests -q
```

The current project documentation includes coverage for API behavior, authentication, Sarvam integration, data integrity, event handling and MongoDB runtime behavior. Test counts may change as the codebase evolves.

---

## 🔮 Future Production Integration

The architecture is designed to connect authorized railway operational feeds through the data-source/adapter layer, for example:

- Authorized CRIS / COA operational APIs
- Authorized GPS / AVL / RTIS telemetry
- Electronic interlocking / signalling feeds
- Weather and severe-weather services
- Additional railway operational systems as access is provided

The current prototype keeps these integrations decoupled from the core ETA engine so that the same prediction and dashboard architecture can consume authorized live telemetry in a future deployment.

---

## 📄 License / Attribution

RailPulse ETA is a Smart India Hackathon 2026 prototype by **Team HazardIQ (SIH27)**.

Railway reference data used in the project is attributed to its respective public sources, including DataMeet and the Government Open Government Data Platform.

---

## 🔗 Repository

**GitHub:** https://github.com/mishita-gogia/RailPulse-ETA-Sarvam

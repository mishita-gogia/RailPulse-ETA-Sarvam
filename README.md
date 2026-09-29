# RailPulse ETA 🚄

### AI-Powered Dynamic Train Arrival Forecasting System

**Smart India Hackathon (SIH) 2026 · Problem Statement 26028 · Ministry of Railways**

RailPulse ETA is a railway operations and passenger information platform for **dynamic Expected Time of Arrival (ETA) forecasting**. Instead of simply carrying forward the current delay, RailPulse continuously estimates **additional expected delay** from changing operational conditions such as congestion, weather, speed restrictions, unscheduled halts, preceding-train delay, speed variation, and station dwell behavior.

> **Prototype / Demo Transparency**
>
> RailPulse uses authentic railway timetable and station master data from open sources, while **real-time operational telemetry is simulated** in the current prototype because authorized Indian Railways operational feeds are not publicly available to the project. The ML model and ETA engine operate on this simulated live state. This keeps the demonstration reproducible while clearly separating real master data from simulated telemetry.

---

## ✨ What RailPulse Does

- **Dynamic ETA Prediction** — Continuously recalculates predicted arrival times as train state and operational conditions change.
- **ML-Based Delay Forecasting** — Uses a trained Gradient Boosting Regressor to predict additional expected delay rather than simply adding current delay to the timetable.
- **Live Simulation** — Simulates train movement, speed, delay progression, congestion, weather impact, speed restrictions, and unscheduled halts.
- **Operational Event Detection** — Inject and resolve disruptions and observe their effect on train state, ETA, and alerts.
- **Explainable Predictions** — Surfaces the operational factors contributing to an ETA change.
- **Control Room Dashboard** — Provides network status, active alerts, congestion, train details, and analytics for railway operations.
- **Passenger View** — Provides a simpler interface for train search, delay information, next-station status, and ETA.
- **Real-Time Updates** — Uses REST APIs and WebSockets for continuously refreshed train and operational state.
- **Multilingual Voice Layer** — Integrates Sarvam AI for regional-language interaction, translation, speech recognition, and text-to-speech without replacing the RailPulse ETA engine.
- **MongoDB Runtime** — The deployed application uses MongoDB Atlas as its runtime data store.

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

The production prototype uses a trained **Gradient Boosting Regressor** with a chronological holdout evaluation on synthetic operational observations.

**Model benchmark in this prototype:** approximately **3.95 minutes MAE** on the project's synthetic chronological test set. This is a project benchmark, **not a claim of live Indian Railways field accuracy**.

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Web / Mobile Browser                    │
│          Passenger View / Railway Control Room          │
└───────────────────────────┬─────────────────────────────┘
                            │
                     REST API + WebSocket
                            │
┌───────────────────────────▼─────────────────────────────┐
│                    FastAPI Backend                      │
│                                                         │
│  Train Service   ETA Service   Alert Service   Analytics│
│          \\          │             │            /        │
│           \\         │             │           /         │
│            └──────────▼─────────────▼──────────┘          │
│                 Simulation Engine                       │
│       telemetry · speed · delay · events · state         │
└───────────────────────────┬─────────────────────────────┘
                            │
                      MongoDB Atlas
                            │
               ┌────────────▼────────────┐
               │    ML ETA Predictor     │
               │ Gradient Boosting Model │
               └─────────────────────────┘

Sarvam AI is an additional language/voice interaction layer
around the RailPulse platform; it is not the source of ETA data.
```

---

## 💻 Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS, Recharts, React-Leaflet, Lucide Icons, Axios |
| Backend | Python, FastAPI, Pydantic, Uvicorn, WebSockets |
| Machine Learning | Scikit-Learn, Gradient Boosting, Pandas, NumPy, Joblib |
| Database | MongoDB Atlas / PyMongo Async |
| Mapping | Leaflet / OpenStreetMap |
| Voice & Language | Sarvam AI |
| Deployment | Render |

---

## 📂 Project Structure

```
RailPulse-ETA/
├── backend/
│   ├── app/
│   │   ├── api/             # REST & WebSocket endpoints
│   │   ├── database/        # MongoDB connection & data helpers
│   │   ├── models/          # Runtime models & API schemas
│   │   ├── services/        # Train, ETA, Alert & Analytics services
│   │   ├── simulation/      # Simulation engine & telemetry logic
│   │   ├── config.py        # Environment configuration
│   │   └── main.py          # FastAPI application entrypoint
│   ├── tests/               # Automated backend tests
│   └── requirements.txt     # Backend dependencies
├── frontend/
│   ├── src/
│   │   ├── components/      # Maps, charts, controls and UI components
│   │   ├── pages/           # Dashboard, Passenger, Control Room, Analytics, etc.
│   │   ├── hooks/           # WebSocket and simulation hooks
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
├── start-windows.bat        # One-click Windows startup
├── render.yaml               # Render deployment configuration
└── README.md
```

---

## 🚀 Quick Start

### Windows — One-Click Startup

Clone the repository:

```bash
git clone https://github.com/jjayesh364/RailPulse-ETA.git
cd RailPulse-ETA
```

Then double-click:

```text
start-windows.bat
```

The script prepares the local Python environment, installs backend/frontend dependencies, starts the FastAPI backend and Vite frontend, and opens the application.

### Manual Startup

#### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Backend:

- API: `http://localhost:8000`
- Swagger/OpenAPI: `http://localhost:8000/docs`

#### Frontend

Open a second terminal:

```bash
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 3000
```

Frontend:

- `http://localhost:3000`

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

Sarvam is an **additional accessibility and interaction layer**.

Typical flow:

```
Passenger voice/question
        ↓
Sarvam speech / language processing
        ↓
RailPulse train + ETA data
        ↓
Factual response
        ↓
Sarvam translation / text-to-speech
```

This allows regional-language and voice interaction while keeping **RailPulse as the source of train-status and ETA information**.

---

## 🎯 SIH Demo Flow

A typical 3–5 minute demonstration:

1. **Dashboard** — Show active trains, delay KPIs and the live network map.
2. **Train Details** — Open a train such as **12951** and inspect station-by-station ETA predictions.
3. **Inject an Event** — Apply signal congestion, weather impact, speed restriction, or an unscheduled halt.
4. **Observe Dynamic Recalculation** — Watch delay/ETA values and operational alerts change.
5. **Explain the Prediction** — Show the factors contributing to the ETA change.
6. **Passenger View** — Search for a train and view passenger-friendly status.
7. **Control Room** — Review alerts, congestion and operational risk.
8. **Analytics** — Show the project's ML evaluation and delay analytics.

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

The current verified suite contains **57 backend tests**, including API, authentication, Sarvam integration, data integrity, event handling, and MongoDB runtime-decoupling checks.

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

**GitHub:** https://github.com/jjayesh364/RailPulse-ETA

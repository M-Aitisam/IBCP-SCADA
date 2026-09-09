# 📝 Updated README.md with Complete Setup Instructions

Here's your updated `README.md` with all the commands and environment setup instructions.

---

## 📋 Replace `README.md` with This:

```markdown
# IBCP-SCADA - Indus Basin Cyber-Physical SCADA System

A unified mega system for flood management, water distribution, and agricultural intelligence in Pakistan's Indus Basin.

---

## 🏗️ Projects

| Project | Description |
|---------|-------------|
| **GeoVision AI** | AI-powered remote sensing for drought & flood prediction |
| **Flood SCADA** | Automated barrage gate control & flood diversion |
| **Soil Monitoring** | Salinity tracking & land degradation monitoring |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|------------|
| **Frontend** | Next.js + TypeScript + Tailwind CSS |
| **Backend** | FastAPI + Python |
| **Database** | Supabase PostgreSQL + TimescaleDB |
| **ML** | XGBoost + Google Earth Engine GEE |
| **Auth** | JWT + Google OAuth |
| **Real-time** | MQTT + WebSockets |

---

## 🚀 Quick Start

### Prerequisites

| Tool | Version | Check Command |
|------|---------|---------------|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| Git | Latest | `git --version` |

### 1. Clone the Repository

```bash
git clone https://github.com/M-Aitisam/IBCP-SCADA.git
cd IBCP-SCADA
```

---

## 🔧 Backend Setup (FastAPI)

### 1. Navigate to Backend

```bash
cd packages/backend
```

### 2. Create Virtual Environment

```bash
python -m venv venv
```

### 3. Activate Virtual Environment

**Windows:**
```bash
venv\Scripts\activate
```

**Mac/Linux:**
```bash
source venv/bin/activate
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Create Environment File

```bash
# Copy example environment file
cp .env.example .env
```

### 6. Update `.env` with Your Credentials

```env
# packages/backend/.env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres
SECRET_KEY=YOUR_SECRET_KEY
GOOGLE_CLIENT_ID=YOUR_GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=YOUR_GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
FRONTEND_URL=http://localhost:3000
```

> **Note:** Get `DATABASE_URL` from Supabase Dashboard → Settings → Database → Connection string.

### 7. Run Database Migrations

```bash
alembic upgrade head
```

### 8. Start Backend Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Backend will run at:** `http://localhost:8000`

---

## 🎨 Frontend Setup (Next.js)

### 1. Navigate to Frontend

```bash
cd packages/dashboard
```

### 2. Install Dependencies

```bash
npm install --legacy-peer-deps
```

### 3. Create Environment File

```bash
cp .env.example .env.local
```

### 4. Update `.env.local`

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

### 5. Start Frontend Server

```bash
npm run dev
```

**Frontend will run at:** `http://localhost:3000`

---

## 📊 Running Both Servers

### Terminal 1 - Backend

```bash
cd packages/backend
venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Terminal 2 - Frontend

```bash
cd packages/dashboard
npm run dev
```

---

## 🌐 Access the Application

| Service | URL |
|---------|-----|
| **Frontend** | http://localhost:3000 |
| **Backend API** | http://localhost:8000/api/v1 |
| **API Docs** | http://localhost:8000/docs |
| **Health Check** | http://localhost:8000/health |

---

## 🗄️ Database Setup (Supabase)

### Option 1: Use Supabase (Recommended - Free)

1. Sign up at [supabase.com](https://supabase.com)
2. Create a new project
3. Go to **Settings** → **Database**
4. Copy the **Connection string** (URI)
5. Add it to `.env` as `DATABASE_URL`

### Option 2: Use Local PostgreSQL

```bash
docker run -d \
  --name ibcp-postgres \
  -e POSTGRES_PASSWORD=scada2026 \
  -e POSTGRES_DB=ibcp_scada \
  -p 5432:5432 \
  postgres:16-alpine
```

Then set `DATABASE_URL` to:
```
postgresql://postgres:scada2026@localhost:5432/ibcp_scada
```

---

## 🔑 Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable OAuth 2.0
4. Create credentials → OAuth Client ID
5. Add authorized redirect URIs:
   - `http://localhost:8000/api/v1/auth/google/callback`
   - `https://your-domain.vercel.app/api/v1/auth/google/callback`
6. Copy Client ID and Secret to `.env`

---

## 🚀 Deployment

### Deploy to Vercel

```bash
# Install Vercel CLI
npm install -g vercel

# Login
vercel login

# Deploy
vercel --prod
```

### Add Environment Variables in Vercel

Go to Vercel Dashboard → Settings → Environment Variables:

| Key | Value |
|-----|-------|
| `DATABASE_URL` | Your Supabase connection string |
| `SECRET_KEY` | Your secret key |
| `GOOGLE_CLIENT_ID` | Your Google Client ID |
| `GOOGLE_CLIENT_SECRET` | Your Google Client Secret |
| `GOOGLE_REDIRECT_URI` | `https://your-domain.vercel.app/api/v1/auth/google/callback` |
| `FRONTEND_URL` | `https://your-domain.vercel.app` |
| `NEXT_PUBLIC_API_URL` | `/api/v1` |

---

## 🔧 Common Issues & Fixes

| Issue | Solution |
|-------|----------|
| `uvicorn: command not found` | `pip install uvicorn[standard]` |
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| `npm install fails` | `npm install --legacy-peer-deps` |
| `Port 8000 in use` | `uvicorn app.main:app --port 8001` |
| `Port 3000 in use` | `npm run dev -- -p 3001` |
| `Database connection error` | Check `DATABASE_URL` in `.env` |
| `Google OAuth error` | Check `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` |

---

## 📁 Project Structure

```
IBCP-SCADA/
├── packages/
│   ├── backend/
│   │   ├── app/
│   │   │   ├── api/          # API endpoints
│   │   │   ├── core/         # Config & security
│   │   │   ├── db/           # Database models
│   │   │   └── main.py       # FastAPI entry point
│   │   ├── migrations/       # Alembic migrations
│   │   ├── requirements.txt
│   │   └── .env
│   ├── dashboard/
│   │   ├── src/
│   │   │   ├── app/          # Next.js pages
│   │   │   ├── components/   # React components
│   │   │   └── context/      # Auth & Theme context
│   │   ├── package.json
│   │   └── .env.local
│   └── ml-pipeline/
│       ├── gee/              # GEE scripts
│       ├── models/           # Trained ML models
│       └── scripts/          # Training scripts
├── vercel.json
└── README.md
```

---

## 📝 Environment Variables Reference

### Backend `.env`

```env
# Database
DATABASE_URL=postgresql://...

# Redis
REDIS_URL=redis://localhost:6379

# MQTT
MQTT_BROKER=localhost
MQTT_PORT=1883

# JWT
SECRET_KEY=your-secret-key

# Google OAuth
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
FRONTEND_URL=http://localhost:3000
```

### Frontend `.env.local`

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

---

## 🤝 Team

| Name | Role |
|------|------|
| Ahmed Asher | System Architecture & Integration |
| Hasnat Ali Shah | GIS & Mobile Development |
| Muhammad Aitisam Ahmed | ML Models & Dashboard Development |

---

## 📄 License

This project is part of Air University Islamabad's Final Year Project.

---

**Happy Coding! 🚀**
```

---

## 🚀 Quick Commands to Update

```bash
# 1. Replace README.md
cd C:\Users\aitis\OneDrive\Desktop\IBCP-SCADA

# 2. Copy the content above into README.md
notepad README.md

# 3. Commit and push
git add README.md
git commit -m "docs: Update README with complete setup instructions"
git push origin aitisam
```

---

---

**Your README is now complete and professional!** 🚀📄

# IBCP-SCADA - Indus Basin Cyber-Physical SCADA System

## Overview
A unified mega system for flood management, water distribution, and agricultural intelligence in Pakistan's Indus Basin.

## Projects
1. **GeoVision AI** - AI-powered remote sensing for drought/flood prediction
2. **Flood SCADA** - Automated barrage gate control
3. **Soil Monitoring** - Salinity and land degradation tracking

## Tech Stack
- Frontend: Next.js + TypeScript + Tailwind
- Backend: FastAPI + Python
- ML: XGBoost + Google Earth Engine
- Real-time: MQTT + WebSockets

## Setup

### Backend

```bash
cd packages/backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Dashboard

```bash
cd packages/dashboard
npm install
npm run dev
```

### ML Pipeline

```bash
cd packages/ml-pipeline
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
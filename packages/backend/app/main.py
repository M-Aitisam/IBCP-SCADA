from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(
    title="IBCP-SCADA API",
    description="Indus Basin Cyber-Physical SCADA System",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        "message": "IBCP-SCADA API",
        "version": "1.0.0",
        "status": "operational"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/v1/geovision/drought")
async def get_drought_data():
    """GeoVision AI - Drought severity data"""
    return {
        "status": "success",
        "data": {
            "tehsils": [
                {"name": "Lahore", "severity": "Moderate", "score": 65},
                {"name": "Multan", "severity": "Severe", "score": 25},
                {"name": "Faisalabad", "severity": "Normal", "score": 85}
            ]
        }
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

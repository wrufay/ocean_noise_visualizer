from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
# Command to run the backend server locally: uvicorn main:app --reload

# This file is used to initialize FastAPI, register routes and configure middleware*

app = FastAPI()

# CORS configuration for deployment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"message": "app is running"}

@app.get("/api/noise")
def get_noise():
    return {
        # Fake sample data
        "source": "wind_turbine_1",
        "location": {"lat": 45.361336, "lon": -75.722396},
        "decibels": 120
    }


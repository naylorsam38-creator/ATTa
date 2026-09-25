from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="FastAPI Service")
ITEMS = [{"id": 1, "name": "sensor-a"}, {"id": 2, "name": "sensor-b"}]

@app.get("/", response_class=HTMLResponse)
def index():
    return "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>FastAPI Service</title></head><body><h1>FastAPI Service</h1><p>Sensors: 2</p></body></html>"

@app.get("/api/health")
def health():
    return {"ok": True}

@app.get("/api/items")
def items():
    return ITEMS

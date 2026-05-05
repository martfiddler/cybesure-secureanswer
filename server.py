from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return "running"

@app.get("/health")
def health():
    return "OK"

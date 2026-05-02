from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import upload, answer, export

app = FastAPI(
    title="CybeSure SecureAnswer API",
    description="AI-powered security questionnaire answering system",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(answer.router, prefix="/answer", tags=["answer"])
app.include_router(export.router, prefix="/export", tags=["export"])

@app.get("/health")
def health():
    return {"status": "ok", "service": "CybeSure SecureAnswer"}

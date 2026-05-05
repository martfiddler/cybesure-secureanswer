from typing import Optional

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


app = FastAPI(title="Render FastAPI App", version="1.0.0")


class AskRequest(BaseModel):
    question: Optional[str] = None


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "FastAPI app is running"}


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "OK"


@app.post("/ask")
def ask(request: Optional[AskRequest] = None) -> dict[str, str]:
    question = request.question if request and request.question else "No question provided"
    return {
        "question": question,
        "answer": "This is a mock response from the FastAPI app.",
    }

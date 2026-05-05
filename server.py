from typing import Optional

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel


app = FastAPI(title="Render FastAPI App", version="1.0.0")


class AskRequest(BaseModel):
    question: Optional[str] = None


def mock_answer(question: Optional[str] = None) -> dict[str, str]:
    return {
        "question": question or "No question provided",
        "answer": "This is a mock response from the FastAPI app.",
    }


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "FastAPI app is running"}


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "OK"


@app.get("/ask")
def ask_get(question: Optional[str] = None) -> dict[str, str]:
    return mock_answer(question)


@app.post("/ask")
def ask(request: Optional[AskRequest] = None) -> dict[str, str]:
    return mock_answer(request.question if request else None)

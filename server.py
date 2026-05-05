import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field


app = FastAPI(title="AI Questionnaire API")


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)


class AskResponse(BaseModel):
    question: str
    response: str


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "OK"


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    question = request.question.strip()
    return AskResponse(
        question=question,
        response=f"Mock AI response for: {question}",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)

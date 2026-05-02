import io
import os
import json
import uuid
import numpy as np
import pandas as pd
import pdfplumber
import faiss
import tiktoken
import anthropic

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
from docx import Document
from docx.shared import RGBColor
from openai import OpenAI

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SESSIONS: dict = {}
EMBEDDING_MODEL = "text-embedding-3-large"
CHUNK_SIZE = 750
CHUNK_OVERLAP = 100
TOP_K = 15
encoder = tiktoken.get_encoding("cl100k_base")

app = FastAPI(title="CybeSure SecureAnswer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>CybeSure SecureAnswer is running</h1><p>Visit /docs for API docs</p>")

def parse_file(filename: str, data: bytes) -> list[str]:
    ext = filename.lower().rsplit(".", 1)[-1]
    if ext in ("xlsx", "xls"):
        df = pd.read_excel(io.BytesIO(data))
        return [" | ".join(str(v) for v in r.values if pd.notna(v)) for _, r in df.iterrows()]
    if ext == "csv":
        df = pd.read_csv(io.BytesIO(data))
        return [" | ".join(str(v) for v in r.values if pd.notna(v)) for _, r in df.iterrows()]
    if ext == "pdf":
        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t:
                    pages.append(t)
        return pages
    if ext in ("docx", "doc"):
        doc = Document(io.BytesIO(data))
        return [p.text for p in doc.paragraphs if p.text.strip()]
    return [data.decode("utf-8", errors="ignore")]

def chunk_text(text: str) -> list[str]:
    tokens = encoder.encode(text)
    chunks, start = [], 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE, len(tokens))
        chunks.append(encoder.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

def embed_texts(texts: list[str]) -> list[list[float]]:
    all_emb = []
    for i in range(0, len(texts), 100):
        resp = openai_client.embeddings.create(input=texts[i:i+100], model=EMBEDDING_MODEL)
        all_emb.extend([r.embedding for r in resp.data])
    return all_emb

def expand_query(q: str) -> str:
    return f"{q} cyber security policy compliance evidence control documentation"

def retrieve(session_id: str, question: str) -> list[str]:
    index, chunks = SESSIONS[session_id]
    qv = np.array([embed_texts([expand_query(question)])[0]], dtype="float32")
    k = min(TOP_K, len(chunks))
    _, idxs = index.search(qv, k)
    seen, results = set(), []
    for i in idxs[0]:
        c = chunks[i]
        if c not in seen:
            seen.add(c)
            results.append(c)
    return results

SYSTEM_PROMPT = """You are a cyber security compliance expert.
Answer using ONLY the provided source documents. Do not use prior knowledge.
Rules:
- Combine multiple sources if needed
- Be audit-ready and precise
- If partial evidence: say "Based on available documentation..."
- If no evidence: say "Not evidenced in provided documentation"

Respond in JSON only (no markdown):
{"confidence":"Yes|No|Partial","explanation":"concise audit-ready answer","sources":["brief quote"]}"""

def ask_claude(question: str, chunks: list[str]) -> dict:
    context = "\n\n---\n\n".join(chunks)
    msg = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Source documents:\n{context}\n\nQuestion: {question}\n\nJSON only:"}]
    )
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Running</h1>")
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload/questionnaire")
async def upload_questionnaire(file: UploadFile = File(...)):
    data = await file.read()
    rows = parse_file(file.filename, data)
    questions = [{"id": i, "text": r.strip(), "category": None}
                 for i, r in enumerate(rows) if r.strip() and len(r.strip()) > 5]
    return {"questions": questions, "total": len(questions), "filename": file.filename}

@app.post("/upload/documents")
async def upload_documents(files: List[UploadFile] = File(...), session_id: str = None):
    if not session_id:
        session_id = str(uuid.uuid4())
    raw = []
    for f in files:
        data = await f.read()
        raw.extend(parse_file(f.filename, data))
    chunks = []
    for text in raw:
        chunks.extend(chunk_text(text))
    embeddings = embed_texts(chunks)
    vectors = np.array(embeddings, dtype="float32")
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(vectors)
    SESSIONS[session_id] = (index, chunks)
    return {"session_id": session_id, "chunks_created": len(chunks)}

class Question(BaseModel):
    id: int
    text: str
    category: Optional[str] = None

class AnswerRequest(BaseModel):
    session_id: str
    questions: List[Question]

@app.post("/answer")
async def answer(req: AnswerRequest):
    if req.session_id not in SESSIONS:
        raise HTTPException(404, "Session not found. Upload documents first.")
    results = []
    for q in req.questions:
        chunks = retrieve(req.session_id, q.text)
        r = ask_claude(q.text, chunks)
        results.append({
            "question_id": q.id,
            "question": q.text,
            "category": q.category,
            "confidence": r.get("confidence", "Partial"),
            "explanation": r.get("explanation", ""),
            "sources": r.get("sources", [])
        })
    del SESSIONS[req.session_id]
    return {"results": results, "total": len(results)}

class ResultItem(BaseModel):
    question_id: int
    question: str
    category: Optional[str] = None
    confidence: str
    explanation: str
    sources: List[str]

class ExportRequest(BaseModel):
    results: List[ResultItem]
    format: str

@app.post("/export")
async def export(req: ExportRequest):
    fmt = req.format.lower()
    results = req.results

    if fmt == "json":
        content = json.dumps([r.dict() for r in results], indent=2)
        return StreamingResponse(io.BytesIO(content.encode()), media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=results.json"})

    if fmt == "excel":
        rows = [{"ID": r.question_id, "Question": r.question, "Confidence": r.confidence,
                 "Explanation": r.explanation, "Sources": " | ".join(r.sources)} for r in results]
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Results")
        buf.seek(0)
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=results.xlsx"})

    if fmt == "word":
        doc = Document()
        doc.add_heading("CybeSure SecureAnswer — Results", 0)
        for r in results:
            doc.add_heading(f"Q{r.question_id+1}: {r.question[:80]}", level=2)
            p = doc.add_paragraph()
            run = p.add_run(f"Confidence: {r.confidence}")
            run.font.color.rgb = (RGBColor(0,128,0) if r.confidence=="Yes"
                                  else RGBColor(200,0,0) if r.confidence=="No"
                                  else RGBColor(180,100,0))
            doc.add_paragraph(r.explanation)
            if r.sources:
                doc.add_paragraph("Sources: " + " | ".join(r.sources), style="Intense Quote")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": "attachment; filename=results.docx"})

    if fmt == "pdf":
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [Paragraph("CybeSure SecureAnswer — Results", styles["Title"]), Spacer(1,20)]
        for r in results:
            story.append(Paragraph(f"<b>Q{r.question_id+1}:</b> {r.question}", styles["Heading2"]))
            story.append(Paragraph(f"<b>Confidence:</b> {r.confidence}", styles["Normal"]))
            story.append(Paragraph(r.explanation, styles["Normal"]))
            if r.sources:
                story.append(Paragraph(f"<i>{' | '.join(r.sources)}</i>", styles["Normal"]))
            story.append(Spacer(1,12))
        doc.build(story)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=results.pdf"})

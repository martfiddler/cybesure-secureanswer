import io
import os
import json
import uuid
import gc
import re
import numpy as np
import pandas as pd
import pdfplumber
import faiss
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

# ── memory-optimised constants ────────────────────────────────────────────────
EMBEDDING_MODEL = "text-embedding-3-small"  # Smaller model = less memory than 3-large
CHUNK_SIZE = 400        # Smaller chunks = less memory
CHUNK_OVERLAP = 50      # Reduced overlap
TOP_K = 10              # Fewer chunks retrieved per question
EMBED_BATCH = 20        # Smaller embedding batches
MAX_CHUNKS = 500        # Cap total chunks to prevent OOM

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
    return HTMLResponse(content="<h1>CybeSure SecureAnswer is running</h1>")

# ── file parsing ──────────────────────────────────────────────────────────────

def parse_pdf(data: bytes) -> list[str]:
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t and t.strip():
                    pages.append(t[:3000])  # Cap page size
    except Exception as e:
        print(f"PDF error: {e}")
    return pages

def parse_docx(data: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(data))
        return [p.text for p in doc.paragraphs if p.text.strip()]
    except Exception as e:
        print(f"DOCX error: {e}")
        return []

def parse_doc(data: bytes) -> list[str]:
    # Try python-docx first
    try:
        doc = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if paragraphs:
            return paragraphs
    except Exception:
        pass
    # Raw text fallback
    try:
        text = data.decode('latin-1', errors='ignore')
        chunks = re.findall(r'[A-Za-z0-9\s\.\,\!\?\:\;\-\(\)\'\"]{20,}', text)
        readable = ' '.join(chunks)
        if readable.strip():
            return [readable[:5000]]
    except Exception:
        pass
    return []

def parse_excel(data: bytes) -> list[str]:
    try:
        df = pd.read_excel(io.BytesIO(data))
        rows = []
        for _, row in df.iterrows():
            r = " | ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip())
            if r.strip():
                rows.append(r)
        return rows
    except Exception as e:
        print(f"Excel error: {e}")
        return []

def parse_csv(data: bytes) -> list[str]:
    try:
        df = pd.read_csv(io.BytesIO(data))
        rows = []
        for _, row in df.iterrows():
            r = " | ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip())
            if r.strip():
                rows.append(r)
        return rows
    except Exception as e:
        print(f"CSV error: {e}")
        return []

def parse_file(filename: str, data: bytes) -> list[str]:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "pdf":
        return parse_pdf(data)
    elif ext == "docx":
        return parse_docx(data)
    elif ext == "doc":
        return parse_doc(data)
    elif ext in ("xlsx", "xls"):
        return parse_excel(data)
    elif ext == "csv":
        return parse_csv(data)
    else:
        return [data.decode("utf-8", errors="ignore")[:5000]]

# ── chunking ──────────────────────────────────────────────────────────────────

def simple_chunk(text: str) -> list[str]:
    """Simple word-based chunking - no tiktoken needed = less memory."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + CHUNK_SIZE, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end == len(words):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks

# ── embeddings ────────────────────────────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed in small batches to save memory."""
    all_emb = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i:i+EMBED_BATCH]
        resp = openai_client.embeddings.create(input=batch, model=EMBEDDING_MODEL)
        all_emb.extend([r.embedding for r in resp.data])
        gc.collect()  # Free memory after each batch
    return all_emb

# ── retrieval ─────────────────────────────────────────────────────────────────

def expand_query(q: str) -> str:
    return f"{q} cyber security policy compliance evidence control"

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

# ── Claude ────────────────────────────────────────────────────────────────────

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
    # Limit context size to save memory
    context = "\n\n---\n\n".join(chunks[:8])
    msg = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",  # Haiku = faster and cheaper than Opus
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Source documents:\n{context}\n\nQuestion: {question}\n\nJSON only:"}]
    )
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)

# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "sessions": len(SESSIONS)}

@app.post("/upload/questionnaire")
async def upload_questionnaire(file: UploadFile = File(...)):
    data = await file.read()
    rows = parse_file(file.filename, data)
    questions = [
        {"id": i, "text": r.strip(), "category": None}
        for i, r in enumerate(rows)
        if r.strip() and len(r.strip()) > 5
    ]
    return {"questions": questions, "total": len(questions), "filename": file.filename}

@app.post("/upload/documents")
async def upload_documents(files: List[UploadFile] = File(...), session_id: str = None):
    if not session_id:
        session_id = str(uuid.uuid4())

    raw = []
    failed = []
    for f in files:
        try:
            data = await f.read()
            parsed = parse_file(f.filename, data)
            if parsed:
                raw.extend(parsed)
            else:
                failed.append(f.filename)
            del data  # Free memory immediately
            gc.collect()
        except Exception as e:
            print(f"Failed {f.filename}: {e}")
            failed.append(f.filename)

    if not raw:
        raise HTTPException(400, f"Could not extract text from any documents.")

    # Chunk all text
    chunks = []
    for text in raw:
        if text.strip():
            chunks.extend(simple_chunk(text))
    del raw
    gc.collect()

    # Cap chunks to prevent OOM
    if len(chunks) > MAX_CHUNKS:
        print(f"Capping chunks from {len(chunks)} to {MAX_CHUNKS}")
        chunks = chunks[:MAX_CHUNKS]

    # Embed chunks
    embeddings = embed_texts(chunks)
    vectors = np.array(embeddings, dtype="float32")
    del embeddings
    gc.collect()

    # Build FAISS index
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(vectors)
    del vectors
    gc.collect()

    SESSIONS[session_id] = (index, chunks)

    return {
        "session_id": session_id,
        "chunks_created": len(chunks),
        "files_processed": len(files) - len(failed),
        "files_failed": failed
    }

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
        gc.collect()
    del SESSIONS[req.session_id]
    gc.collect()
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
        return StreamingResponse(io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=results.json"})

    if fmt == "excel":
        rows = [{"ID": r.question_id, "Question": r.question,
                 "Confidence": r.confidence, "Explanation": r.explanation,
                 "Sources": " | ".join(r.sources)} for r in results]
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

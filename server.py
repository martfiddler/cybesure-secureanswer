import io
import os
import json
import uuid
import gc
import re
import requests
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
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
claude_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SESSIONS: dict = {}
EMBEDDING_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
TOP_K = 12
EMBED_BATCH = 20
MAX_CHUNKS = 600
DOC_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv', '.txt'}

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

# ── questionnaire extraction ──────────────────────────────────────────────────

def extract_questions_from_excel(data: bytes) -> list[str]:
    """
    Extract questions from Excel files.
    Handles structure: Row 1 = headers (Ref, Question, Answer)
    Questions are in the column headed 'Question'.
    """
    questions = []
    try:
        xl = pd.ExcelFile(io.BytesIO(data))
        for sheet_name in xl.sheet_names:
            try:
                # Read with first row as header
                df = pd.read_excel(io.BytesIO(data), sheet_name=sheet_name)
                
                if df.empty:
                    continue

                # Normalise column names
                df.columns = [str(c).strip().lower() for c in df.columns]

                # Strategy 1: Find column named 'question' or similar
                q_col = None
                for col in df.columns:
                    if col in ('question', 'questions', 'requirement', 'requirements', 'control', 'controls', 'ask', 'query', 'description'):
                        q_col = col
                        break

                # Strategy 2: Find column containing most question-like text
                if q_col is None:
                    best = 0
                    for col in df.columns:
                        vals = df[col].dropna().astype(str)
                        score = sum(1 for v in vals if len(v) > 15)
                        if score > best:
                            best = score
                            q_col = col

                if q_col is not None:
                    for val in df[q_col].dropna():
                        text = str(val).strip()
                        if text and len(text) > 3 and text.lower() not in ['nan', 'none', 'n/a', '-', 'question']:
                            questions.append(text)

                # If still nothing, try every cell that looks like a question
                if not questions:
                    for col in df.columns:
                        for val in df[col].dropna():
                            text = str(val).strip()
                            if len(text) > 10 and text.lower() not in ['nan', 'none', 'n/a']:
                                questions.append(text)

            except Exception as e:
                print(f"Sheet {sheet_name} error: {e}")
                # Try reading without header
                try:
                    df2 = pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, header=None)
                    # Find the row that has 'question' in it
                    header_row = 0
                    q_col_idx = 1  # Default to column B (index 1)
                    for i, row in df2.iterrows():
                        for j, val in enumerate(row):
                            if pd.notna(val) and 'question' in str(val).lower():
                                header_row = i
                                q_col_idx = j
                                break
                    for i, row in df2.iterrows():
                        if i <= header_row:
                            continue
                        val = row.iloc[q_col_idx] if q_col_idx < len(row) else None
                        if pd.notna(val):
                            text = str(val).strip()
                            if text and len(text) > 3:
                                questions.append(text)
                except Exception:
                    pass

    except Exception as e:
        print(f"Excel parse error: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for q in questions:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def extract_questions_from_csv(data: bytes) -> list[str]:
    """Extract questions from CSV files."""
    questions = []
    try:
        for enc in ['utf-8', 'latin-1', 'cp1252']:
            try:
                df = pd.read_csv(io.BytesIO(data), encoding=enc)
                break
            except Exception:
                continue

        df.columns = [str(c).strip().lower() for c in df.columns]

        q_col = None
        for col in df.columns:
            if col in ('question', 'questions', 'requirement', 'requirements', 'control', 'description'):
                q_col = col
                break

        if q_col is None:
            best = 0
            for col in df.columns:
                vals = df[col].dropna().astype(str)
                score = sum(1 for v in vals if len(v) > 15)
                if score > best:
                    best = score
                    q_col = col

        if q_col:
            for val in df[q_col].dropna():
                text = str(val).strip()
                if text and len(text) > 3 and text.lower() not in ['nan', 'none', 'n/a']:
                    questions.append(text)

    except Exception as e:
        print(f"CSV parse error: {e}")
    return questions


def extract_questions_from_docx(data: bytes) -> list[str]:
    """Extract questions from Word documents."""
    questions = []
    try:
        doc = Document(io.BytesIO(data))
        for p in doc.paragraphs:
            text = p.text.strip()
            if text and len(text) > 3:
                questions.append(text)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text and len(text) > 3:
                        questions.append(text)
    except Exception as e:
        print(f"DOCX parse error: {e}")
    return questions


def extract_questions_from_pdf(data: bytes) -> list[str]:
    """Extract questions from PDF files."""
    questions = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line and len(line) > 5:
                            questions.append(line)
    except Exception as e:
        print(f"PDF parse error: {e}")
    return questions


def extract_questions(filename: str, data: bytes) -> list[str]:
    """Route to correct extractor."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in ("xlsx", "xls"):
        return extract_questions_from_excel(data)
    elif ext == "csv":
        return extract_questions_from_csv(data)
    elif ext in ("docx", "doc"):
        return extract_questions_from_docx(data)
    elif ext == "pdf":
        return extract_questions_from_pdf(data)
    else:
        return [l.strip() for l in data.decode("utf-8", errors="ignore").split('\n') if l.strip() and len(l.strip()) > 5]

# ── document file parsing ─────────────────────────────────────────────────────

def parse_pdf(data: bytes) -> list[str]:
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for p in pdf.pages:
                t = p.extract_text()
                if t and t.strip():
                    pages.append(t[:4000])
    except Exception as e:
        print(f"PDF error: {e}")
    return pages

def parse_docx(data: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    texts.append(row_text)
        return texts
    except Exception as e:
        print(f"DOCX error: {e}")
        return []

def parse_doc(data: bytes) -> list[str]:
    try:
        doc = Document(io.BytesIO(data))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    texts.append(row_text)
        if texts:
            return texts
    except Exception:
        pass
    try:
        text = data.decode('latin-1', errors='ignore')
        chunks = re.findall(r'[A-Za-z0-9\s\.\,\!\?\:\;\-\(\)\'\"]{20,}', text)
        readable = ' '.join(chunks)
        if readable.strip():
            return [readable[:6000]]
    except Exception:
        pass
    return []

def parse_excel_doc(data: bytes) -> list[str]:
    try:
        # Read all sheets
        xl = pd.ExcelFile(io.BytesIO(data))
        all_rows = []
        for sheet in xl.sheet_names:
            df = pd.read_excel(io.BytesIO(data), sheet_name=sheet)
            for _, row in df.iterrows():
                r = " | ".join(str(v) for v in row.values if pd.notna(v) and str(v).strip())
                if r.strip():
                    all_rows.append(r)
        return all_rows
    except Exception as e:
        print(f"Excel error: {e}")
        return []

def parse_csv_doc(data: bytes) -> list[str]:
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
    if ext == "pdf": return parse_pdf(data)
    elif ext == "docx": return parse_docx(data)
    elif ext == "doc": return parse_doc(data)
    elif ext in ("xlsx", "xls"): return parse_excel_doc(data)
    elif ext == "csv": return parse_csv_doc(data)
    else: return [data.decode("utf-8", errors="ignore")[:5000]]

# ── URL fetching ──────────────────────────────────────────────────────────────

def is_document_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOC_EXTENSIONS)

def fetch_document_from_url(url: str) -> tuple[str, bytes]:
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)', 'Accept': '*/*'}
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    filename = url.split('/')[-1].split('?')[0] or 'document'
    cd = resp.headers.get('Content-Disposition', '')
    if 'filename=' in cd:
        filename = cd.split('filename=')[-1].strip().strip('"\'')
    if '.' not in filename:
        ct = resp.headers.get('Content-Type', '')
        if 'pdf' in ct: filename += '.pdf'
        elif 'word' in ct: filename += '.docx'
        elif 'excel' in ct: filename += '.xlsx'
    return filename, resp.content

def discover_documents_from_page(url: str) -> list[str]:
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)'}
    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch page {url}: {e}")
        return []
    soup = BeautifulSoup(resp.text, 'html.parser')
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    doc_urls = []
    for tag in soup.find_all('a', href=True):
        href = tag['href']
        if href.startswith('http'): full_url = href
        elif href.startswith('/'): full_url = base + href
        else: full_url = urljoin(url, href)
        if is_document_url(full_url):
            doc_urls.append(full_url)
    return list(set(doc_urls))

def fetch_all_from_url(url: str) -> list[tuple[str, bytes]]:
    results = []
    # Google Drive single file
    if 'drive.google.com/file' in url or ('docs.google.com' in url and '/d/' in url):
        file_id = None
        if '/d/' in url: file_id = url.split('/d/')[1].split('/')[0]
        elif 'id=' in url: file_id = url.split('id=')[1].split('&')[0]
        if file_id:
            try:
                fname, data = fetch_document_from_url(
                    f"https://drive.google.com/uc?export=download&id={file_id}")
                results.append((fname, data))
                return results
            except Exception as e:
                print(f"Google Drive download failed: {e}")
    # Direct document URL
    if is_document_url(url):
        try:
            fname, data = fetch_document_from_url(url)
            results.append((fname, data))
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
        return results
    # Page scan
    try:
        doc_urls = discover_documents_from_page(url)
        if doc_urls:
            for doc_url in doc_urls[:20]:
                try:
                    fname, data = fetch_document_from_url(doc_url)
                    results.append((fname, data))
                except Exception as e:
                    print(f"Failed {doc_url}: {e}")
        else:
            resp = requests.get(url, timeout=20)
            soup = BeautifulSoup(resp.text, 'html.parser')
            text = soup.get_text(separator='\n', strip=True)
            if text:
                results.append(('webpage.txt', text.encode('utf-8')))
    except Exception as e:
        print(f"Page scan failed: {e}")
    return results

# ── chunking ──────────────────────────────────────────────────────────────────

def simple_chunk(text: str) -> list[str]:
    words = text.split()
    chunks, start = [], 0
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
    all_emb = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = openai_client.embeddings.create(
            input=texts[i:i+EMBED_BATCH], model=EMBEDDING_MODEL)
        all_emb.extend([r.embedding for r in resp.data])
        gc.collect()
    return all_emb

# ── retrieval ─────────────────────────────────────────────────────────────────

def expand_query(q: str) -> str:
    return f"{q} cyber security policy procedure control evidence implementation"

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

SYSTEM_PROMPT = """You are a cyber security compliance expert answering a security questionnaire on behalf of an organisation.

Using the provided document extracts, write a clear, direct answer to each question.

Rules:
- Write the answer as if you are the organisation responding to the questionnaire
- Use the actual content from the documents to construct your answer
- Be specific and detailed - quote exact processes, controls, and procedures described in the documents
- Do NOT mention what policies are called or what documents contain
- Do NOT say what is missing or not covered
- Do NOT reference document titles or policy names
- Just answer the question directly and thoroughly using all relevant information found
- Assign a confidence percentage based on how fully the documents answer the question

Respond in JSON only (no markdown):
{
  "confidence": "Yes|No|Partial",
  "confidence_pct": 85,
  "explanation": "Direct detailed answer using specific content from the documents",
  "sources": ["Key quote or specific detail from the documents"]
}"""

def ask_claude(question: str, chunks: list[str]) -> dict:
    context = "\n\n---\n\n".join(chunks[:10])
    msg = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Document extracts:\n{context}\n\nQuestion: {question}\n\nJSON only:"}]
    )
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    result = json.loads(text)
    if "confidence_pct" not in result:
        result["confidence_pct"] = {"Yes": 90, "Partial": 55, "No": 10}.get(
            result.get("confidence", "Partial"), 50)
    return result

# ── index builder ─────────────────────────────────────────────────────────────

def build_index(raw_texts: list[str], session_id: str) -> dict:
    chunks = []
    for text in raw_texts:
        if text.strip():
            chunks.extend(simple_chunk(text))
    if not chunks:
        raise HTTPException(400, "Could not extract any text from the provided documents.")
    if len(chunks) > MAX_CHUNKS:
        chunks = chunks[:MAX_CHUNKS]
    embeddings = embed_texts(chunks)
    vectors = np.array(embeddings, dtype="float32")
    del embeddings
    gc.collect()
    index = faiss.IndexFlatL2(vectors.shape[1])
    index.add(vectors)
    del vectors
    gc.collect()
    SESSIONS[session_id] = (index, chunks)
    return {"session_id": session_id, "chunks_created": len(chunks)}

# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload/questionnaire")
async def upload_questionnaire(file: UploadFile = File(...)):
    data = await file.read()
    questions_text = extract_questions(file.filename, data)

    if not questions_text:
        raise HTTPException(
            400,
            f"No questions found in '{file.filename}'. "
            f"Expected a column named 'Question' in your Excel file."
        )

    questions = [
        {"id": i, "text": q.strip(), "category": None}
        for i, q in enumerate(questions_text)
        if q.strip() and len(q.strip()) > 3
    ]

    return {"questions": questions, "total": len(questions), "filename": file.filename}

@app.post("/upload/documents")
async def upload_documents(files: List[UploadFile] = File(...), session_id: str = None):
    if not session_id:
        session_id = str(uuid.uuid4())
    raw, failed = [], []
    for f in files:
        try:
            data = await f.read()
            parsed = parse_file(f.filename, data)
            if parsed: raw.extend(parsed)
            else: failed.append(f.filename)
            del data
            gc.collect()
        except Exception as e:
            print(f"Failed {f.filename}: {e}")
            failed.append(f.filename)
    if not raw:
        raise HTTPException(400, "Could not extract text from any documents.")
    result = build_index(raw, session_id)
    result["files_processed"] = len(files) - len(failed)
    result["files_failed"] = failed
    return result

class UrlRequest(BaseModel):
    urls: List[str]
    session_id: Optional[str] = None

@app.post("/upload/documents-url")
async def upload_documents_url(req: UrlRequest):
    session_id = req.session_id or str(uuid.uuid4())
    raw, files_processed, files_failed, doc_names = [], 0, [], []
    for url in req.urls:
        try:
            file_pairs = fetch_all_from_url(url)
            if not file_pairs:
                files_failed.append(url)
                continue
            for fname, data in file_pairs:
                try:
                    parsed = parse_file(fname, data)
                    if parsed:
                        raw.extend(parsed)
                        files_processed += 1
                        doc_names.append(fname)
                    else:
                        files_failed.append(fname)
                except Exception as e:
                    print(f"Parse error {fname}: {e}")
                    files_failed.append(fname)
                del data
                gc.collect()
        except Exception as e:
            print(f"URL error {url}: {e}")
            files_failed.append(url)
    if not raw:
        raise HTTPException(
            400,
            "Could not fetch or read documents from the provided URLs. "
            "For Google Drive: share each file individually with 'Anyone with link can view' "
            "and paste the individual file share link."
        )
    result = build_index(raw, session_id)
    result["files_processed"] = files_processed
    result["files_failed"] = files_failed
    result["documents_found"] = doc_names
    return result

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
            "question_id": q.id, "question": q.text, "category": q.category,
            "confidence": r.get("confidence", "Partial"),
            "confidence_pct": r.get("confidence_pct", 50),
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
    confidence_pct: Optional[int] = 50
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
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.json"})

    if fmt == "excel":
        rows = [{"ID": r.question_id+1, "Question": r.question,
                 "Confidence": r.confidence, "Confidence %": f"{r.confidence_pct}%",
                 "Explanation": r.explanation, "Sources": " | ".join(r.sources)} for r in results]
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="SecureAnswer Results")
        buf.seek(0)
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.xlsx"})

    if fmt == "word":
        doc = Document()
        doc.add_heading("CybeSure SecureAnswer — Compliance Results", 0)
        for r in results:
            doc.add_heading(f"Q{r.question_id+1}: {r.question[:100]}", level=2)
            p = doc.add_paragraph()
            run = p.add_run(f"Confidence: {r.confidence} ({r.confidence_pct}%)")
            run.font.color.rgb = (RGBColor(0,160,100) if r.confidence=="Yes"
                                  else RGBColor(200,0,0) if r.confidence=="No"
                                  else RGBColor(200,130,0))
            run.bold = True
            doc.add_paragraph(r.explanation)
            if r.sources:
                for s in r.sources:
                    doc.add_paragraph(f"• {s}", style="Intense Quote")
            doc.add_paragraph("")
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        return StreamingResponse(buf,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.docx"})

    if fmt == "pdf":
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [Paragraph("CybeSure SecureAnswer — Compliance Results", styles["Title"]),
                 Spacer(1, 20)]
        for r in results:
            story.append(Paragraph(f"<b>Q{r.question_id+1}:</b> {r.question}", styles["Heading2"]))
            story.append(Paragraph(f"<b>Confidence:</b> {r.confidence} ({r.confidence_pct}%)", styles["Normal"]))
            story.append(Paragraph(r.explanation, styles["Normal"]))
            if r.sources:
                for s in r.sources:
                    story.append(Paragraph(f"<i>• {s}</i>", styles["Normal"]))
            story.append(Spacer(1, 12))
        doc.build(story)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.pdf"})

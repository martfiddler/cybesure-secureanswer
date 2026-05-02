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

# Supported document extensions
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

# ── file parsing ──────────────────────────────────────────────────────────────

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
    elif ext == "txt":
        return [data.decode("utf-8", errors="ignore")[:5000]]
    else:
        return [data.decode("utf-8", errors="ignore")[:5000]]

# ── URL / SharePoint fetching ─────────────────────────────────────────────────

def is_document_url(url: str) -> bool:
    """Check if URL points directly to a document file."""
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in DOC_EXTENSIONS)

def fetch_document_from_url(url: str) -> tuple[str, bytes]:
    """Download a document from a URL. Returns (filename, bytes)."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)',
        'Accept': '*/*'
    }
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    
    # Try to get filename from URL or Content-Disposition header
    filename = url.split('/')[-1].split('?')[0] or 'document'
    cd = resp.headers.get('Content-Disposition', '')
    if 'filename=' in cd:
        filename = cd.split('filename=')[-1].strip().strip('"\'')
    
    # Detect type from content-type if no extension
    if '.' not in filename:
        ct = resp.headers.get('Content-Type', '')
        if 'pdf' in ct:
            filename += '.pdf'
        elif 'word' in ct or 'docx' in ct:
            filename += '.docx'
        elif 'excel' in ct or 'spreadsheet' in ct:
            filename += '.xlsx'
    
    return filename, resp.content

def discover_documents_from_page(url: str) -> list[str]:
    """
    Scan a web page or SharePoint/Google Drive folder URL 
    and find all linked document files.
    """
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; CybeSure/1.0)'}
    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch page {url}: {e}")
        return []

    # Parse HTML for document links
    soup = BeautifulSoup(resp.text, 'html.parser')
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    
    doc_urls = []
    for tag in soup.find_all('a', href=True):
        href = tag['href']
        # Make absolute URL
        if href.startswith('http'):
            full_url = href
        elif href.startswith('/'):
            full_url = base + href
        else:
            full_url = urljoin(url, href)
        
        # Check if it's a document
        if is_document_url(full_url):
            doc_urls.append(full_url)
    
    return list(set(doc_urls))  # Deduplicate

def fetch_all_from_url(url: str) -> list[tuple[str, bytes]]:
    """
    Smart URL handler:
    - If URL points directly to a document → download it
    - If URL is a page/folder → scan for document links and download all
    - Handles SharePoint, Google Drive export links, and regular web pages
    """
    results = []
    
    # Handle Google Drive folder links
    if 'drive.google.com/drive/folders' in url:
        # Google Drive folders require API access - return helpful error
        print(f"Google Drive folder detected - direct folder access requires API key")
        # Try to fetch the page anyway in case it's publicly accessible
        try:
            doc_urls = discover_documents_from_page(url)
            for doc_url in doc_urls[:20]:  # Cap at 20 docs
                try:
                    fname, data = fetch_document_from_url(doc_url)
                    results.append((fname, data))
                except Exception as e:
                    print(f"Failed to fetch {doc_url}: {e}")
        except Exception:
            pass
        return results
    
    # Handle Google Drive single file share links
    if 'drive.google.com/file' in url or 'docs.google.com' in url:
        # Convert to direct download URL
        file_id = None
        if '/d/' in url:
            file_id = url.split('/d/')[1].split('/')[0]
        elif 'id=' in url:
            file_id = url.split('id=')[1].split('&')[0]
        
        if file_id:
            download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            try:
                fname, data = fetch_document_from_url(download_url)
                results.append((fname, data))
                return results
            except Exception as e:
                print(f"Google Drive download failed: {e}")
    
    # Handle SharePoint links
    if 'sharepoint.com' in url:
        # SharePoint requires authentication for private files
        # Try to fetch directly - works for publicly shared links
        try:
            if is_document_url(url):
                fname, data = fetch_document_from_url(url)
                results.append((fname, data))
            else:
                # Try scanning the page
                doc_urls = discover_documents_from_page(url)
                for doc_url in doc_urls[:20]:
                    try:
                        fname, data = fetch_document_from_url(doc_url)
                        results.append((fname, data))
                    except Exception as e:
                        print(f"Failed to fetch {doc_url}: {e}")
        except Exception as e:
            print(f"SharePoint fetch failed: {e}")
        return results
    
    # Direct document URL
    if is_document_url(url):
        try:
            fname, data = fetch_document_from_url(url)
            results.append((fname, data))
        except Exception as e:
            print(f"Failed to fetch direct URL {url}: {e}")
        return results
    
    # Generic web page - scan for document links
    try:
        doc_urls = discover_documents_from_page(url)
        if doc_urls:
            for doc_url in doc_urls[:20]:
                try:
                    fname, data = fetch_document_from_url(doc_url)
                    results.append((fname, data))
                except Exception as e:
                    print(f"Failed to fetch {doc_url}: {e}")
        else:
            # No doc links found - try to extract text from the page itself
            resp = requests.get(url, timeout=20)
            soup = BeautifulSoup(resp.text, 'html.parser')
            text = soup.get_text(separator='\n', strip=True)
            if text:
                results.append(('webpage.txt', text.encode('utf-8')))
    except Exception as e:
        print(f"Page scan failed for {url}: {e}")
    
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
        batch = texts[i:i+EMBED_BATCH]
        resp = openai_client.embeddings.create(input=batch, model=EMBEDDING_MODEL)
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

SYSTEM_PROMPT = """You are a senior cyber security compliance auditor reviewing an organisation's security questionnaire.

Your job is to answer each question by carefully analysing the provided source documents.

Rules:
- Answer ONLY using the provided source documents
- Be thorough and detailed in your explanation
- Quote or reference specific sections, policies, or procedures from the documents
- Assign a confidence percentage (0-100%) based on how well the documents evidence the answer
- If Yes: explain exactly what the documents say that confirms this with specific references
- If Partial: explain what IS evidenced and what IS MISSING from the documentation
- If No: explain what is not evidenced in the documentation

Respond in JSON only (no markdown fences):
{
  "confidence": "Yes|No|Partial",
  "confidence_pct": 85,
  "explanation": "Detailed audit-ready explanation referencing specific document content and policy names",
  "sources": ["Exact quote or specific policy/section reference from the source documents"]
}"""

def ask_claude(question: str, chunks: list[str]) -> dict:
    context = "\n\n---\n\n".join(chunks[:10])
    msg = claude_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Source documents:\n{context}\n\nQuestion: {question}\n\nJSON only:"}]
    )
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    result = json.loads(text)
    if "confidence_pct" not in result:
        result["confidence_pct"] = {"Yes": 90, "Partial": 55, "No": 10}.get(result.get("confidence", "Partial"), 50)
    return result

# ── shared indexing logic ─────────────────────────────────────────────────────

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

    raw, failed = [], []
    for f in files:
        try:
            data = await f.read()
            parsed = parse_file(f.filename, data)
            if parsed:
                raw.extend(parsed)
            else:
                failed.append(f.filename)
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
    """
    Fetch and index documents from URLs.
    Supports: direct file URLs, Google Drive share links,
    SharePoint public links, and web pages with document links.
    """
    session_id = req.session_id or str(uuid.uuid4())
    
    raw = []
    files_processed = 0
    files_failed = []
    doc_names = []

    for url in req.urls:
        print(f"Processing URL: {url}")
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
                    print(f"Parse error for {fname}: {e}")
                    files_failed.append(fname)
                del data
                gc.collect()
        except Exception as e:
            print(f"URL fetch error {url}: {e}")
            files_failed.append(url)

    if not raw:
        raise HTTPException(400, 
            f"Could not fetch or extract text from any of the provided URLs. "
            f"Note: Private SharePoint/Google Drive links require the documents to be publicly shared. "
            f"Failed: {files_failed}"
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
            "question_id": q.id,
            "question": q.text,
            "category": q.category,
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
            run.font.color.rgb = (RGBColor(0,128,0) if r.confidence=="Yes"
                                  else RGBColor(200,0,0) if r.confidence=="No"
                                  else RGBColor(180,100,0))
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
        story = [Paragraph("CybeSure SecureAnswer — Compliance Results", styles["Title"]), Spacer(1,20)]
        for r in results:
            story.append(Paragraph(f"<b>Q{r.question_id+1}:</b> {r.question}", styles["Heading2"]))
            story.append(Paragraph(f"<b>Confidence:</b> {r.confidence} ({r.confidence_pct}%)", styles["Normal"]))
            story.append(Paragraph(r.explanation, styles["Normal"]))
            if r.sources:
                for s in r.sources:
                    story.append(Paragraph(f"<i>• {s}</i>", styles["Normal"]))
            story.append(Spacer(1,12))
        doc.build(story)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=secureanswer_results.pdf"})

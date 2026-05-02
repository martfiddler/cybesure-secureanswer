# CybeSure SecureAnswer

AI-powered security questionnaire auto-answering system.

## What it does

1. Upload a security questionnaire (Excel, Word, CSV, PDF)
2. Upload your policy/evidence documents
3. Claude AI reads the documents and answers every question with Yes / No / Partial + explanation + source references
4. Export results as Excel, Word, PDF, or JSON

---

## Tech Stack

- **Backend**: Python FastAPI + Uvicorn
- **AI**: Claude (Anthropic API) + OpenAI text-embedding-3-large
- **Vector Store**: FAISS (in-memory)
- **File Parsing**: pandas, pdfplumber, python-docx
- **Frontend**: HTML/CSS/JS (deployable as static or Next.js)
- **Deployment**: Render (backend) + Vercel or Render static (frontend)

---

## Local Development

### Backend

```bash
cd backend
pip install -r requirements.txt

export ANTHROPIC_API_KEY=your_key_here
export OPENAI_API_KEY=your_key_here

uvicorn app.main:app --reload --port 8000
```

API will be live at http://localhost:8000
Swagger docs at http://localhost:8000/docs

### Frontend

Open `frontend/index.html` directly in a browser for local testing.

For Next.js version:
```bash
cd frontend
npm install
npm run dev
```

---

## GitHub Repository Structure

```
cybesure-secureanswer/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── upload.py
│   │   │   ├── answer.py
│   │   │   └── export.py
│   │   ├── services/
│   │   │   ├── embeddings.py
│   │   │   ├── retrieval.py
│   │   │   ├── claude_service.py
│   │   │   ├── document_loader.py
│   │   │   └── chunking.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   └── utils/
│   │       ├── file_parsers.py
│   │       └── query_expansion.py
│   ├── requirements.txt
├── Dockerfile
├── render.yaml
├── frontend/
│   ├── index.html
│   ├── services/api.ts
│   └── package.json
└── README.md
```

---

## Deploy to Render (Backend)

1. Push this repo to GitHub
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo
4. Render will detect `render.yaml` automatically
5. Add environment variables in Render dashboard:
   - `ANTHROPIC_API_KEY`
   - `OPENAI_API_KEY`
6. Deploy — Render builds the Docker image and starts the service

Your backend URL will be: `https://cybesure-backend.onrender.com`

---

## Deploy Frontend to Vercel

1. Update `API` variable in `frontend/index.html`:
   ```javascript
   const API = 'https://cybesure-backend.onrender.com';
   ```
2. Push frontend to GitHub (or separate repo)
3. Import into Vercel → Deploy as static site

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /upload/questionnaire | Parse questionnaire file → extract questions |
| POST | /upload/documents | Upload policy docs → build FAISS index |
| POST | /answer | Answer all questions using RAG + Claude |
| POST | /export | Export results (excel/word/pdf/json) |
| GET | /health | Health check |

---

## Security

- No persistent document storage
- In-memory processing only (FAISS index lives in RAM)
- Session data deleted after answering completes
- Max ~30k tokens per Claude request

---

## Environment Variables

```
ANTHROPIC_API_KEY=xxx
OPENAI_API_KEY=xxx
```

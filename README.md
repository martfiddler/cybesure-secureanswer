# Cybersure AI Questionnaire Engine

Multi-tenant SaaS platform for automating cyber security questionnaire responses.

## What is included

- `apps/api` - Express API with JWT auth, bcrypt password hashing, Prisma/PostgreSQL persistence, Excel upload parsing, OpenAI answer generation, credit deduction, and Excel/Word/PDF export.
- `apps/web` - Next.js 14 / React 18 portal with login, registration, dashboard, upload, results, credit display, AI generation, and exports.
- `apps/extension` - Chrome Manifest v3 extension that detects portal inputs/textareas, extracts question context, calls the protected backend, and autofills answers.
- `prisma/schema.prisma` - PostgreSQL Prisma schema for users, questionnaires, questions, and answers.

Uploaded Excel files are processed in memory with Multer and are not written to disk. The database stores extracted questions and generated answers only.

## Requirements

- Node.js 20+
- PostgreSQL
- OpenAI API key

## Environment

Copy `.env.example` and fill in the values:

```bash
cp .env.example .env
```

Required variables:

- `DATABASE_URL`
- `JWT_SECRET`
- `OPENAI_API_KEY`
- `NEXT_PUBLIC_API_URL`

Optional variables:

- `OPENAI_MODEL` defaults to `gpt-4o-mini`
- `CORS_ORIGIN` defaults to `*`
- `PORT` defaults to `4000`

## Local setup

```bash
npm install
npm run prisma:generate
npm run prisma:migrate
npm run dev
```

API: `http://localhost:4000`

Web: `http://localhost:3000`

## API endpoints

Public:

- `POST /api/auth/register`
- `POST /api/auth/login`

Protected with `Authorization: Bearer <jwt>`:

- `GET /api/auth/me`
- `GET /api/questionnaire`
- `POST /api/questionnaire/upload`
- `GET /api/questionnaire/:id`
- `POST /api/ai/generate`
- `POST /api/ai/generate-questionnaire/:id`
- `GET /api/output/excel/:id`
- `GET /api/output/word/:id`
- `GET /api/output/pdf/:id`

## Chrome extension

1. Open `chrome://extensions`.
2. Enable Developer Mode.
3. Load unpacked extension from `apps/extension`.
4. Open the extension popup and set:
   - API URL, for example `https://your-api-url`
   - JWT token copied from an authenticated web session or login response.
5. Open a questionnaire portal and click "Autofill visible fields".

The extension reads placeholder, label, ARIA label, name, or nearby visible text for each field and sends it to `/api/ai/generate`.

## Render deployment

### Database

1. Create a Render PostgreSQL instance.
2. Copy its external or internal `DATABASE_URL`.

### Backend web service

- Root directory: repository root
- Build command:

```bash
npm install && npm run prisma:generate && npm run prisma:deploy
```

- Start command:

```bash
npm run start:api
```

Set environment variables in Render:

- `DATABASE_URL`
- `JWT_SECRET`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `CORS_ORIGIN`

### Frontend

Deploy `apps/web` to Vercel or Render.

For Vercel:

- Framework: Next.js
- Root directory: `apps/web`
- Build command: `npm run build`
- Environment: `NEXT_PUBLIC_API_URL=https://your-api-url`

## Security notes

- Passwords are hashed with bcrypt.
- Protected routes require JWT.
- Uploads use in-memory storage and are not persisted.
- User inputs are trimmed and constrained before persistence or AI calls.
- Use HTTPS for production API and frontend deployments.
- Credits are checked before AI calls and decremented one credit per question.

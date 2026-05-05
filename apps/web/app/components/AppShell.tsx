"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, clearAuthToken, getAuthToken } from "../lib/api";

type User = {
  id: string;
  email: string;
  credits: number;
};

type AppShellProps = {
  children?: ReactNode;
  initialView?: "dashboard" | "upload" | "results";
};

type Answer = {
  id: string;
  answer: string;
  confidence: string;
};

type Question = {
  id: string;
  text: string;
  category?: string | null;
  answers: Answer[];
};

type Questionnaire = {
  id: string;
  filename: string;
  createdAt: string;
  questions: Question[];
};

export default function AppShell({ children, initialView = "dashboard" }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [questionnaires, setQuestionnaires] = useState<Questionnaire[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!getAuthToken()) {
      router.push("/login");
      return;
    }

    api
      .get("/api/auth/me")
      .then((res) => {
        setUser(res.data.user);
        return refreshQuestionnaires();
      })
      .catch(() => {
        clearAuthToken();
        router.push("/login");
      });
  }, [router]);

  async function refreshQuestionnaires(preferredId?: string) {
    const res = await api.get("/api/questionnaire");
    setQuestionnaires(res.data.questionnaires);
    if (preferredId) {
      setSelectedId(preferredId);
    } else if (!selectedId && res.data.questionnaires[0]) {
      setSelectedId(res.data.questionnaires[0].id);
    }
  }

  function logout() {
    clearAuthToken();
    router.push("/login");
  }

  async function uploadQuestionnaire() {
    if (!file) {
      setError("Choose an Excel file before uploading.");
      return;
    }

    setLoading(true);
    setError("");
    setStatus("Uploading and parsing questionnaire...");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await api.post("/api/questionnaire/upload", form);
      setStatus(`Uploaded ${res.data.totalQuestions} questions from ${res.data.questionnaire.filename}.`);
      await refreshQuestionnaires(res.data.questionnaire.id);
    } catch (err: any) {
      setError(err.response?.data?.message || "Upload failed.");
    } finally {
      setLoading(false);
    }
  }

  async function generateAnswers(id: string) {
    setLoading(true);
    setError("");
    setStatus("Generating compliance answers...");
    try {
      const res = await api.post(`/api/ai/generate-questionnaire/${id}`);
      setUser((current) => (current ? { ...current, credits: res.data.credits } : current));
      setStatus(`Generated ${res.data.generated.length} answer(s).`);
      await refreshQuestionnaires();
    } catch (err: any) {
      setError(err.response?.data?.message || "AI generation failed.");
    } finally {
      setLoading(false);
    }
  }

  async function exportFile(id: string, format: "excel" | "word" | "pdf") {
    const res = await api.get(`/api/output/${format}/${id}`, { responseType: "blob" });
    const extension = format === "word" ? "docx" : format === "excel" ? "xlsx" : "pdf";
    const url = window.URL.createObjectURL(res.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `questionnaire-answers.${extension}`;
    link.click();
    window.URL.revokeObjectURL(url);
  }

  const selected = questionnaires.find((questionnaire) => questionnaire.id === selectedId);
  const dashboardContent = (
    <div className="stack">
      <section className="card hero-card">
        <p className="eyebrow">Dashboard</p>
        <h1>Questionnaire workspace</h1>
        <p className="muted">
          Upload Excel questionnaires, generate compliance answers, export outputs, and track usage.
        </p>
        <div className="stats">
          <span className="badge">{user?.credits ?? "..."} credits remaining</span>
          <span className="badge">{questionnaires.length} questionnaires</span>
        </div>
      </section>

      <section className="card">
        <div className="section-title">
          <div>
            <p className="eyebrow">Upload</p>
            <h2>Import an Excel questionnaire</h2>
          </div>
        </div>
        <div className="upload-row">
          <input
            className="input"
            type="file"
            accept=".xlsx,.xls"
            onChange={(event) => setFile(event.target.files?.[0] || null)}
          />
          <button className="button" disabled={loading} onClick={uploadQuestionnaire}>
            Upload
          </button>
        </div>
      </section>

      <section className="card">
        <div className="section-title">
          <div>
            <p className="eyebrow">Questionnaires</p>
            <h2>Processed files</h2>
          </div>
        </div>
        {questionnaires.length === 0 ? (
          <p className="muted">No questionnaires uploaded yet.</p>
        ) : (
          <div className="grid grid-two">
            <div className="list">
              {questionnaires.map((questionnaire) => (
                <button
                  className={`list-item ${questionnaire.id === selectedId ? "active" : ""}`}
                  key={questionnaire.id}
                  onClick={() => setSelectedId(questionnaire.id)}
                >
                  <strong>{questionnaire.filename}</strong>
                  <span>
                    {questionnaire.questions.length} question(s) ·{" "}
                    {new Date(questionnaire.createdAt).toLocaleString()}
                  </span>
                </button>
              ))}
            </div>
            {selected && (
              <div className="panel">
                <h3>{selected.filename}</h3>
                <p className="muted">
                  {selected.questions.filter((question) => question.answers.length > 0).length} of{" "}
                  {selected.questions.length} answered
                </p>
                <div className="actions">
                  <button className="button" disabled={loading} onClick={() => generateAnswers(selected.id)}>
                    Generate AI answers
                  </button>
                  <button className="button ghost" onClick={() => exportFile(selected.id, "excel")}>
                    Excel
                  </button>
                  <button className="button ghost" onClick={() => exportFile(selected.id, "word")}>
                    Word
                  </button>
                  <button className="button ghost" onClick={() => exportFile(selected.id, "pdf")}>
                    PDF
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {selected && (
        <section className="card">
          <p className="eyebrow">Results</p>
          <h2>Questions and answers</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Question</th>
                  <th>Answer</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {selected.questions.map((question, index) => {
                  const answer = question.answers[0];
                  return (
                    <tr key={question.id}>
                      <td>{index + 1}</td>
                      <td>{question.text}</td>
                      <td>{answer?.answer || <span className="muted">Not generated</span>}</td>
                      <td>{answer?.confidence || "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );

  const fallbackContent =
    children ||
    (initialView === "results" ? (
      selected ? dashboardContent : (
        <section className="card">
          <p className="eyebrow">Results</p>
          <h1>Processed questionnaire results</h1>
          <p className="muted">Open or upload a questionnaire from the dashboard to view answers.</p>
        </section>
      )
    ) : (
      dashboardContent
    ));

  return (
    <div>
      <header className="topbar">
        <Link href="/dashboard" className="brand">
          <span className="brand-mark">CS</span>
          Cybersure AI
        </Link>
        <nav className="nav">
          <Link className={pathname === "/dashboard" ? "active" : ""} href="/dashboard">
            Dashboard
          </Link>
          <Link className={pathname === "/upload" ? "active" : ""} href="/upload">
            Upload
          </Link>
          <Link className={pathname === "/results" ? "active" : ""} href="/results">
            Results
          </Link>
        </nav>
        <div className="user-pill">
          <span>{user?.credits ?? "..."} credits</span>
          <button className="button ghost small" onClick={logout}>
            Log out
          </button>
        </div>
      </header>
      <main className="container">
        {error && <div className="error">{error}</div>}
        {status && <div className="success">{status}</div>}
        {fallbackContent}
      </main>
    </div>
  );
}

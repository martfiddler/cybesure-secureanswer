"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, setToken } from "../lib/api";

type AuthFormProps = {
  mode: "login" | "register";
};

export default function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");

    try {
      const response = await api.post(`/api/auth/${mode}`, { email, password });
      setToken(response.data.token);
      router.push("/dashboard");
    } catch (err: any) {
      setError(err.response?.data?.message || err.response?.data?.error || "Authentication failed");
    } finally {
      setLoading(false);
    }
  }

  const otherMode = mode === "login" ? "register" : "login";

  return (
    <main className="auth-page">
      <section className="card auth-card">
        <p className="eyebrow">Cybersure AI</p>
        <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
        <p className="muted">
          {mode === "login"
            ? "Access your security questionnaire workspace."
            : "Start with 100 credits for questionnaire automation."}
        </p>
        <form onSubmit={onSubmit} className="stack">
          <label>
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
          </label>
          <label>
            Password
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              minLength={8}
              required
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button disabled={loading} type="submit">
            {loading ? "Please wait..." : mode === "login" ? "Log in" : "Register"}
          </button>
        </form>
        <p className="muted auth-switch">
          {mode === "login" ? "No account?" : "Already registered?"}{" "}
          <Link href={`/${otherMode}`}>{otherMode === "login" ? "Log in" : "Register"}</Link>
        </p>
      </section>
    </main>
  );
}

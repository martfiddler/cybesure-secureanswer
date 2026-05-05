import Link from "next/link";

export default function HomePage() {
  return (
    <main className="hero">
      <section className="card hero-card">
        <p className="eyebrow">Cybersure AI Questionnaire Engine</p>
        <h1>Automate cyber security questionnaire responses with controlled AI.</h1>
        <p className="muted">
          Upload Excel questionnaires, generate compliance-focused answers, export
          results, and use the Chrome extension to fill browser portals.
        </p>
        <div className="actions">
          <Link className="button" href="/register">
            Create account
          </Link>
          <Link className="button secondary" href="/login">
            Sign in
          </Link>
        </div>
      </section>
    </main>
  );
}

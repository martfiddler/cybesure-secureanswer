import os
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a cyber security compliance expert.

You MUST answer using ONLY the provided source documents. Do not use any prior knowledge.

Rules:
- Combine multiple sources if needed
- Be audit-ready and precise
- If partial: say "Based on available documentation..."
- If none: say "Not evidenced in provided documentation"

Output format (JSON only, no markdown):
{
  "confidence": "Yes" | "No" | "Partial",
  "explanation": "concise audit-ready explanation",
  "sources": ["brief quote or reference from source doc"]
}"""


def answer_question(question: str, chunks: list[str]) -> dict:
    """
    Use Claude to answer a single question given retrieved chunks.
    Returns dict with confidence, explanation, sources.
    """
    context = "\n\n---\n\n".join(chunks)
    user_message = f"""Source documents:
{context}

Question: {question}

Answer in JSON only."""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )

    import json
    text = message.content[0].text.strip()
    # Strip any accidental markdown fences
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def answer_questions_batch(questions: list[str], session_data, session_id: str) -> list[dict]:
    """
    Answer a list of questions, retrieving relevant chunks for each.
    Processes sequentially with per-question retrieval.
    """
    from app.services.retrieval import retrieve_chunks
    results = []
    for i, question in enumerate(questions):
        chunks = retrieve_chunks(session_id, question, session_data)
        result = answer_question(question, chunks)
        result["question"] = question
        result["question_index"] = i
        results.append(result)
    return results

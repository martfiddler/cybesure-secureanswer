import OpenAI from "openai";
import { config } from "../config.js";

const openai = config.openaiApiKey
  ? new OpenAI({ apiKey: config.openaiApiKey })
  : null;

export const COMPLIANCE_PROMPT = `You are a cyber security compliance expert.

Answer the following questionnaire question in a way that would score highly in an enterprise security assessment.

Requirements:
- Align with ISO 27001, NIST CSF, PCI DSS where applicable
- Be clear, structured, and professional
- Do not hallucinate controls
- If uncertain, say: "This is controlled via internal policy and supporting procedures"

Question:
{{QUESTION}}

Return:
- Answer
- Confidence (High / Medium / Low)`;

function parseJsonResponse(content) {
  const cleaned = content.replace(/```json|```/g, "").trim();
  try {
    const parsed = JSON.parse(cleaned);
    return {
      answer: String(parsed.answer ?? parsed.Answer ?? "").trim(),
      confidence: String(parsed.confidence ?? parsed.Confidence ?? "Medium").trim()
    };
  } catch {
    const answerMatch = cleaned.match(/answer\s*:\s*([\s\S]*?)(confidence\s*:|$)/i);
    const confidenceMatch = cleaned.match(/confidence\s*:\s*(High|Medium|Low)/i);
    return {
      answer: (answerMatch?.[1] ?? cleaned).trim(),
      confidence: confidenceMatch?.[1] ?? "Medium"
    };
  }
}

export async function generateComplianceAnswer(question) {
  const fallback = {
    answer: "This is controlled via internal policy and supporting procedures.",
    confidence: "Low"
  };

  if (!openai) {
    return fallback;
  }

  const prompt = COMPLIANCE_PROMPT.replace("{{QUESTION}}", question);

  const completion = await openai.chat.completions.create({
    model: config.openaiModel,
    temperature: 0.2,
    response_format: { type: "json_object" },
    messages: [
      {
        role: "system",
        content:
          "Return strict JSON only with keys answer and confidence. Confidence must be High, Medium, or Low."
      },
      {
        role: "user",
        content: `${prompt}\n\nReturn JSON: {"answer":"...","confidence":"High|Medium|Low"}`
      }
    ]
  });

  const content = completion.choices?.[0]?.message?.content;
  if (!content) {
    return fallback;
  }

  const parsed = parseJsonResponse(content);
  return {
    answer: parsed.answer || fallback.answer,
    confidence: ["High", "Medium", "Low"].includes(parsed.confidence)
      ? parsed.confidence
      : "Medium"
  };
}

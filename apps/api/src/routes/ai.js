import express from "express";
import { prisma } from "../prisma.js";
import { generateComplianceAnswer } from "../services/aiService.js";
import { sanitizeText } from "../utils/sanitize.js";

export const aiRouter = express.Router();

async function generateAndStoreForQuestion({ questionRecord, userId }) {
  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: { credits: true }
  });

  if (!user || user.credits <= 0) {
    const error = new Error("Insufficient credits");
    error.status = 402;
    throw error;
  }

  const generated = await generateComplianceAnswer(questionRecord.text);

  const [, answer, updatedUser] = await prisma.$transaction([
    prisma.user.update({
      where: { id: userId },
      data: { credits: { decrement: 1 } }
    }),
    prisma.answer.create({
      data: {
        questionId: questionRecord.id,
        answer: generated.answer,
        confidence: generated.confidence
      }
    }),
    prisma.user.findUnique({
      where: { id: userId },
      select: { credits: true }
    })
  ]);

  return { answer, credits: updatedUser?.credits ?? 0 };
}

aiRouter.post("/generate", async (req, res, next) => {
  try {
    const question = sanitizeText(req.body.question, 4000);
    const questionId = sanitizeText(req.body.questionId, 120);

    if (!question) {
      return res.status(400).json({ message: "Question is required" });
    }

    if (questionId) {
      const questionRecord = await prisma.question.findFirst({
        where: {
          id: questionId,
          questionnaire: { userId: req.user.id }
        }
      });

      if (!questionRecord) {
        return res.status(404).json({ message: "Question not found" });
      }

      const result = await generateAndStoreForQuestion({
        questionRecord,
        userId: req.user.id
      });

      return res.json({
        answer: result.answer.answer,
        confidence: result.answer.confidence,
        credits: result.credits
      });
    }

    const user = await prisma.user.findUnique({
      where: { id: req.user.id },
      select: { credits: true }
    });

    if (!user || user.credits <= 0) {
      return res.status(402).json({ message: "Insufficient credits" });
    }

    const generated = await generateComplianceAnswer(question);
    const updatedUser = await prisma.user.update({
      where: { id: req.user.id },
      data: { credits: { decrement: 1 } },
      select: { credits: true }
    });

    return res.json({
      answer: generated.answer,
      confidence: generated.confidence,
      credits: updatedUser.credits
    });
  } catch (error) {
    next(error);
  }
});

aiRouter.post("/generate-questionnaire/:id", async (req, res, next) => {
  try {
    const questionnaire = await prisma.questionnaire.findFirst({
      where: { id: req.params.id, userId: req.user.id },
      include: {
        questions: {
          include: { answers: { orderBy: { createdAt: "desc" }, take: 1 } },
          orderBy: { createdAt: "asc" }
        }
      }
    });

    if (!questionnaire) {
      return res.status(404).json({ message: "Questionnaire not found" });
    }

    const unansweredQuestions = questionnaire.questions.filter(
      (question) => question.answers.length === 0
    );

    const user = await prisma.user.findUnique({
      where: { id: req.user.id },
      select: { credits: true }
    });

    if (!user || user.credits <= 0) {
      return res.status(402).json({ message: "Insufficient credits" });
    }

    if (user.credits < unansweredQuestions.length) {
      return res.status(402).json({
        message: "Not enough credits to process every unanswered question",
        required: unansweredQuestions.length,
        credits: user.credits
      });
    }

    const generated = [];
    for (const questionRecord of unansweredQuestions) {
      const result = await generateAndStoreForQuestion({
        questionRecord,
        userId: req.user.id
      });
      generated.push({
        questionId: questionRecord.id,
        question: questionRecord.text,
        answer: result.answer.answer,
        confidence: result.answer.confidence
      });
    }

    const updatedQuestionnaire = await prisma.questionnaire.findFirst({
      where: { id: req.params.id, userId: req.user.id },
      include: {
        questions: {
          include: { answers: { orderBy: { createdAt: "desc" } } },
          orderBy: { createdAt: "asc" }
        }
      }
    });

    const credits = await prisma.user.findUnique({
      where: { id: req.user.id },
      select: { credits: true }
    });

    return res.json({
      generated,
      questionnaire: updatedQuestionnaire,
      credits: credits?.credits ?? 0
    });
  } catch (error) {
    next(error);
  }
});

import { Router } from "express";
import PDFDocument from "pdfkit";
import { Document, Packer, Paragraph, TextRun } from "docx";
import * as XLSX from "xlsx";
import prisma from "../prisma.js";

const router = Router();

router.get("/excel/:id", async (req, res, next) => {
  try {
    const questionnaire = await prisma.questionnaire.findFirst({
      where: {
        id: req.params.id,
        userId: req.user.id
      },
      include: {
        questions: {
          include: { answers: { orderBy: { createdAt: "desc" } } },
          orderBy: { createdAt: "asc" }
        }
      }
    });

    if (!questionnaire) {
      return res.status(404).json({ message: "Questionnaire not found" });
    }

    const rows = questionnaire.questions.map((question, index) => {
      const answer = question.answers[0];
      return {
        Number: index + 1,
        Question: question.text,
        Category: question.category || "",
        Answer: answer?.answer || "",
        Confidence: answer?.confidence || ""
      };
    });

    const workbook = XLSX.utils.book_new();
    const sheet = XLSX.utils.json_to_sheet(rows);
    XLSX.utils.book_append_sheet(workbook, sheet, "Answers");
    const buffer = XLSX.write(workbook, {
      type: "buffer",
      bookType: "xlsx"
    });

    const safeFilename = questionnaire.filename.replace(/[^a-z0-9._-]+/gi, "_");
    res.setHeader(
      "Content-Type",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    );
    res.setHeader(
      "Content-Disposition",
      `attachment; filename="${safeFilename || "questionnaire"}-answers.xlsx"`
    );
    return res.send(buffer);
  } catch (error) {
    return next(error);
  }
});

router.get("/word/:id", async (req, res, next) => {
  try {
    const questionnaire = await loadQuestionnaire(req.params.id, req.user.id);
    if (!questionnaire) {
      return res.status(404).json({ message: "Questionnaire not found" });
    }

    const children = [
      new Paragraph({
        children: [new TextRun({ text: "Cybersure AI Questionnaire Answers", bold: true, size: 32 })]
      }),
      new Paragraph({ text: `Source file: ${questionnaire.filename}` }),
      new Paragraph({ text: "" })
    ];

    questionnaire.questions.forEach((question, index) => {
      const answer = question.answers[0];
      children.push(
        new Paragraph({
          children: [new TextRun({ text: `Q${index + 1}. ${question.text}`, bold: true })]
        }),
        new Paragraph({ text: `Answer: ${answer?.answer || ""}` }),
        new Paragraph({ text: `Confidence: ${answer?.confidence || ""}` }),
        new Paragraph({ text: "" })
      );
    });

    const document = new Document({ sections: [{ children }] });
    const buffer = await Packer.toBuffer(document);
    res.setHeader(
      "Content-Type",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    res.setHeader("Content-Disposition", `attachment; filename="${exportName(questionnaire, "docx")}"`);
    return res.send(buffer);
  } catch (error) {
    return next(error);
  }
});

router.get("/pdf/:id", async (req, res, next) => {
  try {
    const questionnaire = await loadQuestionnaire(req.params.id, req.user.id);
    if (!questionnaire) {
      return res.status(404).json({ message: "Questionnaire not found" });
    }

    res.setHeader("Content-Type", "application/pdf");
    res.setHeader("Content-Disposition", `attachment; filename="${exportName(questionnaire, "pdf")}"`);

    const pdf = new PDFDocument({ margin: 48 });
    pdf.pipe(res);
    pdf.fontSize(18).text("Cybersure AI Questionnaire Answers", { underline: true });
    pdf.moveDown();
    pdf.fontSize(10).fillColor("#444").text(`Source file: ${questionnaire.filename}`);
    pdf.moveDown();

    questionnaire.questions.forEach((question, index) => {
      const answer = question.answers[0];
      pdf.fillColor("#111").fontSize(12).text(`Q${index + 1}. ${question.text}`, { continued: false });
      pdf.fillColor("#333").fontSize(10).text(`Answer: ${answer?.answer || ""}`);
      pdf.fillColor("#555").text(`Confidence: ${answer?.confidence || ""}`);
      pdf.moveDown();
    });

    pdf.end();
  } catch (error) {
    next(error);
  }
});

function loadQuestionnaire(id, userId) {
  return prisma.questionnaire.findFirst({
    where: { id, userId },
    include: {
      questions: {
        include: { answers: { orderBy: { createdAt: "desc" }, take: 1 } },
        orderBy: { createdAt: "asc" }
      }
    }
  });
}

function exportName(questionnaire, extension) {
  const base = questionnaire.filename.replace(/\.[^.]+$/, "").replace(/[^a-z0-9._-]+/gi, "_");
  return `${base || "questionnaire"}-answers.${extension}`;
}

export default router;

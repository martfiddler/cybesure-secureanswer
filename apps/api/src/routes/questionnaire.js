import { Router } from "express";
import multer from "multer";
import { prisma } from "../prisma.js";
import { extractQuestionsFromWorkbook } from "../utils/excel.js";
import { sanitizeFilename } from "../utils/sanitize.js";

const router = Router();

const upload = multer({
  storage: multer.memoryStorage(),
  limits: {
    fileSize: 10 * 1024 * 1024
  },
  fileFilter: (_req, file, cb) => {
    const allowedMimeTypes = [
      "application/vnd.ms-excel",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "application/octet-stream"
    ];
    const allowedExtensions = [".xls", ".xlsx"];
    const hasAllowedExtension = allowedExtensions.some((extension) =>
      file.originalname.toLowerCase().endsWith(extension)
    );

    if (hasAllowedExtension || allowedMimeTypes.includes(file.mimetype)) {
      cb(null, true);
      return;
    }

    cb(new Error("Only Excel .xls and .xlsx files are supported initially."));
  }
});

router.get("/", async (req, res, next) => {
  try {
    const questionnaires = await prisma.questionnaire.findMany({
      where: { userId: req.user.id },
      orderBy: { createdAt: "desc" },
      include: {
        questions: {
          orderBy: { createdAt: "asc" },
          include: { answers: { orderBy: { createdAt: "desc" } } }
        }
      }
    });

    res.json({ questionnaires });
  } catch (error) {
    next(error);
  }
});

router.post("/upload", upload.single("file"), async (req, res, next) => {
  try {
    if (!req.file) {
      return res.status(400).json({ message: "An Excel file is required." });
    }

    const filename = sanitizeFilename(req.file.originalname);
    const questionTexts = extractQuestionsFromWorkbook(req.file.buffer);

    if (!questionTexts.length) {
      return res.status(400).json({
        message: "No questions were found. The first column should contain questionnaire questions."
      });
    }

    const questionnaire = await prisma.questionnaire.create({
      data: {
        userId: req.user.id,
        filename,
        questions: {
          create: questionTexts.map((text) => ({ text }))
        }
      },
      include: {
        questions: {
          orderBy: { createdAt: "asc" },
          include: { answers: { orderBy: { createdAt: "desc" } } }
        }
      }
    });

    res.status(201).json({
      questionnaire,
      totalQuestions: questionnaire.questions.length
    });
  } catch (error) {
    next(error);
  }
});

router.get("/:id", async (req, res, next) => {
  try {
    const questionnaire = await prisma.questionnaire.findFirst({
      where: {
        id: req.params.id,
        userId: req.user.id
      },
      include: {
        questions: {
          orderBy: { createdAt: "asc" },
          include: { answers: { orderBy: { createdAt: "desc" } } }
        }
      }
    });

    if (!questionnaire) {
      return res.status(404).json({ message: "Questionnaire not found." });
    }

    res.json({ questionnaire });
  } catch (error) {
    next(error);
  }
});

export default router;

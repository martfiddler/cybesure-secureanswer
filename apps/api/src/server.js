import cors from "cors";
import express from "express";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import { assertRequiredConfig, config } from "./config.js";
import { authenticate } from "./middleware/auth.js";
import { errorHandler, notFound } from "./middleware/errorHandler.js";
import authRoutes from "./routes/auth.js";
import questionnaireRoutes from "./routes/questionnaire.js";
import aiRoutes from "./routes/ai.js";
import outputRoutes from "./routes/output.js";

const app = express();
assertRequiredConfig();
app.set("trust proxy", 1);
app.use(helmet());
app.use(cors({ origin: config.corsOrigin, credentials: true }));
app.use(express.json({ limit: "1mb" }));
app.use(
  rateLimit({
    windowMs: 15 * 60 * 1000,
    limit: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false
  })
);

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "Cybersure AI Questionnaire API" });
});

app.use("/api/auth", authRoutes);
app.get("/api/auth/me", authenticate, (req, res) => {
  res.json({ user: req.user });
});
app.use("/api/questionnaire", authenticate, questionnaireRoutes);
app.use("/api/ai", authenticate, aiRoutes);
app.use("/api/output", authenticate, outputRoutes);

app.use(notFound);
app.use(errorHandler);

app.listen(config.port, () => {
  console.log(`API listening on port ${config.port}`);
});

import dotenv from "dotenv";

dotenv.config();

export const config = {
  port: Number(process.env.PORT || 4000),
  databaseUrl: process.env.DATABASE_URL,
  jwtSecret: process.env.JWT_SECRET,
  openaiApiKey: process.env.OPENAI_API_KEY,
  openaiModel: process.env.OPENAI_MODEL || "gpt-4o-mini",
  corsOrigin: process.env.CORS_ORIGIN || "*",
  extensionApiToken: process.env.EXTENSION_API_TOKEN || "",
  nodeEnv: process.env.NODE_ENV || "development"
};

export const env = config;

export function assertRequiredConfig() {
  const missing = [];

  if (!config.databaseUrl) missing.push("DATABASE_URL");
  if (!config.jwtSecret) missing.push("JWT_SECRET");

  if (missing.length > 0) {
    throw new Error(`Missing required environment variables: ${missing.join(", ")}`);
  }
}

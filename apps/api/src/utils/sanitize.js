export function normalizeEmail(email) {
  return String(email || "").trim().toLowerCase();
}

export function sanitizeText(value, maxLength = 10000) {
  return String(value || "")
    .replace(/\u0000/g, "")
    .replace(/[\u0001-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .trim()
    .slice(0, maxLength);
}

export function sanitizeFilename(filename) {
  const safe = sanitizeText(filename, 255).replace(/[^a-zA-Z0-9._ -]/g, "_");
  return safe || "questionnaire.xlsx";
}

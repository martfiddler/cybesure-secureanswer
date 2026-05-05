import * as XLSX from "xlsx";

export function extractQuestionsFromWorkbook(buffer) {
  const workbook = XLSX.read(buffer, { type: "buffer" });
  const questions = [];

  workbook.SheetNames.forEach((sheetName) => {
    const worksheet = workbook.Sheets[sheetName];
    const rows = XLSX.utils.sheet_to_json(worksheet, { header: 1, defval: "" });

    rows.forEach((row) => {
      const firstCell = String(row[0] ?? "").trim();
      if (!firstCell || firstCell.toLowerCase() === "question") {
        return;
      }
      questions.push(firstCell);
    });
  });

  return dedupeQuestions(questions);
}

export function buildAnswersWorkbook(questionnaire) {
  const rows = questionnaire.questions.map((question, index) => {
    const latestAnswer = question.answers[0];

    return {
      "#": index + 1,
      Question: question.text,
      Category: question.category ?? "",
      Answer: latestAnswer?.answer ?? "",
      Confidence: latestAnswer?.confidence ?? ""
    };
  });

  const worksheet = XLSX.utils.json_to_sheet(rows);
  worksheet["!cols"] = [
    { wch: 8 },
    { wch: 80 },
    { wch: 24 },
    { wch: 100 },
    { wch: 16 }
  ];

  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, worksheet, "Answers");
  return XLSX.write(workbook, { type: "buffer", bookType: "xlsx" });
}

function dedupeQuestions(questions) {
  const seen = new Set();
  return questions.filter((question) => {
    const key = question.toLowerCase();
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function nearestLabelText(element) {
  if (element.id) {
    const explicitLabel = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
    if (explicitLabel?.textContent?.trim()) {
      return explicitLabel.textContent.trim();
    }
  }

  const wrappingLabel = element.closest("label");
  if (wrappingLabel?.textContent?.trim()) {
    return wrappingLabel.textContent.trim();
  }

  const container = element.closest("div, section, fieldset, tr, li, p");
  if (container?.textContent?.trim()) {
    return container.textContent.replace(element.value || "", "").trim().slice(0, 500);
  }

  return "";
}

function inferQuestion(element) {
  return (
    element.getAttribute("placeholder") ||
    nearestLabelText(element) ||
    element.getAttribute("aria-label") ||
    element.getAttribute("name") ||
    ""
  ).trim();
}

async function generateAnswer(question) {
  return chrome.runtime.sendMessage({
    type: "GENERATE_ANSWER",
    question
  });
}

function setNativeValue(element, value) {
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");

  descriptor?.set?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function eligibleFields() {
  return Array.from(document.querySelectorAll("textarea, input[type='text'], input:not([type])"))
    .filter((element) => !element.disabled && !element.readOnly && inferQuestion(element));
}

async function autofillField(element) {
  const question = inferQuestion(element);
  if (!question) return;

  element.dataset.cybersureStatus = "loading";
  const response = await generateAnswer(question);

  if (response?.ok && response.answer) {
    setNativeValue(element, response.answer);
    element.dataset.cybersureStatus = "filled";
  } else {
    element.dataset.cybersureStatus = "error";
    console.warn("Cybersure autofill failed", response?.message || response?.error);
  }
}

async function autofillAll() {
  const fields = eligibleFields();
  for (const field of fields) {
    await autofillField(field);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "CYBERSURE_AUTOFILL") return false;

  autofillAll()
    .then(() => sendResponse({ ok: true }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});

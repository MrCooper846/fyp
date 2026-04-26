const enabledValue = document.getElementById("enabledValue");
const sessionValue = document.getElementById("sessionValue");
const attemptValue = document.getElementById("attemptValue");
const currentUniversityValue = document.getElementById("currentUniversityValue");
const countValue = document.getElementById("countValue");
const contactCountValue = document.getElementById("contactCountValue");
const toggleBtn = document.getElementById("toggleBtn");
const nextUniBtn = document.getElementById("nextUniBtn");
const exportBtn = document.getElementById("exportBtn");
const exportContactsBtn = document.getElementById("exportContactsBtn");
const clearBtn = document.getElementById("clearBtn");
const flash = document.getElementById("flash");

const universityInput = document.getElementById("universityInput");
const nameInput = document.getElementById("nameInput");
const roleInput = document.getElementById("roleInput");
const emailInput = document.getElementById("emailInput");
const pageUrlInput = document.getElementById("pageUrlInput");
const confidenceInput = document.getElementById("confidenceInput");
const contactTypeInput = document.getElementById("contactTypeInput");
const notesInput = document.getElementById("notesInput");
const saveContactBtn = document.getElementById("saveContactBtn");

function sendMessage(message) {
  return chrome.runtime.sendMessage(message);
}

function flashMessage(text, isError = false) {
  flash.textContent = text || "";
  flash.style.color = isError ? "#b91c1c" : "#0f766e";
}

function render(state) {
  enabledValue.textContent = state.enabled ? "Active" : "Stopped";
  sessionValue.textContent = state.sessionId || "-";
  attemptValue.textContent = String(state.attemptIndex || 1);
  currentUniversityValue.textContent = state.currentUniversity || "-";
  countValue.textContent = String(state.recordCount || 0);
  contactCountValue.textContent = String(state.contactCount || 0);
  toggleBtn.textContent = state.enabled ? "Stop tracking" : "Start tracking";
}

async function currentTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0] || null;
}

function clearContactForm() {
  nameInput.value = "";
  roleInput.value = "";
  emailInput.value = "";
  pageUrlInput.value = "";
  confidenceInput.value = "";
  contactTypeInput.value = "";
  notesInput.value = "";
}

async function refresh() {
  try {
    const state = await sendMessage({ type: "tracker:getState" });
    render(state);
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
}

toggleBtn.addEventListener("click", async () => {
  try {
    const current = await sendMessage({ type: "tracker:getState" });
    const next = current.enabled
      ? await sendMessage({ type: "tracker:stop" })
      : await sendMessage({ type: "tracker:start" });
    render(next);
    flashMessage(current.enabled ? "Tracking stopped." : "New session started.");
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
});

nextUniBtn.addEventListener("click", async () => {
  try {
    const tab = await currentTab();
    const chosenUniversity = universityInput.value.trim() || tab?.title || tab?.url || "";
    const state = await sendMessage({
      type: "tracker:nextUniversity",
      university: chosenUniversity,
      homepageUrl: tab?.url || "",
      pageTitle: tab?.title || "",
    });
    render(state);
    if (!universityInput.value.trim() && state.currentUniversity) {
      universityInput.value = state.currentUniversity;
    }
    flashMessage(`Now tracking attempt ${state.attemptIndex || ""} for ${state.currentUniversity || "next university"}.`.trim());
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
});

exportBtn.addEventListener("click", async () => {
  try {
    const result = await sendMessage({ type: "tracker:export" });
    flashMessage(`Exported ${result.recordCount || 0} trace rows.`);
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
});

exportContactsBtn.addEventListener("click", async () => {
  try {
    const result = await sendMessage({ type: "tracker:exportContacts" });
    flashMessage(`Exported ${result.contactCount || 0} contacts.`);
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
});

clearBtn.addEventListener("click", async () => {
  try {
    const state = await sendMessage({ type: "tracker:clear" });
    render(state);
    clearContactForm();
    flashMessage("Trace records and saved contacts cleared.");
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
});

saveContactBtn.addEventListener("click", async () => {
  try {
    const tab = await currentTab();
    const result = await sendMessage({
      type: "tracker:addContact",
      university: universityInput.value.trim(),
      name: nameInput.value.trim(),
      role: roleInput.value.trim(),
      email: emailInput.value.trim(),
      pageUrl: pageUrlInput.value.trim(),
      sourceUrl: tab?.url || "",
      confidence: confidenceInput.value.trim(),
      contactType: contactTypeInput.value.trim(),
      notes: notesInput.value.trim(),
    });
    const state = await sendMessage({ type: "tracker:getState" });
    render(state);
    clearContactForm();
    flashMessage(`Saved contact ${result.contactCount || ""}`.trim());
  } catch (error) {
    flashMessage(error.message || String(error), true);
  }
});

refresh();

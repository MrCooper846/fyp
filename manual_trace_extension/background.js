const STORAGE_KEYS = {
  enabled: "tracker_enabled",
  sessionId: "tracker_session_id",
  sessionStartedAt: "tracker_session_started_at",
  attemptIndex: "tracker_attempt_index",
  currentUniversity: "tracker_current_university",
  currentHomepageUrl: "tracker_current_homepage_url",
  records: "tracker_records",
  contacts: "tracker_contacts",
};

const pendingClicks = new Map();

function nowIso() {
  return new Date().toISOString();
}

function newSessionId() {
  return `session_${Date.now()}`;
}

function boundaryId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

function csvEscape(value) {
  const text = String(value ?? "");
  if (/[",\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

async function getState() {
  const state = await chrome.storage.local.get({
    [STORAGE_KEYS.enabled]: false,
    [STORAGE_KEYS.sessionId]: "",
    [STORAGE_KEYS.sessionStartedAt]: "",
    [STORAGE_KEYS.attemptIndex]: 1,
    [STORAGE_KEYS.currentUniversity]: "",
    [STORAGE_KEYS.currentHomepageUrl]: "",
    [STORAGE_KEYS.records]: [],
    [STORAGE_KEYS.contacts]: [],
  });
  return {
    enabled: Boolean(state[STORAGE_KEYS.enabled]),
    sessionId: state[STORAGE_KEYS.sessionId] || "",
    sessionStartedAt: state[STORAGE_KEYS.sessionStartedAt] || "",
    attemptIndex: Number(state[STORAGE_KEYS.attemptIndex] || 1),
    currentUniversity: state[STORAGE_KEYS.currentUniversity] || "",
    currentHomepageUrl: state[STORAGE_KEYS.currentHomepageUrl] || "",
    records: Array.isArray(state[STORAGE_KEYS.records]) ? state[STORAGE_KEYS.records] : [],
    contacts: Array.isArray(state[STORAGE_KEYS.contacts]) ? state[STORAGE_KEYS.contacts] : [],
  };
}

async function saveState(partial) {
  await chrome.storage.local.set(partial);
}

async function appendRecord(record) {
  const state = await getState();
  const records = state.records.concat(record);
  await saveState({ [STORAGE_KEYS.records]: records });
  return records.length;
}

async function updateRecord(recordId, updater) {
  const state = await getState();
  const records = state.records.map((record) => {
    if (record.record_id !== recordId) {
      return record;
    }
    return updater(record);
  });
  await saveState({ [STORAGE_KEYS.records]: records });
}

async function startTracking() {
  const sessionId = newSessionId();
  const sessionStartedAt = nowIso();
  await saveState({
    [STORAGE_KEYS.enabled]: true,
    [STORAGE_KEYS.sessionId]: sessionId,
    [STORAGE_KEYS.sessionStartedAt]: sessionStartedAt,
    [STORAGE_KEYS.attemptIndex]: 1,
    [STORAGE_KEYS.currentUniversity]: "",
    [STORAGE_KEYS.currentHomepageUrl]: "",
    [STORAGE_KEYS.records]: [],
    [STORAGE_KEYS.contacts]: [],
  });
  pendingClicks.clear();
  return {
    enabled: true,
    sessionId,
    sessionStartedAt,
    attemptIndex: 1,
    currentUniversity: "",
    recordCount: 0,
    contactCount: 0,
  };
}

async function stopTracking() {
  await saveState({ [STORAGE_KEYS.enabled]: false });
  pendingClicks.clear();
  const state = await getState();
  return {
    enabled: false,
    sessionId: state.sessionId,
    sessionStartedAt: state.sessionStartedAt,
    attemptIndex: state.attemptIndex,
    currentUniversity: state.currentUniversity,
    recordCount: state.records.length,
    contactCount: state.contacts.length,
  };
}

async function clearRecords() {
  await saveState({ [STORAGE_KEYS.records]: [], [STORAGE_KEYS.contacts]: [] });
  pendingClicks.clear();
  const state = await getState();
  return {
    enabled: state.enabled,
    sessionId: state.sessionId,
    sessionStartedAt: state.sessionStartedAt,
    attemptIndex: state.attemptIndex,
    currentUniversity: state.currentUniversity,
    recordCount: 0,
    contactCount: 0,
  };
}

async function markNextUniversity({ university, homepageUrl, pageTitle }) {
  const state = await getState();
  const attemptIndex = (state.attemptIndex || 1) + 1;
  const currentUniversity = university || pageTitle || homepageUrl || "";
  await saveState({
    [STORAGE_KEYS.attemptIndex]: attemptIndex,
    [STORAGE_KEYS.currentUniversity]: currentUniversity,
    [STORAGE_KEYS.currentHomepageUrl]: homepageUrl || "",
  });

  const boundaryRecord = {
    record_id: boundaryId("boundary"),
    session_id: state.sessionId || "",
    attempt_index: attemptIndex,
    current_university: currentUniversity,
    clicked_at: nowIso(),
    source_url: homepageUrl || "",
    source_title: pageTitle || "",
    clicked_href: "",
    anchor_text: "",
    link_target: "",
    open_in_new_context: false,
    same_tab_navigation_confirmed: false,
    final_url: homepageUrl || "",
    navigation_confirmed_at: "",
    tab_id: "",
    frame_id: "",
    row_type: "attempt_boundary",
  };
  await appendRecord(boundaryRecord);

  return {
    enabled: state.enabled,
    sessionId: state.sessionId,
    sessionStartedAt: state.sessionStartedAt,
    attemptIndex,
    currentUniversity,
    recordCount: state.records.length + 1,
    contactCount: state.contacts.length,
  };
}

async function exportCsv() {
  const state = await getState();
  const headers = [
    "record_id",
    "session_id",
    "attempt_index",
    "current_university",
    "row_type",
    "clicked_at",
    "source_url",
    "source_title",
    "clicked_href",
    "anchor_text",
    "link_target",
    "open_in_new_context",
    "same_tab_navigation_confirmed",
    "final_url",
    "navigation_confirmed_at",
    "tab_id",
    "frame_id",
  ];
  const rows = [headers.join(",")];
  for (const record of state.records) {
    rows.push(headers.map((header) => csvEscape(record[header] ?? "")).join(","));
  }
  const csv = rows.join("\n");
  const filename = `${state.sessionId || "manual_link_trace"}.csv`;
  const url = `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`;
  await chrome.downloads.download({
    url,
    filename,
    saveAs: true,
  });
  return { filename, recordCount: state.records.length };
}

async function appendContact(contact) {
  const state = await getState();
  const contacts = state.contacts.concat(contact);
  await saveState({ [STORAGE_KEYS.contacts]: contacts });
  return contacts.length;
}

async function exportContactsCsv() {
  const state = await getState();
  const headers = [
    "contact_id",
    "session_id",
    "attempt_index",
    "current_university",
    "saved_at",
    "university",
    "name",
    "role",
    "email",
    "page_url",
    "source_url",
    "confidence",
    "contact_type",
    "notes",
  ];
  const rows = [headers.join(",")];
  for (const contact of state.contacts) {
    rows.push(headers.map((header) => csvEscape(contact[header] ?? "")).join(","));
  }
  const csv = rows.join("\n");
  const filename = `${state.sessionId || "manual_contacts"}_contacts.csv`;
  const url = `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`;
  await chrome.downloads.download({
    url,
    filename,
    saveAs: true,
  });
  return { filename, contactCount: state.contacts.length };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message?.type === "tracker:getState") {
      const state = await getState();
      sendResponse({
        enabled: state.enabled,
        sessionId: state.sessionId,
        sessionStartedAt: state.sessionStartedAt,
        attemptIndex: state.attemptIndex,
        currentUniversity: state.currentUniversity,
        recordCount: state.records.length,
        contactCount: state.contacts.length,
      });
      return;
    }

    if (message?.type === "tracker:start") {
      sendResponse(await startTracking());
      return;
    }

    if (message?.type === "tracker:stop") {
      sendResponse(await stopTracking());
      return;
    }

    if (message?.type === "tracker:clear") {
      sendResponse(await clearRecords());
      return;
    }

    if (message?.type === "tracker:nextUniversity") {
      sendResponse(await markNextUniversity(message));
      return;
    }

    if (message?.type === "tracker:export") {
      sendResponse(await exportCsv());
      return;
    }

    if (message?.type === "tracker:exportContacts") {
      sendResponse(await exportContactsCsv());
      return;
    }

    if (message?.type === "tracker:addContact") {
      const state = await getState();
      const contact = {
        contact_id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        session_id: state.sessionId || "",
        attempt_index: state.attemptIndex || 1,
        current_university: message.university || state.currentUniversity || "",
        saved_at: nowIso(),
        university: message.university || state.currentUniversity || "",
        name: message.name || "",
        role: message.role || "",
        email: message.email || "",
        page_url: message.pageUrl || "",
        source_url: message.sourceUrl || "",
        confidence: message.confidence || "",
        contact_type: message.contactType || "",
        notes: message.notes || "",
      };
      const contactCount = await appendContact(contact);
      sendResponse({ ok: true, contactCount, contact });
      return;
    }

    if (message?.type === "tracker:linkClick") {
      const state = await getState();
      if (!state.enabled) {
        sendResponse({ ignored: true });
        return;
      }

      const record = {
        record_id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        session_id: state.sessionId,
        attempt_index: state.attemptIndex || 1,
        current_university: state.currentUniversity || "",
        clicked_at: message.clickedAt || nowIso(),
        source_url: message.sourceUrl || "",
        source_title: message.sourceTitle || "",
        clicked_href: message.clickedHref || "",
        anchor_text: message.anchorText || "",
        link_target: message.linkTarget || "",
        open_in_new_context: Boolean(message.openInNewContext),
        same_tab_navigation_confirmed: false,
        final_url: "",
        navigation_confirmed_at: "",
        tab_id: sender.tab?.id ?? message.tabId ?? "",
        frame_id: sender.frameId ?? 0,
        row_type: "click",
      };

      await appendRecord(record);

      if (sender.tab?.id !== undefined && !record.open_in_new_context) {
        pendingClicks.set(sender.tab.id, {
          recordId: record.record_id,
          clickedAtMs: Date.parse(record.clicked_at) || Date.now(),
          clickedHref: record.clicked_href,
        });
      }

      sendResponse({ ok: true, recordId: record.record_id });
      return;
    }

    sendResponse({ ok: false, error: "Unknown message type." });
  })();

  return true;
});

chrome.webNavigation.onCommitted.addListener(async (details) => {
  if (details.frameId !== 0) {
    return;
  }

  const pending = pendingClicks.get(details.tabId);
  if (!pending) {
    return;
  }

  const withinWindow = Date.now() - pending.clickedAtMs < 15000;
  if (!withinWindow) {
    pendingClicks.delete(details.tabId);
    return;
  }

  await updateRecord(pending.recordId, (record) => ({
    ...record,
    same_tab_navigation_confirmed: true,
    final_url: details.url || record.final_url,
    navigation_confirmed_at: nowIso(),
  }));

  pendingClicks.delete(details.tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  pendingClicks.delete(tabId);
});

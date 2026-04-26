(function () {
  function closestAnchor(target) {
    if (!target || !target.closest) {
      return null;
    }
    return target.closest("a[href]");
  }

  function shouldIgnore(anchor) {
    if (!anchor) {
      return true;
    }
    const href = anchor.getAttribute("href") || "";
    return !href || href.startsWith("#") || href.startsWith("javascript:");
  }

  function clickPayload(event, anchor) {
    const href = anchor.href || anchor.getAttribute("href") || "";
    const anchorText = (anchor.innerText || anchor.textContent || "").replace(/\s+/g, " ").trim();
    const target = anchor.getAttribute("target") || "";
    const modifierNewContext = event.button === 1 || event.ctrlKey || event.metaKey || event.shiftKey;
    const openInNewContext = modifierNewContext || target === "_blank";

    return {
      type: "tracker:linkClick",
      clickedAt: new Date().toISOString(),
      sourceUrl: window.location.href,
      sourceTitle: document.title || "",
      clickedHref: href,
      anchorText,
      linkTarget: target,
      openInNewContext,
    };
  }

  function handleEvent(event) {
    const anchor = closestAnchor(event.target);
    if (shouldIgnore(anchor)) {
      return;
    }
    try {
      chrome.runtime.sendMessage(clickPayload(event, anchor), () => {
        void chrome.runtime.lastError;
      });
    } catch (_err) {
      // Ignore messaging issues on restricted pages.
    }
  }

  document.addEventListener("click", handleEvent, true);
  document.addEventListener("auxclick", handleEvent, true);
})();

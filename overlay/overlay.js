/* One text/background element; no HTML from model output is ever interpreted. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api; // Node's built-in test runner; no build step/packages.
  } else {
    root.AkaneOverlay = api;
    const client = api.createClient({
      panel: document.getElementById("caption-panel"),
      text: document.getElementById("caption-text"),
      fetch: root.fetch.bind(root),
    });
    client.start();
    root.addEventListener("pagehide", client.stop);
    root.addEventListener("pageshow", client.start);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) client.hide(); else client.refresh();
    });
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function createClient(options) {
    const panel = options.panel;
    const text = options.text;
    const fetchCaption = options.fetch;
    const setTimer = options.setTimeout || setTimeout;
    const clearTimer = options.clearTimeout || clearTimeout;
    const setWatch = options.setInterval || setInterval;
    const clearWatch = options.clearInterval || clearInterval;
    const now = options.now || function () { return performance.now(); };
    const Abort = options.AbortController || AbortController;
    const pollMs = 150, timeoutMs = 1000, staleMs = 1500;
    let running = false, generation = 0, next = null, watch = null;
    let active = null, lastSuccess = null;

    function hide() {
      panel.hidden = true;
      text.textContent = "";
    }

    function render(payload) {
      if (!payload || typeof payload.text !== "string") throw new Error("Invalid caption");
      const style = payload.style || {};
      const settings = [
        ["max_width", "--caption-max-width", 240, 3840, "px"],
        ["font_size", "--caption-font-size", 16, 96, "px"],
        ["background_opacity", "--caption-background-opacity", 0, 1, ""],
      ];
      for (const [key, property, low, high, unit] of settings) {
        const value = style[key];
        if (typeof value === "number" && Number.isFinite(value) && value >= low && value <= high) {
          panel.style.setProperty(property, String(value) + unit);
        }
      }
      if (text.textContent !== payload.text) text.textContent = payload.text;
      panel.hidden = payload.text.trim().length === 0;
    }

    async function poll() {
      if (!running) return;
      const request = ++generation;
      const controller = new Abort();
      active = controller;
      let timedOut = false;
      const deadline = setTimer(function () {
        timedOut = true;
        controller.abort();
        if (running && request === generation) hide();
      }, timeoutMs);
      try {
        const response = await fetchCaption("/api/caption", {cache: "no-store", signal: controller.signal});
        if (!response.ok) throw new Error("Overlay unavailable");
        const payload = await response.json();
        if (!running || timedOut || request !== generation) return;
        render(payload);
        lastSuccess = now();
      } catch (_) {
        if (running && request === generation) hide();
      } finally {
        clearTimer(deadline);
        if (active === controller) active = null;
        if (running && request === generation) next = setTimer(poll, pollMs);
      }
    }

    function start() {
      if (running) return;
      running = true;
      lastSuccess = null;
      hide();
      watch = setWatch(function () {
        if (lastSuccess !== null && now() - lastSuccess > staleMs) hide();
      }, 250);
      poll();
    }

    function stop() {
      running = false;
      ++generation;
      if (active) active.abort();
      active = null;
      clearTimer(next);
      clearWatch(watch);
      next = watch = null;
      hide();
    }

    function refresh() {
      if (!running) return;
      ++generation;
      if (active) active.abort();
      active = null;
      clearTimer(next);
      next = null;
      hide();
      poll();
    }

    hide(); // Blank and transparent before the first successful request.
    return {start, stop, refresh, hide};
  }

  return {createClient};
});

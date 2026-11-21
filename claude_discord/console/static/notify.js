// New-event notifications: when a thread comes to the human (❓ 👀 📋 ⚠️) or a running
// turn finishes, the row lights up, a toast offers to open it, a chime plays and, while
// the tab is in the background, the title and a desktop notification say so.
// Loaded before app.js; app.js calls ConsoleNotify.onBoard() after every board refresh.
// Inside the person's quiet hours (night.js) only their urgent conditions announce.
"use strict";

window.ConsoleNotify = (() => {
  const KEY = "notify";
  const INBOX = "me";
  let enabled = localStorage.getItem(KEY) !== "0";
  let seen = null; // id -> { bucket, status, running } from the previous board
  let audio = null;
  let missed = 0;
  let asked = false;

  // Browsers only allow sound after the page has been interacted with.
  const unlock = () => {
    if (!audio) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) audio = new Ctx();
    }
    if (audio && audio.state === "suspended") audio.resume();
    // Ask for desktop notifications once, on the first gesture (browsers refuse without one).
    if (enabled && !asked && "Notification" in window && Notification.permission === "default") {
      asked = true;
      Notification.requestPermission().catch(() => { /* the in-page signals still work */ });
    }
  };
  document.addEventListener("pointerdown", unlock, { capture: true });
  document.addEventListener("keydown", unlock, { capture: true });

  function chime() {
    if (!audio || audio.state !== "running") return;
    const now = audio.currentTime;
    [[880, 0], [1320, 0.13]].forEach(([freq, at]) => {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + at);
      gain.gain.exponentialRampToValueAtTime(0.18, now + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + 0.35);
      osc.connect(gain).connect(audio.destination);
      osc.start(now + at);
      osc.stop(now + at + 0.4);
    });
  }

  // What changed for the human between two boards, at most one event per item.
  function events(items) {
    const out = [];
    for (const item of items) {
      // The canvas secretary (canvas.js) runs on every change; its turns are not news.
      if (item.canvas_secretary) continue;
      const prev = seen.get(item.id);
      const toInbox = item.bucket === INBOX &&
        (!prev || prev.bucket !== INBOX || prev.status !== item.status);
      const finished = prev && prev.running && !item.running;
      if (toInbox || finished) out.push(item);
    }
    // What needs the human leads the toast.
    return out.sort((a, b) => (b.bucket === INBOX) - (a.bucket === INBOX));
  }

  function label(item) {
    const st = (T.st && T.st[item.status]) || item.status;
    return `${STATUS_ICON[item.status] || "🔔"} ${item.title || item.thread_name || item.id} — ${st}`;
  }

  function announce(fresh) {
    chime();
    const first = fresh[0];
    const more = fresh.length > 1 ? ` (+${fresh.length - 1})` : "";
    const t = h("div", { class: "toast clickable", role: "status", text: `🔔 ${label(first)}${more}`,
      onclick: () => { t.remove(); openItem(first.id); } });
    showToast(t);
    setTimeout(() => t.remove(), 6000);
    if (document.hidden || !document.hasFocus()) {
      missed += fresh.length;
      titleFlag();
      if ("Notification" in window && Notification.permission === "granted") {
        const n = new Notification("Ebi AI Console", { body: label(first) + more, tag: `relay-${first.id}` });
        n.onclick = () => { window.focus(); openItem(first.id); n.close(); };
      }
    }
  }

  function titleFlag() {
    const base = document.title.replace(/^🔔(\(\d+\) )?/, "");
    document.title = missed ? `🔔(${missed}) ${base}` : base;
  }
  const back = () => { if (!document.hidden && document.hasFocus()) { missed = 0; titleFlag(); } };
  document.addEventListener("visibilitychange", back);
  window.addEventListener("focus", back);

  function onBoard(items) {
    if (missed) titleFlag(); // renderNav rewrote the title
    const next = new Map(items.map((i) => [i.id, { bucket: i.bucket, status: i.status, running: Boolean(i.running) }]));
    // The first board is the starting point, not news.
    let fresh = seen && enabled ? events(items) : [];
    seen = next;
    // Quiet hours (#1476, off unless the person set them): only their urgent conditions ring;
    // everything else waits for the morning brief and still shows on the board.
    const night = window.ConsoleNight;
    if (night) {
      night.load();
      if (night.quietNow()) fresh = fresh.filter((item) => night.urgent(item));
    }
    if (fresh.length) announce(fresh);
  }

  async function toggle() {
    enabled = !enabled;
    localStorage.setItem(KEY, enabled ? "1" : "0");
    if (enabled) {
      unlock();
      chime();
      if ("Notification" in window && Notification.permission === "default") {
        try { await Notification.requestPermission(); } catch { /* the in-page signals still work */ }
      }
    }
    renderNav();
  }

  const isOn = () => enabled;
  return { onBoard, toggle, isOn };
})();

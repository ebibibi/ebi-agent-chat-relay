// Which AI each automation job runs on (ADR-0027). Opened from the nav ("⚙️ Automation AI"):
// every job the scheduler lists, with "use the default", "follow the master" (the AI you use
// interactively), or a fixed backend with an optional model and effort, plus the AI its last
// run actually got. Saving replaces the whole policy; the server refuses it if any row is
// invalid and names the row. Loaded before app.js, which puts nav() in the nav.
"use strict";

window.ConsoleAutomation = (() => {
  const LABELS = { claude: "Claude", codex: "Codex" };
  const INHERIT = "", MASTER = "master";
  const ja = () => document.documentElement.lang === "ja";
  const L = () => ja() ? {
    nav: "⚙️ 自動化のAI", title: "自動化ジョブのAI",
    lead: "ジョブごとに使うAIを選びます。会話で使う既定AIとは別です。次の実行から効きます（元に戻せます）。",
    unsaved: "まだ保存していません。いまは scheduler 側の控えファイルの設定で動いています。保存するとこちらが正本になります。",
    master: "マスター（会話の既定）", def: "指定のないジョブ", inherit: "既定に従う", follow: "マスターに従う",
    model: "モデル（空＝そのAIの既定）", effort: "effort", last: "前回", never: "まだ記録なし",
    off: "停止中", gone: "scheduler に無い", save: "保存", saved: "保存しました", filter: "絞り込み",
    jobsError: "scheduler のジョブ一覧を読めませんでした。方針に書いてあるジョブだけ表示しています。",
    close: "閉じる", job: "ジョブ", ai: "使うAI",
  } : {
    nav: "⚙️ Automation AI", title: "AI for automation jobs",
    lead: "Choose the AI each job runs on, separately from the default you chat with. Applies from the next run (reversible).",
    unsaved: "Not saved yet: jobs currently follow the scheduler's fallback file. Saving makes this the source of truth.",
    master: "Master (your chat default)", def: "Jobs without their own choice", inherit: "Use the default", follow: "Follow the master",
    model: "Model (empty = that AI's default)", effort: "effort", last: "Last run", never: "no run recorded yet",
    off: "disabled", gone: "not in the scheduler", save: "Save", saved: "Saved", filter: "Filter",
    jobsError: "Could not read the scheduler's job list; showing only the jobs named in the policy.",
    close: "Close", job: "Job", ai: "AI",
  };

  const summary = (ai) => ai ? [LABELS[ai.backend] || ai.backend, ai.model, ai.effort].filter(Boolean).join(" · ") : "";
  const describe = (entry, master) => !entry ? "" :
    entry.mode === MASTER ? `${L().follow} → ${summary(master)}` : summary(entry);
  const when = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString(undefined,
    { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); };

  // One "which AI" control: a choice select, then model and effort when a backend is chosen.
  function picker(entry, backends, { allowInherit, onChange }) {
    const choice = h("select", { class: "auto-choice", "aria-label": L().ai });
    const opts = [...(allowInherit ? [[INHERIT, L().inherit]] : []), [MASTER, L().follow],
      ...Object.keys(backends).map((b) => [b, LABELS[b] || b])];
    for (const [v, t] of opts) choice.append(h("option", { value: v, text: t }));
    choice.value = !entry ? INHERIT : entry.mode === MASTER ? MASTER : entry.backend;
    const listId = `auto-models-${Math.random().toString(36).slice(2)}`;
    const model = h("input", { type: "text", class: "auto-model", list: listId, placeholder: L().model,
      size: 16, value: entry && entry.model ? entry.model : "" });
    const models = h("datalist", { id: listId });
    const effort = h("select", { class: "auto-effort", "aria-label": L().effort });
    const sync = () => {
      const b = backends[choice.value];
      model.hidden = effort.hidden = !b;
      if (!b) return;
      models.replaceChildren(...b.models.map((m) => h("option", { value: m })));
      const keep = effort.value || (entry && entry.effort) || "";
      effort.replaceChildren(h("option", { value: "", text: "—" }), ...b.efforts.map((e) => h("option", { value: e, text: e })));
      effort.value = b.efforts.includes(keep) ? keep : "";
    };
    choice.addEventListener("change", () => { sync(); onChange && onChange(); });
    sync();
    const value = () => {
      if (choice.value === INHERIT) return null;
      if (choice.value === MASTER) return { mode: MASTER };
      return { mode: "backend", backend: choice.value, model: model.value.trim() || null, effort: effort.value || null };
    };
    return { el: h("span", { class: "auto-picker" }, choice, model, models, effort), value };
  }

  async function show() {
    const overlay = $("help");
    let data;
    try { data = await api("/automation/jobs"); } catch (err) { toast(err.message); return; }
    const t = L();
    const defaultPicker = picker(data.default || { mode: MASTER }, data.backends, { allowInherit: false });
    const rows = data.jobs.map((job) => {
      const p = picker(job.entry, data.backends, { allowInherit: true });
      const badges = [job.enabled === false ? t.off : null, job.enabled === null ? t.gone : null,
        ...(job.schedules || [])].filter(Boolean).join(" · ");
      const last = job.last_run ? `${t.last}: ${summary(job.last_run)}${job.last_run.mode === MASTER ? ` (${t.follow})` : ""} · ${when(job.last_run.at)}` : `${t.last}: ${t.never}`;
      const tr = h("tr", { "data-text": `${job.id} ${job.name}`.toLowerCase() },
        h("td", {}, h("div", { text: job.name }), h("div", { class: "dim", text: [job.id, badges].filter(Boolean).join(" · ") })),
        h("td", {}, p.el, h("div", { class: "dim", text: job.entry ? last : `→ ${describe(job.effective, data.master)} · ${last}` })));
      return { id: job.id, tr, value: p.value };
    });
    const search = h("input", { type: "search", class: "auto-filter", placeholder: t.filter, oninput: () => {
      const q = search.value.trim().toLowerCase();
      for (const r of rows) r.tr.hidden = Boolean(q) && !r.tr.dataset.text.includes(q);
    } });
    const save = async () => {
      const jobs = {};
      for (const r of rows) { const v = r.value(); if (v) jobs[r.id] = v; }
      try {
        await api("/automation/ai-policy", { method: "PUT", body: { default: defaultPicker.value(), jobs } });
        toast(`✅ ${t.saved}`);
        show();
      } catch (err) { toast(err.message); }
    };
    const card = h("div", { class: "card automation", onclick: (e) => e.stopPropagation() },
      h("div", { class: "auto-head" }, h("h2", { text: t.title }),
        h("button", { class: "icon-btn", type: "button", "aria-label": t.close, text: "×",
          onclick: () => { overlay.hidden = true; overlay.dataset.mode = ""; } })),
      h("p", { class: "dim", text: t.lead }),
      data.configured ? null : h("p", { class: "auto-warn", text: t.unsaved }),
      data.jobs_error ? h("p", { class: "auto-warn", text: t.jobsError }) : null,
      h("p", {}, h("strong", { text: `${t.master}: ` }), summary(data.master)),
      h("div", { class: "auto-default" }, h("strong", { text: `${t.def}: ` }), defaultPicker.el),
      search,
      h("div", { class: "auto-scroll" }, h("table", { class: "auto-table" },
        h("thead", {}, h("tr", {}, h("th", { text: t.job }), h("th", { text: t.ai }))),
        h("tbody", {}, rows.map((r) => r.tr)))),
      h("div", { class: "auth-actions" }, h("button", { class: "btn primary", type: "button", text: t.save, onclick: save })));
    overlay.dataset.mode = "automation";
    overlay.hidden = false;
    overlay.replaceChildren(card);
  }

  function nav() {
    return h("button", { class: "nav-automation", type: "button", text: L().nav,
      onclick: (e) => { e.stopPropagation(); show(); } });
  }

  return { nav, show };
})();

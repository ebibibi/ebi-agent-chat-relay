// Optional Ebi Workspace: references point to Markdown, never a second task database.
"use strict";
window.ConsoleWorkspace = (() => {
  let data = {available:false, entries:[]};
  let chosen = localStorage.getItem("workspacePath") || "";
  let browsing = chosen;
  let panel;
  let busy = false;
  let loaded = false;
  const SALES_URL = "https://note.com/ebibibi/n/n300772aeac93";
  const SALES_URL_EN = "https://note.com/ebibibi/n/nc327ab1de017";
  const lang = () => document.documentElement.lang === "ja";
  const L = () => lang() ? {
    title:"Ebi Workspace", pick:"コンテキスト・プロジェクト", auto:"自動で紐づけ", tasks:"タスク",
    add:"追加", edit:"編集", save:"保存", cancel:"取消", run:"AIに実行", close:"閉じる", reload:"更新",
    newTask:"新しいタスク", use:"ここで仕事を始める", overview:"概要", empty:"タスクはありません",
    project:"プロジェクト", context:"コンテキスト", pending:"処理中…", threads:"関連する会話",
  } : {title:"Ebi Workspace",pick:"Context / project",auto:"Associate automatically",tasks:"Tasks",
    add:"Add",edit:"Edit",save:"Save",cancel:"Cancel",run:"Run with AI",close:"Close",reload:"Refresh",
    newTask:"New task",use:"Start work here",overview:"Overview",empty:"No tasks",project:"Project",
    context:"Context",pending:"Working…",threads:"Related conversations"};
  const entry = () => data.entries.find(e => e.path === browsing);
  const button = (text, onclick, cls="btn") => h("button", {type:"button",class:cls,text,onclick});

  function selector(value, change) {
    return h("select", {class:"workspace-picker", "aria-label":L().pick,
      onchange:e=>change(e.target.value)},
      h("option", {value:"",text:L().auto,selected:!value}),
      data.entries.map(e=>h("option",{value:e.path,selected:e.path===value,
        text:`${e.kind==='project'?'📁':'🗂️'} ${e.path}`})));
  }
  function choose(path) {
    chosen=path; localStorage.setItem("workspacePath",path); capturePicker();
  }
  function capturePicker() {
    let box=document.getElementById("workspace-capture");
    if (!box) {
      box=h("div",{id:"workspace-capture",class:"workspace-capture"});
      $("capture").before(box);
    }
    const offer = !data.available && loaded && localStorage.getItem("workspaceOfferDismissed") !== "1";
    box.hidden=!data.available && !offer;
    if (offer) box.replaceChildren(h("div",{class:"workspace-offer"},
      h("span",{text:lang()?"会話をプロジェクト・タスクにつなげる — Ebi Workspace":"Connect conversations to projects and tasks — Ebi Workspace"}),
      button(lang()?"詳しく見る":"Learn more",show),
      button(lang()?"あとで":"Not now",()=>{localStorage.setItem("workspaceOfferDismissed","1");capturePicker();})));
    else if (!data.available) box.replaceChildren();
    if (data.available) box.replaceChildren(h("span",{text:"🗂️ Ebi Workspace"}),selector(chosen,choose));
  }
  async function load() {
    try {
      data=await api("/workspace");
      loaded=true;
      data.entries ||= [];
      if (!data.entries.some(e=>e.path===chosen)) chosen="";
      if (!data.entries.some(e=>e.path===browsing)) browsing=data.entries[0]?.path || "";
      capturePicker(); renderNav();
      if (panel && !panel.hidden && !panel.querySelector("input:focus,textarea:focus")) draw();
    } catch (err) { if (panel && !panel.hidden) toast(err.message); }
  }
  function nav() {
    if (!data.available) return loaded ? button(`🗂️ Ebi Workspace · ${lang()?"導入案内":"Discover"}`,show,"nav-workspace") : null;
    const count=data.entries.reduce((n,e)=>n+e.tasks.filter(t=>!t.done).length,0);
    return button(`🗂️ ${L().title} · ${count}`,show,"nav-workspace");
  }
  function show() {
    if (!panel) {
      panel=h("section",{id:"workspace-panel",class:"workspace-panel",role:"dialog",
        "aria-modal":"true","aria-label":L().title});
      document.body.append(panel);
      panel.addEventListener("keydown",e=>{if(e.key==="Escape"){e.stopPropagation();hide();}});
    }
    panel.hidden=false; $("nav").classList.remove("open"); draw(); load();
  }
  function hide() { if(panel) panel.hidden=true; }
  function draw() {
    if (!panel) return;
    if (!data.available) { onboarding(); return; }
    const l=L();
    const old=panel.querySelector(".ws-content")?.scrollTop || 0;
    const menu=h("div",{class:"ws-entries"},...data.entries.map(e=>button(
      `${e.kind==='project'?'📁':'🗂️'} ${e.path}`,
      ()=>{browsing=e.path;draw();},`ws-entry${e.path===browsing?' on':''}`)));
    const content=h("div",{class:"ws-content"});
    const selected=entry();
    if (selected) {
      content.append(h("h2",{text:selected.path}),h("p",{class:"dim",text:l[selected.kind]}),
        button(l.use,()=>{choose(selected.path);hide();$("capture-input").focus();}),
        button(l.overview,()=>overview(selected,content)),h("h3",{text:l.tasks}));
      const list=h("div",{class:"ws-tasks"});
      for(const task of selected.tasks) list.append(taskRow(selected,task));
      if(!selected.tasks.length) list.append(h("p",{text:l.empty}));
      content.append(list);
      const input=h("input",{type:"text",placeholder:l.newTask,"aria-label":l.newTask,maxlength:4000});
      content.append(h("form",{class:"ws-add",onsubmit:e=>{
        e.preventDefault(); if(input.value.trim()) change(selected,{text:input.value},"POST");
      }},input,h("button",{class:"btn",type:"submit",disabled:busy,text:l.add})));
      const related=state.items.filter(i=>i.workspace_path===selected.path);
      if(related.length) content.append(h("h3",{text:l.threads}),...related.map(i=>button(
        i.title||i.id,()=>{hide();openItem(i.id);},"btn ws-related")));
    }
    panel.replaceChildren(h("div",{class:"ws-shell"},h("header",{class:"ws-head"},
      h("h1",{text:l.title}),button(l.reload,load),button(l.close,hide)),h("div",{class:"ws-body"},menu,content)));
    content.scrollTop=old;
  }
  function onboarding() {
    const ja=lang();
    const features=ja ? [
      "会話をコンテキストやプロジェクトに紐づけ、前回の続きから仕事を再開。",
      "ObsidianのタスクをConsoleで確認・更新し、そのままAIに実行を依頼。",
      "計画・記録・知識を手元のMarkdownに蓄積。人間とAIで同じ情報を使えます。",
    ] : [
      "Link conversations to contexts and projects, then resume where you left off.",
      "Review and update Obsidian tasks in Console and ask AI to carry them out.",
      "Keep plans, work logs and knowledge in local Markdown shared by you and your AI.",
    ];
    panel.replaceChildren(h("div",{class:"ws-shell ws-onboarding"},
      h("header",{class:"ws-head"},h("h1",{text:"Ebi Workspace"}),button(L().close,hide)),
      h("div",{class:"ws-content"},
        h("h2",{text:ja?"会話の続きを、仕事の続きに。":"Turn ongoing conversations into ongoing work."}),
        h("p",{text:ja?"Claude Code / CodexとObsidianをつなぐ、買い切りの有料プラグインです。Consoleとの連携で、会話と仕事の文脈を一緒に扱えます。":"A paid, one-time-purchase plugin connecting Claude Code / Codex with Obsidian. Console integration brings your conversations and work context together."}),
        h("ul",{},...features.map(text=>h("li",{text}))),
        h("a",{class:"btn ws-purchase",href:ja?SALES_URL:SALES_URL_EN,target:"_blank",rel:"noopener noreferrer",
          text:ja?"Ebi Workspaceの内容・購入方法を見る":"Explore Ebi Workspace and purchase options"}),
        h("h3",{text:ja?"すでにお持ちの場合":"Already have Ebi Workspace?"}),
        h("p",{text:ja?"Consoleを動かしている環境でWorkspaceをセットアップしてから、下のボタンで接続を確認してください。":"Set up Workspace in the environment running Console, then check the connection below."}),
        button(ja?"接続を再確認":"Check connection",load))));
  }
  async function overview(selected, content) {
    try {
      const name=selected.kind==="project"?"overview.md":"AGENTS.md";
      const result=await api(`/workspace/document?path=${encodeURIComponent(selected.path)}&name=${name}`);
      let box=content.querySelector(".ws-overview");
      if(!box){box=h("div",{class:"ws-overview"});content.append(box);}
      box.replaceChildren(h("pre",{text:result.text}));
    } catch(err){toast(err.message);}
  }
  function taskRow(selected,task) {
    const l=L();
    const row=h("div",{class:`ws-task${task.done?' done':''}`});
    const check=h("input",{type:"checkbox",checked:task.done,disabled:busy,"aria-label":task.text,
      onchange:e=>change(selected,{line:task.line,done:e.target.checked},"PATCH")});
    row.append(check,h("span",{class:"ws-task-text",text:task.text}),
      button(l.edit,()=>edit(row,selected,task)),
      h("button",{type:"button",class:"btn",disabled:busy||task.done,text:l.run,
        onclick:()=>run(selected,task)}));
    return row;
  }
  function edit(row,selected,task) {
    const input=h("input",{type:"text",value:task.text,"aria-label":L().edit,maxlength:4000});
    row.replaceChildren(h("form",{class:"ws-edit",onsubmit:e=>{
      e.preventDefault();change(selected,{line:task.line,text:input.value},"PATCH");
    }},input,h("button",{type:"submit",class:"btn",text:L().save}),button(L().cancel,draw)));
    input.focus();
  }
  async function change(selected,fields,method) {
    if(busy)return;
    busy=true;
    try {
      const updated=await api("/workspace/tasks",{method,body:{path:selected.path,revision:selected.revision,...fields}});
      data.entries=data.entries.map(e=>e.path===updated.path?updated:e);
      toast(T.saved);draw();renderNav();
    } catch(err){toast(err.message);await load();}
    finally{busy=false;draw();}
  }
  async function run(selected,task) {
    if(busy)return;
    busy=true;draw();
    try{
      const result=await api("/workspace/run",{method:"POST",body:{path:selected.path,line:task.line,revision:selected.revision}});
      choose(selected.path);hide();await refresh();openItem(result.item.id);
    }catch(err){toast(err.message);}
    finally{busy=false;draw();}
  }
  function picker(item) {
    if(!data.available)return null;
    return selector(item.workspace_path||"",path=>patch(item.id,{workspace_path:path||null}));
  }
  return {load,nav,picker,path:()=>chosen||null};
})();

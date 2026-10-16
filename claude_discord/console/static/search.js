// Deliberate body search, separate from the instant board filter.
"use strict";
window.ConsoleSearch = (() => {
  let panel, input, results, status, sequence=0, returnFocus;
  const L=()=>document.documentElement.lang==='ja'?{
    label:'本文も検索', title:'会話の本文を検索', placeholder:'覚えている言葉で検索', search:'検索', close:'閉じる',
    scope:'保存済みのConsole本文と、この端末に残るClaude会話記録を検索します。完了した会話や、Discordから削除した会話の退避分も対象です。',
    loading:'検索中…',empty:'見つかりませんでした。別の言葉で検索できます。',body:'本文',summary:'タイトル・メモ',
    archived:'Discordから削除済み・読むだけ',back:'← 検索結果に戻る',
    count:n=>`${n} 件`,partial:'会話記録の検索が時間切れになりました。取得できた結果を表示しています。',more:'上位50件です。言葉を追加して絞り込めます。',
  }:{label:'Search messages',title:'Search conversation bodies',placeholder:'Words you remember',search:'Search',close:'Close',
    scope:'Search saved Console messages and local Claude transcripts, including completed conversations and exports of threads deleted from Discord.',
    loading:'Searching…',empty:'No matches. Try another phrase.',body:'Message',summary:'Title / note',archived:'Deleted from Discord · read only',back:'← Back to results',count:n=>`${n} results`,
    partial:'Transcript search timed out. Showing available results.',more:'Showing 50 results. Use a more specific phrase.'};
  function close(){sequence++;panel.hidden=true;returnFocus?.focus({preventScroll:true});}
  function show(){
    const l=L(); returnFocus=document.activeElement;
    if(!panel){
      input=h('input',{id:'message-search-query',type:'search',maxlength:500,placeholder:l.placeholder,'aria-label':l.placeholder});
      results=h('div',{class:'search-results'});
      status=h('p',{'aria-live':'polite',class:'dim'});
      panel=h('section',{id:'message-search',class:'message-search',role:'dialog','aria-modal':'true','aria-label':l.title},
        h('div',{class:'search-shell'},h('header',{},h('h2',{text:l.title}),h('button',{type:'button',class:'btn',text:l.close,onclick:close})),
          h('form',{onsubmit:e=>{e.preventDefault();search();}},input,h('button',{class:'btn',type:'submit',text:l.search})),
          h('p',{class:'dim',text:l.scope}),status,results));
      panel.addEventListener('keydown',e=>{
        e.stopPropagation();
        if(e.key==='Escape'){e.preventDefault();close();}
        if(e.key==='Tab'){
          const nodes=[...panel.querySelectorAll('input,button')], first=nodes[0],last=nodes.at(-1);
          if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
          else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
        }
      });
      document.body.append(panel);
    }
    panel.hidden=false;input.value=$('filter').value;input.focus();
    if(input.value.trim())search();
  }
  async function search(){
    const current=++sequence, query=input.value.trim(), l=L();
    results.replaceChildren();status.textContent=query?l.loading:'';
    if(!query)return;
    try{
      const data=await api(`/search?q=${encodeURIComponent(query)}`);
      if(current!==sequence||panel.hidden)return;
      status.textContent=[data.results.length?l.count(data.results.length):l.empty,data.partial?l.partial:'',data.more?l.more:''].filter(Boolean).join(' ');
      results.replaceChildren(...data.results.map(hit=>h('button',{type:'button',class:'search-result',onclick:async()=>{
        if(hit.archived)return readArchived(hit,current);
        close();
        if(!state.byId.has(hit.item.id)){state.items.push(hit.item);state.byId.set(hit.item.id,hit.item);}
        await openItem(hit.item.id);
      }},h('strong',{text:hit.item.title||hit.item.id}),h('span',{class:'dim',text:hit.archived?l.archived:hit.source==='body'?l.body:l.summary}),h('span',{text:hit.snippet}))));
    }catch(err){if(current===sequence&&!panel.hidden)status.textContent=err.message;}
  }
  // A thread deleted from Discord cannot be resumed; show its export in place, read only.
  async function readArchived(hit,current){
    const l=L(), shown=[...results.children], scroll=results.scrollTop;
    const back=h('button',{type:'button',class:'btn',text:l.back,onclick:()=>{
      results.replaceChildren(...shown);results.scrollTop=scroll;shown[0]?.focus({preventScroll:true});}});
    results.replaceChildren(back,h('p',{class:'dim',text:l.loading}));back.focus();
    try{
      const data=await api(`/archive/${encodeURIComponent(hit.item.id.slice(1))}`);
      if(current!==sequence||panel.hidden||!back.isConnected)return;
      results.replaceChildren(back,h('h3',{class:'archive-title',text:data.title||hit.item.title||''}),h('p',{class:'dim',text:l.archived}),
        ...data.messages.map(m=>h('div',{class:`msg ${m.kind}`},
          h('div',{class:'who',text:[m.author,(m.created_at||'').slice(0,16).replace('T',' ')].join(' · ')}),
          h('div',{class:'body md'},MD.render(m.content)))));
    }catch(err){if(back.isConnected)results.replaceChildren(back,h('p',{class:'dim',text:err.message}));}
  }
  function wire(){
    const button=$('search-messages');button.textContent=L().label;button.addEventListener('click',show);
  }
  window.addEventListener('console-language-change',()=>{
    sequence++;
    panel?.remove(); panel=null;
    $('search-messages').textContent=L().label;
  });
  return {wire};
})();

'use strict';
const fs=require('node:fs'), vm=require('node:vm'), crypto=require('node:crypto');
const d=require('node:path').resolve(__dirname,'..')+'/';
const html=fs.readFileSync(d+'te-agent-console.html','utf8');
function extract(a,b){const i=html.indexOf(a),j=html.indexOf(b,i);if(i<0||j<0)throw Error('Missing source segment');return html.slice(i,j);}
const dispatch=extract('    async function dispatchPrompt() {',"    document.getElementById('sendBtn').addEventListener('click', dispatchPrompt);");
const covision=extract('    function updateCovisionRibbon() {','    let lastLedgerSnapshot');
const ping=extract('    async function executePingPong(', '    window.executePingPong = executePingPong;');
async function simulatePing(mode){
  const button={disabled:false,textContent:''},sessions=[{id:'sess-1',messages:[]},{id:'sess-2',messages:[]}],renders=[];let payload=null,ledgerCalls=0;
  const c={document:{getElementById:()=>button},sessions,activeSessionId:'sess-1',renderThread:s=>renders.push(s.id),recordSillokDebateEntry:()=>{ledgerCalls++},
    fetch:async (url,opts)=>{payload=JSON.parse(opts.body);if(mode==='late')c.activeSessionId='sess-2';if(mode==='offline')throw Error('offline');if(mode==='bad-http')return{ok:false};if(mode==='bad-json')return{ok:true,json:async()=>{throw Error('bad json')}};if(mode==='bad-answer')return{ok:true,json:async()=>({ok:true,externalReply:' '})};if(mode==='not-ok')return{ok:true,json:async()=>({ok:false})};return{ok:true,json:async()=>({ok:true,target:'Hermes local',externalReply:'synthetic review'})};}};
  vm.createContext(c);vm.runInContext(ping,c,{timeout:1000});await c.executePingPong('synthetic review');return {payload,sessions,renders,ledgerCalls,button};
}
async function simulate({mode='ok',session='CPA',includeBrowser=false,stale=false,scope=true,live=false,invalidState=false}={}){
  const input={value:'synthetic prompt'}, panel={scrollTop:0,scrollHeight:1}, wave={style:{}}, helperStatus={textContent:''}, helperProgress={style:{}};
  let payload=null; const renders=[]; const sessions=[{id:'SYNTH-CPA',messages:[]},{id:'SYNTH-FAMILY',messages:[]}];
  const c={document:{getElementById:(id)=>({promptInput:input,activeModelLabel:{textContent:'model'},panelConsole:panel,coVisionSoundwave:wave,helperDeskStatusLabel:helperStatus,helperProgressFill:helperProgress})[id]||{},body:{classList:{contains:()=>false}}},
    harnessNodes:[{id:'A',name:'synthetic agent',kind:'agent',provider:'test',model:'model'}],selectedNodeId:'A',getGoverningFolder:()=>scope?{name:session}:null,
    sessions,activeSessionId:'SYNTH-'+session,getDescendants:()=>[{name:'synthetic child'}],PROVIDER_META:{test:{label:'mock'},chatgpt:{label:'mock'}},
    skillsState:[{id:'skill-on',on:true},{id:'skill-off',on:false}],toolsState:[{id:'tool-on',on:true},{id:'tool-off',on:false}],
    safeFetch:async (...args)=>{const r=await c.fetch(...args);if(!r.ok){const e=new Error('HTTP 403');e.status=403;throw e;}return r;},
    classifyHttpError:(status,err)=>({label:status===403?'권한 차단':'연결 실패'}),showToast:()=>{},persistEncryptedChats:async()=>true,updateMeasuredGenerationTokens:()=>{},
    currentAuthToken:null,currentAuthSessionId:'sess-ui-test',
    lessonsLedger:[{scope:'CPA',rule:'CPA_RULE'},{scope:'FAMILY',rule:'FAMILY_RULE'}],activeBrowserContext:'',
    browserState:invalidState?{}:{kind:'chatgpt',externalLlm:'Synthetic',url:'https://example.invalid/',recentMessages:[{role:'user',text:'SYNTH_PRIVATE_CONTEXT'}]},browserState2:null,activeFocusedPane:'top',isDualBrowser:false,covisionLabel:{textContent:''},
    switchStageTab:()=>{},renderThread:(s)=>renders.push(s.id),renderLessonsLedger:()=>{},
    fetch:async (url,opts)=>{payload=JSON.parse(opts.body);if(mode==='switch-success'||mode==='switch-fail')c.activeSessionId='SYNTH-FAMILY';if(mode==='offline'||mode==='switch-fail')throw Error('synthetic offline'); if(mode==='http-error')return{ok:false,json:async()=>({ok:true,reply:'bad'})};if(mode==='malformed')return{ok:true,json:async()=>{throw Error('bad json')}};if(mode==='not-ok')return{ok:true,json:async()=>({ok:false})};if(mode==='missing-reply')return{ok:true,json:async()=>({ok:true})};if(mode==='blank-reply')return{ok:true,json:async()=>({ok:true,reply:'  '})};return{ok:true,json:async()=>({ok:true,live,usedModel:'mock-model',reply:'SYNTHETIC'})};}};
  vm.createContext(c);vm.runInContext(dispatch,c,{timeout:1000});vm.runInContext(covision,c,{timeout:1000});
  if(includeBrowser){c.updateCovisionRibbon();if(stale){c.browserState=null;c.updateCovisionRibbon();}}
  await c.dispatchPrompt();return{payload,reply:sessions.find(s=>s.id==='SYNTH-'+session).messages.at(-1),remainingContext:c.activeBrowserContext,renders,messagesBySession:sessions.map(s=>({id:s.id,messages:s.messages}))};
}
let results=[];function test(name,condition){results.push({test:name,result:condition?'PASS':'FAIL'});}
(async()=>{
  const a=await simulate({session:'CPA'}), b=await simulate({session:'FAMILY'}), unknown=await simulate({scope:false}), ctx=await simulate({includeBrowser:true}), stale=await simulate({includeBrowser:true,stale:true}), invalid=await simulate({includeBrowser:true,invalidState:true}), live=await simulate({live:true}), late=await simulate({mode:'switch-success'}), lateFail=await simulate({mode:'switch-fail'});
  test('CPA session included',a.payload.sessionId==='SYNTH-CPA');test('family session distinct',b.payload.sessionId==='SYNTH-FAMILY');
  test('selected skill only',JSON.stringify(a.payload.skills)==='["skill-on"]');test('selected tool only',JSON.stringify(a.payload.tools)==='["tool-on"]');
  test('CPA excludes all unverified lessons',a.payload.lessons.length===0);test('family excludes all unverified lessons',b.payload.lessons.length===0);test('unknown scope excludes lessons',unknown.payload.lessons.length===0);
  test('browser content disabled by default',ctx.payload.browserContext==='' && ctx.remainingContext==='');test('stale browser content cleared',stale.remainingContext==='' && stale.payload.browserContext==='');test('invalid browser state fails closed',invalid.remainingContext==='' && invalid.payload.browserContext==='');
  test('late success does not render in another session',JSON.stringify(late.renders)==='["SYNTH-CPA"]');test('late error does not render in another session',JSON.stringify(lateFail.renders)==='["SYNTH-CPA"]');
  test('late success stays in originating session',late.messagesBySession[0].messages.length===2 && late.messagesBySession[0].messages[1].body==='SYNTHETIC' && late.messagesBySession[1].messages.length===0);
  test('late failure stays in originating session',lateFail.messagesBySession[0].messages.length===2 && lateFail.messagesBySession[0].messages[1].heading.startsWith('Request failed [') && lateFail.messagesBySession[1].messages.length===0);
  for(const mode of ['offline','http-error','not-ok','malformed','missing-reply','blank-reply']){const x=await simulate({mode});test(mode+' fails closed',x.reply.heading.startsWith('Request failed [') && !/orchestrat|cache hit|live graphrag/i.test(JSON.stringify(x.reply)));}
  test('live false neutral status',a.reply.heading==='synthetic agent — Response received' && !/live graphrag|local execution/i.test(JSON.stringify(a.reply)));
  test('live true neutral status',live.reply.heading==='synthetic agent — Response received' && !/live graphrag|local execution/i.test(JSON.stringify(live.reply)));
  test('hardcoded vault removed',/let vaultItems = \[\];/.test(html));
  test('false independent-judge badge removed',!html.includes('Outside Judge Verified ·'));
  test('fake browser allocation removed',!html.includes('Dedicated Session & Browser Initialized'));
  const okPing=await simulatePing('ok'),latePing=await simulatePing('late');
  test('local review sends actual session',okPing.payload.sessionId==='sess-1');test('local review does not fabricate second reviewer',okPing.sessions[0].messages.length===2 && okPing.sessions[0].messages[1].heading==='Local response received');
  test('local review does not auto-write ledger',okPing.ledgerCalls===0);test('late review remains in original session',latePing.sessions[1].messages.length===0 && JSON.stringify(latePing.renders)==='["sess-1"]');
  for(const mode of ['offline','bad-http','bad-json','bad-answer','not-ok']){const x=await simulatePing(mode);test('review '+mode+' fails closed',x.sessions[0].messages.at(-1).heading==='Local review failed' && x.ledgerCalls===0 && !x.button.disabled);}
  test('no GraphRAG search on startup',!html.includes("executeGraphRagSearch(document.getElementById('graphRagInput').value);"));
  const patterns=[/\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b/g,/\bAIza[A-Za-z0-9_-]{30,}\b/g,/\bgh[pousr]_[A-Za-z0-9_]{20,}\b/g,/\bxox[baprs]-[A-Za-z0-9-]{15,}\b/g];
  test('known key-like patterns absent',!patterns.some(p=>p.test(html)));test('key detector positive controls',patterns[0].test('sk-'+'A'.repeat(30)) && patterns[2].test('ghp_'+'A'.repeat(30)));
  // Real Markdown formatter regression: content remains text, never executable HTML.
  const formatter = extract('    function renderSafeInlineMarkdown(value) {', '    function renderEvidenceBlock(msg) {');
  const mdCtx = {
    escapeHtml: value => String(value ?? '').replace(/&/g,'&amp;')
      .replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;')
  };
  vm.createContext(mdCtx);
  vm.runInContext(formatter,mdCtx,{timeout:1000});
  const markdownSample = [
    '**최종 결론:**', '', '- **응답 속도:** 100ms 미만',
    '- **물리적 이동:** 2–4px', '',
    '<img src=x onerror=alert(1)>', '',
    '`code`'
  ].join(String.fromCharCode(10));
  const mdOutput=mdCtx.renderSafeMarkdown(markdownSample);
  test('Markdown bold headings render as markup',mdOutput.includes('<strong>최종 결론:</strong>'));
  test('Markdown bullet list renders without raw stars',mdOutput.includes('<ul>') && mdOutput.includes('<li><strong>응답 속도:</strong>'));
  test('Markdown HTML injection escapes dangerous tags',mdOutput.includes('&lt;img ') && !mdOutput.includes('<img '));
  test('Markdown inline code stays inert',mdOutput.includes('<code>code</code>'));
  test('composer grouped status/footer controls',html.includes('class="composer-status-footer"') && html.includes('class="composer-meta-strip"'));
  test('chat response uses safe markdown renderer',html.includes('renderSafeMarkdown(msg.body)'));
  test('composer actions wrap within narrow pane width',html.includes('.composer-controls { flex-wrap: wrap; gap: 8px; min-width: 0; }'));
  test('send action stays in a shrink-safe control group',html.includes('.controls-right { flex-shrink: 0; margin-left: auto; }'));
  test('chat history save is explicit opt-in',html.includes('id="chatVaultBtn"') && html.includes('🔒 Save chats'));
  test('chat history uses AES-GCM encrypted storage',html.includes("name:'AES-GCM'") && html.includes('crypto.subtle.encrypt') && html.includes('crypto.subtle.decrypt'));
  test('chat history KDF is PBKDF2 with slow SHA256',html.includes('iterations:300000') && html.includes("hash:'SHA-256'"));
  test('chat history passphrase field stays password masked',html.includes('id="chatVaultPass" type="password"'));
  test('chat history restores only validated sessions',html.includes('chatVaultValidate(JSON.parse('));
  test('chat history avoids silently discarding unsaved tabs',html.includes('Replace unsaved current chats'));
  test('measured generation tokens never claim cache hits',html.includes('updateMeasuredGenerationTokens()') && html.includes('cache hits UNOBSERVED'));


  const failed=results.filter(x=>x.result==='FAIL');console.log(JSON.stringify({total:results.length,passed:results.length-failed.length,failed:failed.length,results,scope:'isolated JS functions and fake data only; no server request',sha256:crypto.createHash('sha256').update(html).digest('hex')}));if(failed.length)process.exitCode=1;
})().catch(e=>{console.error(e.stack);process.exitCode=1});

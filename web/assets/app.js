'use strict';
const $ = id => document.getElementById(id);
const labels = {queued:'排队中',running:'运行中',succeeded:'已完成',failed:'失败',cancelled:'已取消'};
const pages = {
  overview:['研究总览','掌握研究进度，从数据到结果。','WORKSPACE OVERVIEW','总览'],
  research:['研究任务','创建实验、跟踪执行进度，保留每一次研究记录。','RESEARCH WORKSPACE','研究任务'],
  reports:['报告中心','集中查看已完成的研究，追溯指标、模型与归档文件。','RESEARCH REPORTS','报告中心'],
  data:['数据管理','查看数据快照、覆盖范围与质量验收结果。','DATA MANAGEMENT','数据管理'],
  factors:['因子目录','检索因子定义、固定版本与内置组合。','FACTOR LIBRARY','因子目录'],
  detail:['研究详情','从结果回溯配置与执行过程。','EXPERIMENT DETAIL','研究任务']
};
let routeVersion=0, route='overview', active=null, detailTab='result', offset=0, reportOffset=0, factorOffset=0, pendingRequest=null, dataCache=[], factorQuery='';
const pageSize=20;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function message(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function safe(fn){try{await fn();}catch(e){message('操作未完成：'+e.message);}}
async function api(path,options={}){
 const r=await fetch('/api/v1'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
 if(!r.ok){let d=await r.json().catch(()=>({detail:'服务暂时不可用'}));throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));}return r.json();
}
function link(text,href){const a=el('a',text);a.href=href;return a;}
function button(text,fn,cls='quiet'){const b=el('button',text,cls);b.onclick=()=>safe(fn);return b;}
function badge(status){return el('span',labels[status]||status,'badge '+status);}
function date(s){return s?new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';}
function empty(parent,text){parent.append(el('div',text,'empty'));}
function cells(row,values){for(const v of values){const td=el('td');td.append(v instanceof Node?v:document.createTextNode(String(v??'—')));row.append(td);}return row;}
function pagination(prefix,offset,total){$(prefix+'-page').textContent=total?`${offset+1}–${Math.min(offset+pageSize,total)} / 共 ${total} 条`:'暂无记录';}
function detailHref(id,source='research'){return '#'+source+'/'+id;}
async function loadStatus(){
 const s=await api('/status');const healthy=s.services.length>=2&&s.services.every(x=>x.healthy);
 $('connection').textContent=healthy?'● 本地服务正常':'◌ 部分服务等待就绪';
 const counts=Object.fromEntries(s.runs.map(x=>[x.status,Number(x.count)]));
 const stats=[['研究任务',Object.values(counts).reduce((a,b)=>a+b,0),'所有已登记实验'],['已完成研究',counts.succeeded||0,'结果已统一归档'],['执行中',(counts.running||0)+(counts.queued||0),'包含运行与排队任务'],['数据快照',dataCache.length,'已登记的独立数据版本']];
 $('stats').replaceChildren();for(const [label,value,note]of stats){const box=el('div',undefined,'stat');box.append(el('span',label,'stat-label'),el('strong',value),el('small',note));$('stats').append(box);}
 $('services').replaceChildren();for(const [name,ok]of [['平台 API',true],...s.services.map(x=>[x.name==='worker'?'研究执行器':'任务调度器',x.healthy])]){const box=el('div',undefined,'service');box.append(el('span',name),el('span',ok?'● 正常':'◌ 等待',ok?'badge succeeded':'badge queued'));$('services').append(box);}
}
async function loadData(){
 dataCache=await api('/datasets');const selected=$('dataset').value;$('dataset').replaceChildren();$('datasets').replaceChildren();
 for(const d of dataCache){const o=el('option',d.name);o.value=d.id;$('dataset').append(o);
 const box=el('article',undefined,'card'),heading=el('div',undefined,'card-heading');heading.append(el('h2',d.name),el('span',d.acceptance.status==='engineering_sample_pass'?'工程验收通过':'待检查','badge '+(d.acceptance.status==='engineering_sample_pass'?'succeeded':'')));box.append(heading);
 const summary=el('div',undefined,'data-summary');for(const [name,value]of [['股票池',`${d.config.symbols.length} 只股票`],['覆盖区间',`${d.config.start} 至 ${d.config.end}`],['交易日',`${d.acceptance.calendar_sessions??'—'} 天`],['基准',d.config.benchmark]]){const v=el('div');v.append(el('small',name),el('strong',value));summary.append(v);}box.append(summary);
 const symbols=el('div',undefined,'symbols');for(const s of d.config.symbols)symbols.append(el('span',s));box.append(symbols);
 const segments=el('div',undefined,'data-summary');for(const [key,name]of [['train','训练区间'],['valid','验证区间'],['test','测试区间']]){const v=el('div');v.append(el('small',name),el('strong',d.config.segments[key].join(' → ')));segments.append(v);}box.append(segments);
 const issues=Object.values(d.acceptance.stocks||{}).reduce((n,s)=>n+(s.missing_sessions||0),0);box.append(el('p',`缺失交易日记录：${issues} 条。固定工程样本包含选样偏差；数据是当前存储版本，不是完整历史时点数据。`,'hint'));
 const details=el('details');details.append(el('summary','查看完整验收与快照标识'),el('pre',JSON.stringify({id:d.id,source_hash:d.source_hash,acceptance:d.acceptance},null,2)));box.append(details);$('datasets').append(box);
 }
 updateReadiness();
 if(dataCache.some(d=>d.id===selected))$('dataset').value=selected;
 $('submit').disabled=!dataCache.length;if(!dataCache.length)empty($('datasets'),'尚未登记数据快照。完成数据导出与验收后，使用登记脚本接入。');
}
async function loadRecent(){const d=await api('/runs?limit=4');$('recent-runs').replaceChildren();for(const r of d.items){const row=el('div',undefined,'recent-row'),info=el('div');info.append(link(r.title,detailHref(r.id)),el('small',`${date(r.created_at)} · ${r.origin==='import'?'历史导入':'平台研究'}`));row.append(info,badge(r.status));$('recent-runs').append(row);}if(!d.items.length)empty($('recent-runs'),'还没有研究记录，创建第一个实验开始探索。');}
async function loadRuns(){
 const d=await api('/runs?limit='+pageSize+'&offset='+offset+($('run-filter').value?'&status='+encodeURIComponent($('run-filter').value):''));
 renderRuns(d);
}
function renderRuns(d){$('run-count').textContent=`${d.total} 项`;$('runs').replaceChildren();for(const r of d.items){const name=link(r.title,detailHref(r.id));const tr=cells(el('tr'),[name,badge(r.status),r.origin==='import'?'历史导入':r.origin==='schedule'?'定时计划':'平台提交',date(r.created_at),link('查看详情 →',detailHref(r.id))]);$('runs').append(tr);}if(!d.items.length){const tr=el('tr'),td=el('td','暂无符合条件的研究任务','empty');td.colSpan=5;tr.append(td);$('runs').append(tr);}pagination('run',offset,d.total);$('previous').disabled=offset===0;$('next').disabled=offset+pageSize>=d.total;}
async function loadReports(){const d=await api('/runs?status=succeeded&limit='+pageSize+'&offset='+reportOffset);$('report-list').replaceChildren();for(const r of d.items){const card=el('div',undefined,'report-card');card.append(badge(r.status),el('h3',r.title),el('p',`${date(r.finished_at||r.created_at)} · 第 ${r.attempt} 次执行`),link('查看报告与产物 →',detailHref(r.id,'reports')));$('report-list').append(card);}if(!d.items.length)empty($('report-list'),'还没有完成的研究。任务成功后，报告会自动出现在这里。');pagination('report',reportOffset,d.total);$('report-previous').disabled=reportOffset===0;$('report-next').disabled=reportOffset+pageSize>=d.total;}
async function loadFactors(){const d=await api('/factors?limit='+pageSize+'&offset='+factorOffset+'&q='+encodeURIComponent(factorQuery)+($('factor-set-filter').value?'&factor_set='+encodeURIComponent($('factor-set-filter').value):''));$('factor-count').textContent=d.total+' 个';$('factors').replaceChildren();for(const f of d.items){$('factors').append(cells(el('tr'),[f.name,el('span',f.version,'tag'),factorDefinition(f)]));}if(!d.items.length){const td=el('td','未找到匹配的因子，请尝试其他名称。','empty');td.colSpan=3;const tr=el('tr');tr.append(td);$('factors').append(tr);}pagination('factor',factorOffset,d.total);$('factor-previous').disabled=factorOffset===0;$('factor-next').disabled=factorOffset+pageSize>=d.total;}
async function loadSets(){const sets=await api('/factor-sets');$('factor-sets').replaceChildren();for(const s of sets){const c=el('article',undefined,'card');c.append(el('h2',s.name),el('p',`${s.members.length} 个因子 · ${s.id}`),button('查看因子',()=>{$('factor-set-filter').value=s.id;factorOffset=0;return loadFactors();}));$('factor-sets').append(c);}}
function setTab(tab){detailTab=tab;document.querySelectorAll('[data-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.tab===tab)));document.querySelectorAll('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==tab);}
async function showDetail(id){
 const d=await api('/runs/'+encodeURIComponent(id));if(active!==id||route!=='detail')return;
 $('detail-title').textContent=d.title;$('detail-status').replaceChildren(badge(d.status),el('span',`第 ${d.attempt} 次执行 · ${date(d.created_at)} · ${d.id.slice(0,8)}`,'muted'));$('detail-error').textContent=d.error||'';$('detail-error').hidden=!d.error;
 $('spec').textContent=JSON.stringify({id:d.id,spec:d.spec},null,2);$('events').replaceChildren();for(const e of d.events){const v=el('div');v.append(el('time',date(e.created_at)),el('span',labels[e.event]||({started:'开始执行',retry_requested:'请求重试',cancel_requested:'请求取消',imported:'历史导入',lease_expired:'执行器租约过期'}[e.event])||e.event));$('events').append(v);}
 const aq=d.result?.alpha191_quality; $('factor-coverage').hidden=!aq; if(aq)$('factor-coverage').textContent=`Alpha191：定义 ${aq.requested} 项，已计算 ${aq.computed} 项，模型使用 ${aq.model_features} 项。缺数据排除：${Object.keys(aq.excluded).join('、')||'无'}；训练期全空排除：${aq.training_unusable.join('、')||'无'}。完整覆盖率见 alpha191-quality.json。`;
 $('metrics').replaceChildren();const results=Object.entries(d.result?.results||{});$('no-result').hidden=results.length>0;
 for(const [name,m]of results){const card=el('div',undefined,'metric');card.append(el('h3',name==='momentum20'?'20 日动量':name==='alpha191_lightgbm'?'Alpha191 + LightGBM':'Alpha158 + LightGBM'));const values=el('div',undefined,'metric-values');for(const [label,value,type]of [['扣费后累计收益',(m.net.total_return*100).toFixed(2)+'%',m.net.total_return>=0?'positive':'negative'],['最大回撤',(m.net.max_drawdown*100).toFixed(2)+'%','negative'],['日均 IC',m.signal_quality.ic?.toFixed(4)??'—','']]){const box=el('div');box.append(el('small',label),el('strong',value,type));values.append(box);}card.append(values);$('metrics').append(card);}
 const chart=d.artifacts.find(f=>f.name.endsWith('/equity.png')&&f.attempt===d.attempt);$('equity').hidden=!chart;if(chart){const src='/api/v1/artifacts/'+chart.id;if($('equity').getAttribute('src')!==src)$('equity').src=src;}else $('equity').removeAttribute('src');
 $('file-count').textContent=d.artifacts.length;$('files').replaceChildren();for(const f of d.artifacts){const row=el('div'),info=el('div');info.append(el('strong',f.name),el('small',`第 ${f.attempt} 次执行 · ${Math.max(1,Math.ceil(f.size/1024))} KB`));const a=link('下载 ↓','/api/v1/artifacts/'+f.id);a.download=f.name.split('/').pop();row.append(info,a);$('files').append(row);}if(!d.artifacts.length)empty($('files'),'暂无归档文件，任务执行后将自动登记。');
 $('actions').replaceChildren();if(['queued','running'].includes(d.status))$('actions').append(button('取消任务',async()=>{await api('/runs/'+id+'/cancel',{method:'POST'});await showDetail(id);}));if(['failed','cancelled'].includes(d.status))$('actions').append(button('重试任务',async()=>{await api('/runs/'+id+'/retry',{method:'POST'});await showDetail(id);}));
}
async function navigate(){
 const version=++routeVersion;const [section='overview',item]=location.hash.slice(1).split('/');const valid=['overview','research','reports','data','factors'].includes(section)?section:'overview';active=['research','reports'].includes(valid)&&item&&item!=='new'?item:null;route=active?'detail':valid;
 const [title,description,eyebrow,crumb]=pages[route];$('page-title').textContent=title;$('page-description').textContent=description;$('eyebrow').textContent=eyebrow;$('breadcrumb').textContent=route==='detail'?(valid==='reports'?'报告中心 / 研究详情':'研究任务 / 研究详情'):crumb;document.title=title+' · claw-factor-lab';
 document.querySelectorAll('[data-page]').forEach(p=>p.hidden=p.dataset.page!==route);document.querySelectorAll('[data-nav]').forEach(a=>{if(a.dataset.nav===valid)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});$('new-research').hidden=!(valid==='research'&&item==='new');message('');window.scrollTo(0,0);
 if(route==='detail'){$('detail-title').textContent='正在加载…';$('metrics').replaceChildren();$('files').replaceChildren();$('events').replaceChildren();$('actions').replaceChildren();$('detail-status').replaceChildren();$('spec').textContent='';$('equity').hidden=true;$('detail-error').hidden=true;setTab('result');$('back-link').href='#'+valid;$('back-link').textContent=valid==='reports'?'← 返回报告中心':'← 返回研究任务';await showDetail(active);}
 else if(route==='overview')await Promise.all([loadStatus(),loadRecent()]);
 else if(route==='research')await loadRuns();else if(route==='reports')await loadReports();else if(route==='data')await loadData();else if(route==='factors')await Promise.all([loadFactors(),loadSets()]);
 if(version!==routeVersion)return;
}
function updateReadiness(){
 const alpha=$('research-factor-set').value.startsWith('alpha191');$('alpha191-options').hidden=!alpha;
 const d=dataCache.find(x=>x.id===$('dataset').value);const info=d?.alpha191;
 $('alpha191-readiness').textContent=info?`已实现 191 项；当前快照具备 ${info.input_ready} 项的输入。缺失依赖：${Object.entries(info.missing_inputs).map(([n,v])=>n+'（'+v.join('、')+'）').join('；')||'无'}。常量截面、停牌及预热窗口仍可能导致计算值缺失。`:'先选择数据快照。';
}
function factorDefinition(f){const box=el('div');box.append(el('code',f.expression));if(f.required_fields){const detail=el('details');detail.append(el('summary','依赖与计算约定'),el('p','输入：'+f.required_fields.join('、')),el('p',f.implementation_note));box.append(detail);}return box;}
$('research-factor-set').onchange=()=>{$('exclude-missing').checked=false;updateReadiness();};$('dataset').onchange=updateReadiness;
$('factor-set-filter').onchange=()=>{factorOffset=0;safe(loadFactors);};
$('close-new').onclick=()=>location.hash='research';$('refresh').onclick=()=>safe(loadRuns);$('run-filter').onchange=()=>{offset=0;safe(loadRuns);};
for(const [id,fn]of [['previous',()=>{offset=Math.max(0,offset-pageSize);return loadRuns();}],['next',()=>{offset+=pageSize;return loadRuns();}],['report-previous',()=>{reportOffset=Math.max(0,reportOffset-pageSize);return loadReports();}],['report-next',()=>{reportOffset+=pageSize;return loadReports();}],['factor-previous',()=>{factorOffset=Math.max(0,factorOffset-pageSize);return loadFactors();}],['factor-next',()=>{factorOffset+=pageSize;return loadFactors();}]])$(id).onclick=()=>safe(fn);
$('factor-search').onsubmit=e=>{e.preventDefault();factorQuery=$('factor-query').value.trim();factorOffset=0;safe(loadFactors);};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
$('research-form').onsubmit=e=>{e.preventDefault();safe(async()=>{const payload={dataset_id:$('dataset').value,title:$('title').value.trim(),factor_set:$('research-factor-set').value,missing_factor_policy:$('research-factor-set').value.startsWith('alpha191')&&$('exclude-missing').checked?'exclude':'error'};if(!payload.title)throw Error('请输入研究名称');const fingerprint=JSON.stringify(payload);if(!pendingRequest||pendingRequest.fingerprint!==fingerprint)pendingRequest={fingerprint,key:crypto.randomUUID()};$('submit').disabled=true;try{const r=await api('/runs',{method:'POST',body:JSON.stringify({...payload,idempotency_key:pendingRequest.key})});pendingRequest=null;offset=0;location.hash='research/'+r.id;}finally{$('submit').disabled=!dataCache.length;}});};
window.addEventListener('hashchange',()=>safe(navigate));
safe(async()=>{await loadData();await Promise.all([loadStatus(),navigate()]);});
let refreshing=false;setInterval(async()=>{if(document.hidden||refreshing)return;refreshing=true;try{await loadStatus();if(route==='overview')await loadRecent();else if(route==='research')await loadRuns();else if(route==='reports')await loadReports();else if(route==='detail')await showDetail(active);}catch(e){$('connection').textContent='◌ 连接中断，正在重试';}finally{refreshing=false;}},15000);

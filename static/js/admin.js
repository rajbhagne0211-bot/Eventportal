/* ASTRA admin UI. All data comes from /admin/api/* (login, role and CSRF are enforced by the server). */
const $=s=>document.querySelector(s),csrf=document.querySelector('meta[name=csrf]')?.content;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inr=p=>'₹'+(p/100).toLocaleString('en-IN');
const pill=v=>`<span class="pill p-${esc(v)}">${esc(String(v??'').replace('_',' '))}</span>`;
const dt=v=>v?new Date(v).toLocaleString('en-IN'):'';
async function api(path,method='GET',body){
 const r=await fetch('/admin/api/'+path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body?JSON.stringify(body):undefined});
 if(r.status===401){location.href='/admin/login';return}
 const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error((d.error||'Request failed')+(d.details?': '+d.details.join('; '):''));return d}
const fail=e=>alert(e.message);
function table(cols,rows){return `<div class="wrap"><table><thead><tr>${cols.map(c=>`<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${c[1](r)}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${cols.length}">No records.</td></tr>`}</tbody></table></div>`}
const qs=o=>Object.entries(o).filter(([,v])=>v).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');
const P=$('#page');
const pages={
async dashboard(){const d=await api('dashboard'),t=d.totals;
 const L={total_events:'Total events',total_registrations:'Registrations',individual_registrations:'Individual',team_registrations:'Team',total_participants:'Participants',paid_registrations:'Paid',pending_payments:'Pending payments',failed_payments:'Failed payments',awaiting_documents:'Awaiting document verification'};
 const byCat={};d.events.forEach(e=>byCat[e.category]=(byCat[e.category]||0)+e.registered);
 P.innerHTML='<div class="cards">'+Object.keys(L).map(k=>`<div class="card"><b>${t[k]}</b>${L[k]}</div>`).join('')+'</div><h2>By event</h2>'+
 table([['Event',e=>esc(e.name)],['Category',e=>esc(e.category)],['Registered',e=>e.registered],['Capacity',e=>e.capacity??'∞'],['Seats left',e=>e.seats_left??'∞'],['Status',e=>pill(e.registration_status)]],d.events)+
 '<h2>By category</h2>'+table([['Category',r=>esc(r[0])],['Registrations',r=>r[1]]],Object.entries(byCat))+
 '<h2>Participants by department</h2>'+table([['Department',r=>esc(r.department)],['Participants',r=>r.participants]],d.departments)},

async events(){const d=await api('events');let list=d.events;
 const draw=()=>{P.innerHTML='<button class="btn" id="new">+ Add event</button><div id="form"></div>'+table([['ID',e=>e.id],['Event',e=>esc(e.name)+(e.is_active?'':' <small>(inactive)</small>')],['Category',e=>esc(e.category)+' '+esc(e.subcategory)],['Formats',e=>[e.allow_individual&&'individual',e.allow_team&&'team'].filter(Boolean).join(' / ')],['Team size',e=>e.min_team_size==null?'—':e.min_team_size+'–'+e.max_team_size],['Fee',e=>'₹'+e.fee],['Registered / cap',e=>e.registered+' / '+(e.capacity??'∞')],['Status',e=>pill(e.registration_status)],['',e=>`<button class="btn sm" data-edit="${e.id}">Edit</button> <a class="btn sm sec" href="/admin/api/events/${e.id}/export.csv">CSV</a> <button class="btn sm sec" data-off="${e.id}">Deactivate</button>`]],list);
  $('#new').onclick=()=>form({allow_individual:true,registration_status:'draft',extra_fields:[],is_active:true});
  P.onclick=async ev=>{const t=ev.target;if(t.dataset.edit)form(list.find(e=>e.id==t.dataset.edit));
   if(t.dataset.off&&confirm('Deactivate this event? Existing registrations are kept.')){try{await api('events/'+t.dataset.off,'DELETE');list=(await api('events')).events;draw()}catch(e){fail(e)}}}};
 const f=(n,l,v,type='text')=>`<label>${l}<input name="${n}" type="${type}" value="${esc(v??'')}"></label>`,
  local=v=>v?new Date(new Date(v)-new Date(v).getTimezoneOffset()*6e4).toISOString().slice(0,16):'';
 const form=e=>{$('#form').innerHTML=`<form class="form" id="ef">${f('name','Name',e.name)}${f('category','Category',e.category)}${f('subcategory','Subcategory',e.subcategory)}${f('fee','Fee (₹)',e.fee_paise?e.fee_paise/100:0,'number')}${f('capacity','Capacity (blank = unlimited)',e.capacity,'number')}${f('min_team_size','Min team size',e.min_team_size,'number')}${f('max_team_size','Max team size',e.max_team_size,'number')}${f('max_substitutes','Max substitutes',e.max_substitutes||0,'number')}${f('registration_opens_at','Opens',local(e.registration_opens_at),'datetime-local')}${f('registration_closes_at','Closes',local(e.registration_closes_at),'datetime-local')}
  <label>Status<select name="registration_status">${['draft','open','closed'].map(s=>`<option ${e.registration_status===s?'selected':''}>${s}</option>`).join('')}</select></label>
  <label><span><input type="checkbox" name="allow_individual" ${e.allow_individual?'checked':''}> Individual</span><span><input type="checkbox" name="allow_team" ${e.allow_team?'checked':''}> Team</span><span><input type="checkbox" name="is_active" ${e.is_active!==false?'checked':''}> Active</span></label>
  <label style="grid-column:1/-1">Description<textarea name="description">${esc(e.description)}</textarea></label>
  <label style="grid-column:1/-1">Extra fields (JSON: [{"key":"x","label":"…","type":"text|url|textarea","required":true}])<textarea name="extra_fields">${esc(JSON.stringify(e.extra_fields||[]))}</textarea></label>
  <div><button class="btn">Save</button> <button type="button" class="btn sec" id="cx">Cancel</button></div></form>`;
  $('#cx').onclick=()=>$('#form').innerHTML='';
  $('#ef').onsubmit=async ev=>{ev.preventDefault();const o={};for(const el of ev.target.elements){if(!el.name)continue;o[el.name]=el.type==='checkbox'?el.checked:el.value}
   try{o.extra_fields=JSON.parse(o.extra_fields||'[]')}catch(x){return alert('Extra fields must be valid JSON')}
   for(const k of['registration_opens_at','registration_closes_at'])o[k]=o[k]?new Date(o[k]).toISOString():null;
   try{e.id?await api('events/'+e.id,'PUT',o):await api('events','POST',o);list=(await api('events')).events;draw()}catch(x){fail(x)}}};
 draw()},

async registrations(){const ev=(await api('events')).events;
 P.innerHTML=`<div class="bar2"><input id="q" placeholder="Search code, name, email, event, college"><select id="st"><option value="">Any status</option>${['pending','confirmed','rejected','cancelled','waitlisted'].map(s=>`<option>${s}</option>`).join('')}</select><select id="ps"><option value="">Any payment</option>${['not_required','pending','paid','failed','cancelled','refunded'].map(s=>`<option>${s}</option>`).join('')}</select><select id="ev"><option value="">Any event</option>${ev.map(e=>`<option value="${e.id}">${esc(e.name)}</option>`).join('')}</select><select id="cat"><option value="">Any category</option>${[...new Set(ev.map(e=>e.category))].map(c=>`<option>${esc(c)}</option>`).join('')}</select><button class="btn" id="go">Search</button><a class="btn sec" id="csv" href="#">Export CSV</a></div><div id="res"></div><dialog id="dlg"></dialog>`;
 const f=()=>({q:$('#q').value,status:$('#st').value,payment_status:$('#ps').value,event_id:$('#ev').value,category:$('#cat').value});
 const run=async()=>{const d=await api('registrations?'+qs(f()));$('#csv').href='/admin/api/registrations/export.csv?'+qs(f());
  $('#res').innerHTML=table([['Code',r=>`<a href="#" data-id="${r.id}">${esc(r.reg_code)}</a>`],['Event',r=>esc(r.event_name)],['Lead / team',r=>esc(r.team_name||r.lead_name)],['People',r=>r.participant_count],['Contact',r=>esc(r.contact_email)+'<br>'+esc(r.contact_phone)],['Status',r=>pill(r.status)+(r.needs_review?' <b class="bad">review</b>':'')],['Payment',r=>pill(r.payment_status)],['Docs pending',r=>r.docs_pending],['Created',r=>dt(r.created_at)]],d.registrations)};
 $('#go').onclick=()=>run().catch(fail);run().catch(fail);
 $('#res').onclick=async e=>{const id=e.target.dataset.id;if(!id)return;e.preventDefault();const d=await api('registrations/'+id);const r=d.registration;
  $('#dlg').innerHTML=`<h2>${esc(r.reg_code)} — ${esc(d.event.name)}</h2><p>${pill(r.status)} ${pill(r.payment_status)} · ${esc(r.college_name)} · ${esc(r.contact_email)} · ${esc(r.contact_phone)}</p>
  ${table([['Name',p=>esc(p.full_name)],['Role',p=>p.role],['Dept',p=>esc(p.department)],['Year',p=>esc(p.course_year)],['Roll no.',p=>esc(p.roll_number)],['Email',p=>esc(p.email)],['Mobile',p=>esc(p.mobile)]],d.participants)}
  <h3>Payments</h3>${table([['Amount',p=>inr(p.amount_paise)],['Status',p=>pill(p.status)],['Order',p=>esc(p.razorpay_order_id)],['Payment',p=>esc(p.razorpay_payment_id)],['Method',p=>esc(p.method)],['Verified',p=>dt(p.verified_at)]],d.payments)}
  <h3>Documents</h3>${table([['Type',x=>x.doc_type],['For',x=>esc(x.participant_name)],['Status',x=>pill(x.status)],['Notes',x=>esc(x.review_notes)]],d.documents)}
  <div class="bar2">${['confirmed','rejected','cancelled','waitlisted'].map(s=>`<button class="btn sm sec" data-s="${s}">Mark ${s}</button>`).join('')}${PERMS.includes('manual_payment')?'<button class="btn sm" id="mp">Record manual payment</button>':''} <button class="btn sm sec" id="cl">Close</button></div>`;
  $('#dlg').showModal();$('#cl').onclick=()=>$('#dlg').close();
  $('#dlg').onclick=async b=>{if(b.target.dataset.s){try{await api(`registrations/${id}/status`,'POST',{status:b.target.dataset.s});$('#dlg').close();run()}catch(x){fail(x)}}};
  const mp=$('#mp');if(mp)mp.onclick=async()=>{const reason=prompt('Reason / bank reference (min 10 chars) — this is audited');if(!reason)return;try{await api(`registrations/${id}/manual-payment`,'POST',{reason});$('#dlg').close();run()}catch(x){fail(x)}}}},

async participants(){P.innerHTML='<div class="bar2"><input id="q" placeholder="Search name, code, email, roll no."><button class="btn" id="go">Search</button></div><div id="res"></div>';
 const run=async()=>{const d=await api('participants?'+qs({q:$('#q').value}));$('#res').innerHTML=table([['Name',p=>esc(p.full_name)],['Code',p=>esc(p.reg_code)],['Event',p=>esc(p.event_name)],['Role',p=>p.role],['College',p=>esc(p.college_name)],['Dept',p=>esc(p.department)],['Year',p=>esc(p.course_year)],['Roll',p=>esc(p.roll_number)],['Email',p=>esc(p.email)],['Mobile',p=>esc(p.mobile)]],d.participants)};
 $('#go').onclick=()=>run().catch(fail);run().catch(fail)},

async payments(){P.innerHTML=`<div class="bar2"><select id="st"><option value="">All</option><option value="paid">Successful</option><option value="created">Pending</option><option value="failed">Failed</option><option value="refunded">Refunded</option></select></div><div id="res"></div>`;
 const run=async()=>{const d=await api('payments?'+qs({status:$('#st').value}));$('#res').innerHTML=table([['Code',p=>esc(p.reg_code)],['Event',p=>esc(p.event_name)],['Payer / team',p=>esc(p.payer_name)],['Amount',p=>inr(p.amount_paise)],['Order ID',p=>esc(p.razorpay_order_id)],['Payment ID',p=>esc(p.razorpay_payment_id)],['Status',p=>pill(p.status)],['Verified at',p=>dt(p.verified_at)],['Refund',p=>esc(p.refund_status)],['Note',p=>esc(p.failure_reason)]],d.payments)};
 $('#st').onchange=()=>run().catch(fail);run().catch(fail)},

async documents(){P.innerHTML=`<div class="bar2"><select id="st"><option value="pending">Pending</option><option value="">All</option><option value="verified">Verified</option><option value="rejected">Rejected</option></select></div><div id="res"></div>`;
 const run=async()=>{const d=await api('documents?'+qs({status:$('#st').value}));$('#res').innerHTML=table([['Code',x=>esc(x.reg_code)],['Event',x=>esc(x.event_name)],['Participant',x=>esc(x.participant_name)],['Type',x=>x.doc_type],['Status',x=>pill(x.status)],['Notes',x=>esc(x.review_notes)],['Uploaded',x=>dt(x.uploaded_at)],['',x=>`<button class="btn sm" data-v="${x.id}">View</button> <button class="btn sm sec" data-a="${x.id}">Approve</button> <button class="btn sm sec" data-r="${x.id}">Reject</button>`]],d.documents)};
 $('#st').onchange=()=>run().catch(fail);run().catch(fail);
 P.onclick=async e=>{const t=e.target;try{
  if(t.dataset.v){const w=window.open('','_blank');const d=await api(`documents/${t.dataset.v}/url`);w.location=d.url}   /* 60-second signed link */
  if(t.dataset.a){await api(`documents/${t.dataset.a}/review`,'POST',{status:'verified'});run()}
  if(t.dataset.r){const n=prompt('Reason for rejection (shown to the participant, who can re-upload):');if(n){await api(`documents/${t.dataset.r}/review`,'POST',{status:'rejected',notes:n});run()}}}catch(x){fail(x)}}},

async departments(){const draw=async()=>{const d=await api('departments');P.innerHTML='<div class="bar2"><input id="n" placeholder="New department name"><button class="btn" id="add">Add</button></div>'+table([['Name',x=>esc(x.name)],['Active',x=>x.active?'yes':'no'],['',x=>`<button class="btn sm sec" data-t="${x.id}" data-a="${x.active}">${x.active?'Deactivate':'Activate'}</button>`]],d.departments);
  $('#add').onclick=async()=>{try{await api('departments','POST',{name:$('#n').value});draw()}catch(e){fail(e)}};
  P.onclick=async e=>{if(e.target.dataset.t){try{await api('departments/'+e.target.dataset.t,'PUT',{active:e.target.dataset.a!=='true'});draw()}catch(x){fail(x)}}}};draw()},

async settings(){const s=await api('settings');const cb=(k,l)=>`<label><input type="checkbox" name="${k}" ${s[k]?'checked':''}> ${l}</label>`;
 P.innerHTML=`<form class="form" id="sf">${cb('require_documents','Require college ID before payment')}${cb('manual_approval','Admin must approve every registration')}${cb('allow_manual_payment','Allow manual payment review (screenshot)')}<label>Seat hold (minutes)<input type="number" name="reservation_minutes" min="1" value="${s.reservation_minutes}"></label><label>Delete ID documents after (days)<input type="number" name="document_retention_days" min="1" value="${s.document_retention_days}"></label><div><button class="btn">Save</button></div></form>`;
 $('#sf').onsubmit=async e=>{e.preventDefault();const f=e.target.elements,o={};['require_documents','manual_approval','allow_manual_payment'].forEach(k=>o[k]=f[k].checked);['reservation_minutes','document_retention_days'].forEach(k=>o[k]=+f[k].value);
  try{await api('settings','PUT',o);alert('Saved')}catch(x){fail(x)}}}
};
const PERMS=window.PERMS_JSON||[];
(async()=>{const pg=document.body.dataset.page;if(pages[pg])try{await pages[pg]()}catch(e){P.innerHTML='<p class="bad">'+esc(e.message)+'</p>'}})();

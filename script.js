const $ = (s) => document.querySelector(s);
const video = $('#video'), drop = $('#drop'), fileInfo = $('#fileInfo');
const start = $('#start'), statusBox = $('#status'), result = $('#result');
const bar = $('#bar'), percent = $('#percent'), statusText = $('#statusText'), detail = $('#detail');
const download = $('#download'), backendUrl = $('#backendUrl');
let selected = null;

backendUrl.value = localStorage.getItem('bangladub_backend_url') || '';
backendUrl.addEventListener('change', () => localStorage.setItem('bangladub_backend_url', backendUrl.value.trim().replace(/\/$/, '')));
function base(){ return backendUrl.value.trim().replace(/\/$/, ''); }
function api(path){ return base() + path; }
async function jsonFetch(url, options={}){
  const response = await fetch(url, options);
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; }
  catch { throw new Error(`Backend JSON দেয়নি (${response.status}).\n${text.slice(0,220)}`); }
  if(!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
  return data;
}
function showFile(){ fileInfo.textContent = selected ? `✓ ${selected.name} • ${(selected.size/1024/1024).toFixed(1)} MB` : ''; }
video.addEventListener('change',()=>{ selected=video.files[0]||null; showFile(); });
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.add('drag')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,x=>{x.preventDefault();drop.classList.remove('drag')}));
drop.addEventListener('drop',e=>{selected=e.dataTransfer.files[0]||null;showFile()});
function progress(p,msg){bar.style.width=p+'%';percent.textContent=p+'%';statusText.textContent=msg;detail.textContent=msg;}

start.addEventListener('click', async()=>{
  if(!selected) return alert('আগে একটি ভিডিও নির্বাচন করুন।');
  if(!base()) return alert('প্রথমে Backend URL দিন।');
  start.disabled=true; statusBox.classList.remove('hidden'); result.classList.add('hidden'); progress(5,'ভিডিও upload হচ্ছে…');
  const fd=new FormData(); fd.append('video',selected); fd.append('source_language',$('#sourceLanguage').value);
  try { const data=await jsonFetch(api('/api/jobs'),{method:'POST',body:fd}); poll(data.job_id); }
  catch(e){progress(0,'কাজ ব্যর্থ');detail.textContent=e.message;start.disabled=false;}
});

async function poll(id){
  try{
    const data=await jsonFetch(api('/api/jobs/'+encodeURIComponent(id)));
    progress(data.progress||0,data.message||data.status);
    if(data.status==='completed'){
      download.href=api(data.download);
      download.setAttribute('download','BanglaDubAI_Bengali.mp4');
      result.classList.remove('hidden'); start.disabled=false; return;
    }
    if(data.status==='error') throw new Error(data.message||'Dubbing failed');
    setTimeout(()=>poll(id),5000);
  }catch(e){progress(0,'কাজ ব্যর্থ হয়েছে');detail.textContent=e.message;start.disabled=false;}
}

$('#health').addEventListener('click',async()=>{
  const out=$('#healthOut'); out.textContent='Checking…';
  try{out.textContent=JSON.stringify(await jsonFetch(api('/api/health')),null,2)}catch(e){out.textContent=e.message}
});
$('#themeBtn').addEventListener('click',()=>document.body.classList.toggle('dark'));

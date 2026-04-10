#!/usr/bin/env python3
"""
Pyrology WIP Production Dashboard — Cloud Version
--------------------------------------------------
Data arrives two ways:
  1. Server-pull: set SESSION_COOKIE env var.
  2. Browser-push: POST raw dithtracker JSON to /api/push-wip.
"""

import os, time, logging, threading, json
from datetime import datetime

import requests
from flask import Flask, jsonify, Response, request
from flask_cors import CORS

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL    = os.getenv('WIP_API_URL',
             'https://dithtracker-reporting.azurewebsites.net/Api/Reports/Wip?pageSize=500')
PORT       = int(os.getenv('PORT', 8080))
CACHE_TTL  = int(os.getenv('CACHE_TTL', 60))
SESSION_COOKIE = os.getenv('SESSION_COOKIE', '')
OVERRIDES_FILE = '/tmp/metal_overrides.json'

# ── Status → Stage mapping ─────────────────────────────────────────────────────
STATUS_MAP = {
    'Mold':'molds','Waiting on Creation/Mold':'molds','Scan':'molds',
    'Waiting on Production':'creation','Print/Cast':'creation',
    'Print Surfacing':'creation','Mn Print':'creation',
    'Pull':'waxpull','Mn Pull':'waxpull',
    'Sm Chase':'waxchase','Sm Sprue':'waxchase',
    'Shell Room/Pouryard':'shell',
    'Sm Metal':'metal','Mn Metal':'metal',
    'Patina':'patina',
    'Base':'base','Dep Transfer':'base',
    'Ready':'ready','Packing/Shipping':'ready',
}

# ── Globals ────────────────────────────────────────────────────────────────────
_cache           = {'items': [], 'updated': None, 'error': None}
_metal_overrides = {}
_lock            = threading.Lock()
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s  %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

def _load_overrides():
    global _metal_overrides
    try:
        with open(OVERRIDES_FILE) as f:
            _metal_overrides = json.load(f)
        log.info(f'Loaded {len(_metal_overrides)} metal override(s) from disk.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load overrides: {e}')

def _save_overrides():
    try:
        with open(OVERRIDES_FILE, 'w') as f:
            json.dump(_metal_overrides, f)
    except Exception as e:
        log.warning(f'Could not save overrides: {e}')

_load_overrides()

# ── Transform raw API rows → internal format ───────────────────────────────────
def transform_rows(raw):
    items = []
    for row in raw:
        status = row.get('status', '')
        stage  = STATUS_MAP.get(status)
        if not stage:
            continue
        due_raw = row.get('dueDate') or row.get('shipDate') or ''
        first = (row.get('firstName') or '').strip()
        last  = (row.get('lastName')  or '').strip()
        items.append({
            'job':           str(row.get('dithPieceNo', '')),
            'name':          row.get('itemDescription', ''),
            'customer':      f'{first} {last}'.strip(),
            'edition':       str(row.get('editionNo', '')),
            'due':           due_raw[:10] if due_raw else '',
            'stage':         stage,
            'status':        status,
            'monument':      bool(row.get('monument', False)),
            'price':         float(row.get('price') or 0),
            'hWaxPull':      float(row.get('waxPullBidHours') or 0),
            'hWax':          float(row.get('waxBidHours') or 0),
            'hSprue':        float(row.get('sprueBidHours') or 0),
            'hMetal':        float(row.get('metalBidHours') or 0),
            'hPolish':       float(row.get('polishBidHours') or 0),
            'hPatina':       float(row.get('patinaBidHours') or 0),
            'hBasing':       float(row.get('basingBidHours') or 0),
            'hMetalWorked':  float(row.get('metalHoursWorked') or 0),
            'hPolishWorked': float(row.get('polishHoursWorked') or 0),
        })
    return items

# ── Server-side fetch ──────────────────────────────────────────────────────────
def fetch():
    log.info('Fetching from Tracker API...')
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Referer': 'https://dithtracker-reporting.azurewebsites.net/Reports/Wip',
        }
        cookies = {}
        if SESSION_COOKIE:
            cookies['.AspNetCore.Session'] = SESSION_COOKIE
        r = requests.get(API_URL, headers=headers, cookies=cookies, timeout=20)
        r.raise_for_status()
        body = r.json()
        raw  = body.get('items', body) if isinstance(body, dict) else body
        items = transform_rows(raw)
        log.info(f'Loaded {len(items)} items.')
        return items, None
    except Exception as e:
        log.error(f'Fetch failed: {e}')
        return None, str(e)

def refresh_loop():
    while True:
        items, err = fetch()
        with _lock:
            if items is not None:
                _cache['items']   = items
                _cache['error']   = None
                _cache['updated'] = datetime.utcnow().isoformat() + 'Z'
            else:
                _cache['error'] = err
        time.sleep(CACHE_TTL)

# ── Dashboard HTML ─────────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Production Status Board — Pyrology</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:#0f1117;color:#e8e8e8;font-family:'Segoe UI',Arial,sans-serif;overflow:hidden}
#wtop{display:flex;align-items:center;justify-content:space-between;padding:6px 14px;background:#1a1d27;border-bottom:1px solid #2a2d3a}
#wtop h1{font-size:1.3em;font-weight:700;letter-spacing:1px;color:#fff}
#wtop h1 span{font-size:.65em;font-weight:400;color:#888;display:block;letter-spacing:.5px}
#wclock{text-align:right;font-size:1.6em;font-weight:700;color:#4db8b8;line-height:1}
#wclock small{font-size:.45em;font-weight:400;color:#888;display:block}
#wstats{display:flex;gap:18px;padding:5px 14px;background:#141620;border-bottom:1px solid #2a2d3a;align-items:center;flex-wrap:wrap}
.wstat{font-size:.78em;color:#aaa}.wstat strong{color:#fff;font-size:1.1em}
.wstat.green strong{color:#5a9e5a}.wstat.red strong{color:#e05555}
.wstat.gold strong{color:#e8a838}.wstat.teal strong{color:#4db8b8}
#wgrid{display:flex;gap:4px;padding:6px;overflow:hidden;height:calc(100vh - 96px)}
.wcol{flex:1;min-width:0;background:#1a1d27;border-radius:6px;display:flex;flex-direction:column;border:1px solid #2a2d3a;cursor:pointer;transition:border-color .2s}
.wcol:hover{border-color:#4db8b8}
.wchdr{padding:7px 8px 5px;text-align:center;border-radius:6px 6px 0 0;flex-shrink:0}
.wclabel{font-size:.72em;font-weight:700;letter-spacing:.8px;text-transform:uppercase}
.wcsub{font-size:.58em;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.4px}
.wccount{font-size:1.15em;font-weight:700;color:#fff;margin-top:2px}
.wcval{font-size:.78em;color:rgba(255,255,255,.85);margin-top:1px}
.wchrs{font-size:.72em;color:#ffd580;margin-top:2px;font-weight:600}
.wcbody{flex:1;overflow:hidden;padding:4px;display:flex;flex-direction:column;gap:3px}
.wcard{background:#0f1117;border-radius:4px;padding:5px 6px;border-left:3px solid #333;font-size:.7em;flex-shrink:0}
.wctitle{font-weight:600;color:#e8e8e8;line-height:1.2}
.wclient{color:#888;font-size:.9em}
.wcmeta{display:flex;justify-content:space-between;align-items:center;margin-top:2px;flex-wrap:wrap;gap:2px}
.wcdue{font-size:.85em;padding:1px 4px;border-radius:3px;background:#1e2230;color:#aaa}
.wcdue.over{background:#3d1515;color:#ff6b6b}
.wcdue.warn{background:#3d2e10;color:#ffaa44}
.wcdue.ok{background:#0f2d1f;color:#5a9e5a}
.wcprice{font-size:.85em;color:#4db8b8;font-weight:600}
.wcmon{background:#7b5ea7;color:#fff;font-size:.7em;padding:1px 4px;border-radius:3px;margin-left:3px}
.wmore{text-align:center;font-size:.65em;color:#555;padding:4px}
#wlive{font-size:.65em;color:#5a9e5a;text-align:right;margin-left:auto}
#werr{background:#3d1515;color:#ff6b6b;padding:8px 14px;font-size:.8em;display:none}
#wdrillbg{position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.85);display:none;flex-direction:column}
#wdrill{background:#1a1d27;margin:20px;border-radius:10px;flex:1;display:flex;flex-direction:column;overflow:hidden;border:1px solid #2a2d3a}
#wdhdr{padding:14px 18px;display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid #2a2d3a;flex-shrink:0}
#wdhdr h2{font-size:1.4em;font-weight:700}
#wdstats{display:flex;gap:20px;margin-top:6px;flex-wrap:wrap}
#wdstats span{font-size:.8em;color:#aaa}
#wdtools{display:flex;gap:8px;align-items:center;flex-shrink:0}
#wdsearch{background:#0f1117;border:1px solid #3a3d4a;color:#e8e8e8;padding:6px 10px;border-radius:4px;font-size:.85em;width:200px}
.wdbtn{background:#2a2d3a;border:1px solid #3a3d4a;color:#aaa;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:.78em}
.wdbtn.active{background:#4db8b8;color:#000;border-color:#4db8b8;font-weight:700}
#wdback{background:#2a2d3a;border:1px solid #3a3d4a;color:#e8e8e8;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:.85em}
#wdtable{flex:1;overflow-y:auto;padding:10px 18px}
table.wdt{width:100%;border-collapse:collapse;font-size:.82em}
table.wdt th{color:#888;font-weight:600;text-align:left;padding:6px 8px;border-bottom:1px solid #2a2d3a;position:sticky;top:0;background:#1a1d27;font-size:.85em;text-transform:uppercase;letter-spacing:.5px}
table.wdt td{padding:7px 8px;border-bottom:1px solid #1e2230;vertical-align:middle}
table.wdt tr:hover td{background:#1e2130}
.tdmon{background:#7b5ea7;color:#fff;font-size:.7em;padding:1px 4px;border-radius:3px;margin-left:4px}
.tdover{color:#ff6b6b;font-weight:600}.tdwarn{color:#ffaa44}.tdok{color:#5a9e5a}
.tdval{color:#4db8b8;font-weight:600}.tdhrs{color:#ffd580;font-size:.85em}
.metal-section-hdr{display:flex;align-items:center;gap:10px;padding:10px 0 6px;margin-top:4px}
.metal-section-hdr h3{font-size:.9em;font-weight:700;letter-spacing:1px;text-transform:uppercase}
.metal-section-hdr .metal-badge{font-size:.72em;padding:2px 8px;border-radius:10px;font-weight:600}
.metal-section-stats{display:flex;gap:16px;margin-bottom:8px;padding:0 2px}
.metal-section-stats span{font-size:.75em;color:#aaa}
.metal-section-stats strong{color:#fff}
.prog-wrap{display:flex;flex-direction:column;gap:4px;min-width:160px}
.prog-row{display:flex;align-items:center;gap:7px}
.prog-label{font-size:.7em;color:#888;width:52px;flex-shrink:0}
.prog-bar-bg{flex:1;height:7px;background:#2a2d3a;border-radius:4px;overflow:hidden}
.prog-bar-fill{height:100%;border-radius:4px;transition:width .4s}
.prog-pct{font-size:.75em;font-weight:700;width:34px;text-align:right;flex-shrink:0}
.prog-val{font-size:.7em;color:#aaa;margin-top:1px;text-align:right}
.pct-btns{display:flex;gap:4px;margin-top:7px;flex-wrap:wrap}
.pct-btn{background:#1e2130;border:1px solid #3a3d4a;color:#777;padding:3px 9px;border-radius:10px;cursor:pointer;font-size:.68em;font-weight:700;transition:background .15s,color .15s,border-color .15s;user-select:none;line-height:1.4}
.pct-btn:hover{background:#2a2d3a;color:#e8e8e8;border-color:#5a6a8a}
.pct-btn.active{background:#8b9dc3;color:#000;border-color:#8b9dc3}
.pct-btn.active-full{background:#5a9e5a;color:#fff;border-color:#5a9e5a}
.pct-btn.active-half{background:#e8a838;color:#000;border-color:#e8a838}
</style>
</head>
<body>
<div id="wtop">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.6em">🏭</div>
    <h1>PRODUCTION STATUS BOARD<span>Work In Progress — Click any department to drill down</span></h1>
  </div>
  <div id="wclock">--:--:--<small>Loading...</small></div>
</div>
<div id="werr"></div>
<div id="wstats">
  <div class="wstat">● TOTAL ITEMS <strong id="stotal">—</strong></div>
  <div class="wstat teal">● TOTAL VALUE <strong id="svalue">—</strong></div>
  <div class="wstat green">● READY <strong id="sready">—</strong></div>
  <div class="wstat red">● OVERDUE <strong id="sover">—</strong></div>
  <div class="wstat gold">● DUE THIS WEEK <strong id="sweek">—</strong></div>
  <div class="wstat gold">● MONUMENTS <strong id="smon">—</strong></div>
  <div id="wlive">Loading...</div>
</div>
<div id="wgrid"></div>

<div id="wdrillbg">
  <div id="wdrill">
    <div id="wdhdr">
      <div>
        <h2 id="wdtitle"></h2>
        <div id="wdstats"></div>
      </div>
      <div id="wdtools">
        <input id="wdsearch" placeholder="Search pieces..." type="text"/>
        <button class="wdbtn active" id="wdsortdue">Sort: Due Date</button>
        <button class="wdbtn" id="wdsortval">Sort: Value ↓</button>
        <button class="wdbtn" id="wdsortname">Sort: Name</button>
        <button id="wdback">← Back to All</button>
      </div>
    </div>
    <div id="wdtable"></div>
  </div>
</div>

<script>
const STAGES=[
  {k:'molds',   l:'Molds',          c:'#4a6fa5'},
  {k:'creation',l:'Creation',       c:'#7b5ea7'},
  {k:'waxpull', l:'Wax Pull',       c:'#e8a838'},
  {k:'waxchase',l:'Wax Chase',      c:'#d4763b'},
  {k:'shell',   l:'Shell/Pouryard', c:'#5a9e6f'},
  {k:'metal',   l:'Metal Work',     c:'#8b9dc3', sub:'Small & Monument'},
  {k:'patina',  l:'Patina',         c:'#c45c8a'},
  {k:'base',    l:'Base',           c:'#4db8b8'},
  {k:'ready',   l:'✓ Ready',        c:'#5a9e5a'},
];
const STAGE_HRS={
  waxpull: i=>i.hWaxPull||0,
  waxchase:i=>(i.hWax||0)+(i.hSprue||0),
  metal:   i=>(i.hMetal||0)+(i.hPolish||0),
  patina:  i=>i.hPatina||0,
  base:    i=>i.hBasing||0,
};

const fmt=v=>v?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v):'—';
const fmtH=h=>h>0?h.toLocaleString('en-US',{maximumFractionDigits:1})+' hrs bid':'';
let _items=[], _drillStage=null, _drillSort='due', _metalOverrides={};

function daysDiff(d){if(!d)return null;return Math.floor((new Date(d)-new Date())/(86400000));}
function dueLabel(d){
  const diff=daysDiff(d);if(diff===null)return null;
  if(diff<0)return{t:'OVERDUE '+Math.abs(diff)+'D',c:'over'};
  if(diff<=7)return{t:'DUE '+d,c:'warn'};
  return{t:d,c:'ok'};
}

/* ── progress bar helper ── */
function metalPct(item){
  if(Object.prototype.hasOwnProperty.call(_metalOverrides,item.job))return _metalOverrides[item.job];
  const bid=(item.hMetal||0)+(item.hPolish||0);
  const done=(item.hMetalWorked||0)+(item.hPolishWorked||0);
  return bid>0?Math.min(100,Math.round(done/bid*100)):0;
}
function setPct(job,pct){
  _metalOverrides[job]=pct;
  fetch('/api/metal-override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job,pct})})
    .catch(e=>console.error('setPct failed:',e));
  renderDrill();
}
function pctBars(item){
  const pct=metalPct(item);
  const rem=100-pct;
  const doneVal=item.price*(pct/100);
  const remVal=item.price*((100-pct)/100);
  const doneColor=pct>=80?'#5a9e5a':pct>=50?'#e8a838':'#8b9dc3';
  const milestones=[0,25,50,75,100];
  const btns=milestones.map(m=>{
    let cls='pct-btn';
    if(pct===m){cls+=m===100?' active-full':m>=50?' active-half':' active';}
    return`<button class="${cls}" onclick="event.stopPropagation();setPct('${item.job}',${m})">${m}%</button>`;
  }).join('');
  return `<div class="prog-wrap">
    <div class="prog-row">
      <span class="prog-label" style="color:${doneColor}">Done</span>
      <div class="prog-bar-bg"><div class="prog-bar-fill" style="width:${pct}%;background:${doneColor}"></div></div>
      <span class="prog-pct" style="color:${doneColor}">${pct}%</span>
    </div>
    <div class="prog-val" style="color:${doneColor};padding-left:59px">${fmt(doneVal)} completed</div>
    <div class="prog-row" style="margin-top:2px">
      <span class="prog-label" style="color:#e8a838">Remain</span>
      <div class="prog-bar-bg"><div class="prog-bar-fill" style="width:${rem}%;background:#e8a838"></div></div>
      <span class="prog-pct" style="color:#e8a838">${rem}%</span>
    </div>
    <div class="prog-val" style="color:#e8a838;padding-left:59px">${fmt(remVal)} remaining</div>
    <div class="pct-btns">${btns}</div>
  </div>`;
}

function renderBoard(){
  const sm={};
  STAGES.forEach(s=>{sm[s.k]={items:[],val:0,hrs:0};});
  _items.forEach(item=>{
    const st=sm[item.stage];
    if(st){st.items.push(item);st.val+=item.price||0;if(STAGE_HRS[item.stage])st.hrs+=STAGE_HRS[item.stage](item);}
  });
  document.getElementById('stotal').textContent=_items.length;
  document.getElementById('svalue').textContent=fmt(_items.reduce((a,i)=>a+(i.price||0),0));
  document.getElementById('sready').textContent=sm.ready.items.length;
  const over=_items.filter(i=>{const d=daysDiff(i.due);return d!==null&&d<0;}).length;
  const week=_items.filter(i=>{const d=daysDiff(i.due);return d!==null&&d>=0&&d<=7;}).length;
  document.getElementById('sover').textContent=over;
  document.getElementById('sweek').textContent=week;
  document.getElementById('smon').textContent=_items.filter(i=>i.monument).length;

  const grid=document.getElementById('wgrid');
  grid.innerHTML=STAGES.map(s=>{
    const sd=sm[s.k];
    const MAX=50, shown=sd.items.slice(0,MAX), extra=sd.items.length-MAX;
    return `<div class="wcol" onclick="openDrill('${s.k}','${s.l}','${s.c}')">
      <div class="wchdr" style="background:${s.c}22;border-bottom:3px solid ${s.c}">
        <div class="wclabel" style="color:${s.c}">${s.l}</div>
        ${s.sub?`<div class="wcsub">${s.sub}</div>`:''}
        <div class="wccount">${sd.items.length} ITEMS</div>
        <div class="wcval">${fmt(sd.val)}</div>
        ${sd.hrs>0?`<div class="wchrs">⏱ ${fmtH(sd.hrs)}</div>`:''}
      </div>
      <div class="wcbody">
        ${shown.map(item=>{
          const dl=dueLabel(item.due);
          return `<div class="wcard" style="border-left-color:${s.c}">
            <div class="wctitle">#${item.job} ${item.name}${item.monument?'<span class="wcmon">MON</span>':''}</div>
            <div class="wclient">${item.customer||''}</div>
            <div class="wcmeta">
              ${item.edition?`<span style="color:#666;font-size:.85em">Ed.${item.edition}</span>`:''}
              ${dl?`<span class="wcdue ${dl.c}">${dl.t}</span>`:''}
              ${item.price?`<span class="wcprice">${fmt(item.price)}</span>`:''}
            </div>
          </div>`;
        }).join('')}
        ${extra>0?`<div class="wmore">+${extra} more — click to see all</div>`:''}
      </div>
    </div>`;
  }).join('');
}

function sortItems(items){
  if(_drillSort==='due')items.sort((a,b)=>{if(!a.due&&!b.due)return 0;if(!a.due)return 1;if(!b.due)return-1;return a.due.localeCompare(b.due);});
  else if(_drillSort==='val')items.sort((a,b)=>(b.price||0)-(a.price||0));
  else items.sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  return items;
}

function openDrill(stageKey,stageName,stageColor){
  _drillStage=stageKey;_drillSort='due';
  document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));
  document.getElementById('wdsortdue').classList.add('active');
  document.getElementById('wdtitle').textContent=stageName.toUpperCase();
  document.getElementById('wdtitle').style.color=stageColor;
  document.getElementById('wdsearch').value='';
  renderDrill();
  document.getElementById('wdrillbg').style.display='flex';
}
function closeDrill(){document.getElementById('wdrillbg').style.display='none';_drillStage=null;}
document.getElementById('wdback').onclick=closeDrill;
document.getElementById('wdsearch').oninput=renderDrill;
document.getElementById('wdsortdue').onclick=function(){_drillSort='due';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};
document.getElementById('wdsortval').onclick=function(){_drillSort='val';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};
document.getElementById('wdsortname').onclick=function(){_drillSort='name';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};

/* ── Metal Work special drill-down ── */
function renderDrillMetal(q){
  let all=_items.filter(i=>i.stage==='metal');
  if(q)all=all.filter(i=>(i.name+' '+i.customer+' '+i.job).toLowerCase().includes(q));

  const small=sortItems(all.filter(i=>!i.monument));
  const mon=sortItems(all.filter(i=>i.monument));

  const totalVal=all.reduce((a,i)=>a+(i.price||0),0);
  const totalHrs=all.reduce((a,i)=>a+(i.hMetal||0)+(i.hPolish||0),0);
  const over=all.filter(i=>{const d=daysDiff(i.due);return d!==null&&d<0;}).length;

  document.getElementById('wdstats').innerHTML=
    `<span>Items: <strong>${all.length}</strong></span>`+
    `<span>Value: <strong style="color:#4db8b8">${fmt(totalVal)}</strong></span>`+
    (totalHrs>0?`<span>Hrs Bid: <strong style="color:#ffd580">${fmtH(totalHrs)}</strong></span>`:'')+
    `<span>Overdue: <strong style="color:#ff6b6b">${over}</strong></span>`+
    `<span>Small: <strong>${small.length}</strong></span>`+
    `<span>Monument: <strong style="color:#7b5ea7">${mon.length}</strong></span>`;

  function smallTable(items){
    if(!items.length)return'<p style="color:#555;font-size:.8em;padding:8px 0">No items.</p>';
    return`<table class="wdt"><thead><tr><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hMetal||0)+(item.hPolish||0);
      return`<tr>
        <td style="color:#888">#${item.job}</td>
        <td><strong>${item.name||'—'}</strong><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'—'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">—</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
      </tr>`;
    }).join('')+'</tbody></table>';
  }

  function monTable(items){
    if(!items.length)return'<p style="color:#555;font-size:.8em;padding:8px 0">No items.</p>';
    // summary totals for monument section
    const monVal=items.reduce((a,i)=>a+(i.price||0),0);
    const monDoneVal=items.reduce((a,i)=>a+(i.price||0)*(metalPct(i)/100),0);
    const monRemVal=monVal-monDoneVal;
    const avgPct=items.length?Math.round(items.reduce((a,i)=>a+metalPct(i),0)/items.length):0;
    const summaryBar=`<div style="background:#12151f;border:1px solid #2a2d3a;border-radius:6px;padding:10px 14px;margin-bottom:10px;display:flex;gap:28px;align-items:center;flex-wrap:wrap">
      <div>
        <div style="font-size:.65em;color:#888;text-transform:uppercase;letter-spacing:.5px">Avg Completion</div>
        <div style="font-size:1.4em;font-weight:700;color:#8b9dc3;margin-top:2px">${avgPct}%</div>
        <div style="width:120px;height:8px;background:#2a2d3a;border-radius:4px;margin-top:4px;overflow:hidden">
          <div style="width:${avgPct}%;height:100%;background:#8b9dc3;border-radius:4px"></div>
        </div>
      </div>
      <div>
        <div style="font-size:.65em;color:#5a9e5a;text-transform:uppercase;letter-spacing:.5px">Value Completed</div>
        <div style="font-size:1.1em;font-weight:700;color:#5a9e5a;margin-top:2px">${fmt(monDoneVal)}</div>
      </div>
      <div>
        <div style="font-size:.65em;color:#e8a838;text-transform:uppercase;letter-spacing:.5px">Value Remaining</div>
        <div style="font-size:1.1em;font-weight:700;color:#e8a838;margin-top:2px">${fmt(monRemVal)}</div>
      </div>
      <div style="flex:1;min-width:160px">
        <div style="font-size:.65em;color:#888;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px">Total Value Progress</div>
        <div style="height:12px;background:#2a2d3a;border-radius:6px;overflow:hidden">
          <div style="width:${Math.round(monDoneVal/Math.max(monVal,1)*100)}%;height:100%;background:linear-gradient(90deg,#5a9e5a,#4db8b8);border-radius:6px"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:3px">
          <span style="font-size:.65em;color:#5a9e5a">${fmt(monDoneVal)} done</span>
          <span style="font-size:.65em;color:#e8a838">${fmt(monRemVal)} left</span>
        </div>
      </div>
    </div>`;
    return summaryBar+
    `<table class="wdt"><thead><tr><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hMetal||0)+(item.hPolish||0);
      return`<tr>
        <td style="color:#888">#${item.job}</td>
        <td><strong>${item.name||'—'}</strong><span class="tdmon">MON</span><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'—'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">—</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
        <td>${pctBars(item)}</td>
      </tr>`;
    }).join('')+'</tbody></table>';
  }

  document.getElementById('wdtable').innerHTML=
    `<div class="metal-section-hdr"><h3 style="color:#8b9dc3">Small Metal</h3><span class="metal-badge" style="background:#8b9dc322;color:#8b9dc3">${small.length} items · ${fmt(small.reduce((a,i)=>a+(i.price||0),0))}</span></div>`+
    smallTable(small)+
    `<div class="metal-section-hdr" style="margin-top:18px"><h3 style="color:#7b5ea7">Monument Metal</h3><span class="metal-badge" style="background:#7b5ea722;color:#7b5ea7">${mon.length} items · ${fmt(mon.reduce((a,i)=>a+(i.price||0),0))}</span></div>`+
    monTable(mon);
}

function renderDrill(){
  const q=(document.getElementById('wdsearch').value||'').toLowerCase();

  if(_drillStage==='metal'){
    renderDrillMetal(q);
    return;
  }

  let items=_items.filter(i=>i.stage===_drillStage);
  if(q)items=items.filter(i=>(i.name+' '+i.customer+' '+i.job).toLowerCase().includes(q));
  sortItems(items);

  const val=items.reduce((a,i)=>a+(i.price||0),0);
  const hrs=STAGE_HRS[_drillStage]?items.reduce((a,i)=>a+STAGE_HRS[_drillStage](i),0):0;
  const over=items.filter(i=>{const d=daysDiff(i.due);return d!==null&&d<0;}).length;
  document.getElementById('wdstats').innerHTML=
    `<span>Items: <strong>${items.length}</strong></span>`+
    `<span>Total Value: <strong style="color:#4db8b8">${fmt(val)}</strong></span>`+
    (hrs>0?`<span>Hrs Bid: <strong style="color:#ffd580">${fmtH(hrs)}</strong></span>`:'')+
    `<span>Overdue: <strong style="color:#ff6b6b">${over}</strong></span>`+
    `<span>Monuments: <strong style="color:#7b5ea7">${items.filter(i=>i.monument).length}</strong></span>`;
  document.getElementById('wdtable').innerHTML=
    `<table class="wdt"><thead><tr><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=STAGE_HRS[_drillStage]?STAGE_HRS[_drillStage](item):0;
      return`<tr>
        <td style="color:#888">#${item.job}</td>
        <td><strong>${item.name||'—'}</strong>${item.monument?'<span class="tdmon">MON</span>':''}<br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'—'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">—</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
      </tr>`;
    }).join('')+'</tbody></table>';
}

function updateClock(){
  const n=new Date();
  document.getElementById('wclock').innerHTML=
    n.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'})+
    '<small>'+n.toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'})+'</small>';
}
setInterval(updateClock,1000);updateClock();

function loadData(){
  fetch('/api/wip').then(r=>r.json()).then(d=>{
    if(d.error){
      document.getElementById('werr').style.display='block';
      document.getElementById('werr').textContent='⚠ '+d.error;
    } else {
      document.getElementById('werr').style.display='none';
    }
    if(d.metal_overrides)Object.assign(_metalOverrides,d.metal_overrides);
    if(d.items&&d.items.length){
      _items=d.items;
      renderBoard();
      if(_drillStage)renderDrill();
      document.getElementById('wlive').textContent='● Live · Updated '+new Date(d.updated).toLocaleTimeString();
    }
  }).catch(()=>{
    document.getElementById('werr').style.display='block';
    document.getElementById('werr').textContent='⚠ Cannot reach server.';
  });
}
loadData();
setInterval(loadData,60000);
</script>
</body>
</html>"""

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins='*')

@app.route('/')
def dashboard():
    return Response(DASHBOARD_HTML, mimetype='text/html')

@app.route('/api/wip')
def api_wip():
    with _lock:
        return jsonify({
            'items':           _cache['items'],
            'updated':         _cache['updated'],
            'count':           len(_cache['items']),
            'error':           _cache['error'],
            'metal_overrides': dict(_metal_overrides),
        })

@app.route('/api/metal-override', methods=['POST'])
def metal_override():
    try:
        body = request.get_json(force=True)
        job  = str(body.get('job', '')).strip()
        pct  = int(body.get('pct', 0))
        if not job:
            return jsonify({'error': 'missing job'}), 400
        pct = max(0, min(100, pct))
        with _lock:
            _metal_overrides[job] = pct
        _save_overrides()
        log.info(f'Metal override set: job={job} pct={pct}%')
        return jsonify({'ok': True, 'job': job, 'pct': pct})
    except Exception as e:
        log.error(f'Override failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/push-wip', methods=['POST'])
def push_wip():
    try:
        body = request.get_json(force=True)
        if body is None:
            return jsonify({'error': 'No JSON body'}), 400
        raw = body.get('items', body) if isinstance(body, dict) else body
        items = transform_rows(raw)
        with _lock:
            _cache['items']   = items
            _cache['error']   = None
            _cache['updated'] = datetime.utcnow().isoformat() + 'Z'
        log.info(f'Browser push received: {len(items)} items.')
        return jsonify({'ok': True, 'items': len(items)})
    except Exception as e:
        log.error(f'Push failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    with _lock:
        return jsonify({'ok': True, 'items': len(_cache['items']), 'updated': _cache['updated']})

# ── Startup ────────────────────────────────────────────────────────────────────
if SESSION_COOKIE:
    log.info('SESSION_COOKIE set — running initial server-side fetch...')
    items, err = fetch()
    with _lock:
        if items is not None:
            _cache['items']   = items
            _cache['updated'] = datetime.utcnow().isoformat() + 'Z'
            log.info(f'✓  {len(items)} items loaded.')
        else:
            _cache['error'] = err
            log.warning(f'⚠  Initial fetch failed: {err}')
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()
else:
    log.info('No SESSION_COOKIE — waiting for browser push to /api/push-wip')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

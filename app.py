#!/usr/bin/env python3
"""
Pyrology WIP Production Dashboard — Cloud Version
--------------------------------------------------
Data arrives two ways:
  1. Server-pull: set SESSION_COOKIE env var.
  2. Browser-push: POST raw dithtracker JSON to /api/push-wip.
"""

import os, time, logging, threading, json
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, Response, request
from flask_cors import CORS

# ── Config ─────────────────────────────────────────────────────────────────────
API_URL    = os.getenv('WIP_API_URL',
             'https://dithtracker-reporting.azurewebsites.net/Api/Reports/Wip?pageSize=500')
PORT       = int(os.getenv('PORT', 8080))
CACHE_TTL  = int(os.getenv('CACHE_TTL', 60))
SESSION_COOKIE = os.getenv('SESSION_COOKIE', '')
OVERRIDES_FILE       = '/tmp/metal_overrides.json'
STAGE_OVERRIDES_FILE = '/tmp/stage_overrides.json'
PRIORITY_FILE        = '/tmp/priority_overrides.json'
KPI_FILE             = '/tmp/kpi_data.json'
KPI_PIN              = os.getenv('KPI_PIN', '1977')
MAINTENANCE_FILE     = '/tmp/maintenance_data.json'
SHIPPING_FILE        = '/tmp/shipping_data.json'
SCHEDULE_FILE        = '/tmp/schedule_data.json'

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
_cache              = {'items': [], 'updated': None, 'error': None}
_metal_overrides    = {}
_stage_overrides    = {}
_priority_overrides = {}          # job → 1 (urgent) | 2 (high) | 0 (normal/default)
_kpi_data           = {'week_start': '', 'entries': [], 'history': []}
_maint_data         = {'requests': [], 'next_id': 1}
_ship_data          = {'shipments': [], 'next_id': 1}
_schedule_data      = {'assignments': {}, 'locked_weeks': []}  # job → {week:'YYYY-MM-DD', carryover:bool, original_week:'YYYY-MM-DD'}
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

def _load_stage_overrides():
    global _stage_overrides
    try:
        with open(STAGE_OVERRIDES_FILE) as f:
            _stage_overrides = json.load(f)
        log.info(f'Loaded {len(_stage_overrides)} stage override(s) from disk.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load stage overrides: {e}')

def _save_stage_overrides():
    try:
        with open(STAGE_OVERRIDES_FILE, 'w') as f:
            json.dump(_stage_overrides, f)
    except Exception as e:
        log.warning(f'Could not save stage overrides: {e}')

def _load_priority_overrides():
    global _priority_overrides
    try:
        with open(PRIORITY_FILE) as f:
            _priority_overrides = json.load(f)
        log.info(f'Loaded {len(_priority_overrides)} priority override(s) from disk.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load priority overrides: {e}')

def _save_priority_overrides():
    try:
        with open(PRIORITY_FILE, 'w') as f:
            json.dump(_priority_overrides, f)
    except Exception as e:
        log.warning(f'Could not save priority overrides: {e}')

def _current_week_start():
    today = datetime.utcnow().date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()

def _load_kpi():
    global _kpi_data
    try:
        with open(KPI_FILE) as f:
            _kpi_data = json.load(f)
        log.info(f'Loaded KPI data: {len(_kpi_data.get("entries", []))} entries this week.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load KPI data: {e}')

def _save_kpi():
    try:
        with open(KPI_FILE, 'w') as f:
            json.dump(_kpi_data, f)
    except Exception as e:
        log.warning(f'Could not save KPI data: {e}')

def _record_kpi_entry(job, item, value, dept, note):
    """Thread-safe KPI entry recorder. Must be called with _lock NOT held."""
    with _lock:
        if not _kpi_data.get('week_start'):
            _kpi_data['week_start'] = _current_week_start()
        if 'entries' not in _kpi_data:
            _kpi_data['entries'] = []
        _kpi_data['entries'].append({
            'job':          job,
            'name':         item.get('name', ''),
            'customer':     item.get('customer', ''),
            'value':        round(float(value), 2),
            'dept':         dept,
            'note':         note,
            'completed_at': datetime.utcnow().isoformat() + 'Z',
        })
    _save_kpi()
    log.info(f'KPI entry: job={job} dept={dept} value={value:.2f} note={note}')

def _load_maintenance():
    global _maint_data
    try:
        with open(MAINTENANCE_FILE) as f:
            _maint_data = json.load(f)
        log.info(f'Loaded {len(_maint_data.get("requests", []))} maintenance request(s) from disk.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load maintenance data: {e}')

def _save_maintenance():
    try:
        with open(MAINTENANCE_FILE, 'w') as f:
            json.dump(_maint_data, f)
    except Exception as e:
        log.warning(f'Could not save maintenance data: {e}')

def _load_shipping():
    global _ship_data
    try:
        with open(SHIPPING_FILE) as f:
            _ship_data = json.load(f)
        log.info(f'Loaded {len(_ship_data.get("shipments", []))} shipment(s) from disk.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load shipping data: {e}')

def _save_shipping():
    try:
        with open(SHIPPING_FILE, 'w') as f:
            json.dump(_ship_data, f)
    except Exception as e:
        log.warning(f'Could not save shipping data: {e}')

def _load_schedule():
    global _schedule_data
    try:
        with open(SCHEDULE_FILE) as f:
            _schedule_data = json.load(f)
        log.info(f'Loaded {len(_schedule_data.get("assignments", {}))} schedule assignment(s) from disk.')
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f'Could not load schedule data: {e}')

def _save_schedule():
    try:
        with open(SCHEDULE_FILE, 'w') as f:
            json.dump(_schedule_data, f)
    except Exception as e:
        log.warning(f'Could not save schedule data: {e}')

def _get_week_monday(dt=None):
    """Return the Monday of the week for a given date (or today)."""
    if dt is None:
        dt = datetime.now()
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime('%Y-%m-%d')

def _auto_rollover():
    """Move incomplete items from past weeks to current week, mark as carryover."""
    today_monday = _get_week_monday()
    assignments = _schedule_data.get('assignments', {})
    changed = False
    for job, info in list(assignments.items()):
        if not info.get('week'):
            continue
        if info['week'] < today_monday and not info.get('done'):
            # This item's scheduled week has passed and it's not done — roll over
            if not info.get('carryover'):
                info['original_week'] = info.get('original_week') or info['week']
            info['week'] = today_monday
            info['carryover'] = True
            changed = True
    if changed:
        _save_schedule()

_load_overrides()
_load_stage_overrides()
_load_priority_overrides()
_load_kpi()
_load_maintenance()
_load_shipping()
_load_schedule()

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
            'tier':          row.get('tierGrade'),
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
.btn-complete{background:#1e3a1e;border:1px solid #3a7a3a;color:#5a9e5a;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:.8em;font-weight:700;transition:background .15s,color .15s;white-space:nowrap}
.btn-complete:hover{background:#2a5a2a;color:#7acc7a;border-color:#5a9e5a}
.btn-complete.done{background:#5a9e5a;color:#fff;border-color:#5a9e5a;cursor:pointer;opacity:.85}
.btn-complete.done:hover{background:#3a6e3a;color:#fff;border-color:#5a9e5a}
.tdtier{display:inline-block;font-size:.72em;font-weight:700;padding:1px 6px;border-radius:3px;margin-top:3px;letter-spacing:.4px}
.tdtier.t1{background:#4a2a6a;color:#c9a0f0;border:1px solid #7a4aaa}
.tdtier.t2{background:#2a3a5a;color:#7aa8e8;border:1px solid #4a6aaa}
.tdtier.t3{background:#3a2a1a;color:#d4924a;border:1px solid #8a5a2a}
.wcard.pri-1{border-left-color:#ff4444 !important;background:#1a0f0f}
.wcard.pri-2{border-left-color:#ffaa22 !important;background:#1a160f}
.wpri{position:absolute;top:2px;right:4px;font-size:.65em;font-weight:800;letter-spacing:.3px;padding:1px 4px;border-radius:3px;line-height:1.3}
.wpri.p1{background:#ff4444;color:#fff}
.wpri.p2{background:#e8a838;color:#000}
.wcard{position:relative}
.pri-btn{background:#1e2130;border:1px solid #3a3d4a;color:#555;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:.7em;font-weight:700;transition:all .15s;margin-left:2px}
.pri-btn:hover{background:#2a2d3a;color:#e8e8e8;border-color:#5a6a8a}
.pri-btn.p1{background:#3d1515;color:#ff6b6b;border-color:#5a2a2a}
.pri-btn.p1:hover{background:#ff4444;color:#fff}
.pri-btn.p2{background:#3d2e10;color:#ffaa44;border-color:#5a3a1a}
.pri-btn.p2:hover{background:#e8a838;color:#000}
.pri-btn.p0{background:#1a2a1a;color:#5a9e5a;border-color:#2a4a2a}
.pri-sort-legend{display:flex;gap:10px;align-items:center;font-size:.7em;color:#666;margin-left:auto;padding-right:4px}
.pri-sort-legend span{display:inline-flex;align-items:center;gap:3px}
.pri-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.pri-dot.p1{background:#ff4444}
.pri-dot.p2{background:#e8a838}
</style>
</head>
<body>
<div id="wtop">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.6em">🏭</div>
    <h1>PRODUCTION STATUS BOARD<span>Work In Progress — Click any department to drill down</span></h1>
  </div>
  <div style="display:flex;align-items:center;gap:12px">
    <a href="/schedule" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#5ae8a8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">📅 Schedule</a>
    <a href="/kpi" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">📊 KPI</a>
    <a href="/maintenance" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#e8a838;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">🔧 Maintenance</a>
    <a href="/shipping" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#7aa8e8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">📦 Shipping</a>
    <div id="wclock">--:--:--<small>Loading...</small></div>
  </div>
</div>
<div id="werr"></div>
<div id="wstats">
  <div class="wstat">● TOTAL ITEMS <strong id="stotal">—</strong></div>
  <div class="wstat teal">● TOTAL VALUE <strong id="svalue">—</strong></div>
  <div class="wstat green">● READY <strong id="sready">—</strong></div>
  <div class="wstat red">● OVERDUE <strong id="sover">—</strong></div>
  <div class="wstat gold">● DUE THIS WEEK <strong id="sweek">—</strong></div>
  <div class="wstat gold">● MONUMENTS <strong id="smon">—</strong></div>
  <div class="pri-sort-legend"><span><span class="pri-dot p1"></span> Urgent</span><span><span class="pri-dot p2"></span> High</span><span style="color:#555">Right-click card to flag</span></div>
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
        <button class="wdbtn" id="wdsorttier" style="display:none">Sort: Tier</button>
        <button class="wdbtn" id="wdsortval">Sort: Value ↓</button>
        <button class="wdbtn" id="wdsortname">Sort: Name</button>
        <button class="wdbtn" id="wdsortpri">Sort: Priority</button>
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
let _items=[], _drillStage=null, _drillSort='due', _metalOverrides={}, _stageOverrides={}, _priorityOverrides={}, _scheduleData={};
function getMonday(d){const dt=new Date(d);const day=dt.getDay();const diff=dt.getDate()-day+(day===0?-6:1);dt.setDate(diff);return dt.toISOString().slice(0,10);}
function schedBadge(job){
  const a=_scheduleData[job];if(!a||!a.week)return'';
  const today=getMonday(new Date().toISOString().slice(0,10));
  const w=a.week;
  let label,color;
  if(w===today){label='THIS WEEK';color='#4db8b8';}
  else if(w>today){const diff=Math.round((new Date(w)-new Date(today))/(7*86400000));label=diff===1?'NEXT WEEK':`+${diff}W`;color='#5ae8a8';}
  else{label='PAST';color='#888';}
  if(a.carryover){label='⚠ CARRY';color='#e8a838';}
  if(a.done){label='✓ SCHED';color='#5a9e5a';}
  return`<span style="font-size:.6em;font-weight:700;padding:1px 4px;border-radius:3px;background:${color}22;color:${color};margin-left:3px">${label}</span>`;
}

function daysDiff(d){if(!d)return null;return Math.floor((new Date(d)-new Date())/(86400000));}
function dueLabel(d){
  const diff=daysDiff(d);if(diff===null)return null;
  if(diff<0)return{t:'OVERDUE '+Math.abs(diff)+'D',c:'over'};
  if(diff<=7)return{t:'DUE '+d,c:'warn'};
  return{t:d,c:'ok'};
}

/* ── priority helpers ── */
function getPri(job){return _priorityOverrides[job]||0;}
function cyclePri(job,e){
  if(e){e.preventDefault();e.stopPropagation();}
  const cur=getPri(job);
  const next=cur===0?1:cur===1?2:0;  // 0→1(urgent)→2(high)→0(normal)
  _priorityOverrides[job]=next;
  if(next===0)delete _priorityOverrides[job];
  fetch('/api/priority-override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job,priority:next})})
    .catch(e2=>console.error('priority failed:',e2));
  renderBoard();
  if(_drillStage)renderDrill();
}
function priLabel(job){
  const p=getPri(job);
  if(p===1)return'<span class="wpri p1">URGENT</span>';
  if(p===2)return'<span class="wpri p2">HIGH</span>';
  return'';
}
function priSort(items){
  // Stable sort: urgent(1) first, then high(2), then normal(0)
  return items.sort((a,b)=>{
    const pa=getPri(a.job),pb=getPri(b.job);
    const wa=pa===1?0:pa===2?1:2;
    const wb=pb===1?0:pb===2?1:2;
    return wa-wb;
  });
}

function priBtns(job){
  const p=getPri(job);
  return`<div style="display:flex;gap:2px">`+
    `<button class="pri-btn${p===1?' p1':''}" onclick="event.stopPropagation();cyclePriTo('${job}',${p===1?0:1})" title="Urgent">🔴</button>`+
    `<button class="pri-btn${p===2?' p2':''}" onclick="event.stopPropagation();cyclePriTo('${job}',${p===2?0:2})" title="High">🟡</button>`+
    `</div>`;
}
function cyclePriTo(job,pri){
  _priorityOverrides[job]=pri;
  if(pri===0)delete _priorityOverrides[job];
  fetch('/api/priority-override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job,priority:pri})})
    .catch(e2=>console.error('priority failed:',e2));
  renderBoard();
  if(_drillStage)renderDrill();
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

/* ── Stage (non-metal) progress bar helpers ── */
function stagePct(item){
  if(Object.prototype.hasOwnProperty.call(_stageOverrides,item.job))return _stageOverrides[item.job];
  return 0;
}
function setStgPct(job,pct){
  _stageOverrides[job]=pct;
  fetch('/api/stage-override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job,pct})})
    .catch(e=>console.error('setStgPct failed:',e));
  renderDrill();
}
function setStgPctFromClick(e,job){
  const bar=e.currentTarget;const rect=bar.getBoundingClientRect();
  const pct=Math.max(0,Math.min(100,Math.round((e.clientX-rect.left)/rect.width*100)));
  setStgPct(job,pct);
}
function setStgPctFromClickRemain(e,job){
  const bar=e.currentTarget;const rect=bar.getBoundingClientRect();
  const rem=Math.max(0,Math.min(100,Math.round((e.clientX-rect.left)/rect.width*100)));
  setStgPct(job,100-rem);
}
function stgPctBar(item){
  const pct=stagePct(item);const rem=100-pct;
  const doneColor=pct>=80?'#5a9e5a':pct>=50?'#e8a838':'#8b9dc3';
  return `<div class="prog-wrap">
    <div class="prog-row">
      <span class="prog-label" style="color:${doneColor}">Done</span>
      <div class="prog-bar-bg" onclick="event.stopPropagation();setStgPctFromClick(event,'${item.job}')" title="Click to set completion %" style="cursor:pointer"><div class="prog-bar-fill" style="width:${pct}%;background:${doneColor}"></div></div>
      <span class="prog-pct" style="color:${doneColor}">${pct}%</span>
    </div>
    <div class="prog-row" style="margin-top:2px">
      <span class="prog-label" style="color:#e8a838">Remain</span>
      <div class="prog-bar-bg" onclick="event.stopPropagation();setStgPctFromClickRemain(event,'${item.job}')" title="Click to set remaining %" style="cursor:pointer"><div class="prog-bar-fill" style="width:${rem}%;background:#e8a838"></div></div>
      <span class="prog-pct" style="color:#e8a838">${rem}%</span>
    </div>
  </div>`;
}

/* ── Stage scoreboard summary bar (shared by all non-monument sections) ── */
function stgSummaryBar(items,stageColor){
  if(!items.length)return'';
  const totalVal=items.reduce((a,i)=>a+(i.price||0),0);
  const doneVal=items.reduce((a,i)=>a+(i.price||0)*(stagePct(i)/100),0);
  const remVal=totalVal-doneVal;
  const avgPct=items.length?Math.round(items.reduce((a,i)=>a+stagePct(i),0)/items.length):0;
  const donePct=Math.round(doneVal/Math.max(totalVal,1)*100);
  return`<div style="background:#12151f;border:1px solid #2a2d3a;border-radius:6px;padding:10px 14px;margin-bottom:10px;display:flex;gap:28px;align-items:center;flex-wrap:wrap">
    <div>
      <div style="font-size:.65em;color:#888;text-transform:uppercase;letter-spacing:.5px">Avg Completion</div>
      <div style="font-size:1.4em;font-weight:700;color:${stageColor};margin-top:2px">${avgPct}%</div>
      <div style="width:120px;height:8px;background:#2a2d3a;border-radius:4px;margin-top:4px;overflow:hidden"><div style="width:${avgPct}%;height:100%;background:${stageColor};border-radius:4px"></div></div>
    </div>
    <div>
      <div style="font-size:.65em;color:#5a9e5a;text-transform:uppercase;letter-spacing:.5px">Value Completed</div>
      <div style="font-size:1.1em;font-weight:700;color:#5a9e5a;margin-top:2px">${fmt(doneVal)}</div>
    </div>
    <div>
      <div style="font-size:.65em;color:#e8a838;text-transform:uppercase;letter-spacing:.5px">Value Remaining</div>
      <div style="font-size:1.1em;font-weight:700;color:#e8a838;margin-top:2px">${fmt(remVal)}</div>
    </div>
    <div style="flex:1;min-width:160px">
      <div style="font-size:.65em;color:#888;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px">Total Value Progress</div>
      <div style="height:12px;background:#2a2d3a;border-radius:6px;overflow:hidden"><div style="width:${donePct}%;height:100%;background:linear-gradient(90deg,#5a9e5a,#4db8b8);border-radius:6px"></div></div>
      <div style="display:flex;justify-content:space-between;margin-top:3px">
        <span style="font-size:.65em;color:#5a9e5a">${fmt(doneVal)} done</span>
        <span style="font-size:.65em;color:#e8a838">${fmt(remVal)} left</span>
      </div>
    </div>
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
    // Sort items by priority before slicing for display
    priSort(sd.items);
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
          const pri=getPri(item.job);
          const priCls=pri===1?' pri-1':pri===2?' pri-2':'';
          return `<div class="wcard${priCls}" style="border-left-color:${pri?'':s.c}" oncontextmenu="cyclePri('${item.job}',event)">
            ${priLabel(item.job)}
            <div class="wctitle">#${item.job} ${item.name}${item.monument?'<span class="wcmon">MON</span>':''}${schedBadge(item.job)}</div>
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

const TIER_STAGES=['waxpull','waxchase','metal','patina'];
function sortItems(items){
  // Secondary sort first (stable)
  if(_drillSort==='tier'){
    items.sort((a,b)=>{
      const ta=a.tier!=null?a.tier:99;
      const tb=b.tier!=null?b.tier:99;
      if(ta!==tb)return ta-tb;
      if(!a.due&&!b.due)return 0;
      if(!a.due)return 1;
      if(!b.due)return-1;
      return b.due.localeCompare(a.due);
    });
  } else if(_drillSort==='due')items.sort((a,b)=>{if(!a.due&&!b.due)return 0;if(!a.due)return 1;if(!b.due)return-1;return a.due.localeCompare(b.due);});
  else if(_drillSort==='val')items.sort((a,b)=>(b.price||0)-(a.price||0));
  else if(_drillSort==='pri')items.sort((a,b)=>{const pa=getPri(a.job),pb=getPri(b.job);const wa=pa===1?0:pa===2?1:2;const wb=pb===1?0:pb===2?1:2;return wa-wb;});
  else items.sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  // Primary sort: priority always floats to top (unless sorting by priority already)
  if(_drillSort!=='pri'){priSort(items);}
  return items;
}

function openDrill(stageKey,stageName,stageColor){
  _drillStage=stageKey;
  const hasTier=TIER_STAGES.includes(stageKey);
  document.getElementById('wdsorttier').style.display=hasTier?'':'none';
  _drillSort=hasTier?'tier':'due';
  document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));
  document.getElementById(hasTier?'wdsorttier':'wdsortdue').classList.add('active');
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
document.getElementById('wdsorttier').onclick=function(){_drillSort='tier';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};
document.getElementById('wdsortval').onclick=function(){_drillSort='val';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};
document.getElementById('wdsortname').onclick=function(){_drillSort='name';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};
document.getElementById('wdsortpri').onclick=function(){_drillSort='pri';document.querySelectorAll('.wdbtn').forEach(b=>b.classList.remove('active'));this.classList.add('active');renderDrill();};

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
    return stgSummaryBar(items,'#8b9dc3')+
    `<table class="wdt"><thead><tr><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th></th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hMetal||0)+(item.hPolish||0);
      const isDone=stagePct(item)>=100;
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'—'}</strong><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'—'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">—</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
        <td>${stgPctBar(item)}</td>
        <td><button class="btn-complete${isDone?' done':''}" onclick="event.stopPropagation();setStgPct('${item.job}',${isDone?0:100})">${isDone?'✓ Done':'✓'}</button></td>
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
    const monTotalHrs=items.reduce((a,i)=>a+(i.hMetal||0)+(i.hPolish||0),0);
    const monDoneHrs=items.reduce((a,i)=>{const h=(i.hMetal||0)+(i.hPolish||0);return a+h*(metalPct(i)/100);},0);
    const monRemHrs=monTotalHrs-monDoneHrs;
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
      <div>
        <div style="font-size:.65em;color:#5a9e5a;text-transform:uppercase;letter-spacing:.5px">Hrs Completed</div>
        <div style="font-size:1.1em;font-weight:700;color:#5a9e5a;margin-top:2px">${monDoneHrs.toFixed(1)} hrs</div>
      </div>
      <div>
        <div style="font-size:.65em;color:#e8a838;text-transform:uppercase;letter-spacing:.5px">Hrs Remaining</div>
        <div style="font-size:1.1em;font-weight:700;color:#e8a838;margin-top:2px">${monRemHrs.toFixed(1)} hrs</div>
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
    `<table class="wdt"><thead><tr><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hMetal||0)+(item.hPolish||0);
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'—'}</strong><span class="tdmon">MON</span><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'—'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">—</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${(()=>{if(!h)return'';const pct=metalPct(item);const dh=h*(pct/100);const rh=h-dh;return`<div style="color:#ffd580;font-weight:700">${h.toFixed(1)} bid</div><div style="color:#5a9e5a;font-size:.82em">${dh.toFixed(1)} done</div><div style="color:#e8a838;font-size:.82em">${rh.toFixed(1)} left</div>`;})()}</td>
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
  const stageColor=STAGES.find(s=>s.k===_drillStage)?.c||'#4db8b8';
  document.getElementById('wdtable').innerHTML=
    stgSummaryBar(items,stageColor)+
    `<table class="wdt"><thead><tr><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th></th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=STAGE_HRS[_drillStage]?STAGE_HRS[_drillStage](item):0;
      const isDone=stagePct(item)>=100;
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'—'}</strong>${item.monument?'<span class="tdmon">MON</span>':''}<br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'—'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">—</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
        <td>${stgPctBar(item)}</td>
        <td><button class="btn-complete${isDone?' done':''}" onclick="event.stopPropagation();setStgPct('${item.job}',${isDone?0:100})">${isDone?'✓ Done':'✓'}</button></td>
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
    if(d.stage_overrides)Object.assign(_stageOverrides,d.stage_overrides);
    if(d.priority_overrides)Object.assign(_priorityOverrides,d.priority_overrides);
    if(d.schedule)Object.assign(_scheduleData,d.schedule);
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

# ── KPI Page HTML ──────────────────────────────────────────────────────────────
KPI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KPI Tracker — Pyrology</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;min-height:100%;background:#0f1117;color:#e8e8e8;font-family:'Segoe UI',Arial,sans-serif}
#ktop{display:flex;align-items:center;justify-content:space-between;padding:8px 18px;background:#1a1d27;border-bottom:1px solid #2a2d3a}
#ktop h1{font-size:1.3em;font-weight:700;letter-spacing:1px;color:#fff}
#ktop h1 span{font-size:.6em;font-weight:400;color:#888;display:block;letter-spacing:.5px}
.nav-link{display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px}
#kbody{padding:16px 18px;max-width:1400px;margin:0 auto}
.week-banner{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:14px 18px;margin-bottom:18px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}
.week-banner h2{font-size:1.1em;font-weight:700;color:#4db8b8}
.week-banner .week-sub{font-size:.78em;color:#888;margin-top:3px}
.btn-close-week{background:#e8a838;border:none;color:#000;padding:8px 20px;border-radius:6px;cursor:pointer;font-size:.9em;font-weight:700;letter-spacing:.5px;transition:background .15s}
.btn-close-week:hover{background:#f0b848}
.dept-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-bottom:22px}
.dept-card{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:14px 16px}
.dept-card .dc-label{font-size:.7em;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:#888;margin-bottom:6px}
.dept-card .dc-value{font-size:1.7em;font-weight:700;color:#4db8b8;line-height:1}
.dept-card .dc-count{font-size:.75em;color:#666;margin-top:4px}
.section-title{font-size:.85em;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#888;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #2a2d3a}
table.ktbl{width:100%;border-collapse:collapse;font-size:.82em;margin-bottom:24px}
table.ktbl th{color:#888;font-weight:600;text-align:left;padding:6px 10px;border-bottom:1px solid #2a2d3a;font-size:.85em;text-transform:uppercase;letter-spacing:.5px}
table.ktbl td{padding:7px 10px;border-bottom:1px solid #1e2230;vertical-align:middle}
table.ktbl tr:hover td{background:#1e2130}
.ktval{color:#4db8b8;font-weight:600}
.ktdept{display:inline-block;font-size:.72em;font-weight:700;padding:2px 7px;border-radius:3px;text-transform:uppercase;letter-spacing:.4px}
.kd-waxpull{background:#1a2a1a;color:#5a9e5a;border:1px solid #3a6a3a}
.kd-waxchase{background:#2a1a2a;color:#c97ae8;border:1px solid #6a3a8a}
.kd-shell{background:#1a1a2a;color:#7aa8e8;border:1px solid #3a5a8a}
.kd-small_metal{background:#2a2a1a;color:#d4924a;border:1px solid #8a5a2a}
.kd-monument_metal{background:#3a1a4a;color:#c9a0f0;border:1px solid #7a4aaa}
.kd-patina{background:#1a2a2a;color:#4db8b8;border:1px solid #2a7a8a}
.kd-base{background:#2a1a1a;color:#e87a7a;border:1px solid #8a3a3a}
.kd-ready{background:#1a2a1a;color:#5a9e5a;border:1px solid #3a6a3a}
.history-week{background:#14161f;border:1px solid #2a2d3a;border-radius:8px;padding:14px 18px;margin-bottom:14px}
.history-week .hw-title{font-size:.92em;font-weight:700;color:#aaa;margin-bottom:10px;display:flex;justify-content:space-between;align-items:center}
.history-week .hw-total{font-size:1em;color:#4db8b8;font-weight:700}
.hw-depts{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:10px}
.hw-dept{font-size:.75em;color:#888}
.hw-dept strong{color:#e8e8e8}
.kpi-actions{display:flex;gap:4px}
.kpi-btn{background:#2a2d3a;border:1px solid #3a4a5a;color:#aaa;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:.75em;font-weight:600;letter-spacing:.3px;transition:all .15s}
.kpi-btn:hover{background:#3a4a5a;color:#fff}
.kpi-btn.del{color:#e87a7a;border-color:#5a2a2a}
.kpi-btn.del:hover{background:#5a2a2a;color:#ff9a9a}
.kpi-edit-input{background:#1a1d27;border:1px solid #4db8b8;color:#4db8b8;padding:2px 6px;border-radius:3px;font-size:.9em;width:70px;font-weight:600}
.kpi-edit-note{background:#1a1d27;border:1px solid #4a5a6a;color:#ccc;padding:2px 6px;border-radius:3px;font-size:.9em;width:120px}
.hw-actions{display:flex;gap:6px}
.hw-btn{background:#2a2d3a;border:1px solid #3a4a5a;color:#aaa;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:.75em;font-weight:600;letter-spacing:.3px;transition:all .15s}
.hw-btn:hover{background:#3a4a5a;color:#fff}
.hw-btn.reopen{color:#e8a838;border-color:#6a4a1a}
.hw-btn.reopen:hover{background:#4a3a1a;color:#f0c050}
.hw-btn.del{color:#e87a7a;border-color:#5a2a2a}
.hw-btn.del:hover{background:#5a2a2a;color:#ff9a9a}
#pin-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;align-items:center;justify-content:center}
#pin-overlay.show{display:flex}
#pin-modal{background:#1a1d27;border:1px solid #3a4a6a;border-radius:12px;padding:28px 32px;min-width:320px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.5)}
#pin-modal h3{color:#fff;font-size:1.1em;margin-bottom:6px}
#pin-modal .pin-sub{color:#888;font-size:.8em;margin-bottom:16px}
#pin-modal input{background:#0f1117;border:2px solid #3a4a6a;color:#4db8b8;padding:10px;border-radius:6px;font-size:1.3em;width:140px;text-align:center;letter-spacing:6px;font-weight:700}
#pin-modal input:focus{outline:none;border-color:#4db8b8}
.pin-error{color:#e87a7a;font-size:.8em;margin-top:8px;min-height:1.2em}
.pin-btns{display:flex;gap:10px;justify-content:center;margin-top:16px}
.pin-btns button{padding:8px 22px;border-radius:6px;border:none;cursor:pointer;font-size:.85em;font-weight:700;letter-spacing:.3px;transition:all .15s}
.pin-btns .pin-confirm{background:#e8a838;color:#000}
.pin-btns .pin-confirm:hover{background:#f0c050}
.pin-btns .pin-confirm.danger{background:#e85a5a;color:#fff}
.pin-btns .pin-confirm.danger:hover{background:#ff7a7a}
.pin-btns .pin-cancel{background:#2a2d3a;color:#aaa;border:1px solid #3a4a5a}
.pin-btns .pin-cancel:hover{background:#3a4a5a;color:#fff}
</style>
</head>
<body>
<div id="ktop">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.6em">📊</div>
    <h1>KPI TRACKER<span>Weekly Production Value — Per Department</span></h1>
  </div>
  <div class="nav-links" style="display:flex;gap:8px">
    <a href="/" class="nav-link">🏭 Dashboard</a>
    <a href="/maintenance" class="nav-link" style="color:#e8a838;border-color:#6a4a1a">🔧 Maintenance</a>
    <a href="/shipping" class="nav-link" style="color:#7aa8e8;border-color:#3a5a8a">📦 Shipping</a>
  </div>
</div>
<div id="kbody">
  <div class="week-banner">
    <div>
      <div class="week-banner h2" id="kweek-label" style="font-size:1.1em;font-weight:700;color:#4db8b8">Loading...</div>
      <div class="week-sub" id="kweek-sub"></div>
    </div>
    <div style="display:flex;align-items:center;gap:14px">
      <div style="font-size:.82em;color:#888">Total this week: <span id="ktotal-week" style="color:#4db8b8;font-weight:700;font-size:1.2em">—</span></div>
      <button class="btn-close-week" onclick="closeWeek()">🔒 Close Week</button>
    </div>
  </div>

  <div class="dept-grid" id="kdept-grid"></div>

  <div class="section-title">Completions This Week</div>
  <table class="ktbl" id="kentries-tbl">
    <thead><tr><th>Piece #</th><th>Description</th><th>Client</th><th>Department</th><th>Value</th><th>Note</th><th>Completed</th><th>Actions</th></tr></thead>
    <tbody id="kentries-body"></tbody>
  </table>

  <div id="khistory"></div>
</div>

<div id="pin-overlay">
  <div id="pin-modal">
    <h3 id="pin-title">Enter PIN</h3>
    <div class="pin-sub" id="pin-sub">This action requires authorization</div>
    <input type="password" id="pin-input" maxlength="10" placeholder="••••" autocomplete="off">
    <div class="pin-error" id="pin-error"></div>
    <div class="pin-btns">
      <button class="pin-cancel" onclick="closePin()">Cancel</button>
      <button class="pin-confirm" id="pin-confirm-btn" onclick="submitPin()">Confirm</button>
    </div>
  </div>
</div>

<script>
const DEPT_LABELS = {
  waxpull:'Wax Pull', waxchase:'Wax Chase', shell:'Shell Room',
  small_metal:'Small Metal', monument_metal:'Monument Metal',
  patina:'Patina', base:'Base', ready:'Ready'
};
const DEPT_ORDER = ['waxpull','waxchase','shell','small_metal','monument_metal','patina','base','ready'];

function fmt(v){if(!v)return'$0';return'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:0});}

function fmtDate(iso){
  if(!iso)return'—';
  const d=new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric'})+'  '+d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
}

function weekRange(startIso){
  if(!startIso)return'';
  const s=new Date(startIso+'T00:00:00');
  const e=new Date(s); e.setDate(e.getDate()+6);
  const opts={month:'short',day:'numeric'};
  return s.toLocaleDateString('en-US',opts)+' – '+e.toLocaleDateString('en-US',{...opts,year:'numeric'});
}

function renderKPI(data){
  const entries = data.entries || [];
  const weekStart = data.week_start || '';

  document.getElementById('kweek-label').textContent = 'Week of ' + weekRange(weekStart);
  document.getElementById('kweek-sub').textContent = weekStart ? 'Mon ' + weekStart + ' through Sun' : '';

  // dept totals
  const deptTotals = {};
  const deptCounts = {};
  DEPT_ORDER.forEach(d=>{deptTotals[d]=0; deptCounts[d]=0;});
  entries.forEach(e=>{
    if(deptTotals[e.dept]!==undefined){deptTotals[e.dept]+=e.value; deptCounts[e.dept]++;}
  });
  const weekTotal = DEPT_ORDER.reduce((a,d)=>a+deptTotals[d],0);
  document.getElementById('ktotal-week').textContent = fmt(weekTotal);

  // dept cards
  document.getElementById('kdept-grid').innerHTML = DEPT_ORDER.map(d=>`
    <div class="dept-card">
      <div class="dc-label">${DEPT_LABELS[d]||d}</div>
      <div class="dc-value">${fmt(deptTotals[d])}</div>
      <div class="dc-count">${deptCounts[d]} completion${deptCounts[d]!==1?'s':''}</div>
    </div>`).join('');

  // entries table (newest first) — track original index for API calls
  const indexed=entries.map((e,i)=>({...e,_idx:i}));
  const sorted=indexed.sort((a,b)=>b.completed_at.localeCompare(a.completed_at));
  document.getElementById('kentries-body').innerHTML = sorted.length
    ? sorted.map(e=>`<tr data-idx="${e._idx}">
        <td style="color:#888">#${e.job}</td>
        <td><strong>${e.name||'—'}</strong></td>
        <td>${e.customer||'—'}</td>
        <td><span class="ktdept kd-${e.dept}">${DEPT_LABELS[e.dept]||e.dept}</span></td>
        <td class="ktval" id="kval-${e._idx}">${fmt(e.value)}</td>
        <td style="color:#888;font-size:.85em" id="knote-${e._idx}">${e.note||''}</td>
        <td style="color:#666;font-size:.85em">${fmtDate(e.completed_at)}</td>
        <td class="kpi-actions">
          <button class="kpi-btn" onclick="editEntry(${e._idx})" title="Edit value/note">✏️</button>
          <button class="kpi-btn del" onclick="deleteEntry(${e._idx})" title="Delete entry">✕</button>
        </td>
      </tr>`).join('')
    : '<tr><td colspan="8" style="color:#555;text-align:center;padding:18px">No completions recorded this week yet.</td></tr>';

  // history
  const history = (data.history || []).slice().reverse();
  const histEl = document.getElementById('khistory');
  if(!history.length){ histEl.innerHTML=''; return; }
  histEl.innerHTML='<div class="section-title" style="margin-top:6px">Previous Weeks</div>'+
    history.map((w,hi)=>{
      const wEntries=w.entries||[];
      const wTotal=wEntries.reduce((a,e)=>a+e.value,0);
      const wDepts={};
      DEPT_ORDER.forEach(d=>{wDepts[d]=0;});
      wEntries.forEach(e=>{if(wDepts[e.dept]!==undefined)wDepts[e.dept]+=e.value;});
      const activeDepts=DEPT_ORDER.filter(d=>wDepts[d]>0);
      const origIdx=data.history.length-1-hi;
      const wLabel='Week of '+weekRange(w.week_start);
      return`<div class="history-week">
        <div class="hw-title">
          <span>Week of ${weekRange(w.week_start)}</span>
          <div style="display:flex;align-items:center;gap:12px">
            <span class="hw-total">${fmt(wTotal)} · ${wEntries.length} items</span>
            <div class="hw-actions">
              <button class="hw-btn reopen" onclick="reopenWeek(${origIdx},'${wLabel.replace(/'/g,"\\'")}')">🔓 Reopen</button>
              <button class="hw-btn del" onclick="deleteWeek(${origIdx},'${wLabel.replace(/'/g,"\\'")}')">🗑 Delete</button>
            </div>
          </div>
        </div>
        <div class="hw-depts">${activeDepts.map(d=>`<div class="hw-dept"><strong>${DEPT_LABELS[d]}:</strong> ${fmt(wDepts[d])}</div>`).join('')}</div>
      </div>`;
    }).join('');
}

function closeWeek(){
  if(!confirm('Close this week and start a new KPI period?'))return;
  fetch('/api/kpi/close-week',{method:'POST'})
    .then(r=>r.json())
    .then(d=>{if(d.ok){alert('Week closed! KPI reset for new week.');loadKPI();}else{alert('Error: '+(d.error||'unknown'));}})
    .catch(()=>alert('Server error'));
}

function deleteEntry(idx){
  if(!confirm('Delete this KPI entry?'))return;
  fetch('/api/kpi/delete-entry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:idx})})
    .then(r=>r.json())
    .then(d=>{if(d.ok)loadKPI();else alert('Error: '+(d.error||'unknown'));})
    .catch(()=>alert('Server error'));
}

function editEntry(idx){
  const row=document.querySelector(`tr[data-idx="${idx}"]`);
  if(!row)return;
  const valTd=document.getElementById('kval-'+idx);
  const noteTd=document.getElementById('knote-'+idx);
  const curVal=parseFloat(valTd.textContent.replace(/[$,]/g,''))||0;
  const curNote=noteTd.textContent||'';
  // Replace cells with inputs
  valTd.innerHTML=`<input class="kpi-edit-input" type="number" value="${curVal}" id="kedit-val-${idx}">`;
  noteTd.innerHTML=`<input class="kpi-edit-note" type="text" value="${curNote}" id="kedit-note-${idx}">`;
  // Replace action buttons with save/cancel
  const actTd=row.querySelector('.kpi-actions');
  actTd.innerHTML=`<button class="kpi-btn" onclick="saveEntry(${idx})" style="color:#5a9e5a;border-color:#3a6a3a" title="Save">✓</button><button class="kpi-btn" onclick="loadKPI()" title="Cancel">✕</button>`;
  document.getElementById('kedit-val-'+idx).focus();
}

function saveEntry(idx){
  const val=parseFloat(document.getElementById('kedit-val-'+idx).value)||0;
  const note=document.getElementById('kedit-note-'+idx).value;
  fetch('/api/kpi/edit-entry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:idx,value:val,note:note})})
    .then(r=>r.json())
    .then(d=>{if(d.ok)loadKPI();else alert('Error: '+(d.error||'unknown'));})
    .catch(()=>alert('Server error'));
}

/* ── PIN modal helpers ── */
let _pinCallback=null;
let _pinAction='';
function showPin(title,sub,action,isDanger,callback){
  _pinCallback=callback; _pinAction=action;
  document.getElementById('pin-title').textContent=title;
  document.getElementById('pin-sub').textContent=sub;
  document.getElementById('pin-input').value='';
  document.getElementById('pin-error').textContent='';
  const btn=document.getElementById('pin-confirm-btn');
  btn.textContent=action;
  btn.className='pin-confirm'+(isDanger?' danger':'');
  document.getElementById('pin-overlay').classList.add('show');
  setTimeout(()=>document.getElementById('pin-input').focus(),100);
}
function closePin(){
  document.getElementById('pin-overlay').classList.remove('show');
  _pinCallback=null;
}
function submitPin(){
  const pin=document.getElementById('pin-input').value;
  if(!pin){document.getElementById('pin-error').textContent='Please enter PIN';return;}
  if(_pinCallback)_pinCallback(pin);
}
document.getElementById('pin-input').addEventListener('keydown',function(e){
  if(e.key==='Enter')submitPin();
  if(e.key==='Escape')closePin();
});
document.getElementById('pin-overlay').addEventListener('click',function(e){
  if(e.target===this)closePin();
});

function reopenWeek(histIdx,weekLabel){
  showPin('Reopen Week','Reopen "'+weekLabel+'" for editing','Reopen',false,function(pin){
    fetch('/api/kpi/reopen-week',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:histIdx,pin:pin})})
      .then(r=>r.json())
      .then(d=>{
        if(d.ok){closePin();alert('Week reopened! '+d.restored_entries+' entries restored to current week.');loadKPI();}
        else{document.getElementById('pin-error').textContent=d.error||'Failed';}
      }).catch(()=>{document.getElementById('pin-error').textContent='Server error';});
  });
}

function deleteWeek(histIdx,weekLabel){
  showPin('Delete Week','Permanently delete "'+weekLabel+'" and all its entries?','Delete',true,function(pin){
    fetch('/api/kpi/delete-week',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:histIdx,pin:pin})})
      .then(r=>r.json())
      .then(d=>{
        if(d.ok){closePin();alert('Week deleted. '+d.deleted_entries+' entries permanently removed.');loadKPI();}
        else{document.getElementById('pin-error').textContent=d.error||'Failed';}
      }).catch(()=>{document.getElementById('pin-error').textContent='Server error';});
  });
}

function loadKPI(){
  fetch('/api/kpi').then(r=>r.json()).then(renderKPI).catch(()=>{
    document.getElementById('kweek-label').textContent='Cannot reach server';
  });
}
loadKPI();
setInterval(loadKPI,30000);
</script>
</body>
</html>"""

# ── Maintenance Request HTML ──────────────────────────────────────────────────
MAINTENANCE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maintenance Requests &#8212; Pyrology</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f1117;color:#e8e8e8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh}
#mtop{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;background:#161822;border-bottom:1px solid #2a2d3a}
#mtop h1{font-size:.95em;font-weight:700;letter-spacing:.5px;color:#e8e8e8}
#mtop h1 span{display:block;font-size:.78em;font-weight:400;color:#888;margin-top:2px}
.nav-links{display:flex;gap:8px}
.nav-link{display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px}
.nav-link:hover{background:#2a3a5a}
#mbody{padding:16px 18px;max-width:1400px;margin:0 auto}

/* Form */
.maint-form{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:18px;margin-bottom:20px}
.maint-form h2{font-size:.85em;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#e8a838;margin-bottom:14px}
.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.form-group{display:flex;flex-direction:column;gap:4px}
.form-group.full{grid-column:1/-1}
.form-group label{font-size:.72em;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:#888}
.form-group input,.form-group select,.form-group textarea{background:#0f1117;border:1px solid #2a2d3a;color:#e8e8e8;padding:8px 10px;border-radius:5px;font-size:.88em;font-family:inherit}
.form-group input:focus,.form-group select:focus,.form-group textarea:focus{outline:none;border-color:#4db8b8}
.form-group textarea{resize:vertical;min-height:70px}
.form-group select{cursor:pointer}
.form-actions{display:flex;gap:10px;margin-top:14px;align-items:center}
.btn-submit{background:#e8a838;border:none;color:#000;padding:9px 24px;border-radius:6px;cursor:pointer;font-size:.88em;font-weight:700;letter-spacing:.5px;transition:background .15s}
.btn-submit:hover{background:#f0c050}
.btn-submit:disabled{opacity:.5;cursor:not-allowed}
.form-msg{font-size:.82em;color:#5a9e5a;min-height:1.2em}

/* Photo preview */
.photo-preview{display:flex;gap:8px;margin-top:6px;flex-wrap:wrap}
.photo-preview img{width:60px;height:60px;object-fit:cover;border-radius:4px;border:1px solid #3a4a5a}
.photo-label{display:inline-flex;align-items:center;gap:5px;background:#2a2d3a;border:1px solid #3a4a5a;color:#aaa;padding:6px 14px;border-radius:5px;cursor:pointer;font-size:.82em;font-weight:600;transition:all .15s}
.photo-label:hover{background:#3a4a5a;color:#fff}
.photo-label input{display:none}

/* Filters */
.filter-bar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;align-items:center}
.filter-btn{background:#1a1d27;border:1px solid #2a2d3a;color:#888;padding:6px 14px;border-radius:5px;cursor:pointer;font-size:.78em;font-weight:700;letter-spacing:.5px;transition:all .15s}
.filter-btn:hover{background:#2a2d3a;color:#fff}
.filter-btn.active{background:#2a3a5a;color:#4db8b8;border-color:#3a5a8a}
.filter-count{font-size:.78em;color:#666;margin-left:auto}

/* Request cards */
.req-card{background:#1a1d27;border:1px solid #2a2d3a;border-radius:8px;padding:14px 16px;margin-bottom:10px;transition:border-color .15s}
.req-card:hover{border-color:#3a4a5a}
.req-card.status-open{border-left:3px solid #e8a838}
.req-card.status-in_progress{border-left:3px solid #4db8b8}
.req-card.status-resolved{border-left:3px solid #5a9e5a}
.req-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;gap:10px}
.req-title{font-size:.92em;font-weight:700;color:#e8e8e8;flex:1}
.req-id{font-size:.75em;color:#555;font-weight:600}
.req-meta{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px}
.req-tag{font-size:.72em;font-weight:700;padding:2px 8px;border-radius:3px;text-transform:uppercase;letter-spacing:.4px}
.tag-priority-low{background:#1a2a1a;color:#5a9e5a;border:1px solid #3a6a3a}
.tag-priority-medium{background:#2a2a1a;color:#e8a838;border:1px solid #6a5a1a}
.tag-priority-high{background:#2a1a1a;color:#e87a7a;border:1px solid #6a2a2a}
.tag-priority-critical{background:#3a0a0a;color:#ff6a6a;border:1px solid #8a2a2a;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.tag-status{font-size:.72em;font-weight:700;padding:2px 8px;border-radius:3px;letter-spacing:.4px}
.tag-open{background:#2a2a1a;color:#e8a838;border:1px solid #6a5a1a}
.tag-in_progress{background:#1a2a2a;color:#4db8b8;border:1px solid #2a7a8a}
.tag-resolved{background:#1a2a1a;color:#5a9e5a;border:1px solid #3a6a3a}
.req-dept{font-size:.72em;font-weight:700;padding:2px 8px;border-radius:3px;background:#1a1a2a;color:#7aa8e8;border:1px solid #3a5a8a;text-transform:uppercase;letter-spacing:.4px}
.req-desc{font-size:.85em;color:#aaa;line-height:1.5;margin-bottom:8px}
.req-photos{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px}
.req-photos img{width:80px;height:80px;object-fit:cover;border-radius:4px;border:1px solid #2a2d3a;cursor:pointer;transition:transform .15s}
.req-photos img:hover{transform:scale(1.05)}
.req-footer{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.req-info{font-size:.75em;color:#555}
.req-actions{display:flex;gap:6px}
.req-btn{background:#2a2d3a;border:1px solid #3a4a5a;color:#aaa;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:.75em;font-weight:600;letter-spacing:.3px;transition:all .15s}
.req-btn:hover{background:#3a4a5a;color:#fff}
.req-btn.del{color:#e87a7a;border-color:#5a2a2a}
.req-btn.del:hover{background:#5a2a2a;color:#ff9a9a}
.empty-state{text-align:center;padding:40px;color:#555;font-size:.9em}

/* Lightbox */
#lightbox{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:9999;align-items:center;justify-content:center;cursor:pointer}
#lightbox.show{display:flex}
#lightbox img{max-width:90vw;max-height:90vh;border-radius:8px}
</style>
</head>
<body>
<div id="mtop">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.6em">&#x1F527;</div>
    <h1>MAINTENANCE REQUESTS<span>Submit &amp; Track Equipment Maintenance</span></h1>
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">&#x1F3ED; Dashboard</a>
    <a href="/kpi" class="nav-link">&#x1F4CA; KPI</a>
    <a href="/shipping" class="nav-link" style="color:#7aa8e8;border-color:#3a5a8a">&#x1F4E6; Shipping</a>
  </div>
</div>
<div id="mbody">

  <div class="maint-form">
    <h2>&#x1F4DD; Submit New Request</h2>
    <div class="form-grid">
      <div class="form-group">
        <label>Equipment / Area *</label>
        <input type="text" id="mf-equip" placeholder="e.g. Shell room kiln #2">
      </div>
      <div class="form-group">
        <label>Department</label>
        <select id="mf-dept">
          <option value="">— Select —</option>
          <option value="Wax Pull">Wax Pull</option>
          <option value="Wax Chase">Wax Chase</option>
          <option value="Shell Room">Shell Room</option>
          <option value="Small Metal">Small Metal</option>
          <option value="Monument Metal">Monument Metal</option>
          <option value="Patina">Patina</option>
          <option value="Base">Base</option>
          <option value="General / Facility">General / Facility</option>
        </select>
      </div>
      <div class="form-group">
        <label>Priority *</label>
        <select id="mf-priority">
          <option value="low">Low — Can wait</option>
          <option value="medium" selected>Medium — Needs attention soon</option>
          <option value="high">High — Affecting production</option>
          <option value="critical">Critical — Production stopped</option>
        </select>
      </div>
      <div class="form-group">
        <label>Requested By *</label>
        <input type="text" id="mf-name" placeholder="Your name">
      </div>
      <div class="form-group full">
        <label>Description *</label>
        <textarea id="mf-desc" placeholder="Describe the issue..."></textarea>
      </div>
      <div class="form-group full">
        <label>Photos (optional)</label>
        <div style="display:flex;align-items:center;gap:10px">
          <label class="photo-label">&#x1F4F7; Attach Photos<input type="file" id="mf-photos" accept="image/*" multiple></label>
          <span id="mf-photo-count" style="font-size:.78em;color:#666"></span>
        </div>
        <div class="photo-preview" id="mf-preview"></div>
      </div>
    </div>
    <div class="form-actions">
      <button class="btn-submit" id="mf-submit" onclick="submitRequest()">Submit Request</button>
      <span class="form-msg" id="mf-msg"></span>
    </div>
  </div>

  <div class="filter-bar" id="filter-bar">
    <button class="filter-btn active" data-filter="all" onclick="setFilter('all')">All</button>
    <button class="filter-btn" data-filter="open" onclick="setFilter('open')">&#x1F7E1; Open</button>
    <button class="filter-btn" data-filter="in_progress" onclick="setFilter('in_progress')">&#x1F535; In Progress</button>
    <button class="filter-btn" data-filter="resolved" onclick="setFilter('resolved')">&#x1F7E2; Resolved</button>
    <span class="filter-count" id="filter-count"></span>
  </div>

  <div id="req-list"></div>
</div>

<div id="lightbox" onclick="this.classList.remove('show')">
  <img id="lightbox-img" src="">
</div>

<script>
let _requests=[];
let _filter='all';
let _photoData=[];

document.getElementById('mf-photos').addEventListener('change',function(){
  _photoData=[];
  const preview=document.getElementById('mf-preview');
  preview.innerHTML='';
  const files=[...this.files].slice(0,4);
  document.getElementById('mf-photo-count').textContent=files.length?files.length+' file(s) selected':'';
  files.forEach(file=>{
    const reader=new FileReader();
    reader.onload=function(e){
      _photoData.push(e.target.result);
      const img=document.createElement('img');
      img.src=e.target.result;
      preview.appendChild(img);
    };
    reader.readAsDataURL(file);
  });
});

function submitRequest(){
  const equip=document.getElementById('mf-equip').value.trim();
  const dept=document.getElementById('mf-dept').value;
  const priority=document.getElementById('mf-priority').value;
  const name=document.getElementById('mf-name').value.trim();
  const desc=document.getElementById('mf-desc').value.trim();
  const msg=document.getElementById('mf-msg');
  if(!equip||!desc||!name){msg.style.color='#e87a7a';msg.textContent='Please fill in all required fields.';return;}
  const btn=document.getElementById('mf-submit');
  btn.disabled=true;btn.textContent='Submitting...';
  fetch('/api/maintenance/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({equipment:equip,department:dept,priority:priority,requested_by:name,description:desc,photos:_photoData})
  }).then(r=>r.json()).then(d=>{
    btn.disabled=false;btn.textContent='Submit Request';
    if(d.ok){
      msg.style.color='#5a9e5a';msg.textContent='Request #'+d.id+' submitted successfully!';
      document.getElementById('mf-equip').value='';
      document.getElementById('mf-desc').value='';
      document.getElementById('mf-photos').value='';
      document.getElementById('mf-preview').innerHTML='';
      document.getElementById('mf-photo-count').textContent='';
      _photoData=[];
      loadRequests();
      setTimeout(()=>{msg.textContent='';},4000);
    }else{msg.style.color='#e87a7a';msg.textContent='Error: '+(d.error||'unknown');}
  }).catch(()=>{btn.disabled=false;btn.textContent='Submit Request';msg.style.color='#e87a7a';msg.textContent='Server error';});
}

function setFilter(f){
  _filter=f;
  document.querySelectorAll('.filter-btn').forEach(b=>{b.classList.toggle('active',b.dataset.filter===f);});
  renderRequests();
}

function renderRequests(){
  const list=document.getElementById('req-list');
  let filtered=_filter==='all'?_requests:_requests.filter(r=>r.status===_filter);
  const counts={all:_requests.length,open:_requests.filter(r=>r.status==='open').length,
    in_progress:_requests.filter(r=>r.status==='in_progress').length,
    resolved:_requests.filter(r=>r.status==='resolved').length};
  document.getElementById('filter-count').textContent=filtered.length+' of '+_requests.length+' requests';
  document.querySelectorAll('.filter-btn').forEach(b=>{
    const f=b.dataset.filter;const c=counts[f]||0;
    if(f!=='all')b.textContent=b.textContent.split('(')[0].trim()+' ('+c+')';
  });
  if(!filtered.length){list.innerHTML='<div class="empty-state">No maintenance requests '+(_filter==='all'?'yet':'matching this filter')+'.</div>';return;}
  list.innerHTML=filtered.map(r=>{
    const photos=(r.photos||[]).map(p=>'<img src="'+p+'" onclick="showPhoto(this.src)">').join('');
    const statusOpts=[{v:'open',l:'Open'},{v:'in_progress',l:'In Progress'},{v:'resolved',l:'Resolved'}];
    const statusSelect='<select class="req-btn" onchange="updateStatus('+r.id+',this.value)" style="background:#2a2d3a;color:#aaa;border:1px solid #3a4a5a;padding:4px 8px;border-radius:4px;font-size:.75em;cursor:pointer">'
      +statusOpts.map(o=>'<option value="'+o.v+'"'+(o.v===r.status?' selected':'')+'>'+o.l+'</option>').join('')+'</select>';
    return '<div class="req-card status-'+r.status+'">'
      +'<div class="req-header"><div class="req-title">'+r.equipment+'</div><span class="req-id">#'+r.id+'</span></div>'
      +'<div class="req-meta">'
      +'<span class="req-tag tag-priority-'+r.priority+'">'+r.priority+'</span>'
      +'<span class="tag-status tag-'+r.status+'">'+(r.status==='in_progress'?'In Progress':r.status.charAt(0).toUpperCase()+r.status.slice(1))+'</span>'
      +(r.department?'<span class="req-dept">'+r.department+'</span>':'')
      +'</div>'
      +'<div class="req-desc">'+r.description.replace(/</g,'&lt;').replace(/\n/g,'<br>')+'</div>'
      +(photos?'<div class="req-photos">'+photos+'</div>':'')
      +'<div class="req-footer">'
      +'<span class="req-info">By '+r.requested_by+' &middot; '+fmtDate(r.created_at)+(r.resolved_at?' &middot; Resolved '+fmtDate(r.resolved_at):'')+'</span>'
      +'<div class="req-actions">'+statusSelect+'<button class="req-btn del" onclick="deleteRequest('+r.id+')">Delete</button></div>'
      +'</div></div>';
  }).join('');
}

function fmtDate(iso){
  if(!iso)return '';
  const d=new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric'})+' '+d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
}

function showPhoto(src){
  document.getElementById('lightbox-img').src=src;
  document.getElementById('lightbox').classList.add('show');
}

function updateStatus(id,status){
  fetch('/api/maintenance/update-status',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id,status:status})
  }).then(r=>r.json()).then(d=>{if(d.ok)loadRequests();else alert('Error: '+(d.error||'unknown'));})
  .catch(()=>alert('Server error'));
}

function deleteRequest(id){
  if(!confirm('Delete this maintenance request?'))return;
  fetch('/api/maintenance/delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:id})
  }).then(r=>r.json()).then(d=>{if(d.ok)loadRequests();else alert('Error: '+(d.error||'unknown'));})
  .catch(()=>alert('Server error'));
}

function loadRequests(){
  fetch('/api/maintenance').then(r=>r.json()).then(d=>{
    _requests=(d.requests||[]).slice().sort((a,b)=>{
      const po={critical:0,high:1,medium:2,low:3};
      const so={open:0,in_progress:1,resolved:2};
      if(so[a.status]!==so[b.status])return so[a.status]-so[b.status];
      if(po[a.priority]!==po[b.priority])return po[a.priority]-po[b.priority];
      return b.id-a.id;
    });
    renderRequests();
  }).catch(()=>{document.getElementById('req-list').innerHTML='<div class="empty-state">Cannot reach server</div>';});
}
loadRequests();
setInterval(loadRequests,30000);
</script>
</body>
</html>"""

SHIPPING_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shipping Requests — Pyrology</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{background:#0f1419;color:#ccc;font-family:'Segoe UI',sans-serif;font-size:14px;}
a{color:#7aa8e8;text-decoration:none;}
a:hover{text-decoration:underline;}
#shdr{background:linear-gradient(135deg,#1a2332 0%,#0f1419 100%);border-bottom:2px solid #3a5a8a;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;gap:20px;}
#shdr h1{font-size:1.6em;letter-spacing:1px;}
#shdr h1 span{display:block;font-size:.55em;color:#888;margin-top:3px;font-weight:400;}
.nav-links{display:flex;gap:8px;}
.nav-link{display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px;}
.nav-link:hover{background:#2a3a4a;}

/* ── Toggle form ── */
.toggle-form-btn{background:#3a6a9a;color:#fff;border:1px solid #5a8aca;padding:10px 20px;border-radius:6px;cursor:pointer;font-weight:700;font-size:.95em;margin:16px 20px 0;display:inline-flex;align-items:center;gap:6px;}
.toggle-form-btn:hover{background:#4a7aaa;}
.ship-form{background:#1a2332;border:1px solid #3a4a6a;border-radius:8px;padding:20px;margin:12px 20px 0;display:none;}
.ship-form.open{display:block;}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:14px;}
.form-group{display:flex;flex-direction:column;}
.form-group label{font-weight:600;margin-bottom:4px;color:#ccc;font-size:.88em;}
.form-group input,.form-group textarea,.form-group select{background:#0f1419;border:1px solid #3a4a6a;color:#ccc;padding:8px;border-radius:4px;font-family:inherit;font-size:.9em;}
.form-group input:focus,.form-group textarea:focus,.form-group select:focus{outline:none;border-color:#7aa8e8;box-shadow:0 0 6px rgba(122,168,232,.3);}
.form-group textarea{resize:vertical;min-height:70px;}
.btn-submit,.btn-save{background:#3a6a9a;color:#fff;border:1px solid #5a8aca;padding:8px 18px;border-radius:4px;cursor:pointer;font-weight:700;font-size:.9em;}
.btn-submit:hover,.btn-save:hover{background:#4a7aaa;}

/* ── Board layout ── */
.board-wrapper{padding:16px 20px;overflow-x:auto;}
.board{display:flex;gap:14px;min-height:calc(100vh - 200px);align-items:flex-start;}
.board-col{flex:1 1 0;min-width:260px;background:#141c26;border:1px solid #2a3a4a;border-radius:8px;display:flex;flex-direction:column;max-height:calc(100vh - 180px);}
.col-header{padding:12px 14px;font-weight:700;font-size:.95em;letter-spacing:.5px;display:flex;align-items:center;gap:8px;border-bottom:2px solid transparent;flex-shrink:0;}
.col-header .count{background:rgba(255,255,255,.08);padding:2px 8px;border-radius:10px;font-size:.8em;font-weight:400;}
.board-col.requested .col-header{border-color:#ffb74d;color:#ffb74d;}
.board-col.approved .col-header{border-color:#42a5f5;color:#42a5f5;}
.board-col.packed .col-header{border-color:#4db8b8;color:#4db8b8;}
.board-col.shipped .col-header{border-color:#81c784;color:#81c784;}
.col-body{padding:10px;overflow-y:auto;flex:1;}

/* ── Cards ── */
.req-card{background:#1a2332;border:1px solid #2e3e52;border-radius:6px;padding:12px;margin-bottom:10px;cursor:default;transition:border-color .15s;}
.req-card:hover{border-color:#4a6a8a;}
.req-card .c-job{font-weight:700;font-size:1em;color:#ddd;margin-bottom:2px;}
.req-card .c-client{font-size:.88em;color:#7aa8e8;margin-bottom:6px;}
.req-card .c-items{font-size:.85em;color:#bbb;background:#0f1419;border-radius:4px;padding:6px 8px;margin-bottom:8px;white-space:pre-wrap;max-height:80px;overflow-y:auto;}
.req-card .c-row{display:flex;justify-content:space-between;font-size:.83em;padding:3px 0;color:#999;}
.req-card .c-row b{color:#ccc;font-weight:500;}
.req-card .c-actions{display:flex;gap:6px;margin-top:10px;}
.req-card .c-actions select{flex:1;background:#0f1419;border:1px solid #3a4a6a;color:#ccc;padding:5px;border-radius:4px;font-size:.82em;}
.req-card .c-actions button,.btn-edit,.btn-del{background:#2a3a4a;border:1px solid #3a4a6a;color:#ccc;padding:5px 8px;border-radius:4px;cursor:pointer;font-size:.78em;}
.btn-del{background:#5a2a2a;border-color:#7a4a4a;}
.btn-edit:hover{background:#3a4a5a;}.btn-del:hover{background:#7a3a3a;}
.empty-col{text-align:center;padding:30px 10px;color:#555;font-size:.9em;}

/* ── Edit overlay ── */
.edit-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:100;}
.edit-panel{background:#1a2332;border:1px solid #3a5a8a;border-radius:10px;padding:24px;width:560px;max-width:95vw;max-height:90vh;overflow-y:auto;}
.edit-panel h3{margin-bottom:12px;color:#ddd;}
.edit-panel .form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.edit-panel .full{grid-column:1/-1;}
.edit-panel input,.edit-panel textarea,.edit-panel select{width:100%;background:#0f1419;border:1px solid #3a4a6a;color:#ccc;padding:7px;border-radius:4px;font-family:inherit;font-size:.9em;}
.edit-panel textarea{resize:vertical;min-height:60px;}
.edit-panel label{font-size:.82em;color:#999;margin-bottom:3px;display:block;}
.edit-btns{display:flex;gap:10px;margin-top:16px;justify-content:flex-end;}
.edit-btns button{padding:8px 18px;border-radius:4px;cursor:pointer;font-weight:600;border:1px solid #3a4a6a;}
.edit-btns .btn-save{background:#3a6a9a;color:#fff;border-color:#5a8aca;}
.edit-btns .btn-cancel{background:#2a3a4a;color:#ccc;}

/* ── Responsive ── */
@media(max-width:900px){.board{flex-direction:column;}.board-col{flex:none;width:100%;max-height:none;}}
</style>
</head>
<body>

<div id="shdr">
  <div style="display:flex;align-items:center;gap:14px;">
    <div style="font-size:1.5em">📋</div>
    <h1>SHIPPING REQUESTS<span>Client Shipping Request Board</span></h1>
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">🏭 Dashboard</a>
    <a href="/kpi" class="nav-link">📊 KPI</a>
    <a href="/maintenance" class="nav-link" style="color:#e8a838;border-color:#6a4a1a">🔧 Maintenance</a>
  </div>
</div>

<button class="toggle-form-btn" onclick="toggleForm()">➕ New Shipping Request</button>

<div class="ship-form" id="reqForm">
  <h3 style="margin-bottom:4px;">📦 Add Shipping Request</h3>
  <p style="color:#777;font-size:.82em;margin-bottom:10px;">Enter what the client has requested to be shipped.</p>
  <div class="form-grid">
    <div class="form-group">
      <label>Job / Order # *</label>
      <input type="text" id="sf-job" placeholder="e.g. TOB300">
    </div>
    <div class="form-group">
      <label>Client *</label>
      <input type="text" id="sf-client" placeholder="Client name">
    </div>
    <div class="form-group">
      <label>Client Email</label>
      <input type="email" id="sf-email" placeholder="client@example.com">
    </div>
    <div class="form-group" style="grid-column:1/-1">
      <label>Items Requested to Ship *</label>
      <textarea id="sf-items" placeholder="List what the client wants shipped — e.g. 2x Bronze plaques, 1x Granite base, 3x Engraved panels..."></textarea>
    </div>
    <div class="form-group" style="grid-column:1/-1">
      <label>Ship To Address *</label>
      <textarea id="sf-address" placeholder="Full shipping address" style="min-height:50px"></textarea>
    </div>
    <div class="form-group">
      <label>Requested Ship Date</label>
      <input type="date" id="sf-ship-date">
    </div>
    <div class="form-group">
      <label>Carrier / Method</label>
      <select id="sf-carrier">
        <option value="">— Select —</option>
        <option value="FedEx">FedEx</option>
        <option value="UPS">UPS</option>
        <option value="USPS">USPS</option>
        <option value="Freight/LTL">Freight/LTL</option>
        <option value="Will Call/Pickup">Will Call/Pickup</option>
        <option value="Other">Other</option>
      </select>
    </div>
    <div class="form-group">
      <label>Tracking #</label>
      <input type="text" id="sf-tracking" placeholder="If known">
    </div>
    <div class="form-group">
      <label># of Packages</label>
      <input type="number" id="sf-packages" min="1" value="1">
    </div>
    <div class="form-group">
      <label>Estimated Weight</label>
      <input type="text" id="sf-weight" placeholder="e.g. 150 lbs">
    </div>
    <div class="form-group" style="grid-column:1/-1">
      <label>Special Instructions</label>
      <textarea id="sf-instructions" placeholder="Crating notes, delivery instructions..." style="min-height:50px"></textarea>
    </div>
  </div>
  <button class="btn-submit" onclick="submitRequest()" style="margin-top:14px">✓ Submit Request</button>
</div>

<div class="board-wrapper">
  <div class="board" id="board"></div>
</div>
<div id="editRoot"></div>

<script>
let _shipments=[];
const STATUSES=['requested','approved','packed','shipped'];
const STATUS_CFG={
  requested:{icon:'📝',label:'Requested'},
  approved:{icon:'✅',label:'Approved'},
  packed:{icon:'📦',label:'Packed'},
  shipped:{icon:'🚚',label:'Shipped'}
};
const CARRIERS=['','FedEx','UPS','USPS','Freight/LTL','Will Call/Pickup','Other'];

function toggleForm(){
  document.getElementById('reqForm').classList.toggle('open');
}

function submitRequest(){
  const job=document.getElementById('sf-job').value.trim();
  const client=document.getElementById('sf-client').value.trim();
  const client_email=document.getElementById('sf-email').value.trim();
  const items=document.getElementById('sf-items').value.trim();
  const ship_to=document.getElementById('sf-address').value.trim();
  const ship_date=document.getElementById('sf-ship-date').value;
  const carrier=document.getElementById('sf-carrier').value;
  const tracking=document.getElementById('sf-tracking').value.trim();
  const packages=parseInt(document.getElementById('sf-packages').value)||1;
  const weight=document.getElementById('sf-weight').value.trim();
  const instructions=document.getElementById('sf-instructions').value.trim();

  if(!job||!client||!items||!ship_to){
    alert('Please fill in Job, Client, Items Requested, and Ship To Address');
    return;
  }

  fetch('/api/shipping/submit',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,client,client_email,items_requested:items,ship_to,ship_date,carrier,tracking,packages,weight,instructions,photos:[]})
  }).then(r=>{
    if(r.ok){
      ['sf-job','sf-client','sf-email','sf-items','sf-address','sf-tracking','sf-weight','sf-instructions'].forEach(id=>document.getElementById(id).value='');
      document.getElementById('sf-carrier').value='';
      document.getElementById('sf-ship-date').value='';
      document.getElementById('sf-packages').value='1';
      document.getElementById('reqForm').classList.remove('open');
      loadShipments();
    }else alert('Error submitting request');
  }).catch(e=>alert('Error: '+e));
}

function updateStatus(id,newStatus){
  fetch('/api/shipping/update-status',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id,status:newStatus})
  }).then(r=>r.json()).then(d=>{
    if(d.ok)loadShipments();
    else alert('Error: '+d.error);
  }).catch(e=>alert('Error: '+e));
}

function deleteRequest(id){
  if(!confirm('Remove this shipping request?'))return;
  fetch('/api/shipping/delete',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id})
  }).then(r=>r.json()).then(d=>{
    if(d.ok)loadShipments();
    else alert('Error: '+d.error);
  }).catch(e=>alert('Error: '+e));
}

function openEdit(id){
  const s=_shipments.find(x=>x.id===id);
  if(!s)return;
  const carrierOpts=CARRIERS.map(c=>`<option value="${c}"${c===s.carrier?' selected':''}>${c||'— Select —'}</option>`).join('');
  document.getElementById('editRoot').innerHTML=`
  <div class="edit-overlay" onclick="if(event.target===this)closeEdit()">
    <div class="edit-panel">
      <h3>✏️ Edit Shipment — ${s.job}</h3>
      <div class="form-grid">
        <div><label>Job / Order #</label><input id="ef-job" value="${s.job||''}"></div>
        <div><label>Client</label><input id="ef-client" value="${s.client||''}"></div>
        <div><label>Client Email</label><input type="email" id="ef-email" value="${s.client_email||''}"></div>
        <div class="full"><label>Items Requested</label><textarea id="ef-items">${s.items_requested||''}</textarea></div>
        <div class="full"><label>Ship To Address</label><textarea id="ef-address" style="min-height:50px">${s.ship_to||''}</textarea></div>
        <div><label>Ship Date</label><input type="date" id="ef-date" value="${s.ship_date||''}"></div>
        <div><label>Carrier</label><select id="ef-carrier">${carrierOpts}</select></div>
        <div><label>Tracking #</label><input id="ef-tracking" value="${s.tracking||''}"></div>
        <div><label># Packages</label><input type="number" id="ef-packages" min="1" value="${s.packages||1}"></div>
        <div><label>Weight</label><input id="ef-weight" value="${s.weight||''}"></div>
        <div class="full"><label>Special Instructions</label><textarea id="ef-instructions" style="min-height:50px">${s.instructions||''}</textarea></div>
      </div>
      <div class="edit-btns">
        <button class="btn-cancel" onclick="closeEdit()">Cancel</button>
        <button class="btn-save" onclick="saveEdit(${s.id})">Save Changes</button>
      </div>
    </div>
  </div>`;
}

function closeEdit(){document.getElementById('editRoot').innerHTML='';}

function saveEdit(id){
  const payload={
    id,
    job:document.getElementById('ef-job').value.trim(),
    client:document.getElementById('ef-client').value.trim(),
    client_email:document.getElementById('ef-email').value.trim(),
    items_requested:document.getElementById('ef-items').value.trim(),
    ship_to:document.getElementById('ef-address').value.trim(),
    ship_date:document.getElementById('ef-date').value,
    carrier:document.getElementById('ef-carrier').value,
    tracking:document.getElementById('ef-tracking').value.trim(),
    packages:parseInt(document.getElementById('ef-packages').value)||1,
    weight:document.getElementById('ef-weight').value.trim(),
    instructions:document.getElementById('ef-instructions').value.trim()
  };
  fetch('/api/shipping/edit',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)
  }).then(r=>r.json()).then(d=>{
    if(d.ok){closeEdit();loadShipments();}
    else alert('Error: '+d.error);
  }).catch(e=>alert('Error: '+e));
}

function renderBoard(){
  const board=document.getElementById('board');
  let html='';
  STATUSES.forEach(st=>{
    const cfg=STATUS_CFG[st];
    const cards=_shipments.filter(s=>(s.status||'requested')===st);
    cards.sort((a,b)=>b.id-a.id);
    html+=`<div class="board-col ${st}">
      <div class="col-header">${cfg.icon} ${cfg.label} <span class="count">${cards.length}</span></div>
      <div class="col-body">`;
    if(cards.length){
      cards.forEach(s=>{
        const items=s.items_requested||s.instructions||'—';
        html+=`<div class="req-card">
          <div class="c-job">${s.job}</div>
          <div class="c-client">${s.client}</div>
          ${s.client_email?`<div class="c-row" style="margin-bottom:6px"><span>📧</span><b style="color:#7aa8e8">${s.client_email}</b></div>`:''}
          <div class="c-items">${items}</div>
          <div class="c-row"><span>Ship To:</span><b>${s.ship_to||'—'}</b></div>
          <div class="c-row"><span>Date:</span><b>${s.ship_date||'—'}</b></div>
          ${s.carrier?`<div class="c-row"><span>Carrier:</span><b>${s.carrier}</b></div>`:''}
          ${s.tracking?`<div class="c-row"><span>Tracking:</span><b>${s.tracking}</b></div>`:''}
          ${s.packages&&s.packages>1?`<div class="c-row"><span>Pkgs:</span><b>${s.packages}</b></div>`:''}
          ${s.weight?`<div class="c-row"><span>Weight:</span><b>${s.weight}</b></div>`:''}
          ${s.instructions?`<div class="c-row" style="margin-top:4px;padding-top:6px;border-top:1px solid #2e3e52"><span>📋 Notes:</span><b style="white-space:pre-wrap">${s.instructions}</b></div>`:''}
          <div class="c-actions">
            <select onchange="updateStatus(${s.id},this.value)">
              ${STATUSES.map(o=>`<option value="${o}"${o===st?' selected':''}>${STATUS_CFG[o].icon} ${STATUS_CFG[o].label}</option>`).join('')}
            </select>
            <button class="btn-edit" onclick="openEdit(${s.id})">✏️</button>
            <button class="btn-del" onclick="deleteRequest(${s.id})">✕</button>
          </div>
        </div>`;
      });
    }else{
      html+=`<div class="empty-col">No requests</div>`;
    }
    html+=`</div></div>`;
  });
  board.innerHTML=html;
}

function loadShipments(){
  fetch('/api/shipping').then(r=>r.json()).then(d=>{
    _shipments=(d.shipments||[]).slice();
    renderBoard();
  }).catch(()=>{document.getElementById('board').innerHTML='<div class="empty-col" style="padding:40px">Cannot reach server</div>';});
}

loadShipments();
setInterval(loadShipments,30000);
</script>

</body>
</html>"""

# ── Flask app ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins='*')

@app.route('/')
def dashboard():
    return Response(DASHBOARD_HTML, mimetype='text/html; charset=utf-8')

@app.route('/api/wip')
def api_wip():
    with _lock:
        return jsonify({
            'items':               _cache['items'],
            'updated':             _cache['updated'],
            'count':               len(_cache['items']),
            'error':               _cache['error'],
            'metal_overrides':     dict(_metal_overrides),
            'stage_overrides':     dict(_stage_overrides),
            'priority_overrides':  dict(_priority_overrides),
            'schedule':            dict(_schedule_data.get('assignments', {})),
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
            pct_old = _metal_overrides.get(job, 0)
            _metal_overrides[job] = pct
            item = next((i for i in _cache['items'] if i['job'] == job), None)
        _save_overrides()
        log.info(f'Metal override set: job={job} pct={pct}%')
        # KPI tracking
        if item and pct > pct_old:
            price = item.get('price') or 0
            if item.get('monument'):
                # Monument metal: any % increase records the full assigned price
                _record_kpi_entry(job, item, price, 'monument_metal',
                                  f'{pct_old}%→{pct}% progress')
            elif pct == 100 and pct_old < 100:
                # Small metal: only full completion counts
                _record_kpi_entry(job, item, price, 'small_metal', '100% complete')
        return jsonify({'ok': True, 'job': job, 'pct': pct})
    except Exception as e:
        log.error(f'Override failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/stage-override', methods=['POST'])
def stage_override():
    try:
        body = request.get_json(force=True)
        job  = str(body.get('job', '')).strip()
        pct  = int(body.get('pct', 0))
        if not job:
            return jsonify({'error': 'missing job'}), 400
        pct = max(0, min(100, pct))
        with _lock:
            pct_old = _stage_overrides.get(job, 0)
            _stage_overrides[job] = pct
            item = next((i for i in _cache['items'] if i['job'] == job), None)
        _save_stage_overrides()
        log.info(f'Stage override set: job={job} pct={pct}%')
        # KPI tracking: only record when hitting 100% from below 100%
        if item and pct == 100 and pct_old < 100:
            price = item.get('price') or 0
            dept  = item.get('stage', 'unknown')
            # Metal items: differentiate small vs monument for KPI
            if dept == 'metal':
                dept = 'monument_metal' if item.get('monument') else 'small_metal'
            _record_kpi_entry(job, item, price, dept, '100% complete')
        return jsonify({'ok': True, 'job': job, 'pct': pct})
    except Exception as e:
        log.error(f'Stage override failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/priority-override', methods=['POST'])
def priority_override():
    try:
        body = request.get_json(force=True)
        job  = str(body.get('job', '')).strip()
        pri  = int(body.get('priority', 0))
        if not job:
            return jsonify({'error': 'missing job'}), 400
        pri = max(0, min(2, pri))           # 0=normal, 1=urgent, 2=high
        with _lock:
            if pri == 0:
                _priority_overrides.pop(job, None)
            else:
                _priority_overrides[job] = pri
        _save_priority_overrides()
        log.info(f'Priority override: job={job} priority={pri}')
        return jsonify({'ok': True, 'job': job, 'priority': pri})
    except Exception as e:
        log.error(f'Priority override failed: {e}')
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

@app.route('/kpi')
def kpi_page():
    return Response(KPI_HTML, mimetype='text/html; charset=utf-8')

@app.route('/api/kpi')
def api_kpi():
    with _lock:
        return jsonify(_kpi_data)

@app.route('/api/kpi/delete-entry', methods=['POST'])
def kpi_delete_entry():
    try:
        body = request.get_json(force=True)
        idx  = int(body.get('index', -1))
        with _lock:
            entries = _kpi_data.get('entries', [])
            if idx < 0 or idx >= len(entries):
                return jsonify({'error': 'invalid index'}), 400
            removed = entries.pop(idx)
        _save_kpi()
        log.info(f'KPI entry deleted: index={idx} job={removed.get("job")}')
        return jsonify({'ok': True, 'removed': removed})
    except Exception as e:
        log.error(f'KPI delete failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/kpi/edit-entry', methods=['POST'])
def kpi_edit_entry():
    try:
        body  = request.get_json(force=True)
        idx   = int(body.get('index', -1))
        with _lock:
            entries = _kpi_data.get('entries', [])
            if idx < 0 or idx >= len(entries):
                return jsonify({'error': 'invalid index'}), 400
            entry = entries[idx]
            if 'value' in body:
                entry['value'] = round(float(body['value']), 2)
            if 'note' in body:
                entry['note'] = str(body['note'])
            if 'dept' in body and body['dept']:
                entry['dept'] = str(body['dept'])
        _save_kpi()
        log.info(f'KPI entry edited: index={idx} job={entry.get("job")}')
        return jsonify({'ok': True, 'entry': entry})
    except Exception as e:
        log.error(f'KPI edit failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/kpi/close-week', methods=['POST'])
def kpi_close_week():
    try:
        with _lock:
            current = {
                'week_start': _kpi_data.get('week_start', ''),
                'closed_at':  datetime.utcnow().isoformat() + 'Z',
                'entries':    list(_kpi_data.get('entries', [])),
            }
            if 'history' not in _kpi_data:
                _kpi_data['history'] = []
            _kpi_data['history'].append(current)
            _kpi_data['week_start'] = _current_week_start()
            _kpi_data['entries'] = []
        _save_kpi()
        log.info(f'Week closed: {current["week_start"]} → {len(current["entries"])} entries archived.')
        return jsonify({'ok': True, 'archived_entries': len(current['entries']),
                        'new_week_start': _kpi_data['week_start']})
    except Exception as e:
        log.error(f'Close week failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/kpi/reopen-week', methods=['POST'])
def kpi_reopen_week():
    try:
        body = request.get_json(force=True)
        pin  = str(body.get('pin', ''))
        idx  = int(body.get('index', -1))
        if pin != KPI_PIN:
            return jsonify({'error': 'Invalid PIN'}), 403
        with _lock:
            history = _kpi_data.get('history', [])
            if idx < 0 or idx >= len(history):
                return jsonify({'error': 'invalid history index'}), 400
            week = history.pop(idx)
            # Merge reopened entries into current week
            _kpi_data['week_start'] = week.get('week_start', _kpi_data.get('week_start', ''))
            _kpi_data['entries'] = week.get('entries', []) + _kpi_data.get('entries', [])
        _save_kpi()
        log.info(f'Week reopened: {week.get("week_start")} — {len(week.get("entries",[]))} entries restored.')
        return jsonify({'ok': True, 'restored_entries': len(week.get('entries', []))})
    except Exception as e:
        log.error(f'Reopen week failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/kpi/delete-week', methods=['POST'])
def kpi_delete_week():
    try:
        body = request.get_json(force=True)
        pin  = str(body.get('pin', ''))
        idx  = int(body.get('index', -1))
        if pin != KPI_PIN:
            return jsonify({'error': 'Invalid PIN'}), 403
        with _lock:
            history = _kpi_data.get('history', [])
            if idx < 0 or idx >= len(history):
                return jsonify({'error': 'invalid history index'}), 400
            removed = history.pop(idx)
        _save_kpi()
        log.info(f'Week deleted: {removed.get("week_start")} — {len(removed.get("entries",[]))} entries permanently removed.')
        return jsonify({'ok': True, 'deleted_week': removed.get('week_start', ''),
                        'deleted_entries': len(removed.get('entries', []))})
    except Exception as e:
        log.error(f'Delete week failed: {e}')
        return jsonify({'error': str(e)}), 500

# ── Maintenance routes ────────────────────────────────────────────────────────
@app.route('/maintenance')
def maintenance_page():
    return Response(MAINTENANCE_HTML, mimetype='text/html; charset=utf-8')

@app.route('/api/maintenance')
def api_maintenance():
    with _lock:
        return jsonify(_maint_data)

@app.route('/api/maintenance/submit', methods=['POST'])
def maint_submit():
    try:
        body = request.get_json(force=True)
        equip = str(body.get('equipment', '')).strip()
        desc  = str(body.get('description', '')).strip()
        name  = str(body.get('requested_by', '')).strip()
        dept  = str(body.get('department', '')).strip()
        pri   = str(body.get('priority', 'medium')).strip()
        photos = body.get('photos', [])
        if not equip or not desc or not name:
            return jsonify({'error': 'Missing required fields'}), 400
        if pri not in ('low', 'medium', 'high', 'critical'):
            pri = 'medium'
        # Limit photos to 4, each max ~500KB base64
        if isinstance(photos, list):
            photos = [p for p in photos[:4] if isinstance(p, str) and len(p) < 700000]
        else:
            photos = []
        with _lock:
            req_id = _maint_data.get('next_id', 1)
            _maint_data['next_id'] = req_id + 1
            new_req = {
                'id':           req_id,
                'equipment':    equip,
                'description':  desc,
                'requested_by': name,
                'department':   dept,
                'priority':     pri,
                'status':       'open',
                'photos':       photos,
                'created_at':   datetime.utcnow().isoformat() + 'Z',
                'resolved_at':  None,
            }
            if 'requests' not in _maint_data:
                _maint_data['requests'] = []
            _maint_data['requests'].append(new_req)
        _save_maintenance()
        log.info(f'Maintenance request #{req_id} submitted: {equip} by {name}')
        return jsonify({'ok': True, 'id': req_id})
    except Exception as e:
        log.error(f'Maintenance submit failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/maintenance/update-status', methods=['POST'])
def maint_update_status():
    try:
        body   = request.get_json(force=True)
        req_id = int(body.get('id', -1))
        status = str(body.get('status', '')).strip()
        if status not in ('open', 'in_progress', 'resolved'):
            return jsonify({'error': 'invalid status'}), 400
        with _lock:
            reqs = _maint_data.get('requests', [])
            req  = next((r for r in reqs if r['id'] == req_id), None)
            if not req:
                return jsonify({'error': 'request not found'}), 404
            req['status'] = status
            if status == 'resolved' and not req.get('resolved_at'):
                req['resolved_at'] = datetime.utcnow().isoformat() + 'Z'
            elif status != 'resolved':
                req['resolved_at'] = None
        _save_maintenance()
        log.info(f'Maintenance #{req_id} status → {status}')
        return jsonify({'ok': True, 'id': req_id, 'status': status})
    except Exception as e:
        log.error(f'Maintenance status update failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/maintenance/delete', methods=['POST'])
def maint_delete():
    try:
        body   = request.get_json(force=True)
        req_id = int(body.get('id', -1))
        with _lock:
            reqs = _maint_data.get('requests', [])
            idx  = next((i for i, r in enumerate(reqs) if r['id'] == req_id), None)
            if idx is None:
                return jsonify({'error': 'request not found'}), 404
            removed = reqs.pop(idx)
        _save_maintenance()
        log.info(f'Maintenance #{req_id} deleted: {removed.get("equipment")}')
        return jsonify({'ok': True, 'id': req_id})
    except Exception as e:
        log.error(f'Maintenance delete failed: {e}')
        return jsonify({'error': str(e)}), 500

# ── Shipping routes ────────────────────────────────────────────────────────────
@app.route('/shipping')
def shipping_page():
    return Response(SHIPPING_HTML, mimetype='text/html; charset=utf-8')

@app.route('/api/shipping')
def api_shipping():
    with _lock:
        return jsonify(_ship_data)

@app.route('/api/shipping/submit', methods=['POST'])
def ship_submit():
    try:
        body = request.get_json(force=True)
        job = str(body.get('job', '')).strip()
        client = str(body.get('client', '')).strip()
        client_email = str(body.get('client_email', '')).strip()
        ship_to = str(body.get('ship_to', '')).strip()
        carrier = str(body.get('carrier', '')).strip()
        tracking = str(body.get('tracking', '')).strip()
        ship_date = str(body.get('ship_date', '')).strip()
        packages = int(body.get('packages', 1))
        weight = str(body.get('weight', '')).strip()
        instructions = str(body.get('instructions', '')).strip()
        photos = body.get('photos', [])

        items_requested = str(body.get('items_requested', '')).strip()

        if not job or not client or not ship_to:
            return jsonify({'error': 'Missing required fields'}), 400

        # Limit photos to 4, each max ~500KB base64
        if isinstance(photos, list):
            photos = [p for p in photos[:4] if isinstance(p, str) and len(p) < 700000]
        else:
            photos = []

        with _lock:
            ship_id = _ship_data.get('next_id', 1)
            _ship_data['next_id'] = ship_id + 1
            new_shipment = {
                'id':            ship_id,
                'job':           job,
                'client':        client,
                'client_email':  client_email,
                'items_requested': items_requested,
                'ship_to':       ship_to,
                'carrier':       carrier,
                'tracking':      tracking,
                'ship_date':     ship_date,
                'packages':      packages,
                'weight':        weight,
                'instructions':  instructions,
                'status':        'requested',
                'photos':        photos,
                'created_at':    datetime.utcnow().isoformat() + 'Z',
                'delivered_at':  None,
            }
            if 'shipments' not in _ship_data:
                _ship_data['shipments'] = []
            _ship_data['shipments'].append(new_shipment)
        _save_shipping()
        log.info(f'Shipment #{ship_id} submitted: {job} to {client}')
        return jsonify({'ok': True, 'id': ship_id})
    except Exception as e:
        log.error(f'Shipping submit failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/shipping/update-status', methods=['POST'])
def ship_update_status():
    try:
        body = request.get_json(force=True)
        ship_id = int(body.get('id', -1))
        status = str(body.get('status', '')).strip()
        if status not in ('requested', 'approved', 'pending', 'packed', 'shipped', 'delivered'):
            return jsonify({'error': 'invalid status'}), 400
        with _lock:
            shipments = _ship_data.get('shipments', [])
            shipment = next((s for s in shipments if s['id'] == ship_id), None)
            if not shipment:
                return jsonify({'error': 'shipment not found'}), 404
            shipment['status'] = status
            if status == 'delivered' and not shipment.get('delivered_at'):
                shipment['delivered_at'] = datetime.utcnow().isoformat() + 'Z'
            elif status != 'delivered':
                shipment['delivered_at'] = None
        _save_shipping()
        log.info(f'Shipment #{ship_id} status → {status}')
        return jsonify({'ok': True, 'id': ship_id, 'status': status})
    except Exception as e:
        log.error(f'Shipping status update failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/shipping/edit', methods=['POST'])
def ship_edit():
    try:
        body = request.get_json(force=True)
        ship_id = int(body.get('id', -1))
        with _lock:
            shipments = _ship_data.get('shipments', [])
            shipment = next((s for s in shipments if s['id'] == ship_id), None)
            if not shipment:
                return jsonify({'error': 'shipment not found'}), 404
            editable = ['job','client','client_email','items_requested','ship_to','carrier','tracking','ship_date','packages','weight','instructions']
            for field in editable:
                if field in body:
                    val = body[field]
                    if field == 'packages':
                        val = int(val) if val else 1
                    else:
                        val = str(val).strip() if val is not None else ''
                    shipment[field] = val
        _save_shipping()
        log.info(f'Shipment #{ship_id} edited')
        return jsonify({'ok': True, 'id': ship_id})
    except Exception as e:
        log.error(f'Shipping edit failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/shipping/delete', methods=['POST'])
def ship_delete():
    try:
        body = request.get_json(force=True)
        ship_id = int(body.get('id', -1))
        with _lock:
            shipments = _ship_data.get('shipments', [])
            idx = next((i for i, s in enumerate(shipments) if s['id'] == ship_id), None)
            if idx is None:
                return jsonify({'error': 'shipment not found'}), 404
            removed = shipments.pop(idx)
        _save_shipping()
        log.info(f'Shipment #{ship_id} deleted: {removed.get("job")}')
        return jsonify({'ok': True, 'id': ship_id})
    except Exception as e:
        log.error(f'Shipping delete failed: {e}')
        return jsonify({'error': str(e)}), 500

# ── Schedule Page HTML ─────────────────────────────────────────────────────────
SCHEDULE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Production Schedule — Pyrology</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:#0f1117;color:#e8e8e8;font-family:'Segoe UI',Arial,sans-serif;overflow:hidden}
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;background:#1a1d27;border-bottom:1px solid #2a2d3a}
.top-bar h1{font-size:1.3em;font-weight:700;letter-spacing:1px;color:#fff}
.top-bar h1 span{font-size:.65em;font-weight:400;color:#888;display:block;letter-spacing:.5px}
.nav-links{display:flex;gap:8px;align-items:center}
.nav-links a{display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px}
.summary-bar{display:flex;gap:18px;padding:6px 16px;background:#141620;border-bottom:1px solid #2a2d3a;align-items:center;flex-wrap:wrap}
.sstat{font-size:.78em;color:#aaa}.sstat strong{color:#fff;font-size:1.1em}
.sstat.teal strong{color:#4db8b8}.sstat.red strong{color:#e05555}
.sstat.gold strong{color:#e8a838}.sstat.green strong{color:#5a9e5a}
.dept-grid{display:flex;gap:4px;padding:6px;overflow-x:auto;height:calc(100vh - 90px)}
.dept-col{flex:1;min-width:210px;background:#1a1d27;border-radius:6px;display:flex;flex-direction:column;border:1px solid #2a2d3a;overflow:hidden}
.dept-hdr{padding:7px 8px 5px;text-align:center;border-radius:6px 6px 0 0;flex-shrink:0}
.dept-label{font-size:.72em;font-weight:700;letter-spacing:.8px;text-transform:uppercase}
.dept-sub{font-size:.58em;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.4px}
.dept-count{font-size:1.15em;font-weight:700;color:#fff;margin-top:2px}
.dept-hrs{font-size:.72em;color:#ffd580;margin-top:2px;font-weight:600}
.dept-val{font-size:.78em;color:rgba(255,255,255,.85);margin-top:1px}
.dept-body{flex:1;overflow-y:auto;padding:4px;display:flex;flex-direction:column;gap:6px}
.week-block{background:#141620;border-radius:5px;border:1px solid #2a2d3a;overflow:hidden;transition:border-color .15s}
.week-block.current{border-color:#4db8b8}
.week-block.locked{border-color:#5a9e5a;background:#0f1a0f}
.week-block.drag-over{border-color:#e8a838!important;background:#1a1a10}
.wb-hdr{display:flex;justify-content:space-between;align-items:center;padding:5px 8px;cursor:pointer;user-select:none;font-size:.72em;font-weight:700}
.wb-hdr:hover{background:#1e2130}
.wb-hdr .wlabel{color:#4db8b8}.wb-hdr .wdates{color:#888;font-weight:400;margin-left:4px}
.wb-hdr .wstats{color:#aaa;font-weight:400}
.wb-hdr .wstats strong{color:#fff}
.locked .wb-hdr .wlabel{color:#5a9e5a}
.wb-body{padding:3px;min-height:20px}
.wb-body.collapsed{display:none}
.wb-actions{display:flex;gap:4px;padding:4px 6px;border-top:1px solid #1a1d27;align-items:center;justify-content:space-between}
.scard{background:#0f1117;border-radius:4px;padding:5px 6px;margin-bottom:3px;border-left:3px solid #333;font-size:.7em;cursor:grab;transition:opacity .15s,transform .15s,background .1s}
.scard:hover{border-left-color:#4db8b8;background:#12141e}
.scard.carry{border-left-color:#e8a838;background:#1a160f}
.scard.dragging{opacity:.35;transform:scale(.95)}
.scard.done-item{opacity:.45}
.scard.selected{background:#1a2a3a!important;outline:2px solid #4db8b8;outline-offset:-1px}
.locked .scard{cursor:pointer}
.scard .s-title{font-weight:600;color:#e8e8e8;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.scard .s-meta{display:flex;gap:8px;color:#888;font-size:.9em;margin-top:2px;flex-wrap:wrap;align-items:center}
.scard .s-hrs{color:#ffd580;font-weight:600}.scard .s-val{color:#4db8b8;font-weight:600}
.scard .s-due{font-size:.85em;padding:1px 4px;border-radius:3px;background:#1e2230;color:#aaa}
.scard .s-due.over{background:#3d1515;color:#ff6b6b}.scard .s-due.warn{background:#3d2e10;color:#ffaa44}
.scard .s-actions{display:flex;gap:4px;margin-top:3px;align-items:center}
.btn-sm{background:#2a2d3a;border:1px solid #3a3d4a;color:#aaa;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:.85em;font-weight:600;transition:all .15s}
.btn-sm:hover{background:#3a3d4a;color:#fff}
.btn-sm.done.active{background:#5a9e5a;color:#fff}
.btn-sm.rush{color:#e8a838}.btn-sm.rush:hover{background:#3d2e10;border-color:#e8a838}
.btn-lock{background:#1e3a1e;border:1px solid #3a6a3a;color:#5a9e5a;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:.75em;font-weight:700;letter-spacing:.5px}
.btn-lock:hover{background:#2a4a2a;color:#7ebe7e}
.btn-unlock{background:#3a1e1e;border:1px solid #6a3a3a;color:#e05555;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:.75em;font-weight:700}
.btn-unlock:hover{background:#4a2a2a;color:#ff7777}
.btn-move{background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:.75em;font-weight:700}
.btn-move:hover{background:#2a3a4a;color:#6dd8d8}
.sel-count{font-size:.7em;color:#4db8b8;font-weight:600}
.co-badge{display:inline-block;background:#e8a838;color:#000;font-size:.7em;font-weight:800;padding:0 4px;border-radius:2px;margin-left:4px}
.pri-badge{font-size:.7em;font-weight:800;padding:0 4px;border-radius:2px;margin-left:4px}
.pri-badge.p1{background:#ff4444;color:#fff}.pri-badge.p2{background:#e8a838;color:#000}
.wcmon{background:#7b5ea7;color:#fff;font-size:.7em;padding:0 4px;border-radius:2px;margin-left:3px}
.lock-icon{font-size:.9em;margin-right:3px}
.toast{position:fixed;bottom:20px;right:20px;background:#1e2a3a;border:1px solid #4db8b8;color:#fff;padding:10px 16px;border-radius:6px;font-size:.85em;z-index:999;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
/* Schedule Drill-Down */
#sdrillbg{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.92);z-index:500;overflow:auto;padding:16px}
#sdrill{max-width:1400px;margin:0 auto;background:#141620;border:1px solid #2a2d3a;border-radius:8px;padding:16px}
#sdhdr{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:16px;flex-wrap:wrap}
#sdhdr>div:first-child{flex:1}
#sdhdr h2{color:#fff;font-size:1.3em;margin-bottom:8px}
#sdstats{display:flex;gap:16px;font-size:.85em;flex-wrap:wrap}
.sdstat{color:#aaa}.sdstat strong{color:#fff;font-weight:700}
#sdtools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
#sdsearch{background:#0f1117;border:1px solid #3a4a6a;color:#e8e8e8;padding:8px 12px;border-radius:5px;font-size:.85em;width:200px;outline:none}
#sdsearch:focus{border-color:#4db8b8}
.wdbtn{background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;padding:6px 12px;border-radius:5px;cursor:pointer;font-size:.75em;font-weight:700;transition:all .15s}
.wdbtn:hover{background:#2a3a4a;color:#6dd8d8}
.wdbtn.active{background:#4db8b8;color:#000;border-color:#4db8b8}
#sdback{background:#3a1e1e;border:1px solid #6a3a3a;color:#e05555;padding:6px 16px;border-radius:5px;cursor:pointer;font-weight:700;transition:all .15s}
#sdback:hover{background:#4a2a2a;color:#ff7777}
#sdtable{overflow-x:auto}
.wdt{width:100%;border-collapse:collapse;font-size:.8em}
.wdt th{background:#1a1d27;color:#4db8b8;padding:8px;text-align:left;border-bottom:1px solid #3a4a6a;font-weight:700;white-space:nowrap}
.wdt td{padding:8px;border-bottom:1px solid #2a2d3a;color:#e8e8e8}
.wdt tr:hover{background:#1a2130}
.wdt .tdpri{width:60px;text-align:center}
.wdt .tdpieces{width:70px}
.wdt .tddesc{min-width:180px;max-width:300px;word-break:break-word}
.wdt .tdclient{width:120px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wdt .tdedition{width:80px;text-align:center}
.wdt .tddue{width:90px;text-align:center}
.wdt .tdval{width:80px;text-align:right;color:#4db8b8;font-weight:600}
.wdt .tdhrs{width:70px;text-align:right;color:#ffd580;font-weight:600}
.wdt .tdprog{width:140px;min-width:140px}
.wdt .tddone{width:80px;text-align:center}
.tdover{background:#3d1515;color:#ff6b6b}
.tdwarn{background:#3d2e10;color:#ffaa44}
.tdok{color:#aaa}
.prog-wrap{display:flex;flex-direction:column;gap:2px;min-width:100px;width:100%}
.prog-row{display:flex;align-items:center;gap:5px}
.prog-label{font-size:.62em;color:#888;width:38px;flex-shrink:0}
.prog-bar-bg{flex:1;height:6px;background:#2a2d3a;border-radius:4px;overflow:hidden}
.prog-bar-fill{height:100%;border-radius:4px;transition:width .4s}
.prog-pct{font-size:.65em;font-weight:700;width:30px;text-align:right;flex-shrink:0}
.prog-val{font-size:.6em;color:#aaa;margin-top:0;text-align:right}
.pct-btns{display:flex;gap:3px;margin-top:4px;flex-wrap:wrap;justify-content:center}
.pct-btn{background:#1e2130;border:1px solid #3a3d4a;color:#777;padding:2px 7px;border-radius:10px;cursor:pointer;font-size:.6em;font-weight:700;transition:background .15s,color .15s,border-color .15s;user-select:none;line-height:1.3}
.pct-btn:hover{background:#2a2d3a;color:#e8e8e8;border-color:#5a6a8a}
.pct-btn.active{background:#8b9dc3;color:#000;border-color:#8b9dc3}
.pct-btn.active-full{background:#5a9e5a;color:#fff;border-color:#5a9e5a}
.pct-btn.active-half{background:#e8a838;color:#000;border-color:#e8a838}
.summary-health{display:flex;flex-direction:column;gap:2px;min-width:200px;padding:2px 0}
.summary-health .prog-row{gap:6px}
.summary-health .prog-label{font-size:.68em;width:42px}
.summary-health .prog-pct{font-size:.7em;width:34px}
.dept-health{padding:4px 6px 2px;margin-top:2px}
.pri-btn{display:inline-block;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;padding:2px 6px;border-radius:3px;cursor:pointer;font-size:.7em;font-weight:700;margin:0 2px;transition:all .15s}
.pri-btn.p0{color:#aaa;border-color:#3a3d4a}
.pri-btn.p1{color:#ff4444;border-color:#6a2222;background:#3d1515}
.pri-btn.p2{color:#ffaa44;border-color:#6a4a2a;background:#3d2e10}
.pri-btn:hover{background:#2a3a4a}
.pri-btn.p1:hover{background:#4a1a1a}
.pri-btn.p2:hover{background:#4a3a1a}
.btn-complete{background:#5a9e5a;border:1px solid #7ebe7e;color:#fff;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:.75em;font-weight:700;transition:all .15s}
.btn-complete:hover{background:#7ebe7e}
/* Weekly Scoreboard */
.wb-score{display:flex;gap:12px;margin:6px 0 8px 0;padding:6px 8px;background:#0f1117;border-radius:4px;font-size:.75em;align-items:center}
.score-item{display:flex;align-items:center;gap:6px;min-width:150px}
.score-label{color:#888;font-weight:600}
.score-bar{flex:1;min-width:100px;height:18px;background:#1a1d27;border-radius:3px;overflow:hidden;border:1px solid #2a2d3a;position:relative}
.score-fill{height:100%;background:linear-gradient(90deg,#4db8b8,#3da8a8);transition:width .3s ease;display:flex;align-items:center;justify-content:center}
.score-text{font-size:.85em;font-weight:700;color:#fff;z-index:2;padding:0 4px}
/* PIN Modal */
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.active{display:flex}
.modal-box{background:#1a1d27;border:1px solid #3a4a6a;border-radius:10px;padding:24px;min-width:320px;text-align:center}
.modal-box h3{color:#fff;margin-bottom:6px;font-size:1.1em}
.modal-box p{color:#888;font-size:.85em;margin-bottom:16px}
.modal-box input{background:#0f1117;border:2px solid #3a4a6a;color:#fff;padding:10px 16px;border-radius:6px;font-size:1.2em;text-align:center;letter-spacing:8px;width:160px;outline:none}
.modal-box input:focus{border-color:#4db8b8}
.modal-box input.error{border-color:#e05555;animation:shake .3s}
.modal-box .week-select{background:#0f1117;border:1px solid #3a4a6a;color:#e8e8e8;padding:8px 12px;border-radius:6px;font-size:.9em;width:100%;margin-bottom:12px;outline:none}
.modal-box .week-select:focus{border-color:#4db8b8}
.modal-btns{display:flex;gap:8px;justify-content:center;margin-top:16px}
.modal-btns button{padding:8px 20px;border-radius:5px;border:1px solid #3a4a6a;cursor:pointer;font-weight:700;font-size:.85em}
.modal-btns .btn-confirm{background:#4db8b8;color:#000;border-color:#4db8b8}
.modal-btns .btn-confirm:hover{background:#3da8a8}
.modal-btns .btn-cancel{background:#2a2d3a;color:#aaa}
.modal-btns .btn-cancel:hover{background:#3a3d4a;color:#fff}
@keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-8px)}75%{transform:translateX(8px)}}
</style>
</head>
<body>
<div class="top-bar">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.6em">📅</div>
    <h1>PRODUCTION SCHEDULE<span>Close Week (PIN) to Lock · Drag Items Between Open Weeks · Click to Select in Locked Weeks</span></h1>
  </div>
  <div class="nav-links">
    <a href="/">🏭 Dashboard</a>
    <a href="/kpi">📊 KPI</a>
    <a href="/maintenance">🔧 Maintenance</a>
    <a href="/shipping">📦 Shipping</a>
  </div>
</div>
<div class="summary-bar" id="summary-bar"></div>
<div class="dept-grid" id="dept-grid"></div>
<div class="toast" id="toast"></div>

<!-- PIN Modal -->
<div class="modal-overlay" id="pin-modal">
  <div class="modal-box">
    <h3 id="modal-title">Close Week</h3>
    <p id="modal-desc">Enter PIN to lock this week's schedule</p>
    <div id="move-target-wrap" style="display:none;margin-bottom:12px">
      <select class="week-select" id="move-target"></select>
    </div>
    <input type="password" id="pin-input" maxlength="4" placeholder="····" autocomplete="off">
    <div class="modal-btns">
      <button class="btn-cancel" onclick="closeModal()">Cancel</button>
      <button class="btn-confirm" onclick="submitPin()">Confirm</button>
    </div>
  </div>
</div>

<!-- Schedule Drill-Down Overlay -->
<div id="sdrillbg" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.92);z-index:500;overflow:auto;padding:16px">
  <div id="sdrill" style="max-width:1400px;margin:0 auto">
    <div id="sdhdr">
      <div>
        <h2 id="sdtitle"></h2>
        <div id="sdstats"></div>
      </div>
      <div id="sdtools">
        <input id="sdsearch" placeholder="Search pieces..." type="text"/>
        <button class="wdbtn active" id="sdsortdue">Sort: Due Date</button>
        <button class="wdbtn" id="sdsorttier" style="display:none">Sort: Tier</button>
        <button class="wdbtn" id="sdsortval">Sort: Value ↓</button>
        <button class="wdbtn" id="sdsortname">Sort: Name</button>
        <button class="wdbtn" id="sdsortpri">Sort: Priority</button>
        <button id="sdback" style="background:#3a1e1e;border:1px solid #6a3a3a;color:#e05555;padding:6px 16px;border-radius:5px;cursor:pointer;font-weight:700">← Back</button>
      </div>
    </div>
    <div id="sdtable"></div>
  </div>
</div>

<script>
const STAGES=[
  {k:'molds',c:'#4a6fa5',l:'Molds',hKey:['hWax']},
  {k:'creation',c:'#7b5ea7',l:'Creation',hKey:['hWax']},
  {k:'waxpull',c:'#e8a838',l:'Wax Pull',hKey:['hWaxPull']},
  {k:'waxchase',c:'#d4763b',l:'Wax Chase',hKey:['hSprue']},
  {k:'shell',c:'#5a9e6f',l:'Shell',hKey:[]},
  {k:'metal_small',c:'#8b9dc3',l:'Small Metal',hKey:['hMetal'],filter:i=>i.stage==='metal'&&!i.monument},
  {k:'metal_mon',c:'#7b5ea7',l:'Monument Metal',hKey:['hMetal'],filter:i=>i.stage==='metal'&&!!i.monument},
  {k:'patina',c:'#c45c8a',l:'Patina',hKey:['hPatina']},
  {k:'base',c:'#4db8b8',l:'Base',hKey:['hBasing']},
  {k:'ready',c:'#5a9e5a',l:'Ready',hKey:[]}
];
const STAGE_MAP=Object.fromEntries(STAGES.map(s=>[s.k,s]));
STAGE_MAP['metal']={k:'metal',c:'#8b9dc3',l:'Metal Work',hKey:['hMetal']}; // alias for drill-down
const fmt=v=>v?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v):'—';
const fmtHrs=h=>h?h.toFixed(1)+'h':'—';

let _items=[], _assignments={}, _priorities={};
let _metalOverrides={}, _stageOverrides={}; // from /api/wip
let _lockedWeeks={}; // 'dept-week' => true
let _selected=new Set(); // selected job ids (for multi-select in locked weeks)
let _dragJob=null;
let _pendingAction=null; // {type:'lock'|'unlock'|'move', dept, week, jobs:[]}
let _drillStage=null, _drillSort='due'; // drill-down state
const TIER_STAGES=['waxpull','waxchase','metal','patina'];

// ========== DRILL-DOWN HELPERS ==========
function dueLabel(d){
  if(!d)return{t:'—',c:''};
  const diff=daysDiff(d);
  if(diff<0)return{t:'OD '+Math.abs(diff)+'d',c:'tdover'};
  if(diff<=7)return{t:d.slice(5),c:'tdwarn'};
  return{t:d.slice(5),c:'tdok'};
}
function getPri(job){return _priorities[job]||0;}
function cyclePriTo(job,newVal){
  const current=getPri(job);
  if(newVal!==undefined){_priorities[job]=newVal;}
  else{_priorities[job]=(current+1)%3;}
  fetch('/api/priority-override',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,priority:_priorities[job]})}).catch(e=>console.error('priority-override failed',e));
  renderDrill();
}
function priBtns(job){
  const p=getPri(job);
  return'<div style="display:flex;gap:2px">'+
    '<button class="pri-btn p0'+(p===0?' active':'')+'" onclick="event.stopPropagation();cyclePriTo(\''+job+'\',0)">—</button>'+
    '<button class="pri-btn p1'+(p===1?' active':'')+'" onclick="event.stopPropagation();cyclePriTo(\''+job+'\',1)">URG</button>'+
    '<button class="pri-btn p2'+(p===2?' active':'')+'" onclick="event.stopPropagation();cyclePriTo(\''+job+'\',2)">HI</button>'+
  '</div>';
}
function metalPct(item){
  const o=_metalOverrides[item.job];
  if(o!==undefined)return o;
  if(item.metalwt&&item.metalwt>0){
    const small=(item.metalwt||0)*0.5, mon=(item.monument?item.metalwt||0:0);
    if(mon+small===0)return 0;
    const done=(item.metal_done_small||0)+(item.metal_done_mon||0);
    return Math.round((done/(small+mon))*100);
  }
  return 0;
}
function stagePct(item){
  const o=_stageOverrides[item.job];
  if(o!==undefined)return o;
  if(!item.stage)return 0;
  const stages=['molds','creation','waxpull','waxchase','shell','metal','patina','base','ready'];
  const idx=stages.indexOf(item.stage);
  if(idx<0)return 0;
  return Math.round((idx/8)*100);
}
function setPct(job,pct){
  _metalOverrides[job]=pct;
  fetch('/api/metal-override',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,pct})}).catch(e=>console.error('metal-override failed',e));
  renderDrill();
}
function setStgPct(job,pct){
  _stageOverrides[job]=pct;
  fetch('/api/stage-override',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,pct})}).catch(e=>console.error('stage-override failed',e));
  renderDrill();
}
function pctBars(item){
  const pct=metalPct(item);
  const opts=[0,25,50,75,100];
  return'<div>'+
    '<div style="display:flex;gap:2px;margin-bottom:3px">'+
      opts.map(p=>'<button class="pct-btn'+(pct===p?' active':'')+'" onclick="event.stopPropagation();setPct(\''+item.job+'\','+p+')">'+p+'%</button>').join('')+
    '</div>'+
    '<div class="prog-wrap"><div class="prog-bar-bg"><div class="prog-bar-fill" style="width:'+pct+'%"></div><span class="prog-text">'+pct+'%</span></div></div>'+
  '</div>';
}
function stgPctBar(item){
  const pct=stagePct(item);
  return'<div class="prog-wrap"><div class="prog-bar-bg"><div class="prog-bar-fill" style="width:'+pct+'%"></div><span class="prog-text">'+pct+'%</span></div></div>';
}
function stgSummaryBar(items,color){
  if(!items.length)return'—';
  const done=items.filter(i=>_assignments[i.job]&&_assignments[i.job].done).length;
  const pct=Math.round((done/items.length)*100);
  return'<div class="prog-wrap"><div class="prog-bar-bg"><div class="prog-bar-fill" style="width:'+pct+'%;background:'+color+'"></div><span class="prog-text">'+done+'/'+items.length+'</span></div></div>';
}
function sortDrillItems(items){
  const search=document.getElementById('sdsearch')?document.getElementById('sdsearch').value.toLowerCase():'';
  let filtered=items.filter(i=>!search||(i.name.toLowerCase().includes(search)||i.job.includes(search)||i.customer.toLowerCase().includes(search)));

  filtered.sort((a,b)=>{
    if(_drillSort==='due'){
      if(!a.due&&!b.due)return a.job.localeCompare(b.job);
      if(!a.due)return 1;
      if(!b.due)return-1;
      return a.due.localeCompare(b.due);
    }else if(_drillSort==='tier'){
      const ta=TIER_STAGES.indexOf(a.stage), tb=TIER_STAGES.indexOf(b.stage);
      if(ta!==tb)return ta-tb;
      return(b.price||0)-(a.price||0);
    }else if(_drillSort==='val'){
      return(b.price||0)-(a.price||0);
    }else if(_drillSort==='name'){
      return a.name.localeCompare(b.name);
    }else if(_drillSort==='pri'){
      const pa=getPri(a.job), pb=getPri(b.job);
      if(pa!==pb)return pb-pa;
      return(a.job||'').localeCompare(b.job||'');
    }
    return 0;
  });
  return filtered;
}
function renderDrillMetal(items){
  const small=[], mon=[];
  items.forEach(i=>{
    if(i.monument)mon.push(i);
    else small.push(i);
  });

  let html='<table class="wdt"><thead><tr><th style="width:100px">Priority</th><th style="width:70px">Piece #</th><th style="min-width:180px">Description</th><th style="width:120px">Client</th><th style="width:80px">Edition</th><th style="width:90px">Due</th><th style="width:80px" class="tdval">Value</th><th style="width:70px" class="tdhrs">Hrs Bid</th><th style="width:200px">Progress</th><th style="width:80px">Done</th></tr></thead><tbody>';

  if(small.length){
    html+='<tr style="background:#0a0f15"><td colspan="10" style="padding:6px 8px;color:#4db8b8;font-weight:700;font-size:.9em">Small / Regular</td></tr>';
    small.forEach(i=>{
      const due=dueLabel(i.due);
      const hrs=itemHours(i);
      const a=_assignments[i.job]||{};
      html+='<tr><td class="tdpri">'+priBtns(i.job)+'</td><td class="tdpieces">#'+i.job+'</td><td class="tddesc">'+i.name+'</td><td class="tdclient">'+i.customer+'</td><td class="tdedition">'+((i.edition||'1')+' ed')+'</td><td class="tddue '+due.c+'">'+due.t+'</td><td class="tdval">'+fmt(i.price)+'</td><td class="tdhrs">'+fmtHrs(hrs)+'</td><td class="tdprog">'+pctBars(i)+'</td><td class="tddone"><button class="btn-complete'+(a.done?' active':'')+'" onclick="event.stopPropagation();toggleDoneMetalItem(\''+i.job+'\')">'+
        (a.done?'✓ Done':'Done')+'</button></td></tr>';
    });
  }

  if(mon.length){
    html+='<tr style="background:#0a0f15"><td colspan="10" style="padding:6px 8px;color:#c45c8a;font-weight:700;font-size:.9em">Monuments</td></tr>';
    mon.forEach(i=>{
      const due=dueLabel(i.due);
      const hrs=itemHours(i);
      const a=_assignments[i.job]||{};
      html+='<tr><td class="tdpri">'+priBtns(i.job)+'</td><td class="tdpieces">#'+i.job+'</td><td class="tddesc">'+i.name+'</td><td class="tdclient">'+i.customer+'</td><td class="tdedition">'+((i.edition||'1')+' ed')+'</td><td class="tddue '+due.c+'">'+due.t+'</td><td class="tdval">'+fmt(i.price)+'</td><td class="tdhrs">'+fmtHrs(hrs)+'</td><td class="tdprog">'+pctBars(i)+'</td><td class="tddone"><button class="btn-complete'+(a.done?' active':'')+'" onclick="event.stopPropagation();toggleDoneMetalItem(\''+i.job+'\')">'+
        (a.done?'✓ Done':'Done')+'</button></td></tr>';
    });
  }

  html+='</tbody></table>';
  return html;
}
function renderDrill(){
  if(!_drillStage)return;
  const stg=STAGE_MAP[_drillStage];
  if(!stg)return;

  const deptItems=_items.filter(i=>i.stage===_drillStage);
  const sorted=sortDrillItems(deptItems);

  const doneCount=sorted.filter(i=>_assignments[i.job]&&_assignments[i.job].done).length;
  const totalHrs=sorted.reduce((a,i)=>a+itemHours(i),0);
  const doneHrs=sorted.filter(i=>_assignments[i.job]&&_assignments[i.job].done).reduce((a,i)=>a+itemHours(i),0);
  const totalVal=sorted.reduce((a,i)=>a+(i.price||0),0);
  const doneVal=sorted.filter(i=>_assignments[i.job]&&_assignments[i.job].done).reduce((a,i)=>a+(i.price||0),0);

  document.getElementById('sdtitle').textContent=stg.l;
  const drillStats=deptPctCalc(sorted);
  document.getElementById('sdstats').innerHTML=
    '<div class="sdstat">Items: <strong>'+doneCount+'/'+sorted.length+'</strong></div>'+
    '<div class="sdstat">Hours: <strong>'+fmtHrs(doneHrs)+'/'+fmtHrs(totalHrs)+'</strong></div>'+
    '<div class="sdstat">Value: <strong>'+fmt(doneVal)+'/'+fmt(totalVal)+'</strong></div>'+
    '<div class="sdstat" style="min-width:180px">'+healthBarHtml(drillStats,stg.c,false)+'</div>';

  // Update sort buttons
  document.querySelectorAll('#sdtools .wdbtn').forEach(b=>{
    b.classList.remove('active');
    if(b.id==='sdsortdue'&&_drillSort==='due')b.classList.add('active');
    if(b.id==='sdsorttier'&&_drillSort==='tier')b.classList.add('active');
    if(b.id==='sdsortval'&&_drillSort==='val')b.classList.add('active');
    if(b.id==='sdsortname'&&_drillSort==='name')b.classList.add('active');
    if(b.id==='sdsortpri'&&_drillSort==='pri')b.classList.add('active');
  });

  if(_drillStage==='metal'){
    document.getElementById('sdtable').innerHTML=renderDrillMetal(sorted);
  } else {
    let html='<table class="wdt"><thead><tr><th style="width:100px">Priority</th><th style="width:70px">Piece #</th><th style="min-width:180px">Description</th><th style="width:120px">Client</th><th style="width:80px">Edition</th><th style="width:90px">Due</th><th style="width:80px" class="tdval">Value</th><th style="width:70px" class="tdhrs">Hrs Bid</th><th style="width:200px">Progress</th><th style="width:80px">Done</th></tr></thead><tbody>';
    sorted.forEach(i=>{
      const due=dueLabel(i.due);
      const hrs=itemHours(i);
      const a=_assignments[i.job]||{};
      html+='<tr><td class="tdpri">'+priBtns(i.job)+'</td><td class="tdpieces">#'+i.job+'</td><td class="tddesc">'+i.name+'</td><td class="tdclient">'+i.customer+'</td><td class="tdedition">'+((i.edition||'1')+' ed')+'</td><td class="tddue '+due.c+'">'+due.t+'</td><td class="tdval">'+fmt(i.price)+'</td><td class="tdhrs">'+fmtHrs(hrs)+'</td><td class="tdprog">'+stgPctBar(i)+'</td><td class="tddone"><button class="btn-complete'+(a.done?' active':'')+'" onclick="event.stopPropagation();toggleDoneNormalItem(\''+i.job+'\')">'+
        (a.done?'✓ Done':'Done')+'</button></td></tr>';
    });
    html+='</tbody></table>';
    document.getElementById('sdtable').innerHTML=html;
  }
}
function openDrill(stgKey,stgLabel,stgColor){
  _drillStage=stgKey;
  _drillSort='due';
  document.getElementById('sdsearch').value='';
  renderDrill();
  document.getElementById('sdrillbg').style.display='block';
  // Wire up event handlers
  document.getElementById('sdsearch').addEventListener('input',renderDrill);
  document.getElementById('sdsortdue').onclick=()=>{_drillSort='due';renderDrill();};
  document.getElementById('sdsorttier').onclick=()=>{_drillSort='tier';renderDrill();};
  document.getElementById('sdsortval').onclick=()=>{_drillSort='val';renderDrill();};
  document.getElementById('sdsortname').onclick=()=>{_drillSort='name';renderDrill();};
  document.getElementById('sdsortpri').onclick=()=>{_drillSort='pri';renderDrill();};
  document.getElementById('sdback').onclick=closeDrill;
}
function closeDrill(){
  _drillStage=null;
  document.getElementById('sdrillbg').style.display='none';
  render();
}
function toggleDoneMetalItem(job){
  const a=_assignments[job];if(!a)return;
  a.done=!a.done;
  const item=_items.find(i=>i.job===job);
  fetch('/api/schedule/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,done:a.done})}).catch(e=>console.error('mark-done failed',e));
  if(a.done){
    setStgPct(job,100);
  }
  renderDrill();
}
function toggleDoneNormalItem(job){
  const a=_assignments[job];if(!a)return;
  a.done=!a.done;
  fetch('/api/schedule/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,done:a.done})}).catch(e=>console.error('mark-done failed',e));
  if(a.done){
    setStgPct(job,100);
  }
  renderDrill();
}

function showToast(msg){
  const t=document.getElementById('toast');
  t.textContent=msg;t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),2500);
}

function getMonday(d){const dt=new Date(d);const day=dt.getDay();const diff=dt.getDate()-day+(day===0?-6:1);dt.setDate(diff);return dt.toISOString().slice(0,10);}
function addWeeks(monday,n){const d=new Date(monday+'T00:00:00');d.setDate(d.getDate()+n*7);return d.toISOString().slice(0,10);}
function fmtWeekRange(monday){
  const d=new Date(monday+'T00:00:00');const end=new Date(d);end.setDate(end.getDate()+6);
  const mo={month:'short',day:'numeric'};
  return d.toLocaleDateString('en-US',mo)+' – '+end.toLocaleDateString('en-US',mo);
}
function weekLabel(monday){
  const today=getMonday(new Date().toISOString().slice(0,10));
  if(monday===today)return'This Week';
  if(monday===addWeeks(today,1))return'Next Week';
  const diff=Math.round((new Date(monday)-new Date(today))/(7*86400000));
  if(diff>0)return'Wk +'+diff;
  return'Wk '+diff;
}

function realDeptKey(dept){return dept==='metal_small'||dept==='metal_mon'?'metal':dept;}
function isLocked(dept,week){return !!_lockedWeeks[realDeptKey(dept)+'-'+week];}

function itemHours(item){
  const s=STAGE_MAP[item.stage];
  if(!s||!s.hKey.length)return 0;
  return s.hKey.reduce((a,k)=>a+(item[k]||0),0);
}
function getDeptItems(stg){
  if(stg.filter)return _items.filter(stg.filter);
  return _items.filter(i=>i.stage===stg.k);
}
function deptPctCalc(items){
  if(!items.length)return{pct:0,rem:100,doneVal:0,remVal:0,doneCount:0};
  let totalVal=0,doneVal=0,doneCount=0;
  items.forEach(i=>{
    const p=i.stage==='metal'?metalPct(i):stagePct(i);
    const v=i.price||0;
    totalVal+=v;
    doneVal+=v*(p/100);
    if(p>=100)doneCount++;
  });
  const pct=totalVal>0?Math.round((doneVal/totalVal)*100):0;
  return{pct,rem:100-pct,doneVal,remVal:totalVal-doneVal,doneCount,total:items.length};
}
function healthBarHtml(stats,color,compact){
  const doneColor=stats.pct>=80?'#5a9e5a':stats.pct>=50?'#e8a838':'#8b9dc3';
  return '<div class="prog-wrap'+(compact?' dept-health':'')+'">'+
    '<div class="prog-row">'+
      '<span class="prog-label" style="color:'+doneColor+'">Done</span>'+
      '<div class="prog-bar-bg"><div class="prog-bar-fill" style="width:'+stats.pct+'%;background:'+doneColor+'"></div></div>'+
      '<span class="prog-pct" style="color:'+doneColor+'">'+stats.pct+'%</span>'+
    '</div>'+
    '<div class="prog-row" style="margin-top:1px">'+
      '<span class="prog-label" style="color:#e8a838">Remain</span>'+
      '<div class="prog-bar-bg"><div class="prog-bar-fill" style="width:'+stats.rem+'%;background:#e8a838"></div></div>'+
      '<span class="prog-pct" style="color:#e8a838">'+stats.rem+'%</span>'+
    '</div>'+
    (!compact?'<div class="prog-val" style="color:'+doneColor+'">'+fmt(stats.doneVal)+' done · '+stats.doneCount+'/'+stats.total+' items</div>':'')+
  '</div>';
}
function daysDiff(d){if(!d)return null;return Math.floor((new Date(d)-new Date())/(86400000));}
function dueTag(d){
  const diff=daysDiff(d);if(diff===null)return'';
  if(diff<0)return'<span class="s-due over">OD '+Math.abs(diff)+'D</span>';
  if(diff<=7)return'<span class="s-due warn">'+d.slice(5)+'</span>';
  return'<span class="s-due">'+d.slice(5)+'</span>';
}
function priTag(job){
  const p=_priorities[job]||0;
  if(p===1)return'<span class="pri-badge p1">URG</span>';
  if(p===2)return'<span class="pri-badge p2">HI</span>';
  return'';
}

// ========== AUTO-ASSIGNMENT ==========
function autoAssign(){
  const today=getMonday(new Date().toISOString().slice(0,10));
  _items.forEach(i=>{
    if(_assignments[i.job] && _assignments[i.job].week) return;
    const dept=i.stage;
    const pri=_priorities[i.job]||0;
    // Priority 1 (urgent) always goes to current week
    if(pri===1){
      _assignments[i.job]={week:today,carryover:false,original_week:null,done:false,auto:true};
      return;
    }
    // If current week is locked for this dept, go to next week
    if(isLocked(dept,today)){
      _assignments[i.job]={week:addWeeks(today,1),carryover:false,original_week:null,done:false,auto:true};
    } else {
      _assignments[i.job]={week:today,carryover:false,original_week:null,done:false,auto:true};
    }
  });
}

// ========== SELECTION (for locked weeks) ==========
function toggleSelect(job, e){
  if(e) e.stopPropagation();
  if(_selected.has(job)) _selected.delete(job);
  else _selected.add(job);
  // Update visual
  document.querySelectorAll('.scard').forEach(el=>{
    const j=el.getAttribute('data-job');
    if(j && _selected.has(j)) el.classList.add('selected');
    else el.classList.remove('selected');
  });
  updateSelectionUI();
}

function updateSelectionUI(){
  // Update move buttons visibility
  document.querySelectorAll('.sel-move-wrap').forEach(el=>{
    const dept=el.dataset.dept;
    const week=el.dataset.week;
    const count=getSelectedInWeek(dept,week).length;
    el.querySelector('.sel-count').textContent=count?count+' selected':'';
    el.querySelector('.btn-move').style.display=count?'inline-block':'none';
  });
}

function getSelectedInWeek(dept,week){
  return _items.filter(i=>
    i.stage===dept && _assignments[i.job] && _assignments[i.job].week===week && _selected.has(i.job)
  ).map(i=>i.job);
}

function requestMoveSelected(dept,week){
  const rd=realDeptKey(dept);
  const jobs=getSelectedInWeek(rd,week);
  if(!jobs.length)return;
  _pendingAction={type:'move',dept:rd,week,jobs};
  document.getElementById('modal-title').textContent='Move '+jobs.length+' Item'+(jobs.length>1?'s':'');
  document.getElementById('modal-desc').textContent='Enter PIN to move from locked week';
  // Build week target options
  const today=getMonday(new Date().toISOString().slice(0,10));
  let opts='';
  for(let i=0;i<8;i++){
    const w=addWeeks(today,i);
    if(w===week)continue;
    opts+='<option value="'+w+'">'+weekLabel(w)+' ('+fmtWeekRange(w)+')</option>';
  }
  document.getElementById('move-target').innerHTML=opts;
  document.getElementById('move-target-wrap').style.display='block';
  document.getElementById('pin-input').value='';
  document.getElementById('pin-input').classList.remove('error');
  document.getElementById('pin-modal').classList.add('active');
  setTimeout(()=>document.getElementById('pin-input').focus(),100);
}

// ========== LOCK / UNLOCK / MOVE ==========
function requestLock(dept,week){
  const rd=realDeptKey(dept);
  _pendingAction={type:'lock',dept:rd,week,jobs:[]};
  document.getElementById('modal-title').textContent='Close Week';
  document.getElementById('modal-desc').textContent='Lock '+(STAGE_MAP[dept]||{l:dept}).l+' — '+weekLabel(week);
  document.getElementById('move-target-wrap').style.display='none';
  document.getElementById('pin-input').value='';
  document.getElementById('pin-input').classList.remove('error');
  document.getElementById('pin-modal').classList.add('active');
  setTimeout(()=>document.getElementById('pin-input').focus(),100);
}
function requestUnlock(dept,week){
  const rd=realDeptKey(dept);
  _pendingAction={type:'unlock',dept:rd,week,jobs:[]};
  document.getElementById('modal-title').textContent='Reopen Week';
  document.getElementById('modal-desc').textContent='Unlock '+(STAGE_MAP[dept]||{l:dept}).l+' — '+weekLabel(week);
  document.getElementById('move-target-wrap').style.display='none';
  document.getElementById('pin-input').value='';
  document.getElementById('pin-input').classList.remove('error');
  document.getElementById('pin-modal').classList.add('active');
  setTimeout(()=>document.getElementById('pin-input').focus(),100);
}
function closeModal(){
  document.getElementById('pin-modal').classList.remove('active');
  _pendingAction=null;
}
function submitPin(){
  const pin=document.getElementById('pin-input').value;
  if(!_pendingAction)return closeModal();
  const {type,dept,week,jobs}=_pendingAction;

  if(type==='move'){
    const targetWeek=document.getElementById('move-target').value;
    if(!targetWeek){showToast('Select a target week');return;}
    fetch('/api/schedule/lock-week',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dept,week,action:'move',pin,jobs,target_week:targetWeek})
    }).then(r=>r.json()).then(d=>{
      if(d.ok){
        jobs.forEach(j=>{
          const a=_assignments[j]||{};
          _assignments[j]={week:targetWeek,carryover:a.carryover||false,original_week:a.original_week||null,done:a.done||false,auto:false};
          _selected.delete(j);
        });
        showToast('Moved '+jobs.length+' item'+(jobs.length>1?'s':'')+' → '+weekLabel(targetWeek));
        closeModal();render();
      } else {
        document.getElementById('pin-input').classList.add('error');
        document.getElementById('pin-input').value='';
        setTimeout(()=>document.getElementById('pin-input').classList.remove('error'),300);
      }
    }).catch(e=>{console.error(e);closeModal();});
  } else {
    const action=type; // 'lock' or 'unlock'
    fetch('/api/schedule/lock-week',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dept,week,action,pin})
    }).then(r=>r.json()).then(d=>{
      if(d.ok){
        if(action==='lock'){
          _lockedWeeks[dept+'-'+week]=true;
          showToast('🔒 '+STAGE_MAP[dept].l+' '+weekLabel(week)+' locked');
        } else {
          delete _lockedWeeks[dept+'-'+week];
          showToast('🔓 '+STAGE_MAP[dept].l+' '+weekLabel(week)+' reopened');
        }
        closeModal();render();
      } else {
        document.getElementById('pin-input').classList.add('error');
        document.getElementById('pin-input').value='';
        setTimeout(()=>document.getElementById('pin-input').classList.remove('error'),300);
      }
    }).catch(e=>{console.error(e);closeModal();});
  }
}
document.getElementById('pin-input').addEventListener('keydown',e=>{
  if(e.key==='Enter')submitPin();
  if(e.key==='Escape')closeModal();
});

// ========== ASSIGN / DONE ==========
function assignWeek(job,week){
  const a=_assignments[job]||{};
  if(!week){delete _assignments[job];}
  else{_assignments[job]={week,carryover:a.carryover||false,original_week:a.original_week||null,done:a.done||false,auto:false};}
  fetch('/api/schedule/assign',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,week:week||''})}).catch(e=>console.error('assign failed',e));
  const item=_items.find(i=>i.job===job);
  showToast('#'+job+(item?' '+item.name:'')+' → '+weekLabel(week));
  render();
}
function rushToThisWeek(job){assignWeek(job,getMonday(new Date().toISOString().slice(0,10)));}
function toggleDone(job){
  const a=_assignments[job];if(!a)return;
  a.done=!a.done;
  const item=_items.find(i=>i.job===job);
  fetch('/api/schedule/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,done:a.done})}).catch(e=>console.error('mark-done failed',e));
  // Also sync stage/metal override
  if(item){
    if(a.done){
      if(item.stage==='metal'){
        _metalOverrides[job]=100;
        fetch('/api/metal-override',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({job,pct:100})}).catch(e=>console.error('metal-override failed',e));
      } else {
        _stageOverrides[job]=100;
        fetch('/api/stage-override',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({job,pct:100})}).catch(e=>console.error('stage-override failed',e));
      }
    } else {
      if(item.stage==='metal'){
        _metalOverrides[job]=0;
        fetch('/api/metal-override',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({job,pct:0})}).catch(e=>console.error('metal-override failed',e));
      } else {
        _stageOverrides[job]=0;
        fetch('/api/stage-override',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({job,pct:0})}).catch(e=>console.error('stage-override failed',e));
      }
    }
  }
  showToast('#'+job+(a.done?' marked done':' unmarked'));
  render();
}
function toggleWb(id){const el=document.getElementById(id);if(el)el.classList.toggle('collapsed');}

// ========== DRAG AND DROP ==========
function onDragStart(e,job){
  const a=_assignments[job];
  if(a&&a.week){const item=_items.find(i=>i.job===job);
    if(item&&isLocked(item.stage,a.week)){e.preventDefault();return;}}
  _dragJob=job;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',job);
  setTimeout(()=>{const el=document.querySelector('[data-job="'+job+'"]');if(el)el.classList.add('dragging');},0);
}
function onDragEnd(e){
  _dragJob=null;
  document.querySelectorAll('.dragging').forEach(el=>el.classList.remove('dragging'));
  document.querySelectorAll('.drag-over').forEach(el=>el.classList.remove('drag-over'));
}
function onDragOver(e){e.preventDefault();e.dataTransfer.dropEffect='move';
  const wb=e.target.closest('.week-block');
  if(wb&&!wb.classList.contains('drag-over')){
    document.querySelectorAll('.drag-over').forEach(el=>el.classList.remove('drag-over'));
    wb.classList.add('drag-over');}}
function onDragLeave(e){const wb=e.target.closest('.week-block');if(wb){
  const r=wb.getBoundingClientRect();
  if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)wb.classList.remove('drag-over');}}
function onDrop(e,dept,week){
  e.preventDefault();
  document.querySelectorAll('.drag-over').forEach(el=>el.classList.remove('drag-over'));
  const job=e.dataTransfer.getData('text/plain')||_dragJob;
  if(!job||!week){_dragJob=null;return;}
  if(isLocked(dept,week)){
    const pri=_priorities[job]||0;
    if(pri!==1){showToast('Week is locked — only urgent items allowed');_dragJob=null;return;}}
  assignWeek(job,week);_dragJob=null;
}

// ========== RENDER ==========
function render(){
  const today=getMonday(new Date().toISOString().slice(0,10));
  autoAssign();

  const allWeeks=new Set();
  for(let i=0;i<6;i++)allWeeks.add(addWeeks(today,i));
  Object.values(_assignments).forEach(a=>{if(a&&a.week)allWeeks.add(a.week);});
  const weeks=Array.from(allWeeks).sort();

  const totalHrs=_items.reduce((a,i)=>a+itemHours(i),0);
  const totalVal=_items.reduce((a,i)=>a+(i.price||0),0);
  const twItems=_items.filter(i=>_assignments[i.job]&&_assignments[i.job].week===today);
  const twHrs=twItems.reduce((a,i)=>a+itemHours(i),0);
  const twVal=twItems.reduce((a,i)=>a+(i.price||0),0);
  const doneItems=_items.filter(i=>_assignments[i.job]&&_assignments[i.job].done);
  const lockedCount=Object.keys(_lockedWeeks).length;
  const overallStats=deptPctCalc(_items);
  document.getElementById('summary-bar').innerHTML=
    '<div class="sstat">TOTAL <strong>'+_items.length+'</strong> items</div>'+
    '<div class="sstat gold">HOURS <strong>'+fmtHrs(totalHrs)+'</strong></div>'+
    '<div class="sstat teal">VALUE <strong>'+fmt(totalVal)+'</strong></div>'+
    '<div class="sstat green">THIS WEEK <strong>'+twItems.length+'</strong> items · '+fmtHrs(twHrs)+' · '+fmt(twVal)+'</div>'+
    (doneItems.length?'<div class="sstat" style="color:#5a9e5a">DONE <strong>'+doneItems.length+'</strong></div>':'')+
    (lockedCount?'<div class="sstat" style="color:#5a9e5a">🔒 <strong>'+lockedCount+'</strong> locked</div>':'')+
    '<div class="summary-health">'+healthBarHtml(overallStats,'#4db8b8',false)+'</div>';

  let grid='';
  STAGES.forEach(stg=>{
    const deptItems=getDeptItems(stg);
    const deptHrs=deptItems.reduce((a,i)=>a+itemHours(i),0);
    const deptVal=deptItems.reduce((a,i)=>a+(i.price||0),0);
    const deptStats=deptPctCalc(deptItems);

    let body='';
    weeks.forEach(w=>{
      const wItems=deptItems.filter(i=>_assignments[i.job]&&_assignments[i.job].week===w);
      if(!wItems.length&&w!==today&&w!==addWeeks(today,1))return;
      const isCurrent=w===today;
      const wLocked=isLocked(stg.k,w);
      const wHrs=wItems.reduce((a,i)=>a+itemHours(i),0);
      const wVal=wItems.reduce((a,i)=>a+(i.price||0),0);
      const doneCount=wItems.filter(i=>_assignments[i.job]&&_assignments[i.job].done).length;

      wItems.sort((a,b)=>{
        const ca=_assignments[a.job]&&_assignments[a.job].carryover?0:1;
        const cb=_assignments[b.job]&&_assignments[b.job].carryover?0:1;
        if(ca!==cb)return ca-cb;
        const pa=_priorities[a.job]||0,pb=_priorities[b.job]||0;
        const wa=pa===1?0:pa===2?1:2,wb=pb===1?0:pb===2?1:2;
        if(wa!==wb)return wa-wb;
        if(!a.due&&!b.due)return 0;if(!a.due)return 1;if(!b.due)return-1;
        return a.due.localeCompare(b.due);
      });

      const wbId='wb-'+stg.k+'-'+w;
      const doneHrsWeek=wItems.filter(i=>_assignments[i.job]&&_assignments[i.job].done).reduce((a,i)=>a+itemHours(i),0);
      const doneValWeek=wItems.filter(i=>_assignments[i.job]&&_assignments[i.job].done).reduce((a,i)=>a+(i.price||0),0);
      body+='<div class="week-block'+(isCurrent?' current':'')+(wLocked?' locked':'')+'" '+
        'ondragover="onDragOver(event)" ondragleave="onDragLeave(event)" ondrop="onDrop(event,\''+stg.k+'\',\''+w+'\')">' +
        '<div class="wb-hdr" onclick="toggleWb(\''+wbId+'\')">'+
          '<span>'+(wLocked?'<span class="lock-icon">🔒</span>':'')+
            '<span class="wlabel">'+weekLabel(w)+'</span><span class="wdates"> '+fmtWeekRange(w)+'</span></span>'+
          '<span class="wstats"><strong>'+wItems.length+'</strong> · '+fmtHrs(wHrs-doneHrsWeek)+' remaining · '+fmt(wVal-doneValWeek)+' remaining'+
            (doneCount?' · <span style="color:#5a9e5a">'+doneCount+'✓</span>':'')+'</span>'+
        '</div>'+
        '<div class="wb-score">'+
          '<div class="score-item"><span class="score-label">Progress:</span><div class="score-bar"><div class="score-fill" style="width:'+(wItems.length?Math.round((doneCount/wItems.length)*100):0)+'%"><span class="score-text">'+doneCount+'/'+wItems.length+'</span></div></div></div>'+
          '<div class="score-item"><span class="score-label">Value:</span><div class="score-bar"><div class="score-fill" style="width:'+(wVal?Math.round((doneValWeek/wVal)*100):0)+'%;background:linear-gradient(90deg,#4db8b8,#3da8a8)"><span class="score-text">'+fmt(doneValWeek)+'</span></div></div></div>'+
          '<div class="score-item"><span class="score-label">Hours:</span><div class="score-bar"><div class="score-fill" style="width:'+(wHrs?Math.round((doneHrsWeek/wHrs)*100):0)+'%;background:linear-gradient(90deg,#ffd580,#ffb840)"><span class="score-text">'+fmtHrs(doneHrsWeek)+'</span></div></div></div>'+
        '</div>'+
        '<div class="wb-body" id="'+wbId+'">'+
          (wItems.length===0?'<div style="text-align:center;padding:10px;color:#444;font-size:.7em">'+(wLocked?'Locked':'Drop items here')+'</div>':'')+
          wItems.map(i=>{
            const a=_assignments[i.job]||{};
            const hrs=itemHours(i);
            const isCarry=a.carryover;
            const isDone=a.done;
            const isSel=_selected.has(i.job);
            const canDrag=!wLocked;
            // In locked weeks, click to select; in open weeks, drag
            const clickHandler=wLocked?
              'onclick="toggleSelect(\''+i.job+'\',event)"':
              '';
            const dragHandler=canDrag?
              'draggable="true" ondragstart="onDragStart(event,\''+i.job+'\')" ondragend="onDragEnd(event)"':
              '';
            return '<div class="scard'+(isCarry?' carry':'')+(isDone?' done-item':'')+(isSel?' selected':'')+'" '+
              'data-job="'+i.job+'" '+dragHandler+' '+clickHandler+
              ' style="border-left-color:'+stg.c+'">'+
              '<div class="s-title">'+(wLocked&&!isSel?'☐ ':'')+(wLocked&&isSel?'☑ ':'')+
                '#'+i.job+' '+i.name+
                (i.monument?'<span class="wcmon">MON</span>':'')+
                (isCarry?'<span class="co-badge">CARRY</span>':'')+priTag(i.job)+
                (isDone?'<span style="color:#5a9e5a;margin-left:4px">✓ Done</span>':'')+'</div>'+
              '<div class="s-meta">'+
                '<span>'+i.customer+'</span>'+
                (hrs?'<span class="s-hrs">'+fmtHrs(hrs)+'</span>':'')+
                (i.price?'<span class="s-val">'+fmt(i.price)+'</span>':'')+
                dueTag(i.due)+
              '</div>'+
              (!wLocked?'<div class="s-actions">'+
                (!isCurrent?'<button class="btn-sm rush" onclick="event.stopPropagation();rushToThisWeek(\''+i.job+'\')">⚡ Rush</button>':'')+
                '<button class="btn-sm done'+(isDone?' active':'')+'" onclick="event.stopPropagation();toggleDone(\''+i.job+'\')">'+
                  (isDone?'✓ Done':'Done')+'</button>'+
              '</div>':'')+
            '</div>';
          }).join('')+
        '</div>'+
        '<div class="wb-actions">'+
          (wLocked?
            '<span class="sel-move-wrap" data-dept="'+stg.k+'" data-week="'+w+'">'+
              '<span class="sel-count"></span> '+
              '<button class="btn-move" style="display:none" onclick="event.stopPropagation();requestMoveSelected(\''+stg.k+'\',\''+w+'\')">📦 Move Selected</button>'+
            '</span>'+
            '<button class="btn-unlock" onclick="event.stopPropagation();requestUnlock(\''+stg.k+'\',\''+w+'\')">🔓 Reopen</button>':
            '<span></span>'+
            '<button class="btn-lock" onclick="event.stopPropagation();requestLock(\''+stg.k+'\',\''+w+'\')">🔒 Close Week</button>')+
        '</div>'+
      '</div>';
    });

    if(!body)body='<div style="text-align:center;padding:20px;color:#555;font-size:.75em">No items</div>';

    const deptDoneHrs=deptItems.filter(i=>{const p=i.stage==='metal'?metalPct(i):stagePct(i);return p>=100;}).reduce((a,i)=>a+itemHours(i),0);
    const deptRemHrs=deptHrs-deptDoneHrs;
    const deptRemVal=deptStats.remVal;
    grid+='<div class="dept-col">'+
      '<div class="dept-hdr" style="background:'+stg.c+'22;cursor:pointer;transition:background .15s" onclick="openDrill(\''+(stg.filter?'metal':stg.k)+'\',\''+stg.l+'\',\''+stg.c+'\')" onmouseover="this.style.background=\''+stg.c+'33\'" onmouseout="this.style.background=\''+stg.c+'22\'">'+
        '<div class="dept-label" style="color:'+stg.c+'">'+stg.l+'</div>'+
        '<div class="dept-count">'+deptItems.length+'</div>'+
        '<div class="dept-sub">ITEMS</div>'+
        (deptHrs?'<div class="dept-hrs">'+fmtHrs(deptRemHrs)+' / '+fmtHrs(deptHrs)+' hrs</div>':'')+
        '<div class="dept-val">'+fmt(deptRemVal)+' / '+fmt(deptVal)+'</div>'+
        healthBarHtml(deptStats,stg.c,true)+
      '</div>'+
      '<div class="dept-body">'+body+'</div>'+
    '</div>';
  });

  document.getElementById('dept-grid').innerHTML=grid;
  // Re-apply selection UI after render
  setTimeout(updateSelectionUI,10);
}

function loadData(){
  Promise.all([
    fetch('/api/wip').then(r=>r.json()),
    fetch('/api/schedule').then(r=>r.json())
  ]).then(([wip,sched])=>{
    if(wip.items)_items=wip.items;
    if(wip.priority_overrides)_priorities=wip.priority_overrides;
    if(wip.metal_overrides)_metalOverrides=wip.metal_overrides;
    if(wip.stage_overrides)_stageOverrides=wip.stage_overrides;
    if(sched.assignments){
      _assignments={};
      Object.entries(sched.assignments).forEach(([job,a])=>{
        _assignments[job]={...a,auto:false};
      });
    }
    if(sched.locked_weeks){
      _lockedWeeks={};
      sched.locked_weeks.forEach(k=>{_lockedWeeks[k]=true;});
    }
    // Auto-mark done if stage_override=100 or metal_override=100
    _items.forEach(i=>{
      const a=_assignments[i.job];
      if(a && !a.done){
        if(_stageOverrides[i.job]===100 || _metalOverrides[i.job]===100){
          a.done=true;
        }
      }
    });
    render();
  }).catch(e=>console.error('load failed',e));
}
loadData();
setInterval(loadData,60000);
</script>
</body>
</html>
"""

@app.route('/schedule')
def schedule_page():
    return Response(SCHEDULE_HTML, mimetype='text/html; charset=utf-8')

@app.route('/api/schedule')
def api_schedule():
    with _lock:
        _auto_rollover()
        return jsonify({
            'assignments': dict(_schedule_data.get('assignments', {})),
            'locked_weeks': list(_schedule_data.get('locked_weeks', [])),
        })

@app.route('/api/schedule/lock-week', methods=['POST'])
def schedule_lock_week():
    try:
        body = request.get_json(force=True)
        dept   = str(body.get('dept', '')).strip()
        week   = str(body.get('week', '')).strip()
        action = str(body.get('action', '')).strip()
        pin    = str(body.get('pin', '')).strip()
        if not dept or not week or not action:
            return jsonify({'error': 'missing fields'}), 400
        if pin != KPI_PIN:
            return jsonify({'ok': False, 'error': 'bad pin'}), 200
        with _lock:
            locked = _schedule_data.setdefault('locked_weeks', [])
            key = f'{dept}-{week}'
            if action == 'lock':
                if key not in locked:
                    locked.append(key)
                log.info(f'Schedule: locked {key}')
            elif action == 'unlock':
                if key in locked:
                    locked.remove(key)
                log.info(f'Schedule: unlocked {key}')
            elif action == 'move':
                # Move selected items from locked week to target
                jobs = body.get('jobs', [])
                target = str(body.get('target_week', '')).strip()
                if not target or not jobs:
                    return jsonify({'error': 'missing target or jobs'}), 400
                assignments = _schedule_data.setdefault('assignments', {})
                for j in jobs:
                    j = str(j).strip()
                    if j in assignments:
                        assignments[j]['week'] = target
                        assignments[j]['carryover'] = False
                log.info(f'Schedule: moved {len(jobs)} items from {key} to {target}')
            _save_schedule()
        return jsonify({'ok': True})
    except Exception as e:
        log.error(f'lock-week error: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedule/assign', methods=['POST'])
def schedule_assign():
    try:
        body = request.get_json(force=True)
        job  = str(body.get('job', '')).strip()
        week = str(body.get('week', '')).strip()
        if not job:
            return jsonify({'error': 'missing job'}), 400
        with _lock:
            assignments = _schedule_data.setdefault('assignments', {})
            if not week:
                assignments.pop(job, None)
                log.info(f'Schedule: unassigned job={job}')
            else:
                existing = assignments.get(job, {})
                assignments[job] = {
                    'week': week,
                    'carryover': existing.get('carryover', False),
                    'original_week': existing.get('original_week'),
                    'done': existing.get('done', False),
                }
                log.info(f'Schedule: assigned job={job} to week={week}')
        _save_schedule()
        return jsonify({'ok': True, 'job': job, 'week': week})
    except Exception as e:
        log.error(f'Schedule assign failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedule/mark-done', methods=['POST'])
def schedule_mark_done():
    try:
        body = request.get_json(force=True)
        job  = str(body.get('job', '')).strip()
        done = bool(body.get('done', False))
        if not job:
            return jsonify({'error': 'missing job'}), 400
        with _lock:
            assignments = _schedule_data.setdefault('assignments', {})
            if job in assignments:
                assignments[job]['done'] = done
                if done:
                    assignments[job]['carryover'] = False
                log.info(f'Schedule: job={job} done={done}')
            else:
                return jsonify({'error': 'job not scheduled'}), 404
        _save_schedule()
        return jsonify({'ok': True, 'job': job, 'done': done})
    except Exception as e:
        log.error(f'Schedule mark-done failed: {e}')
        return jsonify({'error': str(e)}), 500

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

#!/usr/bin/env python3
"""
Pyrology WIP Production Dashboard &mdash; Cloud Version
--------------------------------------------------
Data arrives two ways:
  1. Server-pull: set SESSION_COOKIE env var.
  2. Browser-push: POST raw dithtracker JSON to /api/push-wip.
"""

import os, time, logging, threading, json, base64
from datetime import datetime, timedelta

import requests
from flask import Flask, jsonify, Response, request
from flask_cors import CORS

# &#x2500;&#x2500; Config &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
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

# &#x2500;&#x2500; GitHub Persistence Config &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
GH_REPO    = os.getenv('GH_REPO', 'bryantwilliams71-png/pyrology-wip')
GH_TOKEN   = os.getenv('GH_TOKEN', '')
GH_STATE_FILE = 'state.json'                     # file in repo root
_gh_state_sha = None                              # current SHA of state.json (for updates)
_gh_save_timer = None                             # debounce timer
_gh_ready = False                                 # set True after startup (prevents save during init)
GH_SAVE_DELAY = 5                                 # seconds to debounce before saving

# DithTracker auto-fetch config
DITH_API_BASE = 'https://dithtracker-reporting.azurewebsites.net/Api/Reports/Wip'

# &#x2500;&#x2500; Status &rarr; Stage mapping &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
STATUS_MAP = {
    'Mold':'molds','Waiting on Creation/Mold':'molds','Scan':'molds',
    'Sculpt':'molds',
    'Waiting on Production':'creation','Print/Cast':'creation',
    'Print Surfacing':'creation','Mn Print':'creation',
    'Pull':'waxpull','Mn Pull':'waxpull',
    'Sm Chase':'waxchase','Sm Sprue':'sprue','Mn Chase/Sprue':'sprue',
    'Shell Room/Pouryard':'shell',
    'Sm Metal':'metal','Mn Metal':'metal',
    'Patina':'patina',
    'Base':'base','Dep Transfer':'base',
    'Ready':'ready','Packing/Shipping':'ready',
}

# &#x2500;&#x2500; Globals &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
_cache              = {'items': [], 'updated': None, 'error': None}
_metal_overrides    = {}
_stage_overrides    = {}
_priority_overrides = {}          # job &rarr; 1 (urgent) | 2 (high) | 0 (normal/default)
_kpi_data           = {'week_start': '', 'entries': [], 'history': []}
_maint_data         = {'requests': [], 'next_id': 1}
_ship_data          = {'shipments': [], 'next_id': 1}
_schedule_data      = {'assignments': {}, 'locked_weeks': []}  # job &rarr; {week:'YYYY-MM-DD', carryover:bool, original_week:'YYYY-MM-DD'}
_dt_pending         = []    # pending DithTracker sync moves [{id, pieceIds, statusId, created}]
_dt_pending_id      = 0
_dt_session         = {}    # DithTracker session: {cookies: str, xsrf: str, updated: str}
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
    _schedule_github_save()

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
    _schedule_github_save()

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
    _schedule_github_save()

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
    _schedule_github_save()

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
    _schedule_github_save()

def _load_shipping():
    global _ship_data, _notes_data, _employee_data, _history_data, _team_members
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
    _schedule_github_save()

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
    _schedule_github_save()

# &#x2500;&#x2500; GitHub State Persistence &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
def _gh_headers():
    return {'Authorization': f'token {GH_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'pyrology-wip'}

def _load_state_from_github():
    """Load persistent state (schedule, overrides, KPI, etc.) from GitHub repo."""
    global _gh_state_sha, _schedule_data, _stage_overrides, _priority_overrides
    global _metal_overrides, _kpi_data, _maint_data, _ship_data
    if not GH_TOKEN or not GH_REPO:
        log.info('No GH_TOKEN/GH_REPO &mdash; skipping GitHub state load.')
        return False
    try:
        url = f'https://api.github.com/repos/{GH_REPO}/contents/{GH_STATE_FILE}'
        r = requests.get(url, headers=_gh_headers(), timeout=15)
        if r.status_code == 404:
            log.info('No state.json in repo yet &mdash; starting fresh.')
            return False
        r.raise_for_status()
        data = r.json()
        _gh_state_sha = data['sha']
        content = base64.b64decode(data['content']).decode('utf-8')
        state = json.loads(content)
        # Restore each piece of state
        if 'schedule_data' in state:
            _schedule_data = state['schedule_data']
            log.info(f'  &#10003; Restored {len(_schedule_data.get("assignments", {}))} schedule assignments from GitHub.')
        if 'stage_overrides' in state:
            _stage_overrides = state['stage_overrides']
            log.info(f'  &#10003; Restored {len(_stage_overrides)} stage overrides from GitHub.')
        if 'priority_overrides' in state:
            _priority_overrides = state['priority_overrides']
        if 'metal_overrides' in state:
            _metal_overrides = state['metal_overrides']
        if 'kpi_data' in state:
            _kpi_data = state['kpi_data']
        if 'maint_data' in state:
            _maint_data = state['maint_data']
        if 'ship_data' in state:
            _ship_data = state['ship_data']
            _notes_data = state.get('notes_data', {})
            _employee_data = state.get('employee_data', {})
            _history_data = state.get('history_data', {})
            _team_members = state.get('team_members', [])
        log.info(f'&#10003; State restored from GitHub (sha={_gh_state_sha[:8]})')
        # Also write to /tmp files so existing save functions work locally
        _save_schedule(); _save_stage_overrides(); _save_priority_overrides()
        _save_overrides(); _save_kpi(); _save_maintenance(); _save_shipping()
        return True
    except Exception as e:
        log.warning(f'Could not load state from GitHub: {e}')
        return False

def _build_state_blob():
    """Build the full state dict to persist."""
    return {
        'schedule_data':      _schedule_data,
        'stage_overrides':    _stage_overrides,
        'priority_overrides': _priority_overrides,
        'metal_overrides':    _metal_overrides,
        'kpi_data':           _kpi_data,
        'maint_data':         _maint_data,
        'ship_data':          _ship_data,
        'notes_data':         _notes_data,
        'employee_data':      _employee_data,
        'history_data':       _history_data,
        'team_members':       _team_members,
        'saved_at':           datetime.utcnow().isoformat() + 'Z',
    }

def _save_state_to_github():
    """Push current state to GitHub repo as state.json."""
    global _gh_state_sha
    if not GH_TOKEN or not GH_REPO:
        return
    try:
        state = _build_state_blob()
        content_bytes = json.dumps(state, separators=(',', ':')).encode('utf-8')
        b64 = base64.b64encode(content_bytes).decode('utf-8')
        url = f'https://api.github.com/repos/{GH_REPO}/contents/{GH_STATE_FILE}'
        payload = {
            'message': f'Auto-save state {datetime.utcnow().strftime("%Y-%m-%d %H:%M")}',
            'content': b64,
        }
        if _gh_state_sha:
            payload['sha'] = _gh_state_sha
        r = requests.put(url, headers=_gh_headers(), json=payload, timeout=15)
        r.raise_for_status()
        _gh_state_sha = r.json()['content']['sha']
        log.info(f'&#10003; State saved to GitHub (sha={_gh_state_sha[:8]})')
    except Exception as e:
        log.warning(f'Could not save state to GitHub: {e}')

def _schedule_github_save():
    """Debounced save: waits GH_SAVE_DELAY seconds after last call before actually saving."""
    global _gh_save_timer
    if not _gh_ready:
        return  # don't save during init
    if _gh_save_timer:
        _gh_save_timer.cancel()
    _gh_save_timer = threading.Timer(GH_SAVE_DELAY, _save_state_to_github)
    _gh_save_timer.daemon = True
    _gh_save_timer.start()

def _persist():
    """Save to both local /tmp AND queue a debounced GitHub save."""
    _schedule_github_save()

# &#x2500;&#x2500; DithTracker Auto-Fetch &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
def _auto_fetch_wip():
    """Fetch WIP items directly from DithTracker API (no auth needed)."""
    log.info('Auto-fetching WIP data from DithTracker...')
    try:
        all_items = []
        url = f'{DITH_API_BASE}?pageSize=500'
        r = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        }, timeout=30)
        r.raise_for_status()
        # Strip BOM if present and parse JSON
        txt = r.text.lstrip('\ufeff').strip()
        log.info(f'DithTracker response: status={r.status_code}, len={len(txt)}, first100={txt[:100]!r}')
        body = json.loads(txt)
        raw = body.get('items', body) if isinstance(body, dict) else body
        all_items.extend(raw)
        total = body.get('totalCount', len(raw)) if isinstance(body, dict) else len(raw)
        total_pages = body.get('totalPages', 1) if isinstance(body, dict) else 1
        # Fetch remaining pages if any
        if total_pages > 1:
            for page in range(1, total_pages):
                url2 = f'{DITH_API_BASE}?activeFilter=Include&pageSize=500&pageIndex={page}'
                r2 = requests.get(url2, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': 'application/json',
                }, timeout=30)
                r2.raise_for_status()
                b2 = json.loads(r2.text.lstrip('\ufeff').strip())
                raw2 = b2.get('items', b2) if isinstance(b2, dict) else b2
                all_items.extend(raw2)
        items = transform_rows(all_items)
        log.info(f'&#10003; Auto-fetched {len(items)} WIP items from DithTracker (raw: {len(all_items)})')
        return items
    except Exception as e:
        log.warning(f'Auto-fetch from DithTracker failed: {e}')
        return None

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
            # This item's scheduled week has passed and it's not done &mdash; roll over
            if not info.get('carryover'):
                info['original_week'] = info.get('original_week') or info['week']
            info['week'] = today_monday
            info['carryover'] = True
            changed = True
    if changed:
        _save_schedule()

# &#x2500;&#x2500; Init: Load state (GitHub first, then /tmp fallback) &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
_gh_loaded = _load_state_from_github()
if not _gh_loaded:
    log.info('Falling back to /tmp file state...')
    _load_overrides()
    _load_stage_overrides()
    _load_priority_overrides()
    _load_kpi()
    _load_maintenance()
    _load_shipping()
    _load_schedule()

# &#x2500;&#x2500; Transform raw API rows &rarr; internal format &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
def transform_rows(raw):
    items = []
    seen_jobs = set()
    for row in raw:
        status = row.get('status', '')
        stage  = STATUS_MAP.get(status)
        if not stage:
            continue
        job_id = str(row.get('dithPieceNo', ''))
        if job_id in seen_jobs:
            continue
        seen_jobs.add(job_id)
        due_raw = row.get('dueDate') or row.get('shipDate') or ''
        first = (row.get('firstName') or '').strip()
        last  = (row.get('lastName')  or '').strip()
        items.append({
            'job':           job_id,
            'pieceId':       row.get('pieceId'),
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

# &#x2500;&#x2500; Server-side DithTracker sync &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
def _dt_sync_now(piece_ids, status_id):
    """Call DithTracker's bulk status API directly using stored session credentials.
    Returns True on success, False on failure (will fall back to queue)."""
    if not _dt_session or not _dt_session.get('cookies'):
        log.info('DT sync: no session stored, will queue instead')
        return False
    try:
        import urllib.request
        url = 'https://dithtracker-reporting.azurewebsites.net/api/piece/state/__bulk'
        payload = json.dumps({'pieceIds': piece_ids, 'statusId': status_id}).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Cookie': _dt_session['cookies'],
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Origin': 'https://dithtracker-reporting.azurewebsites.net',
            'Referer': 'https://dithtracker-reporting.azurewebsites.net/Reports/Wip',
        }
        if _dt_session.get('xsrf'):
            headers['X-XSRF-TOKEN'] = _dt_session['xsrf']
        req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            log.info(f'DT sync OK: {len(piece_ids)} pieces &rarr; status {status_id} (HTTP {code})')
            return True
    except Exception as e:
        log.warning(f'DT sync failed: {e}')
        return False

# &#x2500;&#x2500; Server-side fetch &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
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

# &#x2500;&#x2500; Dashboard HTML &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Production Status Board &mdash; Pyrology</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:#0f1117;color:#e8e8e8;font-family:'Segoe UI',Arial,sans-serif;overflow-x:hidden;overflow-y:auto}
#wtop{display:flex;align-items:center;justify-content:space-between;padding:6px 14px;background:#1a1d27;border-bottom:1px solid #2a2d3a;position:sticky;top:0;z-index:10}
#wtop h1{font-size:1.3em;font-weight:700;letter-spacing:1px;color:#fff}
#wtop h1 span{font-size:.65em;font-weight:400;color:#888;display:block;letter-spacing:.5px}
#wclock{text-align:right;font-size:1.6em;font-weight:700;color:#4db8b8;line-height:1}
#wclock small{font-size:.45em;font-weight:400;color:#888;display:block}
#wstats{display:flex;gap:18px;padding:5px 14px;background:#141620;border-bottom:1px solid #2a2d3a;align-items:center;flex-wrap:wrap;position:sticky;top:52px;z-index:9}
.wstat{font-size:.78em;color:#aaa}.wstat strong{color:#fff;font-size:1.1em}
.wstat.green strong{color:#5a9e5a}.wstat.red strong{color:#e05555}
.wstat.gold strong{color:#e8a838}.wstat.teal strong{color:#4db8b8}
#wgrid{display:flex;gap:4px;padding:6px;overflow-x:auto;overflow-y:hidden;height:calc(100vh - 96px)}
.wcol{flex:1;min-width:0;background:#1a1d27;border-radius:6px;display:flex;flex-direction:column;border:1px solid #2a2d3a;cursor:pointer;transition:border-color .2s,box-shadow .2s}
.wcol:hover{border-color:#3a4a5a;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.wcol:hover{border-color:#4db8b8}
.wchdr{padding:7px 8px 5px;text-align:center;border-radius:6px 6px 0 0;flex-shrink:0}
.wclabel{font-size:.72em;font-weight:700;letter-spacing:.8px;text-transform:uppercase}
.wcsub{font-size:.58em;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.4px}
.wccount{font-size:1.15em;font-weight:700;color:#fff;margin-top:2px}
.wcval{font-size:.78em;color:rgba(255,255,255,.85);margin-top:1px}
.wchrs{font-size:.72em;color:#ffd580;margin-top:2px;font-weight:600}
.wcbody{flex:1;overflow-y:auto;padding:4px;display:flex;flex-direction:column;gap:3px}
.wcard{background:#0f1117;border-radius:5px;padding:6px 7px;border-left:3px solid #333;font-size:.7em;flex-shrink:0;transition:background .15s}
.wcard:hover{background:#151820}
.wctitle{font-weight:600;color:#e8e8e8;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wclient{color:#777;font-size:.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wcmeta{display:flex;align-items:center;margin-top:3px;flex-wrap:wrap;gap:3px}
.wcdue{font-size:.82em;padding:1px 5px;border-radius:3px;background:#1e2230;color:#aaa;font-weight:600}
.wcdue.over{background:#3d1515;color:#ff6b6b}
.wcdue.warn{background:#3d2e10;color:#ffaa44}
.wcdue.ok{background:#0f2d1f;color:#5a9e5a}
.wcprice{font-size:.82em;color:#4db8b8;font-weight:700}
.wcmon{background:#7b5ea7;color:#fff;font-size:.68em;padding:1px 4px;border-radius:3px;margin-left:3px;font-weight:700}
.wmore{text-align:center;font-size:.65em;color:#555;padding:4px}
#wlive{font-size:.65em;color:#5a9e5a;text-align:right;margin-left:auto;white-space:nowrap}
#werr{background:#3d1515;color:#ff6b6b;padding:8px 14px;font-size:.8em;display:none}
#wdrillbg{position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.85);display:none;flex-direction:column}
#wdrill{background:#1a1d27;margin:20px;border-radius:10px;flex:1;display:flex;flex-direction:column;overflow:hidden;border:1px solid #2a2d3a}
#wdhdr{padding:14px 18px;display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid #2a2d3a;flex-shrink:0}
#wdhdr h2{font-size:1.4em;font-weight:700}
#wdstats{display:flex;gap:20px;margin-top:6px;flex-wrap:wrap}
#wdstats span{font-size:.8em;color:#aaa}
#wdtools{display:flex;gap:6px;align-items:center;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
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
.wcard.dragging{opacity:.4;transform:scale(.95)}
.wcol.drag-over .wcbody{background:rgba(77,184,184,.08);outline:2px dashed #4db8b8;outline-offset:-4px;border-radius:6px}
.wcard{cursor:grab}
.wcard:active{cursor:grabbing}
.wcard.selected-for-move{outline:2px solid #4db8b8;outline-offset:-2px;background:rgba(77,184,184,.08)!important}
.move-toolbar{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1a1d27;border:1px solid #4db8b8;border-radius:12px;padding:10px 20px;display:flex;align-items:center;gap:16px;z-index:9999;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.move-toolbar .count{color:#4db8b8;font-weight:700;font-size:1.1em}
.move-toolbar select{background:#0f1117;color:#e8e8e8;border:1px solid #2a2d3a;border-radius:6px;padding:6px 12px;font-size:.9em}
.move-toolbar button{padding:6px 16px;border-radius:6px;border:none;font-weight:600;cursor:pointer;font-size:.9em}
.move-toolbar .btn-move{background:#4db8b8;color:#fff}
.move-toolbar .btn-move:hover{background:#3da8a8}
.move-toolbar .btn-cancel{background:#333;color:#ccc}
.move-toolbar .btn-cancel:hover{background:#444}
</style>
</head>
<body>
<div id="wtop">
  <div style="display:flex;align-items:center;gap:10px">
    <div style="font-size:1.6em">&#x1F3ED;</div>
    <h1>PRODUCTION STATUS BOARD<span>Work In Progress &mdash; Click any department to drill down</span></h1>
  </div>
  <div style="display:flex;align-items:center;gap:12px">
    <a href="/schedule" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#5ae8a8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">&#x1F4C5; Schedule</a>
    <a href="/kpi" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">&#x1F4CA; KPI</a>
    <a href="/maintenance" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#e8a838;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">&#x1F527; Maintenance</a>
    <a href="/shipping" style="display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#7aa8e8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px">&#x1F4E6; Shipping</a>
    <div id="wclock">--:--:--<small>Loading...</small></div>
  </div>
</div>
<div id="werr"></div>
<div id="wstats">
  <div class="wstat">&#x25CF; TOTAL ITEMS <strong id="stotal">&mdash;</strong></div>
  <div class="wstat teal">&#x25CF; TOTAL VALUE <strong id="svalue">&mdash;</strong></div>
  <div class="wstat green">&#x25CF; READY <strong id="sready">&mdash;</strong></div>
  <div class="wstat red">&#x25CF; OVERDUE <strong id="sover">&mdash;</strong></div>
  <div class="wstat gold">&#x25CF; DUE THIS WEEK <strong id="sweek">&mdash;</strong></div>
  <div class="wstat gold">&#x25CF; MONUMENTS <strong id="smon">&mdash;</strong></div>
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
        <button class="wdbtn" id="wdsortval">Sort: Value &#x2193;</button>
        <button class="wdbtn" id="wdsortname">Sort: Name</button>
        <button class="wdbtn" id="wdsortpri">Sort: Priority</button>
        <button id="wdselall" style="background:#1e3a2a;border-color:#3a6a4a;color:#5ae8a8;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;cursor:pointer">&#x2610; Select All</button>
        <button id="wdaddweek" style="display:none;background:#1e2a3a;border-color:#3a6a4a;color:#4db8b8;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;cursor:pointer">&#x1F4C5; Add to Week (0)</button>
        <span id="wdmovewrap" style="display:none;align-items:center;gap:4px">
          <select id="wdmovedest" style="background:#0f1117;border:1px solid #3a4a6a;color:#e8e8e8;padding:4px 8px;border-radius:4px;font-size:.82em;cursor:pointer"></select>
          <button id="wdmovebtn" style="background:#3a1e2a;border:1px solid #6a3a5a;color:#e05580;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;cursor:pointer">Move (0)</button>
        </span>
        <button id="wdback">&#x2190; Back to All</button>
      </div>
    </div>
    <div id="wdtable"></div>
  </div>
</div>

<!-- Week Picker Modal -->
<div id="weekPickerBg" style="display:none;position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.82);align-items:center;justify-content:center">
  <div style="position:relative;background:#1a1d27;border:1px solid #2a2d3a;border-radius:12px;padding:28px 32px;min-width:400px;max-width:480px;box-shadow:0 20px 60px rgba(0,0,0,.5)">
    <h3 id="wpTitle" style="color:#fff;font-size:1.15em;font-weight:700;margin-bottom:6px">Schedule Items</h3>
    <p id="wpDesc" style="color:#888;font-size:.85em;margin-bottom:20px">Choose when this work should be completed</p>
    <div id="wpQuickPicks" style="display:flex;flex-direction:column;gap:8px;margin-bottom:20px"></div>
    <div style="border-top:1px solid #2a2d3a;padding-top:14px;margin-top:4px">
      <div style="font-size:.75em;color:#666;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Or choose a specific week</div>
      <select id="wpWeekSelect" style="width:100%;padding:10px 12px;background:#0f1117;border:1px solid #2a2d3a;color:#e8e8e8;border-radius:6px;font-size:.88em;cursor:pointer"></select>
      <button onclick="confirmWeekPicker()" style="width:100%;margin-top:10px;padding:10px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;border-radius:6px;cursor:pointer;font-weight:700;font-size:.88em;transition:all .15s" onmouseover="this.style.background='#2a3a4a'" onmouseout="this.style.background='#1e2a3a'">Schedule to Selected Week</button>
    </div>
    <button onclick="closeWeekPicker()" style="position:absolute;top:12px;right:16px;background:none;border:none;color:#666;font-size:1.3em;cursor:pointer;padding:4px 8px;line-height:1" onmouseover="this.style.color='#fff'" onmouseout="this.style.color='#666'">&times;</button>
  </div>
</div>

<script>
const STAGES=[
  {k:'molds',   l:'Molds',          c:'#4a6fa5'},
  {k:'creation',l:'Creation',       c:'#7b5ea7'},
  {k:'waxpull', l:'Wax Pull',       c:'#e8a838'},
  {k:'waxchase',l:'Wax Chase',       c:'#d4763b'},
  {k:'sprue',   l:'Sprue',          c:'#c97a3b', sub:'Small & Monument'},
  {k:'shell',   l:'Shell/Pouryard', c:'#5a9e6f'},
  {k:'metal',   l:'Metal Work',     c:'#8b9dc3', sub:'Small & Monument'},
  {k:'patina',  l:'Patina',         c:'#c45c8a'},
  {k:'base',    l:'Base',           c:'#4db8b8'},
  {k:'ready',   l:'&#10003; Ready',        c:'#5a9e5a'},
];
const STAGE_HRS={
  waxpull: i=>i.hWaxPull||0,
  waxchase:i=>i.hWax||0,
  sprue:   i=>i.hSprue||0,
  metal:   i=>(i.hMetal||0)+(i.hPolish||0),
  patina:  i=>i.hPatina||0,
  base:    i=>i.hBasing||0,
};

const fmt=v=>v?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v):'&mdash;';
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
  if(a.carryover){label='&#x26A0; CARRY';color='#e8a838';}
  if(a.done){label='&#10003; SCHED';color='#5a9e5a';}
  return`<span style="font-size:.6em;font-weight:700;padding:1px 4px;border-radius:3px;background:${color}22;color:${color};margin-left:3px">${label}</span>`;
}

function daysDiff(d){if(!d)return null;return Math.floor((new Date(d)-new Date())/(86400000));}
function dueLabel(d){
  const diff=daysDiff(d);if(diff===null)return null;
  if(diff<0)return{t:'OVERDUE '+Math.abs(diff)+'D',c:'over'};
  if(diff<=7)return{t:'DUE '+d,c:'warn'};
  return{t:d,c:'ok'};
}

/* &#x2500;&#x2500; priority helpers &#x2500;&#x2500; */
function getPri(job){return _priorityOverrides[job]||0;}
function cyclePri(job,e){
  if(e){e.preventDefault();e.stopPropagation();}
  const cur=getPri(job);
  const next=cur===0?1:cur===1?2:0;  // 0&rarr;1(urgent)&rarr;2(high)&rarr;0(normal)
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
    `<button class="pri-btn${p===1?' p1':''}" onclick="event.stopPropagation();cyclePriTo('${job}',${p===1?0:1})" title="Urgent">&#x1F534;</button>`+
    `<button class="pri-btn${p===2?' p2':''}" onclick="event.stopPropagation();cyclePriTo('${job}',${p===2?0:2})" title="High">&#x1F7E1;</button>`+
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

/* &#x2500;&#x2500; progress bar helper &#x2500;&#x2500; */
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
  // Sync completion to schedule
  const isDone=pct>=100;
  if(_scheduleData[job]&&_scheduleData[job].week){
    _scheduleData[job].done=isDone;
    fetch('/api/schedule/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({job,done:isDone})}).catch(e=>console.error('schedule sync failed:',e));
  }
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

/* &#x2500;&#x2500; Stage (non-metal) progress bar helpers &#x2500;&#x2500; */
function stagePct(item){
  if(Object.prototype.hasOwnProperty.call(_stageOverrides,item.job))return _stageOverrides[item.job];
  return 0;
}
function setStgPct(job,pct){
  _stageOverrides[job]=pct;
  fetch('/api/stage-override',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job,pct})})
    .catch(e=>console.error('setStgPct failed:',e));
  // Sync completion to schedule: if marked 100% done, also mark schedule done
  const isDone=pct>=100;
  if(_scheduleData[job]&&_scheduleData[job].week){
    _scheduleData[job].done=isDone;
    fetch('/api/schedule/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({job,done:isDone})}).catch(e=>console.error('schedule sync failed:',e));
  }
  renderDrill();
  renderBoard();
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

/* &#x2500;&#x2500; Stage scoreboard summary bar (shared by all non-monument sections) &#x2500;&#x2500; */
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
  STAGES.forEach(s=>{sm[s.k]={items:[],val:0,hrs:0,seen:new Set()};});
  _items.forEach(item=>{
    const st=sm[item.stage];
    if(st&&!st.seen.has(item.job)){st.seen.add(item.job);st.items.push(item);st.val+=item.price||0;if(STAGE_HRS[item.stage])st.hrs+=STAGE_HRS[item.stage](item);}
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
    return `<div class="wcol" data-stage="${s.k}" onclick="openDrill('${s.k}','${s.l}','${s.c}')">
      <div class="wchdr" style="background:${s.c}22;border-bottom:3px solid ${s.c}">
        <div class="wclabel" style="color:${s.c}">${s.l}</div>
        ${s.sub?`<div class="wcsub">${s.sub}</div>`:''}
        <div class="wccount">${sd.items.length} ITEMS</div>
        <div class="wcval">${fmt(sd.val)}</div>
        ${sd.hrs>0?`<div class="wchrs">&#x23F1; ${fmtH(sd.hrs)}</div>`:''}
      </div>
      <div class="wcbody">
        ${shown.map(item=>{
          const dl=dueLabel(item.due);
          const pri=getPri(item.job);
          const priCls=pri===1?' pri-1':pri===2?' pri-2':'';
          const pct=stagePct(item);const pctColor=pct>=100?'#5a9e5a':pct>=50?'#e8a838':'#8b9dc3';
          const isScheduled=_scheduleData[item.job]&&_scheduleData[item.job].week;
          return `<div class="wcard${priCls}" data-job="${item.job}" style="border-left-color:${pri?'':s.c}" oncontextmenu="cyclePri('${item.job}',event)">
            ${priLabel(item.job)}
            <div class="wctitle">#${item.job} ${item.name}${item.monument?'<span class="wcmon">MON</span>':''}</div>
            <div class="wclient">${item.customer||''}</div>
            <div class="wcmeta">
              ${item.edition?`<span style="color:#555;font-size:.82em">Ed.${item.edition}</span>`:''}
              ${dl?`<span class="wcdue ${dl.c}">${dl.t}</span>`:''}
              ${item.price?`<span class="wcprice">${fmt(item.price)}</span>`:''}
              ${schedBadge(item.job)}
            </div>
            <div style="display:flex;align-items:center;gap:4px;margin-top:3px">
              <div style="flex:1;height:3px;background:#1a1d27;border-radius:2px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${pctColor};border-radius:2px;transition:width .3s"></div></div>
              ${pct>0?'<span style="font-size:.7em;color:'+pctColor+';font-weight:700;min-width:22px;text-align:right">'+pct+'%</span>':''}
              ${isScheduled?'':'<button onclick="addOneToWeek(\''+item.job+'\',event)" style="padding:1px 5px;background:#1e2a3a;border:1px solid #2a3a5a;color:#4db8b8;border-radius:3px;cursor:pointer;font-size:.6em;white-space:nowrap;font-weight:600;letter-spacing:.3px;opacity:.7;transition:opacity .15s" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=.7">SCHED</button>'}
            </div>
          </div>`;
        }).join('')}
        ${extra>0?`<div class="wmore">+${extra} more &mdash; click to see all</div>`:''}
      </div>
    </div>`;
  }).join('');
  initDragDrop();
}

// &#x2500;&#x2500; Drag & Drop + Batch Move &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
let _selectedJobs = new Set();
let _moveToolbarEl = null;

// DithTracker status mapping for each Pyrology stage
const DT_STATUS_MAP = {
  molds: 18, creation: 47, waxpull: 2, waxchase: 55, sprue: 56,
  shell: 78, metal: 62, patina: 79, base: 22, ready: 7
};

function initDragDrop(){
  document.querySelectorAll('.wcard').forEach(card => {
    card.setAttribute('draggable', 'true');
    card.addEventListener('dragstart', e => {
      card.classList.add('dragging');
      e.dataTransfer.setData('text/plain', card.dataset.job);
      e.dataTransfer.effectAllowed = 'move';
    });
    card.addEventListener('dragend', () => card.classList.remove('dragging'));
    // Click to select for batch move (with ctrl/cmd)
    card.addEventListener('click', e => {
      if(e.metaKey || e.ctrlKey){
        e.stopPropagation();
        e.preventDefault();
        const job = card.dataset.job;
        if(_selectedJobs.has(job)){_selectedJobs.delete(job);card.classList.remove('selected-for-move');}
        else{_selectedJobs.add(job);card.classList.add('selected-for-move');}
        updateMoveToolbar();
      }
    });
  });
  document.querySelectorAll('.wcol').forEach(col => {
    const stageKey = col.dataset.stage;
    col.addEventListener('dragover', e => {e.preventDefault();e.dataTransfer.dropEffect='move';col.classList.add('drag-over');});
    col.addEventListener('dragleave', e => {if(!col.contains(e.relatedTarget))col.classList.remove('drag-over');});
    col.addEventListener('drop', e => {
      e.preventDefault();
      col.classList.remove('drag-over');
      const job = e.dataTransfer.getData('text/plain');
      if(job && stageKey) moveItems([job], stageKey);
    });
  });
}

function moveItems(jobs, targetStage){
  // Find pieceIds for the jobs
  const pieceIds = [];
  jobs.forEach(job => {
    const item = _items.find(i => i.job === job);
    if(item){
      item.stage = targetStage; // Update locally
      if(item.pieceId) pieceIds.push(item.pieceId);
    }
  });
  // Save to Pyrology server
  fetch('/api/move-items', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({jobs, targetStage, pieceIds, dtStatusId: DT_STATUS_MAP[targetStage] || null})
  }).then(r => r.json()).then(d => {
    let msg = jobs.length + ' item(s) moved';
    if(d.reassigned > 0) msg += ' &rarr; scheduled next week';
    if(d.dtQueued) msg += ' &mdash; DT syncing';
    showToast(msg);
  }).catch(e => console.error('move failed', e));
  _selectedJobs.clear();
  updateMoveToolbar();
  renderBoard();
  if(_drillStage) renderDrill();
}

function updateMoveToolbar(){
  if(_selectedJobs.size === 0){
    if(_moveToolbarEl){_moveToolbarEl.remove();_moveToolbarEl=null;}
    return;
  }
  if(!_moveToolbarEl){
    _moveToolbarEl = document.createElement('div');
    _moveToolbarEl.className = 'move-toolbar';
    document.body.appendChild(_moveToolbarEl);
  }
  const opts = STAGES.map(s => '<option value="'+s.k+'">'+s.l+'</option>').join('');
  _moveToolbarEl.innerHTML =
    '<span class="count">'+_selectedJobs.size+' selected</span>'+
    '<select id="moveDest">'+opts+'</select>'+
    '<button class="btn-move" onclick="moveItems(Array.from(_selectedJobs),document.getElementById(\'moveDest\').value)">Move</button>'+
    '<button class="btn-cancel" onclick="_selectedJobs.clear();document.querySelectorAll(\'.selected-for-move\').forEach(c=>c.classList.remove(\'selected-for-move\'));updateMoveToolbar()">Cancel</button>';
}

const TIER_STAGES=['waxpull','waxchase','sprue','metal','patina'];
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
  _drillSelected.clear();
  _allSelectMode=false;
  document.getElementById('wdselall').textContent='\u2610 Select All';
  document.getElementById('wdaddweek').style.display='none';
  document.getElementById('wdmovewrap').style.display='none';
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

/* &#x2500;&#x2500; Metal Work special drill-down &#x2500;&#x2500; */
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
    `<table class="wdt"><thead><tr><th style="width:30px"></th><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th></th><th>Schedule</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hMetal||0)+(item.hPolish||0);
      const isDone=stagePct(item)>=100;
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      const isSel=_drillSelected.has(item.job);
      const hasSched=_scheduleData[item.job]&&_scheduleData[item.job].week;
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td><span class="drill-cb" data-job="${item.job}" onclick="toggleDrillSelect('${item.job}')" style="cursor:pointer;font-size:1.2em;color:${isSel?'#5ae8a8':'#555'}">${isSel?'\u2611':'\u2610'}</span></td>
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'\u2014'}</strong><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'\u2014'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">\u2014</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
        <td>${stgPctBar(item)}</td>
        <td><button class="btn-complete${isDone?' done':''}" onclick="event.stopPropagation();setStgPct('${item.job}',${isDone?0:100})">${isDone?'\u2713 Done':'\u2713'}</button></td>
        <td><button onclick="addOneToWeek('${item.job}',event)" style="padding:3px 8px;background:${hasSched?'#1e3a2a':'#1e2a3a'};border:1px solid ${hasSched?'#3a6a4a':'#3a4a6a'};color:${hasSched?'#5ae8a8':'#4db8b8'};border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">${hasSched?'\u2713 Scheduled '+schedBadge(item.job):'&#x1F4C5; Schedule'}</button></td>
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
    `<table class="wdt"><thead><tr><th style="width:30px"></th><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th>Schedule</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hMetal||0)+(item.hPolish||0);
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      const isSel=_drillSelected.has(item.job);
      const hasSched=_scheduleData[item.job]&&_scheduleData[item.job].week;
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td><span class="drill-cb" data-job="${item.job}" onclick="toggleDrillSelect('${item.job}')" style="cursor:pointer;font-size:1.2em;color:${isSel?'#5ae8a8':'#555'}">${isSel?'\u2611':'\u2610'}</span></td>
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'\u2014'}</strong><span class="tdmon">MON</span><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'\u2014'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">\u2014</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${(()=>{if(!h)return'';const pct=metalPct(item);const dh=h*(pct/100);const rh=h-dh;return`<div style="color:#ffd580;font-weight:700">${h.toFixed(1)} bid</div><div style="color:#5a9e5a;font-size:.82em">${dh.toFixed(1)} done</div><div style="color:#e8a838;font-size:.82em">${rh.toFixed(1)} left</div>`;})()}</td>
        <td>${pctBars(item)}</td>
        <td><button onclick="addOneToWeek('${item.job}',event)" style="padding:3px 8px;background:${hasSched?'#1e3a2a':'#1e2a3a'};border:1px solid ${hasSched?'#3a6a4a':'#3a4a6a'};color:${hasSched?'#5ae8a8':'#4db8b8'};border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">${hasSched?'\u2713 Scheduled '+schedBadge(item.job):'&#x1F4C5; Schedule'}</button></td>
      </tr>`;
    }).join('')+'</tbody></table>';
  }

  document.getElementById('wdtable').innerHTML=
    `<div class="metal-section-hdr"><h3 style="color:#8b9dc3">Small Metal</h3><span class="metal-badge" style="background:#8b9dc322;color:#8b9dc3">${small.length} items &middot; ${fmt(small.reduce((a,i)=>a+(i.price||0),0))}</span></div>`+
    smallTable(small)+
    `<div class="metal-section-hdr" style="margin-top:18px"><h3 style="color:#7b5ea7">Monument Metal</h3><span class="metal-badge" style="background:#7b5ea722;color:#7b5ea7">${mon.length} items &middot; ${fmt(mon.reduce((a,i)=>a+(i.price||0),0))}</span></div>`+
    monTable(mon);
}

/* &#x2500;&#x2500; Wax Sprue special drill-down &#x2500;&#x2500; */
function renderDrillSprue(q){
  let all=_items.filter(i=>i.stage==='sprue');
  if(q)all=all.filter(i=>(i.name+' '+i.customer+' '+i.job).toLowerCase().includes(q));

  const small=sortItems(all.filter(i=>!i.monument));
  const mon=sortItems(all.filter(i=>i.monument));

  const totalVal=all.reduce((a,i)=>a+(i.price||0),0);
  const totalHrs=all.reduce((a,i)=>a+(i.hWax||0)+(i.hSprue||0),0);
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
    return stgSummaryBar(items,'#d4763b')+
    `<table class="wdt"><thead><tr><th style="width:30px"></th><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th></th><th>Schedule</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hWax||0)+(item.hSprue||0);
      const isDone=stagePct(item)>=100;
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      const isSel=_drillSelected.has(item.job);
      const hasSched=_scheduleData[item.job]&&_scheduleData[item.job].week;
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td><span class="drill-cb" data-job="${item.job}" onclick="toggleDrillSelect('${item.job}')" style="cursor:pointer;font-size:1.2em;color:${isSel?'#5ae8a8':'#555'}">${isSel?'\u2611':'\u2610'}</span></td>
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'\u2014'}</strong><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'\u2014'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">\u2014</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
        <td>${stgPctBar(item)}</td>
        <td><button class="btn-complete${isDone?' done':''}" onclick="event.stopPropagation();setStgPct('${item.job}',${isDone?0:100})">${isDone?'\u2713 Done':'\u2713'}</button></td>
        <td><button onclick="addOneToWeek('${item.job}',event)" style="padding:3px 8px;background:${hasSched?'#1e3a2a':'#1e2a3a'};border:1px solid ${hasSched?'#3a6a4a':'#3a4a6a'};color:${hasSched?'#5ae8a8':'#4db8b8'};border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">${hasSched?'\u2713 Scheduled '+schedBadge(item.job):'&#x1F4C5; Schedule'}</button></td>
      </tr>`;
    }).join('')+'</tbody></table>';
  }

  function monTable(items){
    if(!items.length)return'<p style="color:#555;font-size:.8em;padding:8px 0">No items.</p>';
    const monVal=items.reduce((a,i)=>a+(i.price||0),0);
    const monDoneVal=items.reduce((a,i)=>a+(i.price||0)*(stagePct(i)/100),0);
    const monRemVal=monVal-monDoneVal;
    const avgPct=items.length?Math.round(items.reduce((a,i)=>a+stagePct(i),0)/items.length):0;
    const monTotalHrs=items.reduce((a,i)=>a+(i.hWax||0)+(i.hSprue||0),0);
    const monDoneHrs=items.reduce((a,i)=>{const h=(i.hWax||0)+(i.hSprue||0);return a+h*(stagePct(i)/100);},0);
    const monRemHrs=monTotalHrs-monDoneHrs;
    const summaryBar=`<div style="background:#12151f;border:1px solid #2a2d3a;border-radius:6px;padding:10px 14px;margin-bottom:10px;display:flex;gap:28px;align-items:center;flex-wrap:wrap">
      <div>
        <div style="font-size:.65em;color:#888;text-transform:uppercase;letter-spacing:.5px">Avg Completion</div>
        <div style="font-size:1.4em;font-weight:700;color:#7b5ea7;margin-top:2px">${avgPct}%</div>
        <div style="width:120px;height:8px;background:#2a2d3a;border-radius:4px;margin-top:4px;overflow:hidden">
          <div style="width:${avgPct}%;height:100%;background:#7b5ea7;border-radius:4px"></div>
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
    `<table class="wdt"><thead><tr><th style="width:30px"></th><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th>Schedule</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=(item.hWax||0)+(item.hSprue||0);
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      const isSel=_drillSelected.has(item.job);
      const hasSched=_scheduleData[item.job]&&_scheduleData[item.job].week;
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td><span class="drill-cb" data-job="${item.job}" onclick="toggleDrillSelect('${item.job}')" style="cursor:pointer;font-size:1.2em;color:${isSel?'#5ae8a8':'#555'}">${isSel?'\u2611':'\u2610'}</span></td>
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'\u2014'}</strong><span class="tdmon">MON</span><br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'\u2014'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">\u2014</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${(()=>{if(!h)return'';const pct=stagePct(item);const dh=h*(pct/100);const rh=h-dh;return`<div style="color:#ffd580;font-weight:700">${h.toFixed(1)} bid</div><div style="color:#5a9e5a;font-size:.82em">${dh.toFixed(1)} done</div><div style="color:#e8a838;font-size:.82em">${rh.toFixed(1)} left</div>`;})()}</td>
        <td>${stgPctBar(item)}</td>
        <td><button onclick="addOneToWeek('${item.job}',event)" style="padding:3px 8px;background:${hasSched?'#1e3a2a':'#1e2a3a'};border:1px solid ${hasSched?'#3a6a4a':'#3a4a6a'};color:${hasSched?'#5ae8a8':'#4db8b8'};border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">${hasSched?'\u2713 Scheduled '+schedBadge(item.job):'&#x1F4C5; Schedule'}</button></td>
      </tr>`;
    }).join('')+'</tbody></table>';
  }

  document.getElementById('wdtable').innerHTML=
    `<div class="metal-section-hdr"><h3 style="color:#d4763b">Small Sprue</h3><span class="metal-badge" style="background:#d4763b22;color:#d4763b">${small.length} items \u00B7 ${fmt(small.reduce((a,i)=>a+(i.price||0),0))}</span></div>`+
    smallTable(small)+
    `<div class="metal-section-hdr" style="margin-top:18px"><h3 style="color:#7b5ea7">Monument Sprue</h3><span class="metal-badge" style="background:#7b5ea722;color:#7b5ea7">${mon.length} items \u00B7 ${fmt(mon.reduce((a,i)=>a+(i.price||0),0))}</span></div>`+
    monTable(mon);
}

function renderDrill(){
  const q=(document.getElementById('wdsearch').value||'').toLowerCase();

  if(_drillStage==='metal'){
    renderDrillMetal(q);
    return;
  }

  if(_drillStage==='sprue'){
    renderDrillSprue(q);
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
    `<table class="wdt"><thead><tr><th style="width:30px"></th><th>Priority</th><th>Piece #</th><th>Description</th><th>Client</th><th>Edition</th><th>Due Date</th><th>Value</th><th>Hrs Bid</th><th>Progress</th><th></th><th>Schedule</th></tr></thead><tbody>`+
    items.map(item=>{
      const dl=dueLabel(item.due);
      const h=STAGE_HRS[_drillStage]?STAGE_HRS[_drillStage](item):0;
      const isDone=stagePct(item)>=100;
      const tierBadge=item.tier!=null?`<br><span class="tdtier t${item.tier}">TIER ${item.tier}</span>`:'';
      const pri=getPri(item.job);
      const isSel=_drillSelected.has(item.job);
      const hasSched=_scheduleData[item.job]&&_scheduleData[item.job].week;
      return`<tr style="${pri===1?'background:#1a0f0f':pri===2?'background:#1a160f':''}">
        <td><span class="drill-cb" data-job="${item.job}" onclick="toggleDrillSelect('${item.job}')" style="cursor:pointer;font-size:1.2em;color:${isSel?'#5ae8a8':'#555'}">${isSel?'\u2611':'\u2610'}</span></td>
        <td>${priBtns(item.job)}</td>
        <td style="color:#888">#${item.job}${tierBadge}</td>
        <td><strong>${item.name||'\u2014'}</strong>${item.monument?'<span class="tdmon">MON</span>':''}<br><small style="color:#666">${item.status||''}</small></td>
        <td>${item.customer||'\u2014'}</td>
        <td style="color:#888">${item.edition?'Ed.'+item.edition:''}</td>
        <td>${dl?`<span class="${dl.c==='over'?'tdover':dl.c==='warn'?'tdwarn':'tdok'}">${dl.t}</span>`:'<span style="color:#555">\u2014</span>'}</td>
        <td class="tdval">${fmt(item.price)}</td>
        <td class="tdhrs">${h>0?h.toFixed(2)+' hrs':''}</td>
        <td>${stgPctBar(item)}</td>
        <td><button class="btn-complete${isDone?' done':''}" onclick="event.stopPropagation();setStgPct('${item.job}',${isDone?0:100})">${isDone?'\u2713 Done':'\u2713'}</button></td>
        <td><button onclick="addOneToWeek('${item.job}',event)" style="padding:3px 8px;background:${hasSched?'#1e3a2a':'#1e2a3a'};border:1px solid ${hasSched?'#3a6a4a':'#3a4a6a'};color:${hasSched?'#5ae8a8':'#4db8b8'};border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">${hasSched?'\u2713 Scheduled '+schedBadge(item.job):'&#x1F4C5; Schedule'}</button></td>
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

// &#x2500;&#x2500; Drill-down selection & Add to Week &#x2500;&#x2500;
let _drillSelected = new Set();
let _allSelectMode = false;

function toggleDrillSelect(job) {
  if (_drillSelected.has(job)) _drillSelected.delete(job);
  else _drillSelected.add(job);
  updateDrillSelectUI();
}

function updateDrillSelectUI() {
  const count = _drillSelected.size;
  const btn = document.getElementById('wdaddweek');
  const moveWrap = document.getElementById('wdmovewrap');
  const moveBtn = document.getElementById('wdmovebtn');
  const moveDest = document.getElementById('wdmovedest');
  if (count > 0) {
    btn.style.display = '';
    btn.textContent = '\u{1F4C5} Add to Week (' + count + ')';
    moveWrap.style.display = 'flex';
    moveBtn.textContent = '\u27A1 Move (' + count + ')';
    // Populate dept dropdown excluding current stage
    moveDest.innerHTML = STAGES.filter(s=>s.k!==_drillStage).map(s=>'<option value="'+s.k+'">'+s.l+'</option>').join('');
  } else {
    btn.style.display = 'none';
    moveWrap.style.display = 'none';
  }
  document.querySelectorAll('.drill-cb').forEach(cb => {
    cb.textContent = _drillSelected.has(cb.dataset.job) ? '\u2611' : '\u2610';
    cb.style.color = _drillSelected.has(cb.dataset.job) ? '#5ae8a8' : '#555';
  });
}

document.getElementById('wdselall').onclick = function() {
  _allSelectMode = !_allSelectMode;
  if (_allSelectMode) {
    document.querySelectorAll('.drill-cb').forEach(cb => _drillSelected.add(cb.dataset.job));
    this.textContent = '\u2611 Deselect All';
    this.style.color = '#5ae8a8';
  } else {
    _drillSelected.clear();
    this.textContent = '\u2610 Select All';
    this.style.color = '#5ae8a8';
  }
  updateDrillSelectUI();
};

document.getElementById('wdaddweek').onclick = function() {
  if (!_drillSelected.size) return;
  openWeekPicker(Array.from(_drillSelected));
};

document.getElementById('wdmovebtn').onclick = function() {
  if (!_drillSelected.size) return;
  const dest = document.getElementById('wdmovedest').value;
  if (!dest) return;
  const jobs = Array.from(_drillSelected);
  moveItems(jobs, dest);
  _drillSelected.clear();
  updateDrillSelectUI();
  renderDrill();
  const stgLabel = STAGES.find(s=>s.k===dest)?.l || dest;
  showToast(jobs.length + ' item(s) moved to ' + stgLabel);
};

function addWeeksW(base, n) {
  const d = new Date(base);
  d.setDate(d.getDate() + 7 * n);
  return d.toISOString().slice(0, 10);
}

function weekLabelW(d) {
  const dt = new Date(d + 'T00:00:00');
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function fmtWeekRangeW(monday) {
  const start = new Date(monday + 'T00:00:00');
  const end = new Date(start);
  end.setDate(end.getDate() + 4);
  return start.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' - ' +
         end.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

let _weekPickerJobs = [];

function openWeekPicker(jobs) {
  _weekPickerJobs = jobs;
  const today = getMonday(new Date().toISOString().slice(0, 10));
  const n = jobs.length;
  document.getElementById('wpTitle').textContent = 'Schedule ' + n + ' Item' + (n > 1 ? 's' : '');
  document.getElementById('wpDesc').textContent = 'Choose when ' + (n > 1 ? 'these items' : 'this item') + ' should be completed';

  // Quick pick buttons: This Week, Next Week, +2 Weeks
  const qp = document.getElementById('wpQuickPicks');
  const picks = [
    { offset: 0, label: 'This Week', desc: fmtWeekRangeW(today), icon: '\u25CF', color: '#4db8b8', bg: '#1e2a3a', border: '#3a5a6a' },
    { offset: 1, label: 'Next Week', desc: fmtWeekRangeW(addWeeksW(today, 1)), icon: '\u25B6', color: '#5ae8a8', bg: '#1e3a2a', border: '#3a6a4a' },
    { offset: 2, label: 'Week After Next', desc: fmtWeekRangeW(addWeeksW(today, 2)), icon: '\u25B6\u25B6', color: '#e8a838', bg: '#2a2a1a', border: '#5a5a3a' }
  ];
  qp.innerHTML = picks.map(function(p) {
    const w = addWeeksW(today, p.offset);
    return '<button onclick="quickPickWeek(\'' + w + '\')" style="display:flex;align-items:center;gap:12px;width:100%;padding:12px 16px;background:' + p.bg + ';border:1px solid ' + p.border + ';border-radius:8px;cursor:pointer;transition:all .15s;text-align:left" onmouseover="this.style.transform=\'translateX(4px)\';this.style.borderColor=\'' + p.color + '\'" onmouseout="this.style.transform=\'none\';this.style.borderColor=\'' + p.border + '\'">' +
      '<span style="font-size:1.1em;color:' + p.color + '">' + p.icon + '</span>' +
      '<span style="flex:1"><span style="color:#fff;font-weight:700;font-size:.92em">' + p.label + '</span><br><span style="color:#888;font-size:.78em">' + p.desc + '</span></span>' +
      '<span style="color:' + p.color + ';font-size:.78em;font-weight:600">' + weekLabelW(w) + '</span>' +
    '</button>';
  }).join('');

  // Additional weeks in dropdown
  const sel = document.getElementById('wpWeekSelect');
  sel.innerHTML = '';
  for (var i = 3; i < 12; i++) {
    var w = addWeeksW(today, i);
    var opt = document.createElement('option');
    opt.value = w;
    opt.textContent = weekLabelW(w) + ' (' + fmtWeekRangeW(w) + ')';
    sel.appendChild(opt);
  }
  document.getElementById('weekPickerBg').style.display = 'flex';
}

function quickPickWeek(week) {
  if (!_weekPickerJobs.length) return;
  doWeekAssign(_weekPickerJobs, week);
}

function closeWeekPicker() {
  document.getElementById('weekPickerBg').style.display = 'none';
  _weekPickerJobs = [];
}

function confirmWeekPicker() {
  const week = document.getElementById('wpWeekSelect').value;
  if (!week || !_weekPickerJobs.length) return;
  doWeekAssign(_weekPickerJobs, week);
}

function doWeekAssign(jobs, week) {
  fetch('/api/schedule/batch-assign', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ jobs: jobs, week: week })
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      jobs.forEach(j => {
        _scheduleData[j] = { week: week, carryover: false, original_week: null, done: false };
      });
      showToastW(jobs.length + ' item' + (jobs.length > 1 ? 's' : '') + ' scheduled for ' + weekLabelW(week));
      _drillSelected.clear();
      _allSelectMode = false;
      var selBtn = document.getElementById('wdselall');
      if (selBtn) selBtn.textContent = '\u2610 Select All';
      updateDrillSelectUI();
      closeWeekPicker();
      renderBoard();
      if (_drillStage) renderDrill();
    }
  }).catch(e => console.error('Batch assign failed', e));
}

function showToastW(msg) {
  let t = document.getElementById('wtoast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'wtoast';
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1e3a2a;color:#5ae8a8;border:1px solid #3a6a4a;padding:12px 24px;border-radius:8px;font-weight:700;font-size:.95em;z-index:300;transition:opacity .3s';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.style.opacity = '1';
  t.style.display = 'block';
  clearTimeout(t._timer);
  t._timer = setTimeout(function(){ t.style.opacity = '0'; setTimeout(function(){ t.style.display = 'none'; }, 300); }, 3000);
}

function addOneToWeek(job, ev) {
  if (ev) ev.stopPropagation();
  openWeekPicker([job]);
}

function loadData(){
  fetch('/api/wip').then(r=>r.json()).then(d=>{
    if(d.error){
      document.getElementById('werr').style.display='block';
      document.getElementById('werr').textContent='â  '+d.error;
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
      document.getElementById('wlive').textContent='â Live \u00B7 Updated '+new Date(d.updated).toLocaleTimeString();
    }
  }).catch(()=>{
    document.getElementById('werr').style.display='block';
    document.getElementById('werr').textContent='â  Cannot reach server.';
  });
}
loadData();
setInterval(loadData,60000);
</script>
</body>
</html>"""

# ââ KPI Page HTML ââââââ&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
KPI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KPI Tracker &mdash; Pyrology</title>
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
.kd-sprue{background:#2a1f1a;color:#c97a3b;border:1px solid #8a5a2a}
.kd-small_sprue{background:#2a1f1a;color:#c97a3b;border:1px solid #8a5a2a}
.kd-monument_sprue{background:#3a1a4a;color:#c9a0f0;border:1px solid #7a4aaa}
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
    <div style="font-size:1.6em">&#x1F4CA;</div>
    <h1>KPI TRACKER<span>Weekly Production Value &mdash; Per Department</span></h1>
  </div>
  <div class="nav-links" style="display:flex;gap:8px">
    <a href="/" class="nav-link">&#x1F3ED; Dashboard</a>
    <a href="/schedule" class="nav-link" style="color:#5ae8a8;border-color:#2a5a3a">&#x1F4C5; Schedule</a>
    <a href="/maintenance" class="nav-link" style="color:#e8a838;border-color:#6a4a1a">&#x1F527; Maintenance</a>
    <a href="/shipping" class="nav-link" style="color:#7aa8e8;border-color:#3a5a8a">&#x1F4E6; Shipping</a>
  </div>
</div>
<div id="kbody">
  <div class="week-banner">
    <div>
      <div class="week-banner h2" id="kweek-label" style="font-size:1.1em;font-weight:700;color:#4db8b8">Loading...</div>
      <div class="week-sub" id="kweek-sub"></div>
    </div>
    <div style="display:flex;align-items:center;gap:14px">
      <div style="font-size:.82em;color:#888">Total this week: <span id="ktotal-week" style="color:#4db8b8;font-weight:700;font-size:1.2em">&mdash;</span></div>
      <button class="btn-close-week" onclick="closeWeek()">&#x1F512; Close Week</button>
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
    <input type="password" id="pin-input" maxlength="10" placeholder="&bull;&bull;&bull;&bull;" autocomplete="off">
    <div class="pin-error" id="pin-error"></div>
    <div class="pin-btns">
      <button class="pin-cancel" onclick="closePin()">Cancel</button>
      <button class="pin-confirm" id="pin-confirm-btn" onclick="submitPin()">Confirm</button>
    </div>
  </div>
</div>

<script>
const DEPT_LABELS = {
  waxpull:'Wax Pull', waxchase:'Wax Chase', small_sprue:'Small Sprue', monument_sprue:'Monument Sprue',
  shell:'Shell Room', small_metal:'Small Metal', monument_metal:'Monument Metal',
  patina:'Patina', base:'Base', ready:'Ready'
};
const DEPT_ORDER = ['waxpull','waxchase','small_sprue','monument_sprue','shell','small_metal','monument_metal','patina','base','ready'];

function fmt(v){if(!v)return'$0';return'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:0});}

function fmtDate(iso){
  if(!iso)return'&mdash;';
  const d=new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric'})+'  '+d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});
}

function weekRange(startIso){
  if(!startIso)return'';
  const s=new Date(startIso+'T00:00:00');
  const e=new Date(s); e.setDate(e.getDate()+6);
  const opts={month:'short',day:'numeric'};
  return s.toLocaleDateString('en-US',opts)+' - '+e.toLocaleDateString('en-US',{...opts,year:'numeric'});
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

  // entries table (newest first) &mdash; track original index for API calls
  const indexed=entries.map((e,i)=>({...e,_idx:i}));
  const sorted=indexed.sort((a,b)=>b.completed_at.localeCompare(a.completed_at));
  document.getElementById('kentries-body').innerHTML = sorted.length
    ? sorted.map(e=>`<tr data-idx="${e._idx}">
        <td style="color:#888">#${e.job}</td>
        <td><strong>${e.name||'&mdash;'}</strong></td>
        <td>${e.customer||'&mdash;'}</td>
        <td><span class="ktdept kd-${e.dept}">${DEPT_LABELS[e.dept]||e.dept}</span></td>
        <td class="ktval" id="kval-${e._idx}">${fmt(e.value)}</td>
        <td style="color:#888;font-size:.85em" id="knote-${e._idx}">${e.note||''}</td>
        <td style="color:#666;font-size:.85em">${fmtDate(e.completed_at)}</td>
        <td class="kpi-actions">
          <button class="kpi-btn" onclick="editEntry(${e._idx})" title="Edit value/note">&#x270F;&#xEF;&#xB8;&#x8F;</button>
          <button class="kpi-btn del" onclick="deleteEntry(${e._idx})" title="Delete entry">&#x2715;</button>
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
            <span class="hw-total">${fmt(wTotal)} &middot; ${wEntries.length} items</span>
            <div class="hw-actions">
              <button class="hw-btn reopen" onclick="reopenWeek(${origIdx},'${wLabel.replace(/'/g,"\\'")}')">&#x1F513; Reopen</button>
              <button class="hw-btn del" onclick="deleteWeek(${origIdx},'${wLabel.replace(/'/g,"\\'")}')">&#x1F5D1; Delete</button>
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
  actTd.innerHTML=`<button class="kpi-btn" onclick="saveEntry(${idx})" style="color:#5a9e5a;border-color:#3a6a3a" title="Save">&#10003;</button><button class="kpi-btn" onclick="loadKPI()" title="Cancel">&#x2715;</button>`;
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

/* &#x2500;&#x2500; PIN modal helpers &#x2500;&#x2500; */
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

# ââ Maintenance Request HTML âââââ&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
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
    <a href="/schedule" class="nav-link" style="color:#5ae8a8;border-color:#2a5a3a">&#x1F4C5; Schedule</a>
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
          <option value="">&mdash; Select &mdash;</option>
          <option value="Wax Pull">Wax Pull</option>
          <option value="Wax Chase">Wax Chase</option>
          <option value="Sprue">Sprue</option>
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
          <option value="low">Low &mdash; Can wait</option>
          <option value="medium" selected>Medium &mdash; Needs attention soon</option>
          <option value="high">High &mdash; Affecting production</option>
          <option value="critical">Critical &mdash; Production stopped</option>
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
<title>Shipping Requests &mdash; Pyrology</title>
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

/* &#x2500;&#x2500; Toggle form &#x2500;&#x2500; */
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

/* &#x2500;&#x2500; Board layout &#x2500;&#x2500; */
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

/* &#x2500;&#x2500; Cards &#x2500;&#x2500; */
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

/* &#x2500;&#x2500; Edit overlay &#x2500;&#x2500; */
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

/* &#x2500;&#x2500; Responsive &#x2500;&#x2500; */
@media(max-width:900px){.board{flex-direction:column;}.board-col{flex:none;width:100%;max-height:none;}}
</style>
</head>
<body>

<div id="shdr">
  <div style="display:flex;align-items:center;gap:14px;">
    <div style="font-size:1.5em">&#x1F4CB;</div>
    <h1>SHIPPING REQUESTS<span>Client Shipping Request Board</span></h1>
  </div>
  <div class="nav-links">
    <a href="/" class="nav-link">&#x1F3ED; Dashboard</a>
    <a href="/schedule" class="nav-link" style="color:#5ae8a8;border-color:#2a5a3a">&#x1F4C5; Schedule</a>
    <a href="/kpi" class="nav-link">&#x1F4CA; KPI</a>
    <a href="/maintenance" class="nav-link" style="color:#e8a838;border-color:#6a4a1a">&#x1F527; Maintenance</a>
  </div>
</div>

<button class="toggle-form-btn" onclick="toggleForm()">&#x2795; New Shipping Request</button>

<div class="ship-form" id="reqForm">
  <h3 style="margin-bottom:4px;">&#x1F4E6; Add Shipping Request</h3>
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
      <textarea id="sf-items" placeholder="List what the client wants shipped &mdash; e.g. 2x Bronze plaques, 1x Granite base, 3x Engraved panels..."></textarea>
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
        <option value="">&mdash; Select &mdash;</option>
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
  <button class="btn-submit" onclick="submitRequest()" style="margin-top:14px">&#10003; Submit Request</button>
</div>

<div class="board-wrapper">
  <div class="board" id="board"></div>
</div>
<div id="editRoot"></div>

<script>
let _shipments=[];
const STATUSES=['requested','approved','packed','shipped'];
const STATUS_CFG={
  requested:{icon:'&#x1F4DD;',label:'Requested'},
  approved:{icon:'&#x2705;',label:'Approved'},
  packed:{icon:'&#x1F4E6;',label:'Packed'},
  shipped:{icon:'&#x1F69A;',label:'Shipped'}
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
  const carrierOpts=CARRIERS.map(c=>`<option value="${c}"${c===s.carrier?' selected':''}>${c||'&mdash; Select &mdash;'}</option>`).join('');
  document.getElementById('editRoot').innerHTML=`
  <div class="edit-overlay" onclick="if(event.target===this)closeEdit()">
    <div class="edit-panel">
      <h3>&#x270F;&#xEF;&#xB8;&#x8F; Edit Shipment &mdash; ${s.job}</h3>
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
        const items=s.items_requested||s.instructions||'&mdash;';
        html+=`<div class="req-card">
          <div class="c-job">${s.job}</div>
          <div class="c-client">${s.client}</div>
          ${s.client_email?`<div class="c-row" style="margin-bottom:6px"><span>&#x1F4E7;</span><b style="color:#7aa8e8">${s.client_email}</b></div>`:''}
          <div class="c-items">${items}</div>
          <div class="c-row"><span>Ship To:</span><b>${s.ship_to||'&mdash;'}</b></div>
          <div class="c-row"><span>Date:</span><b>${s.ship_date||'&mdash;'}</b></div>
          ${s.carrier?`<div class="c-row"><span>Carrier:</span><b>${s.carrier}</b></div>`:''}
          ${s.tracking?`<div class="c-row"><span>Tracking:</span><b>${s.tracking}</b></div>`:''}
          ${s.packages&&s.packages>1?`<div class="c-row"><span>Pkgs:</span><b>${s.packages}</b></div>`:''}
          ${s.weight?`<div class="c-row"><span>Weight:</span><b>${s.weight}</b></div>`:''}
          ${s.instructions?`<div class="c-row" style="margin-top:4px;padding-top:6px;border-top:1px solid #2e3e52"><span>&#x1F4CB; Notes:</span><b style="white-space:pre-wrap">${s.instructions}</b></div>`:''}
          <div class="c-actions">
            <select onchange="updateStatus(${s.id},this.value)">
              ${STATUSES.map(o=>`<option value="${o}"${o===st?' selected':''}>${STATUS_CFG[o].icon} ${STATUS_CFG[o].label}</option>`).join('')}
            </select>
            <button class="btn-edit" onclick="openEdit(${s.id})">&#x270F;&#xEF;&#xB8;&#x8F;</button>
            <button class="btn-del" onclick="deleteRequest(${s.id})">&#x2715;</button>
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

# &#x2500;&#x2500; Flask app &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
app = Flask(__name__)
CORS(app, origins='*')



# --- Nav injection: Mobile + TV + Rework buttons (server-rendered into every desktop page) ---
NAV_INJECT_HTML = '<a href="/m/" style="padding:5px 13px;border-radius:5px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid #3a4a6a;background:#1e2a3a;color:#4fd1c5;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;margin-right:4px">\U0001F4F1 Mobile</a><a href="/tv" target="_blank" style="padding:5px 13px;border-radius:5px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid #3a4a6a;background:#1e2a3a;color:#4fd1c5;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;margin-right:4px">\U0001F4FA TV</a><a href="/rework" style="padding:5px 13px;border-radius:5px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid #3a4a6a;background:#1e2a3a;color:#4fd1c5;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;margin-right:4px">\U0001F527 Rework</a>'

@app.after_request
def inject_mobile_btn(response):
    if response.content_type and 'text/html' in response.content_type and not request.path.startswith('/m/') and not request.path.startswith('/tv'):
        data = response.get_data(as_text=True)
        body_idx = data.find('<body>')
        if body_idx >= 0:
            after_body = body_idx + 6
            first_a = data.find('<a ', after_body)
            if first_a >= 0:
                data = data[:first_a] + NAV_INJECT_HTML + data[first_a:]
                response.set_data(data)
    return response

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
        # KPI tracking: credit the incremental percentage of the item value
        if item and pct > pct_old:
            price = item.get('price') or 0
            increment = pct - pct_old
            credited_value = round(price * increment / 100, 2)
            dept = 'monument_metal' if item.get('monument') else 'small_metal'
            note = f'{pct}% complete' if pct == 100 else f'{pct_old}%&rarr;{pct}% ({increment}% of value)'
            _record_kpi_entry(job, item, credited_value, dept, note)
        return jsonify({'ok': True, 'job': job, 'pct': pct})
    except Exception as e:
        log.error(f'Override failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/move-items', methods=['POST'])
def move_items():
    global _dt_pending_id
    try:
        body = request.get_json(force=True)
        jobs = body.get('jobs', [])
        target_stage = body.get('targetStage', '')
        piece_ids = body.get('pieceIds', [])
        dt_status_id = body.get('dtStatusId')

        # Auto-assign to next week unless caller explicitly says not to
        auto_week = body.get('autoWeek', True)

        # Update local cache
        with _lock:
            for item in _cache.get('items', []):
                if item['job'] in jobs:
                    item['stage'] = target_stage

        # Auto-assign moved items to next week on department change
        # User can manually drag them back to current week afterward
        reassigned = []
        if auto_week:
            this_monday = _get_week_monday()
            next_monday = (datetime.strptime(this_monday, '%Y-%m-%d') + timedelta(days=7)).strftime('%Y-%m-%d')
            with _lock:
                assignments = _schedule_data.setdefault('assignments', {})
                for j in jobs:
                    existing = assignments.get(j, {})
                    cur_week = existing.get('week', '')
                    assignments[j] = {
                        'week': next_monday,
                        'carryover': False,
                        'original_week': existing.get('original_week') or cur_week or None,
                        'done': False,
                    }
                    reassigned.append(j)
            if reassigned:
                _save_schedule()
                log.info(f'Auto-assigned {len(reassigned)} moved items to next week ({next_monday})')

        # Sync to DithTracker &mdash; always queue for browser worker (server-side cookies are IP-bound)
        queued = False
        if piece_ids and dt_status_id:
            int_pieces = [int(p) for p in piece_ids if p]
            int_status = int(dt_status_id)
            _dt_pending_id += 1
            _dt_pending.append({
                'id': _dt_pending_id,
                'pieceIds': int_pieces,
                'statusId': int_status,
                'jobs': jobs,
                'targetStage': target_stage,
                'created': datetime.utcnow().isoformat() + 'Z'
            })
            queued = True

        log.info(f'Moved {len(jobs)} items to {target_stage}. DT queued: {queued}')
        return jsonify({'ok': True, 'moved': len(jobs), 'dtSynced': False, 'dtQueued': queued, 'pendingCount': len(_dt_pending), 'reassigned': len(reassigned)})
    except Exception as e:
        log.error(f'Move failed: {e}')
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
        # KPI tracking: credit the incremental percentage of the item value
        if item and pct > pct_old:
            price = item.get('price') or 0
            increment = pct - pct_old
            credited_value = round(price * increment / 100, 2)
            dept  = item.get('stage', 'unknown')
            # Metal items: differentiate small vs monument for KPI
            if dept == 'metal':
                dept = 'monument_metal' if item.get('monument') else 'small_metal'
            # Sprue items: differentiate small vs monument for KPI
            if dept == 'sprue':
                dept = 'monument_sprue' if item.get('monument') else 'small_sprue'
            note = f'{pct}% complete' if pct == 100 else f'{pct_old}%&rarr;{pct}% ({increment}% of value)'
            _record_kpi_entry(job, item, credited_value, dept, note)
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

@app.route('/dt-sync')
def dt_sync_page():
    """Serve a page that, when opened on the DithTracker tab, runs the sync worker."""
    return '''<!DOCTYPE html>
<html><head><title>DT Sync Worker</title>
<style>
  body{font-family:system-ui;background:#1a1a2e;color:#e0e0e0;padding:40px;max-width:700px;margin:0 auto}
  h1{color:#4fc3f7}
  .log{background:#0d1117;border:1px solid #333;border-radius:8px;padding:16px;font-family:monospace;font-size:13px;
       max-height:400px;overflow-y:auto;white-space:pre-wrap}
  .status{font-size:18px;margin:16px 0;padding:12px;border-radius:8px}
  .running{background:#1b5e20;border:1px solid #4caf50}
  .stopped{background:#b71c1c;border:1px solid #ef5350}
  button{background:#4fc3f7;color:#000;border:none;padding:10px 24px;border-radius:6px;font-size:15px;cursor:pointer;margin:8px 8px 8px 0}
  button:hover{background:#81d4fa}
  .warn{color:#ff9800;font-size:14px;margin:12px 0}
  code{background:#333;padding:2px 6px;border-radius:4px}
  a{color:#4fc3f7}
</style>
</head><body>
<h1>&#x1f504; DithTracker Sync Worker</h1>
<p>This script syncs department moves from the <a href="/">Pyrology Production Board</a> to DithTracker.</p>
<p class="warn">&#x26a0; <strong>Important:</strong> This must run on the <strong>DithTracker tab</strong>
(same domain as <code>dithtracker-reporting.azurewebsites.net</code>) so it can use the session cookie and XSRF token.</p>

<h3>Quick Start (Bookmarklet)</h3>
<p>Drag this link to your bookmarks bar, then click it while on the DithTracker WIP page:</p>
<p><a href="javascript:void((function(){if(window._dtSyncRunning){alert('Sync worker already running!');return;}var s=document.createElement('script');s.src='https://pyrology-wip.onrender.com/dt-sync-worker.js';document.head.appendChild(s);})())" onclick="return false" style="font-size:18px;background:#333;padding:8px 16px;border-radius:6px;text-decoration:none">&#x1f504; Start DT Sync</a></p>

<h3>Status</h3>
<div class="status stopped" id="status">Not running (open this on DithTracker tab or use bookmarklet)</div>

<h3>Log</h3>
<div class="log" id="log">Waiting...</div>

<button onclick="startSync()">Start Sync</button>
<button onclick="stopSync()" style="background:#ef5350">Stop Sync</button>

<script>
const PYROLOGY = 'https://pyrology-wip.onrender.com';
const POLL_MS = 5000;
const logEl = document.getElementById('log');
const statusEl = document.getElementById('status');

function log(msg) {
  const ts = new Date().toLocaleTimeString();
  logEl.textContent += ts + ' ' + msg + '\\n';
  logEl.scrollTop = logEl.scrollHeight;
}

async function poll() {
  try {
    const res = await fetch(PYROLOGY + '/api/dt-pending');
    if (!res.ok) { log('Poll failed: ' + res.status); return; }
    const data = await res.json();
    const pending = data.moves || [];
    if (!pending.length) return;
    log('Found ' + pending.length + ' pending move(s)');
    for (const move of pending) {
      try {
        log('Exec move id=' + move.id + ': ' + move.pieceIds.length + ' pcs -> status ' + move.statusId);
        // Use axios (loaded on DithTracker page) for XSRF token
        if (typeof axios !== 'undefined') {
          await axios.post('/api/piece/state/__bulk', { pieceIds: move.pieceIds, statusId: move.statusId });
        } else {
          // Fallback: try fetch with manual XSRF
          const xsrf = document.cookie.split(';').map(c=>c.trim()).find(c=>c.startsWith('XSRF-TOKEN='));
          const headers = {'Content-Type':'application/json'};
          if(xsrf) headers['X-XSRF-TOKEN'] = decodeURIComponent(xsrf.split('=')[1]);
          const r = await fetch('/api/piece/state/__bulk', {method:'POST', headers, body:JSON.stringify({pieceIds:move.pieceIds,statusId:move.statusId})});
          if(!r.ok) throw new Error('HTTP ' + r.status);
        }
        log('DT update OK for move ' + move.id);
        await fetch(PYROLOGY + '/api/dt-pending-done', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({id: move.id})
        });
        log('Move ' + move.id + ' done!');
      } catch(e) {
        log('Move ' + move.id + ' FAILED: ' + e.message);
      }
    }
  } catch(e) { log('Poll error: ' + e.message); }
}

function startSync() {
  if (window._dtSyncRunning) { log('Already running'); return; }
  window._dtSyncRunning = true;
  window._dtSyncInterval = setInterval(poll, POLL_MS);
  poll();
  statusEl.textContent = 'Running (polling every ' + (POLL_MS/1000) + 's)';
  statusEl.className = 'status running';
  log('Sync worker started');
}
function stopSync() {
  clearInterval(window._dtSyncInterval);
  window._dtSyncRunning = false;
  statusEl.textContent = 'Stopped';
  statusEl.className = 'status stopped';
  log('Sync worker stopped');
}
</script>
</body></html>'''

@app.route('/dt-sync-worker.js')
def dt_sync_worker_js():
    """JavaScript file that starts the DT sync worker when loaded on the DithTracker tab.
    v4: Also sends session cookies to Pyrology server every 10s for server-side sync."""
    js = '''(function(){
  if(window._dtSyncRunning){console.log('[DT-Sync] Already running');return;}
  window._dtSyncRunning=true;
  window._dtSyncLog=[];
  var PYROLOGY='https://pyrology-wip.onrender.com';
  var POLL_MS=10000;
  function log(m){var e=new Date().toLocaleTimeString()+' '+m;window._dtSyncLog.push(e);if(window._dtSyncLog.length>50)window._dtSyncLog.shift();console.log('[DT-Sync] '+e);}

  /* Send session cookies to Pyrology so the server can call DT API directly */
  async function sendSession(){
    try{
      var cookies=document.cookie;
      var xsrf='';
      var xc=cookies.split(';').map(function(c){return c.trim();}).find(function(c){return c.startsWith('XSRF-TOKEN=');});
      if(xc)xsrf=decodeURIComponent(xc.split('=')[1]);
      await fetch(PYROLOGY+'/api/dt-session',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({cookies:cookies,xsrf:xsrf})});
    }catch(e){log('Session send err:'+e.message);}
  }

  /* Poll for any pending moves that server-side sync missed (fallback) */
  async function poll(){
    try{
      var res=await fetch(PYROLOGY+'/api/dt-pending');
      if(!res.ok){log('Poll fail:'+res.status);return;}
      var data=await res.json();var pending=data.moves||[];
      if(!pending.length)return;
      log('Found '+pending.length+' pending (fallback)');
      for(var move of pending){
        try{
          log('Exec #'+move.id+': '+move.pieceIds.length+'pcs->status'+move.statusId);
          if(typeof axios!=='undefined'){await axios.post('/api/piece/state/__bulk',{pieceIds:move.pieceIds,statusId:move.statusId});}
          else{var x=document.cookie.split(';').map(function(c){return c.trim();}).find(function(c){return c.startsWith('XSRF-TOKEN=');});var h={'Content-Type':'application/json'};if(x)h['X-XSRF-TOKEN']=decodeURIComponent(x.split('=')[1]);var r=await fetch('/api/piece/state/__bulk',{method:'POST',headers:h,body:JSON.stringify({pieceIds:move.pieceIds,statusId:move.statusId})});if(!r.ok)throw new Error('HTTP '+r.status);}
          log('DT OK #'+move.id);
          await fetch(PYROLOGY+'/api/dt-pending-done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:move.id})});
          log('Done #'+move.id);
        }catch(e){log('FAIL #'+move.id+':'+e.message);}
      }
    }catch(e){log('Poll err:'+e.message);}
  }

  /* Combined tick: send session + check pending queue */
  async function tick(){await sendSession();await poll();}

  window._dtSyncInterval=setInterval(tick,POLL_MS);
  tick();
  log('Sync worker started (v4 &mdash; auto session, 10s interval)');
  var badge=document.createElement('div');
  badge.style.cssText='position:fixed;top:8px;right:8px;z-index:99999;background:#1b5e20;color:#4caf50;padding:6px 14px;border-radius:20px;font:bold 13px system-ui;cursor:pointer;border:1px solid #4caf50';
  badge.textContent='\\u{1f504} DT Sync Active';
  badge.title='Click to view sync log';
  badge.onclick=function(){alert(window._dtSyncLog.join('\\n'));};
  document.body.appendChild(badge);
})();'''
    return js, 200, {'Content-Type': 'application/javascript', 'Access-Control-Allow-Origin': '*'}

@app.route('/api/dt-session', methods=['POST', 'OPTIONS'])
def dt_session():
    """Store DithTracker session credentials for server-side sync.
    Called from the DithTracker browser tab to share cookies + XSRF token."""
    global _dt_session
    if request.method == 'OPTIONS':
        return '', 204, {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type'}
    try:
        body = request.get_json(force=True)
        cookies = body.get('cookies', '')
        xsrf = body.get('xsrf', '')
        if cookies:
            _dt_session = {
                'cookies': cookies,
                'xsrf': xsrf,
                'updated': datetime.utcnow().isoformat() + 'Z'
            }
            log.info(f'DT session stored (cookies: {len(cookies)} chars, xsrf: {"yes" if xsrf else "no"})')
            return jsonify({'ok': True, 'stored': True})
        return jsonify({'ok': False, 'error': 'no cookies provided'}), 400
    except Exception as e:
        log.error(f'DT session store failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/dt-pending', methods=['GET'])
def get_dt_pending():
    return jsonify({'moves': _dt_pending})

@app.route('/api/dt-pending-done', methods=['POST'])
def dt_pending_done():
    global _dt_pending
    body = request.get_json(force=True)
    done_id = body.get('id')
    _dt_pending = [m for m in _dt_pending if m['id'] != done_id]
    return jsonify({'ok': True, 'remaining': len(_dt_pending)})

@app.route('/api/push-wip', methods=['POST'])
def push_wip():
    """Accept WIP data. Supports batch mode:
       - append=true: add to pending batch (no publish yet)
       - finalize=true: publish the pending batch
       - neither: replace cache immediately (legacy single-push)
    """
    try:
        body = request.get_json(force=True)
        if body is None:
            return jsonify({'error': 'No JSON body'}), 400
        append   = False
        finalize = False
        if isinstance(body, dict):
            append   = body.get('append', False)
            finalize = body.get('finalize', False)
            raw      = body.get('items', body) if not finalize else []
        else:
            raw = body

        if finalize:
            with _lock:
                pending = getattr(push_wip, '_pending', [])
                _cache['items']   = pending
                _cache['error']   = None
                _cache['updated'] = datetime.utcnow().isoformat() + 'Z'
                count = len(pending)
                push_wip._pending = []
            log.info(f'Browser push finalized: {count} items total.')
            return jsonify({'ok': True, 'items': count})

        items = transform_rows(raw)

        if append:
            with _lock:
                if not hasattr(push_wip, '_pending'):
                    push_wip._pending = []
                push_wip._pending.extend(items)
            log.info(f'Browser push batch appended: {len(items)} items (pending: {len(push_wip._pending)}).')
            return jsonify({'ok': True, 'appended': len(items), 'pending': len(push_wip._pending)})
        else:
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
        log.info(f'Week closed: {current["week_start"]} &rarr; {len(current["entries"])} entries archived.')
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
        log.info(f'Week reopened: {week.get("week_start")} &mdash; {len(week.get("entries",[]))} entries restored.')
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
        log.info(f'Week deleted: {removed.get("week_start")} &mdash; {len(removed.get("entries",[]))} entries permanently removed.')
        return jsonify({'ok': True, 'deleted_week': removed.get('week_start', ''),
                        'deleted_entries': len(removed.get('entries', []))})
    except Exception as e:
        log.error(f'Delete week failed: {e}')
        return jsonify({'error': str(e)}), 500

# &#x2500;&#x2500; Maintenance routes &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
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
        log.info(f'Maintenance #{req_id} status &rarr; {status}')
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

# &#x2500;&#x2500; Shipping routes &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
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
        log.info(f'Shipment #{ship_id} status &rarr; {status}')
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

# &#x2500;&#x2500; Schedule Page HTML &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
SCHEDULE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Production Schedule &mdash; Pyrology</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%;height:100%;background:#0f1117;color:#e8e8e8;font-family:'Segoe UI',Arial,sans-serif;overflow-x:hidden;overflow-y:auto}
.top-bar{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;background:#1a1d27;border-bottom:1px solid #2a2d3a;position:sticky;top:0;z-index:10}
.top-bar h1{font-size:1.3em;font-weight:700;letter-spacing:1px;color:#fff}
.top-bar h1 span{font-size:.65em;font-weight:400;color:#888;display:block;letter-spacing:.5px}
.nav-links{display:flex;gap:8px;align-items:center}
.nav-links a{display:inline-flex;align-items:center;gap:5px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;text-decoration:none;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;letter-spacing:.5px}
.summary-bar{display:flex;gap:18px;padding:6px 16px;background:#141620;border-bottom:1px solid #2a2d3a;align-items:center;flex-wrap:wrap;position:sticky;top:52px;z-index:9}
.sstat{font-size:.82em;color:#aaa}.sstat strong{color:#fff;font-size:1.1em}
.sstat.teal strong{color:#4db8b8}.sstat.red strong{color:#e05555}
.sstat.gold strong{color:#e8a838}.sstat.green strong{color:#5a9e5a}
.dept-grid{display:flex;gap:6px;padding:8px;overflow-x:auto;overflow-y:visible;min-height:calc(100vh - 90px)}
.dept-col{flex:1;min-width:220px;background:#1a1d27;border-radius:8px;display:flex;flex-direction:column;border:1px solid #2a2d3a;overflow:visible}
.dept-hdr{padding:8px 10px 6px;text-align:center;border-radius:8px 8px 0 0;flex-shrink:0}
.dept-label{font-size:.78em;font-weight:700;letter-spacing:.8px;text-transform:uppercase}
.dept-sub{font-size:.62em;color:rgba(255,255,255,.6);text-transform:uppercase;letter-spacing:.4px}
.dept-count{font-size:1.25em;font-weight:700;color:#fff;margin-top:2px}
.dept-hrs{font-size:.76em;color:#ffd580;margin-top:2px;font-weight:600}
.dept-val{font-size:.82em;color:rgba(255,255,255,.85);margin-top:1px}
.dept-body{flex:1;padding:5px;display:flex;flex-direction:column;gap:8px}
.week-block{background:#141620;border-radius:6px;border:1px solid #2a2d3a;overflow:hidden;transition:border-color .15s}
.week-block.current{border-color:#4db8b8}
.week-block.locked{border-color:#5a9e5a;background:#0f1a0f}
.week-block.drag-over{border-color:#e8a838!important;background:#1a1a10}
.wb-hdr{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;cursor:pointer;user-select:none;font-size:.78em;font-weight:700}
.wb-hdr:hover{background:#1e2130}
.wb-hdr .wlabel{color:#4db8b8}.wb-hdr .wdates{color:#888;font-weight:400;margin-left:4px}
.wb-hdr .wstats{color:#aaa;font-weight:400}
.wb-hdr .wstats strong{color:#fff}
.locked .wb-hdr .wlabel{color:#5a9e5a}
.wb-body{padding:4px;min-height:20px}
.wb-body.collapsed{display:none}
.wb-actions{display:flex;gap:4px;padding:5px 8px;border-top:1px solid #1a1d27;align-items:center;justify-content:space-between}
.scard{background:#0f1117;border-radius:5px;padding:7px 8px;margin-bottom:4px;border-left:4px solid #333;font-size:.78em;cursor:grab;transition:opacity .15s,transform .15s,background .1s}
.scard:hover{border-left-color:#4db8b8;background:#12141e}
.scard.carry{border-left-color:#e8a838;background:#1a160f}
.scard.dragging{opacity:.35;transform:scale(.95)}
.scard.done-item{opacity:.45}
.scard.selected{background:#1a2a3a!important;outline:2px solid #4db8b8;outline-offset:-1px}
.locked .scard{cursor:pointer}
.scard .s-title{font-weight:600;color:#e8e8e8;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:1em}
.scard .s-meta{display:flex;gap:8px;color:#999;font-size:.9em;margin-top:3px;flex-wrap:wrap;align-items:center}
.scard .s-hrs{color:#ffd580;font-weight:600}.scard .s-val{color:#4db8b8;font-weight:600}
.scard .s-due{font-size:.88em;padding:2px 5px;border-radius:3px;background:#1e2230;color:#bbb}
.scard .s-due.over{background:#3d1515;color:#ff6b6b}.scard .s-due.warn{background:#3d2e10;color:#ffaa44}
.scard .s-actions{display:flex;gap:5px;margin-top:4px;align-items:center}
.btn-sm{background:#2a2d3a;border:1px solid #3a3d4a;color:#bbb;padding:3px 8px;border-radius:4px;cursor:pointer;font-size:.88em;font-weight:600;transition:all .15s}
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
/* &#x2500;&#x2500; Gantt Chart &#x2500;&#x2500; */
.gantt-wrap{width:100%;overflow-x:auto;margin-top:8px}
.gantt{display:grid;min-width:700px;font-size:.82em}
.gantt-hdr{display:contents}
.gantt-hdr .gh-label{background:#1a1d27;padding:8px 10px;font-weight:700;color:#4db8b8;border-bottom:2px solid #3a4a6a;position:sticky;left:0;z-index:2;min-width:260px}
.gantt-hdr .gh-week{background:#1a1d27;padding:8px 6px;font-weight:700;color:#889;text-align:center;border-bottom:2px solid #3a4a6a;font-size:.85em;white-space:nowrap}
.gantt-hdr .gh-week.current{color:#4db8b8;background:#1a2530}
.gantt-row{display:contents}
.gantt-row:hover .gr-label,.gantt-row:hover .gr-cell{background:#1a2130}
.gr-label{padding:6px 10px;border-bottom:1px solid #1e2230;display:flex;align-items:center;gap:8px;position:sticky;left:0;z-index:1;background:#0f1117;min-width:260px;cursor:pointer}
.gr-label .gr-job{font-weight:700;color:#e8e8e8;white-space:nowrap}
.gr-label .gr-name{color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:140px}
.gr-label .gr-client{color:#888;font-size:.88em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:80px}
.gr-cell{padding:4px 3px;border-bottom:1px solid #1e2230;background:#0f1117;position:relative;min-height:36px}
.gantt-bar{display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:4px;font-size:.88em;font-weight:600;color:#fff;white-space:nowrap;min-height:28px;cursor:pointer;transition:filter .15s}
.gantt-bar .pct-bar{position:absolute;bottom:0;left:0;height:3px;background:linear-gradient(90deg,#5ae8a8,#4db8b8);border-radius:0 0 4px 4px;transition:width .2s}
.gantt-bar.done .pct-bar{background:#5ae8a8}
.gr-cell.drag-over{background:#1a2a3a !important;box-shadow:inset 0 0 8px rgba(77,184,184,.2)}
.gantt-bar.multi-week{min-height:36px}
.gantt-bar:hover{filter:brightness(1.2)}
.gantt-bar.done{opacity:.45}
.gantt-bar.carry{border:2px dashed #e8a838}
.gantt-bar .gb-hrs{color:rgba(255,255,255,.8);font-weight:500;font-size:.9em}
.gantt-bar .gb-val{color:rgba(255,255,255,.8);font-weight:500;font-size:.9em}
.gantt-bar .gb-due{font-size:.82em;padding:1px 5px;border-radius:3px;background:rgba(0,0,0,.3)}
.gantt-bar .gb-due.over{background:#5a1515;color:#ff8888}
.gantt-bar .gb-due.warn{background:#5a3e10;color:#ffcc66}
.gantt-bar .gb-actions{display:flex;gap:4px;margin-left:auto;align-items:center}
.gantt-bar .gb-actions .btn-sm{font-size:.82em;padding:2px 6px}
.gantt-unsched{color:#666;font-style:italic;font-size:.8em;padding:4px 8px}
.gr-sel{font-size:1.1em;cursor:pointer;flex-shrink:0}
.gantt-row.selected .gr-label{background:#1a2a3a}
.gantt-row.selected .gr-cell{background:#1a2a3a}
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
    <div style="font-size:1.6em">&#x1F4C5;</div>
    <h1>PRODUCTION SCHEDULE<span>Click cards to select &middot; Drag or batch-move between weeks &middot; PIN to lock/unlock weeks</span></h1>
  </div>
  <div class="nav-links">
    <a href="/">&#x1F3ED; Dashboard</a>
    <a href="/kpi">&#x1F4CA; KPI</a>
    <a href="/maintenance">&#x1F527; Maintenance</a>
    <a href="/shipping">&#x1F4E6; Shipping</a>
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
    <input type="password" id="pin-input" maxlength="4" placeholder="&middot;&middot;&middot;&middot;" autocomplete="off">
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
      <div id="sdweekfilter" style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:2px"></div>
      <div id="sdtools">
        <input id="sdsearch" placeholder="Search pieces..." type="text"/>
        <button class="wdbtn active" id="sdsortdue">Sort: Due Date</button>
        <button class="wdbtn" id="sdsorttier" style="display:none">Sort: Tier</button>
        <button class="wdbtn" id="sdsortval">Sort: Value &#x2193;</button>
        <button class="wdbtn" id="sdsortname">Sort: Name</button>
        <button class="wdbtn" id="sdsortpri">Sort: Priority</button>
        <button id="sdselall" style="background:#1e3a2a;border:1px solid #3a6a4a;color:#5ae8a8;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;cursor:pointer">\u2610 Select All</button>
        <button id="sdschedbtn" style="display:none;background:#1e2a3a;border:1px solid #3a6a4a;color:#4db8b8;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;cursor:pointer">\uD83D\uDCC5 Schedule (0)</button>
        <span id="sdmovewrap" style="display:none;align-items:center;gap:4px">
          <select id="sdmovedest" style="background:#0f1117;border:1px solid #3a4a6a;color:#e8e8e8;padding:4px 8px;border-radius:4px;font-size:.82em;cursor:pointer"></select>
          <button id="sdmovebtn" style="background:#3a1e2a;border:1px solid #6a3a5a;color:#e05580;padding:5px 13px;border-radius:5px;font-size:.82em;font-weight:700;cursor:pointer">\u27A1 Move (0)</button>
        </span>
        <button id="sdback" style="background:#3a1e1e;border:1px solid #6a3a3a;color:#e05555;padding:6px 16px;border-radius:5px;cursor:pointer;font-weight:700">\u2190 Back</button>
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
  {k:'waxchase',c:'#d4763b',l:'Wax Chase',hKey:['hWax']},
  {k:'sprue_small',c:'#c97a3b',l:'Small Sprue',hKey:['hSprue'],filter:i=>i.stage==='sprue'&&!i.monument},
  {k:'sprue_mon',c:'#7b5ea7',l:'Monument Sprue',hKey:['hSprue'],filter:i=>i.stage==='sprue'&&!!i.monument},
  {k:'shell',c:'#5a9e6f',l:'Shell',hKey:[]},
  {k:'metal_small',c:'#8b9dc3',l:'Small Metal',hKey:['hMetal'],filter:i=>i.stage==='metal'&&!i.monument},
  {k:'metal_mon',c:'#7b5ea7',l:'Monument Metal',hKey:['hMetal'],filter:i=>i.stage==='metal'&&!!i.monument},
  {k:'patina',c:'#c45c8a',l:'Patina',hKey:['hPatina']},
  {k:'base',c:'#4db8b8',l:'Base',hKey:['hBasing']},
  {k:'ready',c:'#5a9e5a',l:'Ready',hKey:[]}
];
const STAGE_MAP=Object.fromEntries(STAGES.map(s=>[s.k,s]));
STAGE_MAP['metal']={k:'metal',c:'#8b9dc3',l:'Metal Work',hKey:['hMetal']}; // alias for drill-down
STAGE_MAP['sprue']={k:'sprue',c:'#c97a3b',l:'Sprue',hKey:['hSprue']}; // alias for drill-down
const fmt=v=>v?new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v):'&mdash;';
const fmtHrs=h=>h?h.toFixed(1)+'h':'&mdash;';

let _items=[], _assignments={}, _priorities={};
let _metalOverrides={}, _stageOverrides={}; // from /api/wip
let _lockedWeeks={}; // 'dept-week' => true
let _selected=new Set(); // selected job ids (for multi-select in locked weeks)
let _dragJob=null;
let _pendingAction=null; // {type:'lock'|'unlock'|'move', dept, week, jobs:[]}
let _drillStage=null, _drillSort='due', _drillWeek=null; // drill-down state (week=null means all)
let _sdSelected=new Set(); // selected jobs in schedule drill-down
let _sdAllSelect=false;
const TIER_STAGES=['waxpull','waxchase','sprue','metal','patina'];

// Schedule-page STAGES list used for "move to dept" dropdown (flat, no filters)
const MOVE_DEPTS=[
  {k:'molds',l:'Molds'},{k:'creation',l:'Creation'},{k:'waxpull',l:'Wax Pull'},
  {k:'waxchase',l:'Wax Chase'},{k:'sprue',l:'Sprue'},{k:'shell',l:'Shell/Pouryard'},
  {k:'metal',l:'Metal Work'},{k:'patina',l:'Patina'},{k:'base',l:'Base'},{k:'ready',l:'Ready'}
];

// DithTracker status mapping for each Pyrology stage
const DT_STATUS_MAP={
  molds:18, creation:47, waxpull:2, waxchase:55, sprue:56,
  shell:78, metal:62, patina:79, base:22, ready:7
};

// ========== DRILL-DOWN HELPERS ==========
function dueLabel(d){
  if(!d)return{t:'&mdash;',c:''};
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
    '<button class="pri-btn'+(p===1?' p1':'')+'" onclick="event.stopPropagation();cyclePriTo(\''+job+'\','+(p===1?0:1)+')" title="Urgent">\uD83D\uDD34</button>'+
    '<button class="pri-btn'+(p===2?' p2':'')+'" onclick="event.stopPropagation();cyclePriTo(\''+job+'\','+(p===2?0:2)+')" title="High">\uD83D\uDFE1</button>'+
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
  // Default: 0% &mdash; completion is only tracked via explicit overrides
  return 0;
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
  if(!items.length)return'&mdash;';
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

  let html='<table class="wdt"><thead><tr><th style="width:30px"></th><th style="width:100px">Priority</th><th style="width:70px">Piece #</th><th style="min-width:180px">Description</th><th style="width:120px">Client</th><th style="width:80px">Edition</th><th style="width:90px">Due</th><th style="width:80px" class="tdval">Value</th><th style="width:70px" class="tdhrs">Hrs Bid</th><th style="width:200px">Progress</th><th style="width:80px">Done</th><th style="width:100px">Schedule</th></tr></thead><tbody>';

  if(small.length){
    html+='<tr style="background:#0a0f15"><td colspan="12" style="padding:6px 8px;color:#4db8b8;font-weight:700;font-size:.9em">Small / Regular</td></tr>';
    small.forEach(i=>{
      const due=dueLabel(i.due);
      const hrs=itemHours(i);
      const a=_assignments[i.job]||{};
      const isSel=_sdSelected.has(i.job);
      const weekLbl=a.week?fmtWeekRange(a.week):'';
      html+='<tr><td><span class="sd-cb" data-job="'+i.job+'" onclick="toggleSdSelect(\''+i.job+'\')" style="cursor:pointer;font-size:1.2em;color:'+(isSel?'#5ae8a8':'#555')+'">'+(isSel?'\u2611':'\u2610')+'</span></td><td class="tdpri">'+priBtns(i.job)+'</td><td class="tdpieces">#'+i.job+'</td><td class="tddesc">'+i.name+'</td><td class="tdclient">'+i.customer+'</td><td class="tdedition">'+((i.edition||'1')+' ed')+'</td><td class="tddue '+due.c+'">'+due.t+'</td><td class="tdval">'+fmt(i.price)+'</td><td class="tdhrs">'+fmtHrs(hrs)+'</td><td class="tdprog">'+pctBars(i)+'</td><td class="tddone"><button class="btn-complete'+(a.done?' active':'')+'" onclick="event.stopPropagation();toggleDoneMetalItem(\''+i.job+'\')">'+
        (a.done?'\u2713 Done':'Done')+'</button></td>'+
        '<td><button onclick="event.stopPropagation();sdScheduleOne(\''+i.job+'\')" style="padding:3px 8px;background:'+(a.week?'#1e3a2a':'#1e2a3a')+';border:1px solid '+(a.week?'#3a6a4a':'#3a4a6a')+';color:'+(a.week?'#5ae8a8':'#4db8b8')+';border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">'+(a.week?'\u2713 '+weekLbl:'\uD83D\uDCC5 Schedule')+'</button></td></tr>';
    });
  }

  if(mon.length){
    html+='<tr style="background:#0a0f15"><td colspan="12" style="padding:6px 8px;color:#c45c8a;font-weight:700;font-size:.9em">Monuments</td></tr>';
    mon.forEach(i=>{
      const due=dueLabel(i.due);
      const hrs=itemHours(i);
      const a=_assignments[i.job]||{};
      const isSel=_sdSelected.has(i.job);
      const weekLbl=a.week?fmtWeekRange(a.week):'';
      html+='<tr><td><span class="sd-cb" data-job="'+i.job+'" onclick="toggleSdSelect(\''+i.job+'\')" style="cursor:pointer;font-size:1.2em;color:'+(isSel?'#5ae8a8':'#555')+'">'+(isSel?'\u2611':'\u2610')+'</span></td><td class="tdpri">'+priBtns(i.job)+'</td><td class="tdpieces">#'+i.job+'</td><td class="tddesc">'+i.name+'</td><td class="tdclient">'+i.customer+'</td><td class="tdedition">'+((i.edition||'1')+' ed')+'</td><td class="tddue '+due.c+'">'+due.t+'</td><td class="tdval">'+fmt(i.price)+'</td><td class="tdhrs">'+fmtHrs(hrs)+'</td><td class="tdprog">'+pctBars(i)+'</td><td class="tddone"><button class="btn-complete'+(a.done?' active':'')+'" onclick="event.stopPropagation();toggleDoneMetalItem(\''+i.job+'\')">'+
        (a.done?'\u2713 Done':'Done')+'</button></td>'+
        '<td><button onclick="event.stopPropagation();sdScheduleOne(\''+i.job+'\')" style="padding:3px 8px;background:'+(a.week?'#1e3a2a':'#1e2a3a')+';border:1px solid '+(a.week?'#3a6a4a':'#3a4a6a')+';color:'+(a.week?'#5ae8a8':'#4db8b8')+';border-radius:4px;cursor:pointer;font-size:.78em;white-space:nowrap">'+(a.week?'\u2713 '+weekLbl:'\uD83D\uDCC5 Schedule')+'</button></td></tr>';
    });
  }

  html+='</tbody></table>';
  return html;
}
function renderDrill(){
  if(!_drillStage)return;
  const stg=STAGE_MAP[_drillStage];
  if(!stg)return;

  const allDeptItems=_items.filter(i=>i.stage===_drillStage);

  // Build week filter buttons
  const weekSet=new Set();
  allDeptItems.forEach(i=>{const a=_assignments[i.job];if(a&&a.week)weekSet.add(a.week);});
  const weekList=Array.from(weekSet).sort();
  const unscheduledCount=allDeptItems.filter(i=>!_assignments[i.job]||!_assignments[i.job].week).length;
  let wfHtml='<button class="wdbtn'+(!_drillWeek?' active':'')+'" onclick="_drillWeek=null;renderDrill()" style="font-size:.75em;padding:3px 10px">All ('+allDeptItems.length+')</button>';
  weekList.forEach(w=>{
    const wCount=allDeptItems.filter(i=>_assignments[i.job]&&_assignments[i.job].week===w).length;
    const isActive=_drillWeek===w;
    wfHtml+='<button class="wdbtn'+(isActive?' active':'')+'" onclick="_drillWeek=\''+w+'\';renderDrill()" style="font-size:.75em;padding:3px 10px">'+weekLabel(w)+' ('+wCount+')</button>';
  });
  if(unscheduledCount){
    wfHtml+='<button class="wdbtn'+(_drillWeek==='unscheduled'?' active':'')+'" onclick="_drillWeek=\'unscheduled\';renderDrill()" style="font-size:.75em;padding:3px 10px;color:#888">Unscheduled ('+unscheduledCount+')</button>';
  }
  document.getElementById('sdweekfilter').innerHTML=wfHtml;

  // Filter items by selected week
  let deptItems=allDeptItems;
  if(_drillWeek==='unscheduled'){
    deptItems=allDeptItems.filter(i=>!_assignments[i.job]||!_assignments[i.job].week);
  } else if(_drillWeek){
    deptItems=allDeptItems.filter(i=>_assignments[i.job]&&_assignments[i.job].week===_drillWeek);
  }
  const sorted=sortDrillItems(deptItems);

  const doneCount=sorted.filter(i=>_assignments[i.job]&&_assignments[i.job].done).length;
  const totalHrs=sorted.reduce((a,i)=>a+itemHours(i),0);
  const doneHrs=sorted.filter(i=>_assignments[i.job]&&_assignments[i.job].done).reduce((a,i)=>a+itemHours(i),0);
  const totalVal=sorted.reduce((a,i)=>a+(i.price||0),0);
  const doneVal=sorted.filter(i=>_assignments[i.job]&&_assignments[i.job].done).reduce((a,i)=>a+(i.price||0),0);

  document.getElementById('sdtitle').textContent=stg.l+(_drillWeek&&_drillWeek!=='unscheduled'?' &mdash; '+weekLabel(_drillWeek):_drillWeek==='unscheduled'?' &mdash; Unscheduled':'');

  // Split items into monument and small groups
  const monItems=sorted.filter(i=>i.monument);
  const smallItems=sorted.filter(i=>!i.monument);
  const hasMonAndSmall=monItems.length>0&&smallItems.length>0;

  // Build health bar section &mdash; separate bars for monument/small when both exist
  let healthHtml='';
  if(hasMonAndSmall){
    const monStats=deptPctCalc(monItems);
    const smallStats=deptPctCalc(smallItems);
    healthHtml=
      '<div class="sdstat" style="min-width:200px">'+
        '<div style="font-size:.68em;color:#c45c8a;font-weight:700;letter-spacing:.5px;margin-bottom:2px">MONUMENTS ('+monItems.length+')</div>'+
        healthBarHtml(monStats,'#c45c8a',false)+
      '</div>'+
      '<div class="sdstat" style="min-width:200px">'+
        '<div style="font-size:.68em;color:'+stg.c+';font-weight:700;letter-spacing:.5px;margin-bottom:2px">SMALLS ('+smallItems.length+')</div>'+
        healthBarHtml(smallStats,stg.c,false)+
      '</div>';
  } else {
    const drillStats=deptPctCalc(sorted);
    healthHtml='<div class="sdstat" style="min-width:180px">'+healthBarHtml(drillStats,stg.c,false)+'</div>';
  }

  document.getElementById('sdstats').innerHTML=
    '<div class="sdstat">Items: <strong>'+doneCount+'/'+sorted.length+'</strong></div>'+
    '<div class="sdstat">Hours: <strong>'+fmtHrs(doneHrs)+'/'+fmtHrs(totalHrs)+'</strong></div>'+
    '<div class="sdstat">Value: <strong>'+fmt(doneVal)+'/'+fmt(totalVal)+'</strong></div>'+
    healthHtml;

  // Update sort buttons
  document.querySelectorAll('#sdtools .wdbtn').forEach(b=>{
    b.classList.remove('active');
    if(b.id==='sdsortdue'&&_drillSort==='due')b.classList.add('active');
    if(b.id==='sdsorttier'&&_drillSort==='tier')b.classList.add('active');
    if(b.id==='sdsortval'&&_drillSort==='val')b.classList.add('active');
    if(b.id==='sdsortname'&&_drillSort==='name')b.classList.add('active');
    if(b.id==='sdsortpri'&&_drillSort==='pri')b.classList.add('active');
  });

    document.getElementById('sdtable').innerHTML=renderDrillGantt(sorted,stg);

}

/* &#x2500;&#x2500; Gantt chart renderer for drill-down &#x2500;&#x2500; */
function renderDrillGantt(sorted,stg){
  const today=getMonday(new Date().toISOString().slice(0,10));
  const allDeptItems=_items.filter(i=>i.stage===_drillStage);
  const weekSet=new Set();
  allDeptItems.forEach(i=>{
    const a=_assignments[i.job];
    if(a&&a.week)weekSet.add(a.week);
    if(a&&a.endWeek)weekSet.add(a.endWeek);
  });
  for(let n=0;n<6;n++)weekSet.add(addWeeks(today,n));
  const weeks=Array.from(weekSet).sort();

  const colTemplate='260px '+weeks.map(()=>'1fr').join(' ');
  let html='<div class="gantt-wrap"><div class="gantt" style="grid-template-columns:'+colTemplate+'">';

  // Header
  html+='<div class="gantt-hdr"><div class="gh-label">Item</div>';
  weeks.forEach(w=>{
    const isCur=w===today;
    html+='<div class="gh-week'+(isCur?' current':'')+'">'+weekLabel(w)+'<br><span style="font-size:.82em;font-weight:400;color:#667">'+fmtWeekRange(w)+'</span></div>';
  });
  html+='</div>';

  const isMetal=(_drillStage==='metal');

  function renderItemRow(i){
    const a=_assignments[i.job]||{};
    const isSel=_sdSelected.has(i.job);
    const pct=a.pct||0;
    const isDone=pct>=100||a.done;
    const due=dueLabel(i.due);
    const hrs=itemHours(i);
    const assignedWeek=a.week||null;
    const endWeek=a.endWeek||null;
    const isMon=i.monument;
    const isMulti=isMon&&isMetal&&endWeek&&endWeek>assignedWeek;
    const barColor=isMon&&isMetal?'#c45c8a':stg.c;
    const priHtml=priTag(i.job);

    html+='<div class="gantt-row'+(isSel?' selected':'')+'">';

    // Label cell &mdash; draggable
    html+='<div class="gr-label" draggable="true" ondragstart="event.dataTransfer.setData(\'text/plain\',\''+i.job+'\')" onclick="toggleSdSelect(\''+i.job+'\')">';
    html+='<span class="gr-sel sd-cb" data-job="'+i.job+'" style="color:'+(isSel?'#5ae8a8':'#555')+'">'+(isSel?'\u2611':'\u2610')+'</span>';
    html+='<span class="gr-job">#'+i.job+'</span>';
    html+='<span class="gr-name" title="'+i.name+'">'+i.name+'</span>';
    html+='<span class="gr-client" title="'+i.customer+'">'+i.customer+'</span>';
    if(isMon)html+='<span style="color:#c45c8a;font-weight:700;font-size:.72em;background:#2a1525;padding:1px 4px;border-radius:3px">MON</span>';
    html+=priHtml;
    html+='</div>';

    if(isMulti){
      // Multi-week bar for monument
      const si=weeks.indexOf(assignedWeek);
      const ei=weeks.indexOf(endWeek);
      // Empty cells before bar
      for(let w=0;w<si;w++){
        html+='<div class="gr-cell" ondragover="event.preventDefault();this.classList.add(\'drag-over\')" ondragleave="this.classList.remove(\'drag-over\')" ondrop="this.classList.remove(\'drag-over\');sdDropJob(event,\''+weeks[w]+'\')"></div>';
      }
      // Spanning bar cell
      const span=ei-si+1;
      html+='<div class="gr-cell" style="grid-column:span '+span+';padding:2px">';
      html+='<div class="gantt-bar multi-week'+(isDone?' done':'')+'" style="background:'+barColor+';display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:4px;font-size:.85em;font-weight:600;color:#fff;white-space:nowrap;cursor:move;position:relative" draggable="true" ondragstart="event.dataTransfer.setData(\'text/plain\',\''+i.job+'\')" onclick="event.stopPropagation()">';
      html+='<div class="pct-bar" style="width:'+pct+'%"></div>';
      if(hrs)html+='<span class="gb-hrs" style="position:relative;z-index:1">'+fmtHrs(hrs)+'</span>';
      if(i.price)html+='<span class="gb-val" style="position:relative;z-index:1">'+fmt(i.price)+'</span>';
      if(due.t!=='&mdash;')html+='<span class="gb-due '+due.c+'" style="position:relative;z-index:1">'+due.t+'</span>';
      // Pct buttons
      html+='<span style="position:relative;z-index:1;margin-left:auto;display:flex;gap:2px">';
      [0,25,50,75,100].forEach(p=>{
        html+='<button onclick="event.stopPropagation();sdSetPct(\''+i.job+'\','+p+')" style="padding:1px 5px;font-size:.68em;background:'+(pct===p?'#4db8b8':'rgba(0,0,0,.3)')+';border:1px solid '+(pct===p?'#4db8b8':'rgba(255,255,255,.15)')+';color:'+(pct===p?'#0f1117':'#aaa')+';border-radius:2px;cursor:pointer">'+(p===100?'&#10003;':p+'%')+'</button>';
      });
      html+='</span>';
      // Resize handle
      html+='<div onmousedown="event.stopPropagation();startResizeEnd(event,\''+i.job+'\')" style="position:absolute;right:0;top:0;bottom:0;width:8px;cursor:col-resize;background:linear-gradient(90deg,transparent,rgba(255,255,255,.25))" title="Drag to extend/shorten"></div>';
      html+='</div></div>';
      // Empty cells after bar
      for(let w=ei+1;w<weeks.length;w++){
        html+='<div class="gr-cell" ondragover="event.preventDefault();this.classList.add(\'drag-over\')" ondragleave="this.classList.remove(\'drag-over\')" ondrop="this.classList.remove(\'drag-over\');sdDropJob(event,\''+weeks[w]+'\')"></div>';
      }
    } else {
      // Single-week bar
      weeks.forEach((w,wi)=>{
        html+='<div class="gr-cell" ondragover="event.preventDefault();this.classList.add(\'drag-over\')" ondragleave="this.classList.remove(\'drag-over\')" ondrop="this.classList.remove(\'drag-over\');sdDropJob(event,\''+w+'\')">';
        if(assignedWeek===w){
          html+='<div class="gantt-bar'+(isDone?' done':'')+'" style="background:'+barColor+';display:flex;align-items:center;gap:5px;padding:4px 8px;min-height:28px;font-size:.85em;font-weight:600;color:#fff;white-space:nowrap;border-radius:4px;cursor:move;position:relative" draggable="true" ondragstart="event.dataTransfer.setData(\'text/plain\',\''+i.job+'\')" onclick="event.stopPropagation()">';
          html+='<div class="pct-bar" style="width:'+pct+'%"></div>';
          if(hrs)html+='<span class="gb-hrs">'+fmtHrs(hrs)+'</span>';
          if(i.price)html+='<span class="gb-val">'+fmt(i.price)+'</span>';
          if(due.t!=='&mdash;')html+='<span class="gb-due '+due.c+'">'+due.t+'</span>';
          // Pct + Move
          html+='<span class="gb-actions" style="margin-left:auto;display:flex;gap:2px;align-items:center">';
          [0,25,50,75,100].forEach(p=>{
            html+='<button onclick="event.stopPropagation();sdSetPct(\''+i.job+'\','+p+')" style="padding:1px 4px;font-size:.68em;background:'+(pct===p?'#4db8b8':'rgba(0,0,0,.3)')+';border:1px solid '+(pct===p?'#4db8b8':'rgba(255,255,255,.15)')+';color:'+(pct===p?'#0f1117':'#aaa')+';border-radius:2px;cursor:pointer">'+(p===100?'&#10003;':p+'%')+'</button>';
          });
          html+='<select onchange="event.stopPropagation();if(this.value){sdMoveDept([\''+i.job+'\'],this.value);this.value=\'\'}" style="background:#1e1e2a;border:1px solid #3a3a5a;color:#c45c8a;padding:2px 3px;border-radius:3px;font-size:.68em;cursor:pointer;max-width:50px"><option value="">Mv</option>'+MOVE_DEPTS.filter(s=>s.k!==realDeptKey(stg.k)).map(s=>'<option value="'+s.k+'">'+s.l+'</option>').join('')+'</select>';
          html+='</span>';
          // If monument in metal, show extend button
          if(isMon&&isMetal){
            html+='<button onclick="event.stopPropagation();sdExtendWeek(\''+i.job+'\')" style="padding:1px 5px;font-size:.68em;background:rgba(0,0,0,.3);border:1px solid #c45c8a;color:#c45c8a;border-radius:2px;cursor:pointer" title="Extend to multiple weeks">&#x27EB;</button>';
          }
          html+='</div>';
        }
        html+='</div>';
      });
    }

    html+='</div>'; // end gantt-row
  }

  // Metal: split into Monument and Small sections
  if(isMetal){
    const monItems=sorted.filter(i=>i.monument);
    const smallItems=sorted.filter(i=>!i.monument);
    if(monItems.length){
      html+='<div style="grid-column:1/-1;padding:8px 12px;background:#1a1525;border-top:2px solid #c45c8a;margin-top:2px"><span style="font-size:.78em;font-weight:700;letter-spacing:.5px;color:#c45c8a">MONUMENTS ('+monItems.length+')</span></div>';
      monItems.forEach(renderItemRow);
    }
    if(smallItems.length){
      html+='<div style="grid-column:1/-1;padding:8px 12px;background:#151a25;border-top:2px solid '+stg.c+';margin-top:4px"><span style="font-size:.78em;font-weight:700;letter-spacing:.5px;color:'+stg.c+'">SMALL / REGULAR ('+smallItems.length+')</span></div>';
      smallItems.forEach(renderItemRow);
    }
  } else {
    if(sorted.length===0){
      html+='<div style="grid-column:1/-1;text-align:center;padding:30px;color:#555;font-size:.9em">No items in this view</div>';
    }
    sorted.forEach(renderItemRow);
  }

  // Unscheduled items at bottom
  if(!_drillWeek){
    const unsched=sorted.filter(i=>!_assignments[i.job]||!_assignments[i.job].week);
    if(unsched.length){
      html+='<div style="grid-column:1/-1;padding:8px 10px;background:#1a1520;border-top:2px solid #3a2a4a;margin-top:4px">';
      html+='<div style="font-size:.78em;color:#c45c8a;font-weight:700;letter-spacing:.5px;margin-bottom:6px">UNSCHEDULED ('+unsched.length+')</div>';
      unsched.forEach(i=>{
        const isSel=_sdSelected.has(i.job);
        html+='<span onclick="toggleSdSelect(\''+i.job+'\')" class="sd-cb" data-job="'+i.job+'" style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;margin:2px;border-radius:4px;background:#0f1117;border:1px solid #2a2d3a;cursor:pointer;font-size:.82em;color:'+(isSel?'#5ae8a8':'#aaa')+'">';
        html+=(isSel?'\u2611':'\u2610')+' #'+i.job+' '+i.name;
        html+='</span>';
      });
      html+='</div>';
    }
  }

  html+='</div></div>';
  return html;
}

// === NEW HELPER FUNCTIONS ===
function sdDropJob(event,week){
  event.preventDefault();
  const job=event.dataTransfer.getData('text/plain');
  if(!job)return;
  sdScheduleJobs([job],week);
}

function sdSetPct(job,pct){
  const a=_assignments[job];if(!a)return;
  a.pct=pct;a.done=(pct>=100);
  fetch('/api/schedule/assign',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,week:a.week,pct,endWeek:a.endWeek||null,done:a.done})
  }).catch(e=>console.error('pct failed',e));
  renderDrill();
}

function sdSetEndWeek(job,endWeek){
  const a=_assignments[job];if(!a)return;
  a.endWeek=endWeek;
  fetch('/api/schedule/assign',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({job,week:a.week,pct:a.pct||0,endWeek})
  }).catch(e=>console.error('endWeek failed',e));
  renderDrill();
}

function sdExtendWeek(job){
  const a=_assignments[job];if(!a||!a.week)return;
  const nw=addWeeks(a.endWeek||a.week,1);
  sdSetEndWeek(job,nw);
}

function startResizeEnd(event,job){
  event.preventDefault();event.stopPropagation();
  const a=_assignments[job];if(!a)return;
  const startX=event.clientX;
  const today=getMonday(new Date().toISOString().slice(0,10));
  const wks=[];for(let n=0;n<12;n++)wks.push(addWeeks(today,n));
  const curEnd=a.endWeek||a.week;
  const curIdx=wks.indexOf(curEnd);
  const startIdx=wks.indexOf(a.week);
  // Estimate cell width from grid
  const grid=document.querySelector('.gantt');
  const cellW=grid?(grid.offsetWidth-260)/wks.length:100;

  function onMove(e){
    const dx=e.clientX-startX;
    const delta=Math.round(dx/cellW);
    let ni=Math.max(startIdx,curIdx+delta);
    ni=Math.min(ni,wks.length-1);
    const newEnd=ni===startIdx?null:wks[ni];
    if(newEnd!==a.endWeek){a.endWeek=newEnd;renderDrill();}
  }
  function onUp(){
    document.removeEventListener('mousemove',onMove);
    document.removeEventListener('mouseup',onUp);
    sdSetEndWeek(job,a.endWeek);
  }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}

function openDrill(stgKey,stgLabel,stgColor,week){
  _drillStage=stgKey;
  _drillSort='due';
  _drillWeek=week||null;
  _sdSelected.clear();
  _sdAllSelect=false;
  document.getElementById('sdsearch').value='';
  document.getElementById('sdschedbtn').style.display='none';
  document.getElementById('sdmovewrap').style.display='none';
  document.getElementById('sdselall').textContent='\u2610 Select All';
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
  document.getElementById('sdselall').onclick=function(){
    _sdAllSelect=!_sdAllSelect;
    if(_sdAllSelect){
      document.querySelectorAll('.sd-cb').forEach(cb=>_sdSelected.add(cb.dataset.job));
      this.textContent='\u2611 Deselect All';
    } else {
      _sdSelected.clear();
      this.textContent='\u2610 Select All';
    }
    updateSdSelectUI();
  };
  document.getElementById('sdschedbtn').onclick=function(){
    if(!_sdSelected.size)return;
    // Quick schedule picker (use current week)
    const today=getMonday(new Date().toISOString().slice(0,10));
    sdScheduleWeekPicker(Array.from(_sdSelected));
  };
  document.getElementById('sdmovebtn').onclick=function(){
    if(!_sdSelected.size)return;
    const dest=document.getElementById('sdmovedest').value;
    if(!dest)return;
    sdMoveDept(Array.from(_sdSelected),dest);
  };
}

// Week picker for schedule drill-down batch scheduling
function sdScheduleWeekPicker(jobs){
  const today=getMonday(new Date().toISOString().slice(0,10));
  const weeks=[
    {w:today,l:'This Week',d:fmtWeekRange(today)},
    {w:addWeeks(today,1),l:'Next Week',d:fmtWeekRange(addWeeks(today,1))},
    {w:addWeeks(today,2),l:'Week +2',d:fmtWeekRange(addWeeks(today,2))},
    {w:addWeeks(today,3),l:'Week +3',d:fmtWeekRange(addWeeks(today,3))}
  ];
  const n=jobs.length;
  let html='<div style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);z-index:600;display:flex;align-items:center;justify-content:center" id="sdWeekPickerBg" onclick="if(event.target===this)this.remove()">'+
    '<div style="background:#12151f;border:1px solid #3a4a6a;border-radius:10px;padding:20px;max-width:400px;width:90%">'+
    '<h3 style="color:#fff;margin:0 0 4px">Schedule '+n+' Item'+(n>1?'s':'')+'</h3>'+
    '<p style="color:#888;font-size:.85em;margin:0 0 12px">Choose target week</p>'+
    '<div style="display:flex;flex-direction:column;gap:6px">'+
    weeks.map(w=>'<button onclick="sdScheduleJobs('+JSON.stringify(jobs)+',\''+w.w+'\');document.getElementById(\'sdWeekPickerBg\').remove();_sdSelected.clear();updateSdSelectUI();" style="display:flex;justify-content:space-between;padding:10px 14px;background:#1e2a3a;border:1px solid #3a4a6a;border-radius:6px;cursor:pointer;color:#fff;font-size:.88em;font-weight:600;transition:all .15s" onmouseover="this.style.borderColor=\'#4db8b8\'" onmouseout="this.style.borderColor=\'#3a4a6a\'"><span>'+w.l+'</span><span style="color:#888;font-weight:400">'+w.d+'</span></button>').join('')+
    '</div>'+
    '<button onclick="document.getElementById(\'sdWeekPickerBg\').remove()" style="margin-top:10px;width:100%;padding:8px;background:#2a2d3a;border:1px solid #3a3d4a;border-radius:5px;color:#aaa;cursor:pointer;font-size:.85em">Cancel</button>'+
    '</div></div>';
  document.body.insertAdjacentHTML('beforeend',html);
}
function closeDrill(){
  _drillStage=null;
  _sdSelected.clear();
  _sdAllSelect=false;
  document.getElementById('sdrillbg').style.display='none';
  render();
}

// &#x2500;&#x2500; Schedule drill-down selection & actions &#x2500;&#x2500;
function toggleSdSelect(job){
  if(_sdSelected.has(job))_sdSelected.delete(job);
  else _sdSelected.add(job);
  updateSdSelectUI();
}
function updateSdSelectUI(){
  const count=_sdSelected.size;
  const schedBtn=document.getElementById('sdschedbtn');
  const moveWrap=document.getElementById('sdmovewrap');
  const moveBtn=document.getElementById('sdmovebtn');
  const moveDest=document.getElementById('sdmovedest');
  if(count>0){
    schedBtn.style.display='';
    schedBtn.textContent='\uD83D\uDCC5 Schedule ('+count+')';
    moveWrap.style.display='flex';
    moveBtn.textContent='\u27A1 Move Dept ('+count+')';
    const curStage=_drillStage==='metal_small'||_drillStage==='metal_mon'?'metal':_drillStage==='sprue_small'||_drillStage==='sprue_mon'?'sprue':_drillStage;
    moveDest.innerHTML=MOVE_DEPTS.filter(s=>s.k!==curStage).map(s=>'<option value="'+s.k+'">'+s.l+'</option>').join('');
  } else {
    schedBtn.style.display='none';
    moveWrap.style.display='none';
  }
  document.querySelectorAll('.sd-cb').forEach(cb=>{
    const sel=_sdSelected.has(cb.dataset.job);
    cb.textContent=sel?'\u2611':'\u2610';
    cb.style.color=sel?'#5ae8a8':'#555';
  });
}
function sdScheduleOne(job){
  // Quick schedule: assign to current week
  const today=getMonday(new Date().toISOString().slice(0,10));
  sdScheduleJobs([job],today);
}
function sdScheduleJobs(jobs,week){
  jobs.forEach(j=>{
    const a=_assignments[j]||{};
    _assignments[j]={week:week,carryover:a.carryover||false,original_week:a.original_week||null,done:a.done||false,auto:false};
    fetch('/api/schedule/assign',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({job:j,week:week})}).catch(e=>console.error('assign failed',e));
  });
  showToast(jobs.length+' item(s) scheduled \u2192 '+weekLabel(week));
  renderDrill();
}
function sdMoveDept(jobs,targetStage){
  // Move items to different department locally + server
  const pieceIds=[];
  jobs.forEach(j=>{
    const item=_items.find(i=>i.job===j);
    if(item){item.stage=targetStage;if(item.pieceId)pieceIds.push(item.pieceId);}
  });
  // Use correct DithTracker status ID for the target department
  const dtId=DT_STATUS_MAP[targetStage]||null;
  fetch('/api/move-items',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({jobs,targetStage,pieceIds,dtStatusId:dtId})
  }).then(r=>r.json()).then(d=>{
    // Update local assignments to reflect next-week auto-assign
    if(d.reassigned>0){
      const nextMon=addWeeks(getMonday(new Date()),1);
      jobs.forEach(j=>{
        _assignments[j]={week:nextMon,carryover:false,original_week:(_assignments[j]||{}).week||null,done:false};
      });
    }
    const deptLabel=(MOVE_DEPTS.find(s=>s.k===targetStage)||{l:targetStage}).l;
    let tmsg=jobs.length+' item(s) moved to '+deptLabel;
    if(d.reassigned>0)tmsg+=' &rarr; next week';
    if(d.dtQueued)tmsg+=' &mdash; DT syncing';
    showToast(tmsg);
  }).catch(e=>console.error('move failed',e));
  _sdSelected.clear();
  updateSdSelectUI();
  renderDrill();
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
  return d.toLocaleDateString('en-US',mo)+' - '+end.toLocaleDateString('en-US',mo);
}
function weekLabel(monday){
  const today=getMonday(new Date().toISOString().slice(0,10));
  if(monday===today)return'This Week';
  if(monday===addWeeks(today,1))return'Next Week';
  const diff=Math.round((new Date(monday)-new Date(today))/(7*86400000));
  if(diff>0)return'Wk +'+diff;
  return'Wk '+diff;
}

function realDeptKey(dept){if(dept==='metal_small'||dept==='metal_mon')return'metal';if(dept==='sprue_small'||dept==='sprue_mon')return'sprue';return dept;}
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
    (!compact?'<div class="prog-val" style="color:'+doneColor+'">'+fmt(stats.doneVal)+' done &middot; '+stats.doneCount+'/'+stats.total+' items</div>':'')+
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
  const wLocked=isLocked(rd,week);
  // Build week target options
  const today=getMonday(new Date().toISOString().slice(0,10));
  let opts='';
  for(let i=0;i<8;i++){
    const w=addWeeks(today,i);
    if(w===week)continue;
    opts+='<option value="'+w+'">'+weekLabel(w)+' ('+fmtWeekRange(w)+')</option>';
  }
  if(wLocked){
    // Locked week: require PIN
    _pendingAction={type:'move',dept:rd,week,jobs};
    document.getElementById('modal-title').textContent='Move '+jobs.length+' Item'+(jobs.length>1?'s':'');
    document.getElementById('modal-desc').textContent='Enter PIN to move from locked week';
    document.getElementById('move-target').innerHTML=opts;
    document.getElementById('move-target-wrap').style.display='block';
    document.getElementById('pin-input').value='';
    document.getElementById('pin-input').classList.remove('error');
    document.getElementById('pin-modal').classList.add('active');
    setTimeout(()=>document.getElementById('pin-input').focus(),100);
  } else {
    // Open week: show quick move dialog (no PIN needed)
    _pendingAction={type:'quickmove',dept:rd,week,jobs};
    document.getElementById('modal-title').textContent='Move '+jobs.length+' Item'+(jobs.length>1?'s':'');
    document.getElementById('modal-desc').textContent='Select target week';
    document.getElementById('move-target').innerHTML=opts;
    document.getElementById('move-target-wrap').style.display='block';
    document.getElementById('pin-input').value='';
    document.getElementById('pin-input').style.display='none';
    document.getElementById('pin-modal').classList.add('active');
  }
}

// ========== LOCK / UNLOCK / MOVE ==========
function requestLock(dept,week){
  const rd=realDeptKey(dept);
  _pendingAction={type:'lock',dept:rd,week,jobs:[]};
  document.getElementById('modal-title').textContent='Close Week';
  document.getElementById('modal-desc').textContent='Lock '+(STAGE_MAP[dept]||{l:dept}).l+' &mdash; '+weekLabel(week);
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
  document.getElementById('modal-desc').textContent='Unlock '+(STAGE_MAP[dept]||{l:dept}).l+' &mdash; '+weekLabel(week);
  document.getElementById('move-target-wrap').style.display='none';
  document.getElementById('pin-input').value='';
  document.getElementById('pin-input').classList.remove('error');
  document.getElementById('pin-modal').classList.add('active');
  setTimeout(()=>document.getElementById('pin-input').focus(),100);
}
function closeModal(){
  document.getElementById('pin-modal').classList.remove('active');
  document.getElementById('pin-input').style.display='';
  _pendingAction=null;
}
function submitPin(){
  const pin=document.getElementById('pin-input').value;
  if(!_pendingAction)return closeModal();
  const {type,dept,week,jobs}=_pendingAction;

  if(type==='quickmove'){
    // Open-week batch move &mdash; no PIN needed, just reassign locally + server
    const targetWeek=document.getElementById('move-target').value;
    if(!targetWeek){showToast('Select a target week');return;}
    jobs.forEach(j=>{
      const a=_assignments[j]||{};
      _assignments[j]={week:targetWeek,carryover:a.carryover||false,original_week:a.original_week||null,done:a.done||false,auto:false};
      _selected.delete(j);
      fetch('/api/schedule/assign',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({job:j,week:targetWeek})}).catch(e=>console.error('assign failed',e));
    });
    showToast('Moved '+jobs.length+' item'+(jobs.length>1?'s':'')+' &rarr; '+weekLabel(targetWeek));
    closeModal();render();
    return;
  }

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
        showToast('Moved '+jobs.length+' item'+(jobs.length>1?'s':'')+' &rarr; '+weekLabel(targetWeek));
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
          showToast('&#x1F512; '+STAGE_MAP[dept].l+' '+weekLabel(week)+' locked');
        } else {
          delete _lockedWeeks[dept+'-'+week];
          showToast('&#x1F513; '+STAGE_MAP[dept].l+' '+weekLabel(week)+' reopened');
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
  showToast('#'+job+(item?' '+item.name:'')+' &rarr; '+weekLabel(week));
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
    if(pri!==1){showToast('Week is locked &mdash; only urgent items allowed');_dragJob=null;return;}}
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
    '<div class="sstat green">THIS WEEK <strong>'+twItems.length+'</strong> items &middot; '+fmtHrs(twHrs)+' &middot; '+fmt(twVal)+'</div>'+
    (doneItems.length?'<div class="sstat" style="color:#5a9e5a">DONE <strong>'+doneItems.length+'</strong></div>':'')+
    (lockedCount?'<div class="sstat" style="color:#5a9e5a">&#x1F512; <strong>'+lockedCount+'</strong> locked</div>':'')+
    '<div class="summary-health">'+healthBarHtml(overallStats,'#4db8b8',false)+'</div>';

  let grid='';
  STAGES.forEach(stg=>{
    const deptItems=getDeptItems(stg);
    const deptHrs=deptItems.reduce((a,i)=>a+itemHours(i),0);
    const deptVal=deptItems.reduce((a,i)=>a+(i.price||0),0);
    const deptStats=deptPctCalc(deptItems);

    let body='';
    weeks.forEach(w=>{
      const _wSeen=new Set();
      const wItems=deptItems.filter(i=>{if(_wSeen.has(i.job))return false;if(!_assignments[i.job]||_assignments[i.job].week!==w)return false;_wSeen.add(i.job);return true;});
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
          '<span>'+(wLocked?'<span class="lock-icon">&#x1F512;</span>':'')+
            '<span class="wlabel">'+weekLabel(w)+'</span><span class="wdates"> '+fmtWeekRange(w)+'</span>'+
            '<button onclick="event.stopPropagation();openDrill(\''+(stg.filter?'metal':stg.k)+'\',\''+stg.l+'\',\''+stg.c+'\',\''+w+'\')" style="margin-left:6px;background:#1e2a3a;border:1px solid #3a4a6a;color:#4db8b8;padding:1px 6px;border-radius:3px;cursor:pointer;font-size:.65em;font-weight:700" title="Drill down this week">&#x1F50D;</button></span>'+
          '<span class="wstats"><strong>'+wItems.length+'</strong> &middot; '+fmtHrs(wHrs-doneHrsWeek)+' remaining &middot; '+fmt(wVal-doneValWeek)+' remaining'+
            (doneCount?' &middot; <span style="color:#5a9e5a">'+doneCount+'&#10003;</span>':'')+'</span>'+
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
            // Click to select in ALL weeks; drag in open weeks
            const clickHandler='onclick="toggleSelect(\''+i.job+'\',event)"';
            const dragHandler=canDrag?
              'draggable="true" ondragstart="onDragStart(event,\''+i.job+'\')" ondragend="onDragEnd(event)"':
              '';
            return '<div class="scard'+(isCarry?' carry':'')+(isDone?' done-item':'')+(isSel?' selected':'')+'" '+
              'data-job="'+i.job+'" '+dragHandler+' '+clickHandler+
              ' style="border-left-color:'+stg.c+'">'+
              '<div class="s-title">'+(!isSel?'&#x2610; ':'&#x2611; ')+
                '#'+i.job+' '+i.name+
                (i.monument?'<span class="wcmon">MON</span>':'')+
                (isCarry?'<span class="co-badge">CARRY</span>':'')+priTag(i.job)+
                (isDone?'<span style="color:#5a9e5a;margin-left:4px">&#10003; Done</span>':'')+'</div>'+
              '<div class="s-meta">'+
                '<span>'+i.customer+'</span>'+
                (hrs?'<span class="s-hrs">'+fmtHrs(hrs)+'</span>':'')+
                (i.price?'<span class="s-val">'+fmt(i.price)+'</span>':'')+
                dueTag(i.due)+
              '</div>'+
              (!wLocked?'<div class="s-actions">'+
                (!isCurrent?'<button class="btn-sm rush" onclick="event.stopPropagation();rushToThisWeek(\''+i.job+'\')">&#x26A1; Rush</button>':'')+
                '<button class="btn-sm done'+(isDone?' active':'')+'" onclick="event.stopPropagation();toggleDone(\''+i.job+'\')">'+
                  (isDone?'&#10003; Done':'Done')+'</button>'+
                '<select class="btn-sm" onchange="event.stopPropagation();if(this.value){sdMoveDept([\''+i.job+'\'],this.value);this.value=\'\'}" style="background:#1e1e2a;border:1px solid #3a3a5a;color:#c45c8a;padding:2px 4px;border-radius:3px;font-size:.72em;cursor:pointer;max-width:80px"><option value="">Move...</option>'+MOVE_DEPTS.filter(s=>s.k!==realDeptKey(stg.k)).map(s=>'<option value="'+s.k+'">'+s.l+'</option>').join('')+'</select>'+
              '</div>':'')+
            '</div>';
          }).join('')+
        '</div>'+
        '<div class="wb-actions">'+
          '<span class="sel-move-wrap" data-dept="'+stg.k+'" data-week="'+w+'">'+
            '<span class="sel-count"></span> '+
            '<button class="btn-move" style="display:none" onclick="event.stopPropagation();requestMoveSelected(\''+stg.k+'\',\''+w+'\')">&#x1F4E6; Move Selected</button>'+
          '</span>'+
          (wLocked?
            '<button class="btn-unlock" onclick="event.stopPropagation();requestUnlock(\''+stg.k+'\',\''+w+'\')">&#x1F513; Reopen</button>':
            '<button class="btn-lock" onclick="event.stopPropagation();requestLock(\''+stg.k+'\',\''+w+'\')">&#x1F512; Close Week</button>')+
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
                pct = body.get('pct', existing.get('pct', 0))
                end_week = body.get('endWeek', existing.get('endWeek'))
                done = body.get('done', existing.get('done', False))
                assignments[job] = {
                    'week': week,
                    'carryover': existing.get('carryover', False),
                    'original_week': existing.get('original_week'),
                    'done': done,
                    'pct': pct,
                    'endWeek': end_week,
                }
                log.info(f'Schedule: assigned job={job} to week={week} pct={pct} endWeek={end_week}')
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

@app.route('/api/schedule/wipe', methods=['POST'])
def schedule_wipe():
    """Wipe all schedule assignments and locked weeks."""
    try:
        with _lock:
            _schedule_data['assignments'] = {}
            _schedule_data['locked_weeks'] = []
        _save_schedule()
        log.info('Schedule: wiped all assignments and locked weeks.')
        return jsonify({'ok': True})
    except Exception as e:
        log.error(f'Schedule wipe failed: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/api/schedule/batch-assign', methods=['POST'])
def schedule_batch_assign():
    """Assign multiple jobs to a week at once.
       Body: { jobs: ['123','456'], week: '2026-04-13' }
    """
    try:
        body = request.get_json(force=True)
        jobs = body.get('jobs', [])
        week = str(body.get('week', '')).strip()
        if not jobs or not week:
            return jsonify({'error': 'missing jobs or week'}), 400
        with _lock:
            assignments = _schedule_data.setdefault('assignments', {})
            for j in jobs:
                j = str(j).strip()
                existing = assignments.get(j, {})
                assignments[j] = {
                    'week': week,
                    'carryover': existing.get('carryover', False),
                    'original_week': existing.get('original_week'),
                    'done': existing.get('done', False),
                }
        _save_schedule()
        log.info(f'Schedule: batch assigned {len(jobs)} items to week={week}')
        return jsonify({'ok': True, 'count': len(jobs), 'week': week})
    except Exception as e:
        log.error(f'Schedule batch-assign failed: {e}')
        return jsonify({'error': str(e)}), 500

# &#x2500;&#x2500; Startup &#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;&#x2500;
if SESSION_COOKIE:
    log.info('SESSION_COOKIE set &mdash; running initial server-side fetch...')
    items, err = fetch()
    with _lock:
        if items is not None:
            _cache['items']   = items
            _cache['updated'] = datetime.utcnow().isoformat() + 'Z'
            log.info(f'&#10003;  {len(items)} items loaded.')
        else:
            _cache['error'] = err
            log.warning(f'&#x26A0;  Initial fetch failed: {err}')
    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()
else:
    # Auto-fetch WIP from DithTracker on startup (no auth required)
    log.info('No SESSION_COOKIE &mdash; auto-fetching WIP from DithTracker...')
    _startup_items = _auto_fetch_wip()
    if _startup_items:
        with _lock:
            _cache['items'] = _startup_items
            _cache['updated'] = datetime.utcnow().isoformat() + 'Z'
        log.info(f'&#10003;  {len(_startup_items)} items auto-loaded from DithTracker on startup.')
    else:
        log.info('Auto-fetch returned nothing &mdash; waiting for browser push to /api/push-wip')

# Mark GitHub persistence as ready (prevents saves during init)
_gh_ready = True
log.info('&#10003; GitHub persistence armed &mdash; state changes will auto-save.')


# ============================================================
# NEW FEATURE: Data Stores
# ============================================================
_notes_data = {}
_employee_data = {}
_history_data = {}
_team_members = []

NOTES_FILE = '/tmp/notes.json'
EMPLOYEE_FILE = '/tmp/employees.json'
HISTORY_FILE = '/tmp/history.json'
TEAM_FILE = '/tmp/team.json'

def _load_notes():
    global _notes_data
    try:
        with open(NOTES_FILE) as f:
            _notes_data = json.load(f)
    except Exception:
        _notes_data = {}

def _save_notes():
    try:
        with open(NOTES_FILE, 'w') as f:
            json.dump(_notes_data, f)
    except Exception:
        pass

def _load_employees():
    global _employee_data
    try:
        with open(EMPLOYEE_FILE) as f:
            _employee_data = json.load(f)
    except Exception:
        _employee_data = {}

def _save_employees():
    try:
        with open(EMPLOYEE_FILE, 'w') as f:
            json.dump(_employee_data, f)
    except Exception:
        pass

def _load_history():
    global _history_data
    try:
        with open(HISTORY_FILE) as f:
            _history_data = json.load(f)
    except Exception:
        _history_data = {}

def _save_history():
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(_history_data, f)
    except Exception:
        pass

def _load_team():
    global _team_members
    try:
        with open(TEAM_FILE) as f:
            _team_members = json.load(f)
    except Exception:
        _team_members = []

def _save_team():
    try:
        with open(TEAM_FILE, 'w') as f:
            json.dump(_team_members, f)
    except Exception:
        pass

# ============================================================
# NEW FEATURE: API Endpoints
# ============================================================

@app.route('/api/notes/<piece_id>')
def api_get_notes(piece_id):
    pid = str(piece_id)
    return jsonify(_notes_data.get(pid, []))

@app.route('/api/notes/add', methods=['POST'])
def api_add_note():
    global _notes_data
    d = request.get_json(force=True)
    pid = str(d.get('pieceId', ''))
    if not pid:
        return jsonify({'error': 'Missing pieceId'}), 400
    if pid not in _notes_data:
        _notes_data[pid] = []
    _notes_data[pid].insert(0, {
        'text': d.get('text', ''),
        'author': d.get('author', 'Unknown'),
        'type': d.get('type', 'note'),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })
    _save_notes()
    _persist()
    return jsonify({'ok': True})

@app.route('/api/notes/delete', methods=['POST'])
def api_delete_note():
    global _notes_data
    d = request.get_json(force=True)
    pid = str(d.get('pieceId', ''))
    idx = d.get('index', -1)
    if pid in _notes_data and 0 <= idx < len(_notes_data[pid]):
        _notes_data[pid].pop(idx)
        _save_notes()
        _persist()
    return jsonify({'ok': True})

@app.route('/api/employees/assign', methods=['POST'])
def api_assign_employee():
    global _employee_data
    d = request.get_json(force=True)
    pid = str(d.get('pieceId', ''))
    dept = d.get('department', '')
    emp = d.get('employee', '')
    if not pid:
        return jsonify({'error': 'Missing pieceId'}), 400
    if pid not in _employee_data:
        _employee_data[pid] = {}
    _employee_data[pid][dept] = emp
    _save_employees()
    _persist()
    return jsonify({'ok': True})

@app.route('/api/employees/<piece_id>')
def api_get_employees(piece_id):
    pid = str(piece_id)
    return jsonify(_employee_data.get(pid, {}))

@app.route('/api/history/<piece_id>')
def api_get_history(piece_id):
    pid = str(piece_id)
    return jsonify(_history_data.get(pid, []))

@app.route('/api/history/log', methods=['POST'])
def api_log_history():
    global _history_data
    d = request.get_json(force=True)
    pid = str(d.get('pieceId', ''))
    if not pid:
        return jsonify({'error': 'Missing pieceId'}), 400
    if pid not in _history_data:
        _history_data[pid] = []
    _history_data[pid].insert(0, {
        'from_dept': d.get('from_dept', ''),
        'to_dept': d.get('to_dept', ''),
        'by': d.get('by', ''),
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })
    _save_history()
    _persist()
    return jsonify({'ok': True})

# Production stages (mirrors JS STAGES const)
STAGES = [
    {'k': 'molds',    'l': 'Molds',          'c': '#4a6fa5'},
    {'k': 'creation', 'l': 'Creation',       'c': '#7b5ea7'},
    {'k': 'waxpull',  'l': 'Wax Pull',       'c': '#e8a838'},
    {'k': 'waxchase', 'l': 'Wax Chase',      'c': '#d4763b'},
    {'k': 'shell',    'l': 'Shell/Pouryard', 'c': '#5a9e6f'},
    {'k': 'patina',   'l': 'Patina',         'c': '#c45c8a'},
    {'k': 'base',     'l': 'Base',           'c': '#4db8b8'},
    {'k': 'ready',    'l': 'Ready',          'c': '#5a9e5a'},
]

# NCR cause code registry (edit to customize)
NCR_CAUSE_CODES = [
    {'code': 'mold_defect',   'label': 'Mold/Creation Defect'},
    {'code': 'wax_defect',    'label': 'Wax Pull/Chase Defect'},
    {'code': 'cast_defect',   'label': 'Cast/Metal Defect'},
    {'code': 'finish_defect', 'label': 'Finish/Patina/Base Defect'},
    {'code': 'dimensional',   'label': 'Dimensional'},
    {'code': 'damage',        'label': 'Handling Damage'},
    {'code': 'material',      'label': 'Material Quality'},
    {'code': 'other',         'label': 'Other'},
]
NCR_VALID_CODES = {c['code'] for c in NCR_CAUSE_CODES}

import secrets as _secrets
def _mk_event_id():
    return 'rw_' + datetime.utcnow().strftime('%Y%m%d%H%M%S') + '_' + _secrets.token_hex(2)

@app.route('/api/rework/log', methods=['POST'])
def api_log_rework():
    global _notes_data, _history_data
    d = request.get_json(force=True)
    pid = str(d.get('pieceId', ''))
    if not pid:
        return jsonify({'error': 'Missing pieceId'}), 400
    cause_code = d.get('cause_code', '')
    if cause_code and cause_code not in NCR_VALID_CODES:
        cause_code = 'other'
    if pid not in _history_data:
        _history_data[pid] = []
    evt = {
        'event_id': _mk_event_id(),
        'from_dept': d.get('from_dept', ''),
        'to_dept': d.get('to_dept', ''),
        'by': d.get('by', ''),
        'reason': d.get('reason', ''),
        'cause_code': cause_code,
        'cause_other': d.get('cause_other', '') if cause_code == 'other' else '',
        'stage': d.get('stage', ''),
        'rework': True,
        'resolved': False,
        'resolved_by': '',
        'resolved_at': '',
        'fix_note': '',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }
    _history_data[pid].insert(0, evt)
    if pid not in _notes_data:
        _notes_data[pid] = []
    note_text = '[REWORK] ' + (d.get('reason', '') or '(no reason)')
    if cause_code:
        note_text += ' (' + cause_code + ')'
    _notes_data[pid].insert(0, {
        'text': note_text,
        'by': d.get('by', ''),
        'timestamp': evt['timestamp'],
        'type': 'rework',
        'event_id': evt['event_id'],
    })
    _save_history()
    _save_notes()
    _persist()
    return jsonify({'ok': True, 'event_id': evt['event_id']})

@app.route('/api/rework/resolve', methods=['POST'])
def api_resolve_rework():
    global _history_data
    d = request.get_json(force=True)
    pid = str(d.get('pieceId', ''))
    eid = d.get('event_id', '')
    if not pid or not eid:
        return jsonify({'error': 'Missing pieceId or event_id'}), 400
    events = _history_data.get(pid, [])
    target = None
    for e in events:
        if e.get('event_id') == eid and e.get('rework'):
            target = e
            break
    if not target:
        return jsonify({'error': 'Rework event not found'}), 404
    target['resolved'] = True
    target['resolved_by'] = d.get('resolved_by', '')
    target['resolved_at'] = datetime.utcnow().isoformat() + 'Z'
    target['fix_note'] = d.get('fix_note', '')
    _save_history()
    _persist()
    return jsonify({'ok': True})

@app.route('/api/rework/open')
def api_open_rework():
    '''Return all unresolved rework events across all pieces.'''
    out = []
    for pid, events in _history_data.items():
        for e in events:
            if e.get('rework') and not e.get('resolved'):
                out.append({
                    'piece_id': pid,
                    'event_id': e.get('event_id', ''),
                    'reason': e.get('reason', ''),
                    'cause_code': e.get('cause_code', ''),
                    'cause_other': e.get('cause_other', ''),
                    'stage': e.get('stage', ''),
                    'by': e.get('by', ''),
                    'timestamp': e.get('timestamp', ''),
                })
    out.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    return jsonify({'events': out, 'count': len(out), 'cause_codes': NCR_CAUSE_CODES})
@app.route('/api/team', methods=['GET'])
def api_get_team():
    return jsonify(_team_members)

@app.route('/api/tv')
def api_tv():
    '''Aggregated data for /tv kiosk view.'''
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with _lock:
        raw_items = _cache.get('items', []) if isinstance(_cache, dict) else []
        items = list(raw_items)
        stage_ov = dict(_stage_overrides or {})
        hist = {pid: list(ev) for pid, ev in (_history_data or {}).items()}
    # Counts per stage (apply overrides)
    stage_counts = {}
    pieces_with_stage = []
    for p in items:
        pid = str(p.get('pieceId', ''))
        st = stage_ov.get(pid, p.get('stage', ''))
        stage_counts[st] = stage_counts.get(st, 0) + 1
        pieces_with_stage.append((p, pid, st))
    # Heatmap in STAGES order, exclude 'ready'
    heatmap = []
    for s in STAGES:
        if s.get('k') == 'ready':
            continue
        c = stage_counts.get(s['k'], 0)
        if c == 0:
            andon = 'idle'
        elif c > 60:
            andon = 'red'
        elif c > 25:
            andon = 'yellow'
        else:
            andon = 'green'
        heatmap.append({'key': s['k'], 'label': s['l'], 'color': s['c'], 'count': c, 'andon': andon})
    ready_count = stage_counts.get('ready', 0)
    total_active = sum(h['count'] for h in heatmap)
    # Stalled: time since last to_dept == current stage
    stalled = []
    for p, pid, st in pieces_with_stage:
        if st == 'ready' or not st:
            continue
        events = hist.get(pid, [])
        arrival = None
        for e in events:
            if e.get('rework'):
                continue
            if e.get('to_dept') == st:
                arrival = e.get('timestamp')
                break
        if not arrival:
            continue
        try:
            at = datetime.fromisoformat(arrival.replace('Z', '+00:00'))
            hours = (now - at).total_seconds() / 3600.0
        except Exception:
            continue
        if hours < 24:
            continue
        stage_label = next((s['l'] for s in STAGES if s['k'] == st), st)
        if hours > 72:
            aging = 'red'
        elif hours > 48:
            aging = 'orange'
        else:
            aging = 'yellow'
        stalled.append({
            'piece_id': pid,
            'name': p.get('name', '') or p.get('monument', '') or pid,
            'customer': p.get('customer', ''),
            'job': p.get('job', ''),
            'stage': st,
            'stage_label': stage_label,
            'hours': round(hours, 1),
            'aging': aging,
        })
    stalled.sort(key=lambda x: x['hours'], reverse=True)
    stalled_top = stalled[:15]
    # Open rework count
    open_rework = 0
    for pid, events in hist.items():
        for e in events:
            if e.get('rework') and not e.get('resolved'):
                open_rework += 1
    return jsonify({
        'heatmap': heatmap,
        'total_active': total_active,
        'ready_count': ready_count,
        'total_pieces': len(items),
        'stalled': stalled_top,
        'stalled_count': len(stalled),
        'open_rework': open_rework,
        'generated_at': now.isoformat(),
    })

TV_PAGE_HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8"><title>TV Board - Pyrology</title><style>
*{box-sizing:border-box}html,body{margin:0;padding:0;background:#0a0e1a;color:#e0e6ed;font-family:-apple-system,Segoe UI,Roboto,sans-serif;overflow:hidden;height:100%;width:100%}
.panel{position:fixed;inset:0;padding:40px;opacity:0;transition:opacity .6s;pointer-events:none}
.panel.active{opacity:1}
.hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
.hdr h1{font-size:56px;margin:0;letter-spacing:-.02em;font-weight:800}
.hdr .right{font-size:26px;opacity:.7;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:14px;height:14px;border-radius:50%;background:#4ade80;margin-right:10px;vertical-align:middle;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.heatmap{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}
.tile{background:linear-gradient(135deg,#1e2a3a 0%,#152030 100%);border:3px solid #3a4a6a;border-radius:18px;padding:30px;min-height:180px;display:flex;flex-direction:column;justify-content:space-between;transition:all .3s}
.tile .count{font-size:112px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}
.tile .label{font-size:28px;font-weight:600;opacity:.85}
.tile.red{border-color:#ef4444;box-shadow:0 0 50px rgba(239,68,68,.35);background:linear-gradient(135deg,#2a1520 0%,#1a0f18 100%)}
.tile.red .count{color:#ff6b6b}
.tile.yellow{border-color:#f59e0b;box-shadow:0 0 30px rgba(245,158,11,.2)}
.tile.yellow .count{color:#fbbf24}
.tile.green{border-color:#10b981}
.tile.green .count{color:#4ade80}
.tile.idle{opacity:.4}
.tile.idle .count{color:#64748b}
.stalled-list{display:flex;flex-direction:column;gap:10px;max-height:calc(100vh - 220px);overflow:hidden}
.stall-row{display:grid;grid-template-columns:1fr 220px 140px;gap:18px;padding:16px 24px;background:#1e2a3a;border-radius:12px;align-items:center;border-left:8px solid #f59e0b}
.stall-row.red{border-left-color:#ef4444;background:#2a1520}
.stall-row.orange{border-left-color:#fb7c0c}
.stall-row .name{font-size:24px;font-weight:700}
.stall-row .sub{font-size:16px;opacity:.6;font-weight:400;margin-top:2px}
.stall-row .stage{font-size:22px;opacity:.85}
.stall-row .hours{font-size:36px;font-weight:800;text-align:right;font-variant-numeric:tabular-nums}
.stall-row.red .hours{color:#ff6b6b}
.stall-row.orange .hours{color:#fb923c}
.stall-row.yellow .hours{color:#fbbf24}
.footer{position:fixed;bottom:20px;left:40px;right:40px;display:flex;justify-content:space-between;font-size:18px;opacity:.55}
.empty{text-align:center;padding:120px 40px;font-size:40px;opacity:.7;line-height:1.3}
.badges{display:flex;gap:14px;margin-top:8px}
.badge{padding:6px 14px;border-radius:999px;font-size:18px;font-weight:600;background:#1e2a3a;border:1px solid #3a4a6a}
.badge.hot{background:#2a1520;border-color:#ef4444;color:#ff6b6b}
</style></head><body>
<div id="panel-heatmap" class="panel active">
  <div class="hdr"><div><h1>Station Load</h1><div class="badges"><div class="badge" id="b-active">-</div><div class="badge" id="b-ready">-</div><div class="badge" id="b-rework">-</div></div></div><div class="right"><span class="dot"></span><span id="tm1">--:--</span></div></div>
  <div class="heatmap" id="hm-grid"></div>
  <div class="footer"><div>Green &lt;26 &middot; Yellow 26-60 &middot; Red &gt;60 WIP</div><div>Pyrology Production Board</div></div>
</div>
<div id="panel-stalled" class="panel">
  <div class="hdr"><div><h1>Stalled Pieces</h1><div class="badges"><div class="badge" id="b-stall-count">-</div></div></div><div class="right"><span class="dot"></span><span id="tm2">--:--</span></div></div>
  <div class="stalled-list" id="st-list"></div>
  <div class="footer"><div>Yellow 24-48h &middot; Orange 48-72h &middot; Red &gt;72h since last stage change</div><div>Pyrology Production Board</div></div>
</div>
<script>
var panels=["panel-heatmap","panel-stalled"];var idx=0;var data=null;
function fmtTime(){var d=new Date();return d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}
function esc(s){return (s==null?"":String(s)).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;","\\x22":"&quot;"}[c]})}
function renderHeatmap(){var g=document.getElementById("hm-grid");if(!data||!data.heatmap)return;g.innerHTML=data.heatmap.map(function(h){return "<div class=\\"tile "+h.andon+"\\"><div class=\\"count\\">"+h.count+"</div><div class=\\"label\\">"+esc(h.label)+"</div></div>"}).join("");document.getElementById("b-active").textContent=data.total_active+" active";document.getElementById("b-ready").textContent=data.ready_count+" ready to ship";var br=document.getElementById("b-rework");br.textContent=(data.open_rework||0)+" open rework";br.className="badge"+(data.open_rework>0?" hot":"")}
function renderStalled(){var g=document.getElementById("st-list");if(!data)return;if(!data.stalled||data.stalled.length===0){g.innerHTML="<div class=\\"empty\\">No pieces stalled over 24 hours.<br>All stations moving normally.</div>";document.getElementById("b-stall-count").textContent="0 stalled";return}g.innerHTML=data.stalled.map(function(s){return "<div class=\\"stall-row "+s.aging+"\\"><div><div class=\\"name\\">"+esc(s.name)+"</div><div class=\\"sub\\">"+esc(s.customer||s.job||"")+"</div></div><div class=\\"stage\\">"+esc(s.stage_label)+"</div><div class=\\"hours\\">"+s.hours+"h</div></div>"}).join("");document.getElementById("b-stall-count").textContent=data.stalled_count+" stalled"}
function tick(){var t=fmtTime();document.getElementById("tm1").textContent=t;document.getElementById("tm2").textContent=t}
async function refresh(){try{var r=await fetch("/api/tv",{cache:"no-store"});data=await r.json();renderHeatmap();renderStalled()}catch(e){}}
function rotate(){document.getElementById(panels[idx]).classList.remove("active");idx=(idx+1)%panels.length;document.getElementById(panels[idx]).classList.add("active")}
refresh();tick();setInterval(refresh,30000);setInterval(tick,15000);setInterval(rotate,12000);
</script></body></html>'''

@app.route('/tv')
def tv_page():
    return TV_PAGE_HTML

@app.route('/api/quality/summary')
def api_quality_summary():
    '''Aggregated quality data: open rework queue + first-pass yield per station.'''
    with _lock:
        raw_items = _cache.get('items', []) if isinstance(_cache, dict) else []
        items = list(raw_items)
        hist = {pid: list(ev) for pid, ev in (_history_data or {}).items()}
    # Open rework queue with piece context
    piece_by_id = {str(p.get('pieceId', '')): p for p in items}
    open_queue = []
    resolved_count = 0
    for pid, events in hist.items():
        for e in events:
            if not e.get('rework'):
                continue
            if e.get('resolved'):
                resolved_count += 1
                continue
            p = piece_by_id.get(pid, {})
            open_queue.append({
                'piece_id': pid,
                'event_id': e.get('event_id', ''),
                'name': p.get('name') or p.get('monument') or pid,
                'customer': p.get('customer', ''),
                'stage': p.get('stage', '') or e.get('stage', ''),
                'cause_code': e.get('cause_code', ''),
                'cause_other': e.get('cause_other', ''),
                'reason': e.get('reason', ''),
                'by': e.get('by', ''),
                'timestamp': e.get('timestamp', ''),
            })
    open_queue.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    # First-pass yield per stage: pieces that reached stage without rework / total reached
    # Approximation: for each stage, count pieces currently at or past it vs those with rework logged at that stage
    stage_order = [s['k'] for s in STAGES]
    stage_pos = {k: i for i, k in enumerate(stage_order)}
    fpy = []
    for s in STAGES:
        k = s['k']
        if k == 'ready':
            continue
        # Pieces that have reached this stage (currently at or past it)
        reached = 0
        cur_idx = stage_pos.get(k, -1)
        for p in items:
            cs = p.get('stage', '')
            if stage_pos.get(cs, -1) >= cur_idx:
                reached += 1
        # Pieces with a rework event logged at this stage
        rw_at_stage = 0
        for pid, events in hist.items():
            if any(e.get('rework') and e.get('stage') == k for e in events):
                rw_at_stage += 1
        yield_pct = round(100.0 * (reached - rw_at_stage) / reached, 1) if reached > 0 else None
        fpy.append({'stage': k, 'label': s['l'], 'reached': reached, 'reworked': rw_at_stage, 'yield_pct': yield_pct})
    return jsonify({
        'open_queue': open_queue,
        'open_count': len(open_queue),
        'resolved_count': resolved_count,
        'fpy': fpy,
        'cause_codes': NCR_CAUSE_CODES,
    })

REWORK_PAGE_HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Rework Queue - Pyrology</title><style>
*{box-sizing:border-box}body{margin:0;background:#0f1520;color:#e0e6ed;font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px}
#wtop{display:flex;align-items:center;gap:12px;padding:10px 20px;background:#0a0e1a;border-bottom:2px solid #3a4a6a}
#wtop a{padding:5px 13px;border-radius:5px;font-weight:700;font-size:13px;text-decoration:none;border:1px solid #3a4a6a;background:#1e2a3a;color:#4fd1c5;display:inline-flex;align-items:center;gap:4px}
#wtop a.active{background:#4fd1c5;color:#0a0e1a;border-color:#4fd1c5}
#wtop h1{font-size:18px;margin:0 0 0 10px;font-weight:700}
.wrap{padding:20px;max-width:1400px;margin:0 auto}
.row{display:grid;grid-template-columns:2fr 1fr;gap:24px;margin-bottom:24px}
@media(max-width:900px){.row{grid-template-columns:1fr}}
.card{background:#1e2a3a;border:1px solid #3a4a6a;border-radius:10px;padding:18px}
.card h2{font-size:18px;margin:0 0 14px;font-weight:700;color:#4fd1c5}
.kpis{display:flex;gap:12px;margin-bottom:18px}
.kpi{flex:1;background:#1e2a3a;border:1px solid #3a4a6a;border-radius:10px;padding:16px;text-align:center}
.kpi .n{font-size:40px;font-weight:800;line-height:1;font-variant-numeric:tabular-nums}
.kpi .l{font-size:12px;opacity:.7;margin-top:6px;text-transform:uppercase;letter-spacing:.05em}
.kpi.hot .n{color:#ff6b6b}.kpi.warm .n{color:#fbbf24}.kpi.cool .n{color:#4fd1c5}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;background:#0a0e1a;font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#4fd1c5;border-bottom:1px solid #3a4a6a}
td{padding:10px;border-bottom:1px solid #2a3a55;vertical-align:top}
tr:hover td{background:#243245}
.cause-chip{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;background:#3a4a6a;color:#e0e6ed}
.btn{padding:5px 12px;border-radius:5px;border:1px solid #3a4a6a;background:#1e2a3a;color:#4fd1c5;font-weight:600;cursor:pointer;font-size:12px;text-decoration:none;display:inline-block}
.btn.primary{background:#4fd1c5;color:#0a0e1a;border-color:#4fd1c5}
.btn.warn{background:#7a2020;color:#ffb3b3;border-color:#a33}
.fpy-bar{background:#0a0e1a;border-radius:4px;height:8px;overflow:hidden;margin-top:4px}
.fpy-bar-fill{height:100%;background:linear-gradient(90deg,#10b981,#4fd1c5)}
.fpy-bar-fill.warn{background:linear-gradient(90deg,#f59e0b,#fbbf24)}
.fpy-bar-fill.low{background:linear-gradient(90deg,#ef4444,#ff6b6b)}
.empty{text-align:center;padding:40px;opacity:.6}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);align-items:center;justify-content:center;z-index:100}
.modal.open{display:flex}
.modal-inner{background:#1e2a3a;border:2px solid #4fd1c5;border-radius:12px;padding:24px;width:min(520px,90vw)}
.modal h3{margin:0 0 14px;color:#4fd1c5}
.modal label{display:block;font-size:12px;opacity:.7;margin:10px 0 4px;text-transform:uppercase;letter-spacing:.05em}
.modal input,.modal textarea{width:100%;padding:8px 10px;background:#0a0e1a;border:1px solid #3a4a6a;border-radius:6px;color:#e0e6ed;font-family:inherit;font-size:14px}
.modal textarea{min-height:60px;resize:vertical}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:16px}
</style></head><body>
<div id="wtop"><h1>Pyrology Rework</h1><a href="/">Dashboard</a><a href="/quality">Quality</a><a href="/rework" class="active">Rework Queue</a><a href="/tv">TV Board</a><a href="/m/">Mobile</a><button class="btn primary" style="margin-left:auto;padding:6px 14px;font-size:13px" onclick="openLog()">+ Log NCR</button></div>
<div class="wrap">
  <div class="kpis">
    <div class="kpi hot"><div class="n" id="k-open">-</div><div class="l">Open NCRs</div></div>
    <div class="kpi cool"><div class="n" id="k-resolved">-</div><div class="l">Resolved</div></div>
    <div class="kpi warm"><div class="n" id="k-top-cause">-</div><div class="l">Top Cause</div></div>
    <div class="kpi cool"><div class="n" id="k-avg-fpy">-</div><div class="l">Avg FPY</div></div>
  </div>
  <div class="row">
    <div class="card">
      <h2>Open Rework Queue</h2>
      <div id="queue-wrap"></div>
    </div>
    <div class="card">
      <h2>First-Pass Yield by Station</h2>
      <div id="fpy-wrap"></div>
    </div>
  </div>
</div>
<div class="modal" id="logModal">  <div class="modal-inner">    <h3>Log New NCR / Rework</h3>    <label>Piece ID</label><input id="lg-pid" placeholder="e.g. 24061">    <label>Cause Code</label><select id="lg-cause" style="width:100%;padding:8px 10px;background:#0a0e1a;border:1px solid #3a4a6a;border-radius:6px;color:#e0e6ed;font-family:inherit;font-size:14px"></select>    <label id="lg-other-label" style="display:none">Other cause (describe)</label><input id="lg-other" style="display:none" placeholder="Describe the cause">    <label>Stage where found</label><select id="lg-stage" style="width:100%;padding:8px 10px;background:#0a0e1a;border:1px solid #3a4a6a;border-radius:6px;color:#e0e6ed;font-family:inherit;font-size:14px"></select>    <label>Reason / description</label><textarea id="lg-reason" placeholder="Describe the defect"></textarea>    <label>Logged by</label><input id="lg-by" placeholder="Your name">    <div class="modal-actions"><button class="btn" onclick="closeLog()">Cancel</button><button class="btn primary" onclick="doLog()">Log NCR</button></div>  </div></div><div class="modal" id="resolveModal">
  <div class="modal-inner">
    <h3>Resolve Rework</h3>
    <div id="resolve-ctx" style="font-size:13px;opacity:.8;margin-bottom:10px"></div>
    <label>Resolved by</label><input id="rv-by" placeholder="Your name">
    <label>Fix note (what did you do?)</label><textarea id="rv-note" placeholder="e.g. Re-polished the base, passed QC"></textarea>
    <div class="modal-actions"><button class="btn" onclick="closeResolve()">Cancel</button><button class="btn primary" onclick="doResolve()">Resolve</button></div>
  </div>
</div>
<script>
var CAUSE_LABELS={};var currentEvent=null;var currentPiece=null;
function esc(s){return (s==null?"":String(s)).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;","\\x22":"&quot;"}[c]})}
function fmtAgo(iso){if(!iso)return "";try{var d=new Date(iso);var mins=Math.round((Date.now()-d.getTime())/60000);if(mins<60)return mins+"m ago";var h=Math.round(mins/60);if(h<24)return h+"h ago";return Math.round(h/24)+"d ago"}catch(e){return iso}}
function renderQueue(data){var g=document.getElementById("queue-wrap");if(!data.open_queue||data.open_queue.length===0){g.innerHTML="<div class=\\"empty\\">No open rework. Nice work.</div>";return}var rows=data.open_queue.map(function(e){var label=CAUSE_LABELS[e.cause_code]||e.cause_code||"";if(e.cause_code=="other"&&e.cause_other)label+=": "+e.cause_other;return "<tr><td><a href=\\"/piece/"+esc(e.piece_id)+"\\" style=\\"color:#4fd1c5;text-decoration:none;font-weight:600\\">"+esc(e.name)+"</a><div style=\\"font-size:11px;opacity:.6\\">"+esc(e.customer||"")+"</div></td><td>"+esc(e.stage||"")+"</td><td><span class=\\"cause-chip\\">"+esc(label)+"</span></td><td style=\\"max-width:260px\\">"+esc(e.reason||"")+"</td><td>"+esc(e.by||"")+"<div style=\\"font-size:11px;opacity:.6\\">"+fmtAgo(e.timestamp)+"</div></td><td><button class=\\"btn primary\\" onclick='openResolve(\\""+e.piece_id+"\\",\\""+e.event_id+"\\",\\""+e.name.replace(/\\"/g,"")+"\\")'>Resolve</button></td></tr>"}).join("");g.innerHTML="<table><thead><tr><th>Piece</th><th>Stage</th><th>Cause</th><th>Reason</th><th>Logged</th><th></th></tr></thead><tbody>"+rows+"</tbody></table>"}
function renderFpy(data){var g=document.getElementById("fpy-wrap");if(!data.fpy){g.innerHTML="";return}var rows=data.fpy.map(function(f){var pct=f.yield_pct;var pctTxt=pct==null?"-":pct.toFixed(1)+"%";var cls="";if(pct!=null){if(pct<90)cls="low";else if(pct<97)cls="warn"}var w=pct==null?0:Math.max(5,pct);return "<div style=\\"margin-bottom:10px\\"><div style=\\"display:flex;justify-content:space-between;font-size:13px\\"><span>"+esc(f.label)+"</span><span style=\\"font-weight:700\\">"+pctTxt+"</span></div><div class=\\"fpy-bar\\"><div class=\\"fpy-bar-fill "+cls+"\\" style=\\"width:"+w+"%\\"></div></div><div style=\\"font-size:11px;opacity:.55;margin-top:2px\\">"+f.reworked+" reworked of "+f.reached+" reached</div></div>"}).join("");g.innerHTML=rows}
function renderKpis(data){document.getElementById("k-open").textContent=data.open_count||0;document.getElementById("k-resolved").textContent=data.resolved_count||0;var counts={};(data.open_queue||[]).forEach(function(e){counts[e.cause_code]=(counts[e.cause_code]||0)+1});var top="-";var max=0;Object.keys(counts).forEach(function(k){if(counts[k]>max){max=counts[k];top=CAUSE_LABELS[k]||k}});document.getElementById("k-top-cause").textContent=top;var ys=(data.fpy||[]).map(function(f){return f.yield_pct}).filter(function(y){return y!=null});var avg=ys.length?Math.round(ys.reduce(function(a,b){return a+b},0)/ys.length*10)/10:"-";document.getElementById("k-avg-fpy").textContent=(avg=="-"?"-":avg+"%")}
function openResolve(pid,eid,name){currentPiece=pid;currentEvent=eid;document.getElementById("resolve-ctx").textContent=name+" ("+pid+")";document.getElementById("rv-by").value="";document.getElementById("rv-note").value="";document.getElementById("resolveModal").classList.add("open")}
function closeResolve(){document.getElementById("resolveModal").classList.remove("open")}
async function doResolve(){var by=document.getElementById("rv-by").value.trim();if(!by){alert("Please enter your name");return}var note=document.getElementById("rv-note").value.trim();var r=await fetch("/api/rework/resolve",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pieceId:currentPiece,event_id:currentEvent,resolved_by:by,fix_note:note})});if(r.ok){closeResolve();load()}else{alert("Failed to resolve")}}
async function load(){var r=await fetch("/api/quality/summary",{cache:"no-store"});var data=await r.json();(data.cause_codes||[]).forEach(function(c){CAUSE_LABELS[c.code]=c.label});window._causeCodes=data.cause_codes||[];renderKpis(data);renderQueue(data);renderFpy(data)}
function openLog(){var sel=document.getElementById("lg-cause");sel.innerHTML=(window._causeCodes||[]).map(function(c){return "<option value=\\""+c.code+"\\">"+c.label+"</option>"}).join("");var st=document.getElementById("lg-stage");var stages=[{k:"molds",l:"Molds"},{k:"creation",l:"Creation"},{k:"waxpull",l:"Wax Pull"},{k:"waxchase",l:"Wax Chase"},{k:"sprue",l:"Sprue"},{k:"shell",l:"Shell/Pouryard"},{k:"metal",l:"Metal Work"},{k:"patina",l:"Patina"},{k:"base",l:"Base"}];st.innerHTML=stages.map(function(s){return "<option value=\\""+s.k+"\\">"+s.l+"</option>"}).join("");document.getElementById("lg-pid").value="";document.getElementById("lg-reason").value="";document.getElementById("lg-by").value="";document.getElementById("lg-other").value="";document.getElementById("lg-other").style.display="none";document.getElementById("lg-other-label").style.display="none";sel.onchange=function(){var show=sel.value=="other";document.getElementById("lg-other").style.display=show?"block":"none";document.getElementById("lg-other-label").style.display=show?"block":"none"};document.getElementById("logModal").classList.add("open")}function closeLog(){document.getElementById("logModal").classList.remove("open")}async function doLog(){var pid=document.getElementById("lg-pid").value.trim();var by=document.getElementById("lg-by").value.trim();if(!pid){alert("Piece ID required");return}if(!by){alert("Logged by required");return}var body={pieceId:pid,cause_code:document.getElementById("lg-cause").value,cause_other:document.getElementById("lg-other").value,stage:document.getElementById("lg-stage").value,reason:document.getElementById("lg-reason").value,by:by};var r=await fetch("/api/rework/log",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});if(r.ok){closeLog();load()}else{var err=await r.json().catch(function(){return{}});alert("Failed to log: "+(err.error||r.status))}}load();setInterval(load,60000);
</script></body></html>'''

@app.route('/rework')
def rework_page():
    return REWORK_PAGE_HTML

@app.route('/api/team/add', methods=['POST'])
def api_add_team_member():
    global _team_members
    d = request.get_json(force=True)
    name = d.get('name', '').strip()
    if name and name not in _team_members:
        _team_members.append(name)
        _save_team()
        _persist()
    return jsonify({'ok': True, 'members': _team_members})

@app.route('/api/team/remove', methods=['POST'])
def api_remove_team_member():
    global _team_members
    d = request.get_json(force=True)
    name = d.get('name', '').strip()
    if name in _team_members:
        _team_members.remove(name)
        _save_team()
        _persist()
    return jsonify({'ok': True, 'members': _team_members})

@app.route('/api/analytics/bottlenecks')
def api_bottlenecks():
    items = _cache.get('items', [])
    today = datetime.utcnow().date()
    result = []
    for it in items:
        due = it.get('due', '')
        days_overdue = 0
        if due:
            try:
                due_date = datetime.strptime(due, '%Y-%m-%d').date()
                days_overdue = (today - due_date).days
            except Exception:
                pass
        result.append({
            'pieceId': it.get('pieceId'),
            'job': it.get('job'),
            'name': it.get('name'),
            'customer': it.get('customer'),
            'stage': it.get('stage'),
            'status': it.get('status'),
            'price': it.get('price', 0),
            'due': due,
            'daysOverdue': days_overdue,
            'monument': it.get('monument', False)
        })
    result.sort(key=lambda x: x['daysOverdue'], reverse=True)
    return jsonify(result)

@app.route('/api/analytics/client-summary')
def api_client_summary():
    items = _cache.get('items', [])
    clients = {}
    today = datetime.utcnow().date()
    for it in items:
        c = it.get('customer', 'Unknown')
        if c not in clients:
            clients[c] = {'name': c, 'pieces': 0, 'totalValue': 0, 'departments': {}, 'items': []}
        clients[c]['pieces'] += 1
        clients[c]['totalValue'] += it.get('price', 0)
        dept = it.get('status', 'Unknown')
        clients[c]['departments'][dept] = clients[c]['departments'].get(dept, 0) + 1
        due = it.get('due', '')
        days_overdue = 0
        if due:
            try:
                due_date = datetime.strptime(due, '%Y-%m-%d').date()
                days_overdue = (today - due_date).days
            except Exception:
                pass
        clients[c]['items'].append({
            'pieceId': it.get('pieceId'),
            'job': it.get('job'),
            'name': it.get('name'),
            'status': it.get('status'),
            'stage': it.get('stage'),
            'price': it.get('price', 0),
            'due': due,
            'daysOverdue': days_overdue
        })
    result = sorted(clients.values(), key=lambda x: x['totalValue'], reverse=True)
    return jsonify(result)

@app.route('/api/analytics/trends')
def api_trends():
    return jsonify({
        'kpiHistory': _kpi_data.get('history', []),
        'currentWeek': _kpi_data.get('week_start', ''),
        'entries': _kpi_data.get('entries', [])
    })

@app.route('/api/search')
def api_search():
    q = request.args.get('q', '').lower().strip()
    if not q:
        return jsonify([])
    items = _cache.get('items', [])
    results = []
    for it in items:
        if (q in str(it.get('name', '')).lower() or
            q in str(it.get('job', '')).lower() or
            q in str(it.get('customer', '')).lower() or
            q in str(it.get('pieceId', '')).lower()):
            results.append(it)
    return jsonify(results[:50])


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# NEW FEATURE PAGES - Auto-generated
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

_PAGE_CSS = "*{margin:0;padding:0;box-sizing:border-box}\nbody{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f1923;color:#e0e0e0;min-height:100vh}\n.container{max-width:1400px;margin:0 auto;padding:80px 20px 20px}\n.card{background:#1a2634;border:1px solid #2a3a4a;border-radius:12px;padding:20px;margin-bottom:16px}\n.card-header{font-size:18px;font-weight:700;color:#4fd1c5;margin-bottom:12px}\n.btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;font-weight:600;font-size:13px;transition:all 0.2s}\n.btn-primary{background:#4fd1c5;color:#0f1923}\n.btn-primary:hover{background:#38b2ac}\n.btn-danger{background:#e53e3e;color:white}\n.btn-sm{padding:4px 10px;font-size:12px}\ninput,select,textarea{background:#0f1923;border:1px solid #2a3a4a;color:#e0e0e0;padding:8px 12px;border-radius:8px;font-size:14px;width:100%}\ninput:focus,select:focus,textarea:focus{outline:none;border-color:#4fd1c5}\ntable{width:100%;border-collapse:collapse}\nth{text-align:left;padding:10px 12px;border-bottom:2px solid #2a3a4a;color:#4fd1c5;font-size:12px;text-transform:uppercase;letter-spacing:1px}\ntd{padding:10px 12px;border-bottom:1px solid #1a2634}\ntr:hover{background:rgba(79,209,197,0.05)}\n.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700}\n.stat-card{text-align:center;padding:20px}\n.stat-value{font-size:28px;font-weight:800;color:#4fd1c5}\n.stat-label{font-size:12px;color:#8a9bb0;text-transform:uppercase;letter-spacing:1px;margin-top:4px}\n.search-box{position:relative;margin-bottom:20px}\n.search-box input{padding:12px 16px;font-size:16px;border-radius:12px}\nh1{font-size:24px;font-weight:800;text-transform:uppercase;letter-spacing:2px;margin-bottom:4px}\nh2{font-size:18px;font-weight:700;color:#4fd1c5;margin-bottom:12px}\n.subtitle{font-size:14px;color:#8a9bb0}\n.grid{display:grid;gap:16px}\n.grid-2{grid-template-columns:repeat(2,1fr)}\n.grid-3{grid-template-columns:repeat(3,1fr)}\n.grid-4{grid-template-columns:repeat(4,1fr)}\n.grid-5{grid-template-columns:repeat(5,1fr)}\n@media(max-width:1024px){.grid-4,.grid-5{grid-template-columns:repeat(2,1fr)}}\n@media(max-width:768px){.grid-2,.grid-3,.grid-4,.grid-5{grid-template-columns:1fr}.container{padding:70px 10px 10px}}\n.overdue-severe{background:rgba(229,62,62,0.15)}\n.overdue-high{background:rgba(237,137,54,0.12)}\n.overdue-medium{background:rgba(236,201,75,0.1)}\n.text-red{color:#fc8181}\n.text-orange{color:#f6ad55}\n.text-yellow{color:#ecc94b}\n.text-green{color:#68d391}\n.text-teal{color:#4fd1c5}\na{color:#4fd1c5;text-decoration:none}\na:hover{text-decoration:underline}\n"
_PAGE_NAV = "<div style=\"position:fixed;top:0;right:0;z-index:1000;display:flex;gap:8px;padding:12px 18px;flex-wrap:wrap;align-items:center;background:rgba(15,25,35,0.95);backdrop-filter:blur(8px);border-bottom-left-radius:12px\">\n<a href=\"/\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(79,209,197,0.15);color:#4fd1c5;border:1px solid rgba(79,209,197,0.3)\">&#127981; Dashboard</a>\n<a href=\"/schedule\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(79,209,197,0.15);color:#4fd1c5;border:1px solid rgba(79,209,197,0.3)\">&#128197; Schedule</a>\n<a href=\"/kpi\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(79,209,197,0.15);color:#4fd1c5;border:1px solid rgba(79,209,197,0.3)\">&#128202; KPI</a>\n<a href=\"/maintenance\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(79,209,197,0.15);color:#4fd1c5;border:1px solid rgba(79,209,197,0.3)\">&#128295; Maintenance</a>\n<a href=\"/shipping\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(79,209,197,0.15);color:#4fd1c5;border:1px solid rgba(79,209,197,0.3)\">&#128230; Shipping</a>\n<span style=\"width:1px;height:20px;background:#2a3a4a;margin:0 4px\"></span>\n<a href=\"/clients\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(255,183,77,0.12);color:#ffb74d;border:1px solid rgba(255,183,77,0.3)\">&#128101; Clients</a>\n<a href=\"/reports\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(255,183,77,0.12);color:#ffb74d;border:1px solid rgba(255,183,77,0.3)\">&#128200; Reports</a>\n<a href=\"/bottlenecks\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(255,183,77,0.12);color:#ffb74d;border:1px solid rgba(255,183,77,0.3)\">&#9888; Bottlenecks</a>\n<a href=\"/due-dates\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(255,183,77,0.12);color:#ffb74d;border:1px solid rgba(255,183,77,0.3)\">&#128197; Due Dates</a>\n<a href=\"/team\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(255,183,77,0.12);color:#ffb74d;border:1px solid rgba(255,183,77,0.3)\">&#128119; Team</a>\n<a href=\"/quality\" style=\"text-decoration:none;padding:6px 14px;border-radius:8px;font-weight:700;font-size:13px;background:rgba(255,183,77,0.12);color:#ffb74d;border:1px solid rgba(255,183,77,0.3)\">&#9989; Quality</a>\n</div>"

def _page_html(title, subtitle, body_js, extra_head=""):
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + title + """ - Pyrology WIP</title>
<style>""" + _PAGE_CSS + """</style>
""" + extra_head + """
</head><body>
""" + _PAGE_NAV + """
<div class="container">
<h1>""" + title + """</h1>
<p class="subtitle">""" + subtitle + """</p>
<div id="app" style="margin-top:20px"></div>
</div>
<script>
const API = window.location.origin;
function $(s){ return document.querySelector(s); }
function $$(s){ return document.querySelectorAll(s); }
function fe(url,opt){ return fetch(API+url,opt).then(r=>r.json()); }
function post(url,data){ return fe(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); }
""" + body_js + """
</script></body></html>"""


CLIENTS_HTML = _page_html("Clients", "Customer lookup &amp; project overview", """
let items=[], filtered=[];
async function load(){
  let r=await fe('/api/wip'); items=r.items||[];
  let cs={}; items.forEach(i=>{if(!cs[i.customer])cs[i.customer]={name:i.customer,total:0,stages:{},value:0};
    cs[i.customer].total++; let s=i.stage||'unknown'; cs[i.customer].stages[s]=(cs[i.customer].stages[s]||0)+1;
    cs[i.customer].value+=(parseFloat(i.price)||0);});
  filtered=Object.values(cs).sort((a,b)=>b.total-a.total); render();
}
function render(){
  let q=($('#sq')||{}).value||''; let f=filtered;
  if(q) f=f.filter(c=>c.name.toLowerCase().includes(q.toLowerCase()));
  let h='<div class="search-box"><input id="sq" placeholder="Search clients..." oninput="render()" value="'+q+'"></div>';
  h+='<div class="grid grid-4" style="margin-bottom:20px">';
  h+='<div class="card stat-card"><div class="stat-value">'+filtered.length+'</div><div class="stat-label">Total Clients</div></div>';
  h+='<div class="card stat-card"><div class="stat-value">'+items.length+'</div><div class="stat-label">Total Pieces</div></div>';
  let tv=filtered.reduce((s,c)=>s+c.value,0);
  h+='<div class="card stat-card"><div class="stat-value">$'+tv.toLocaleString(undefined,{maximumFractionDigits:0})+'</div><div class="stat-label">Total Value</div></div>';
  let avg=filtered.length?Math.round(items.length/filtered.length):0;
  h+='<div class="card stat-card"><div class="stat-value">'+avg+'</div><div class="stat-label">Avg Pieces/Client</div></div>';
  h+='</div>';
  h+='<table><thead><tr><th>Customer</th><th>Pieces</th><th>Value</th><th>Stage Breakdown</th></tr></thead><tbody>';
  f.forEach(c=>{
    let sb=Object.entries(c.stages).map(([k,v])=>'<span class="badge" style="background:rgba(79,209,197,0.15);color:#4fd1c5;margin:2px">'+k+': '+v+'</span>').join(' ');
    h+='<tr><td><a href="/clients/'+encodeURIComponent(c.name)+'">'+c.name+'</a></td><td>'+c.total+'</td><td>$'+(c.value||0).toLocaleString()+'</td><td>'+sb+'</td></tr>';
  });
  h+='</tbody></table>';
  $('#app').innerHTML=h;
}
load();
""")

@app.route('/clients')
def clients_page():
    return Response(CLIENTS_HTML, content_type='text/html')


@app.route('/clients/<path:cname>')
def client_detail_page(cname):
    body_js = """
    const CNAME='"""+cname.replace("'","\\'").replace('"','&quot;')+"""';
    let items=[];
    async function load(){
      let r=await fe('/api/wip'); items=(r.items||[]).filter(i=>i.customer===CNAME); render();
    }
    function render(){
      let h='<h2 style="margin-bottom:16px">'+CNAME+' - '+items.length+' pieces</h2>';
      h+='<a href="/clients" class="btn btn-primary btn-sm" style="margin-bottom:16px;display:inline-block">&larr; Back to Clients</a>';
      h+='<table><thead><tr><th>Piece</th><th>Job</th><th>Stage</th><th>Due</th><th>Price</th><th>Monument</th></tr></thead><tbody>';
      items.forEach(i=>{
        h+='<tr><td><a href="/piece/'+i.pieceId+'">'+i.name+'</a></td><td>'+i.job+'</td><td><span class="badge" style="background:rgba(79,209,197,0.15);color:#4fd1c5">'+i.stage+'</span></td><td>'+(i.due||'N/A')+'</td><td>$'+(parseFloat(i.price)||0).toLocaleString()+'</td><td>'+(i.monument||'N/A')+'</td></tr>';
      });
      h+='</tbody></table>';
      $('#app').innerHTML=h;
    }
    load();
    """
    return Response(_page_html(cname, "Client project detail", body_js), content_type='text/html')


@app.route('/piece/<piece_id>')
def piece_detail_page(piece_id):
    body_js = """
    const PID='"""+piece_id+"""';
    async function load(){
      let r=await fe('/api/wip'); let item=(r.items||[]).find(i=>i.pieceId===PID);
      if(!item){$('#app').innerHTML='<div class="card"><p>Piece not found</p></div>';return;}
      let notes=await fe('/api/notes/'+PID);
      let hist=await fe('/api/history/'+PID);
      let emp=await fe('/api/employees/'+PID);
      render(item,notes,hist,emp);
    }
    function render(item,notes,hist,emp){
      let h='<a href="/clients" class="btn btn-primary btn-sm" style="margin-bottom:16px;display:inline-block">&larr; Back</a>';
      h+='<div class="grid grid-2">';
      // Info card
      h+='<div class="card"><div class="card-header">Piece Information</div>';
      h+='<table>';
      let fields=[["Name",item.name],["Job",item.job],["Customer",item.customer],["Stage",item.stage],["Status",item.status],["Due",item.due||"N/A"],["Price","$"+(parseFloat(item.price)||0).toLocaleString()],["Monument",item.monument||"N/A"],["Edition",item.edition||"N/A"],["Tier",item.tier||"N/A"],["Metal",item.hMetal||"N/A"]];
      fields.forEach(f=>{h+='<tr><td style="color:#8a9bb0;width:120px">'+f[0]+'</td><td>'+f[1]+'</td></tr>';});
      h+='</table></div>';
      // Hours card
      h+='<div class="card"><div class="card-header">Hours Tracking</div><table>';
      let hrs=[["Wax Pull",item.hWaxPull],["Wax",item.hWax],["Sprue",item.hSprue],["Metal",item.hMetalWorked],["Basing",item.hBasing],["Patina",item.hPatina],["Polish",item.hPolishWorked]];
      hrs.forEach(f=>{h+='<tr><td style="color:#8a9bb0;width:120px">'+f[0]+'</td><td>'+(f[1]||'0')+'</td></tr>';});
      h+='</table></div></div>';
      // Assigned employee
      h+='<div class="card" style="margin-top:16px"><div class="card-header">Assigned Employee</div>';
      if(emp.employee){h+='<p>'+emp.employee+'</p>';}else{h+='<p style="color:#8a9bb0">Unassigned</p>';}
      h+='<div style="margin-top:8px"><input id="aemp" placeholder="Employee name" style="width:200px;display:inline-block"><button class="btn btn-primary btn-sm" style="margin-left:8px" onclick="assignEmp()">Assign</button></div></div>';
      // Notes
      h+='<div class="card" style="margin-top:16px"><div class="card-header">Notes &amp; Communication</div>';
      if(notes.notes&&notes.notes.length){
        notes.notes.forEach(n=>{h+='<div style="padding:8px 0;border-bottom:1px solid #2a3a4a"><span style="color:#4fd1c5;font-size:12px">'+n.author+' - '+n.timestamp+'</span><p style="margin-top:4px">'+n.text+'</p></div>';});
      }else{h+='<p style="color:#8a9bb0">No notes yet</p>';}
      h+='<div style="margin-top:12px"><textarea id="nt" rows="2" placeholder="Add a note..."></textarea><button class="btn btn-primary btn-sm" style="margin-top:8px" onclick="addNote()">Add Note</button></div></div>';
      // History
      h+='<div class="card" style="margin-top:16px"><div class="card-header">History Log</div>';
      if(hist.history&&hist.history.length){
        hist.history.forEach(e=>{h+='<div style="padding:6px 0;border-bottom:1px solid #2a3a4a"><span style="color:#8a9bb0;font-size:12px">'+e.timestamp+'</span> - '+e.event+'</div>';});
      }else{h+='<p style="color:#8a9bb0">No history</p>';}
      h+='</div>';
      $('#app').innerHTML=h;
    }
    async function assignEmp(){
      let n=$('#aemp').value.trim(); if(!n)return;
      await post('/api/employees/assign',{piece_id:PID,employee:n}); load();
    }
    async function addNote(){
      let t=$('#nt').value.trim(); if(!t)return;
      await post('/api/notes/add',{piece_id:PID,text:t,author:'Manager'}); load();
    }
    load();
    """
    return Response(_page_html("Piece Detail", piece_id, body_js), content_type='text/html')


REPORTS_HTML = _page_html("Reports & Trends", "Production analytics &amp; visualizations", """
let items=[];
async function load(){
  let r=await fe('/api/wip'); items=r.items||[];
  renderStageChart(); renderValueChart(); renderTrends();
}
function renderStageChart(){
  let sc={}; items.forEach(i=>{let s=i.stage||'unknown';sc[s]=(sc[s]||0)+1;});
  let labels=Object.keys(sc), data=Object.values(sc);
  let colors=['#4fd1c5','#38b2ac','#81e6d9','#f6ad55','#fc8181','#68d391','#ecc94b','#b794f4','#f687b3'];
  new Chart($('#stageChart'),{type:'doughnut',data:{labels:labels,datasets:[{data:data,backgroundColor:colors.slice(0,labels.length)}]},options:{responsive:true,plugins:{legend:{labels:{color:'#e0e0e0'}}}}});
}
function renderValueChart(){
  let cv={}; items.forEach(i=>{let c=i.customer;if(!cv[c])cv[c]=0;cv[c]+=(parseFloat(i.price)||0);});
  let sorted=Object.entries(cv).sort((a,b)=>b[1]-a[1]).slice(0,15);
  new Chart($('#valueChart'),{type:'bar',data:{labels:sorted.map(s=>s[0].substring(0,20)),datasets:[{label:'Value ($)',data:sorted.map(s=>s[1]),backgroundColor:'rgba(79,209,197,0.6)',borderColor:'#4fd1c5',borderWidth:1}]},options:{responsive:true,indexAxis:'y',scales:{x:{ticks:{color:'#8a9bb0'},grid:{color:'#2a3a4a'}},y:{ticks:{color:'#e0e0e0',font:{size:10}},grid:{display:false}}},plugins:{legend:{display:false}}}});
}
function renderTrends(){
  let sm={}; items.forEach(i=>{let s=i.stage||'unknown';if(!sm[s])sm[s]=0;sm[s]++;});
  let h='<div class="grid grid-3" style="margin-top:20px">';
  Object.entries(sm).sort((a,b)=>b[1]-a[1]).forEach(([s,c])=>{
    let pct=Math.round(c/items.length*100);
    h+='<div class="card"><div style="display:flex;justify-content:space-between;align-items:center"><span style="font-weight:700;color:#4fd1c5">'+s+'</span><span class="badge" style="background:rgba(79,209,197,0.15);color:#4fd1c5">'+c+' ('+pct+'%)</span></div>';
    h+='<div style="margin-top:8px;height:6px;background:#0f1923;border-radius:3px"><div style="height:100%;width:'+pct+'%;background:#4fd1c5;border-radius:3px"></div></div></div>';
  });
  h+='</div>';
  $('#trends').innerHTML=h;
}
load();
""", '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>')

# Override body for reports to include chart canvases
REPORTS_HTML = REPORTS_HTML.replace('<div id="app" style="margin-top:20px"></div>', """<div style="margin-top:20px">
<div class="grid grid-2">
<div class="card"><div class="card-header">Stage Distribution</div><canvas id="stageChart" height="250"></canvas></div>
<div class="card"><div class="card-header">Top Clients by Value</div><canvas id="valueChart" height="250"></canvas></div>
</div>
<div id="trends"></div>
</div><div id="app"></div>""")

@app.route('/reports')
def reports_page():
    return Response(REPORTS_HTML, content_type='text/html')


BOTTLENECKS_HTML = _page_html("Bottleneck Analysis", "Identify production slowdowns &amp; aging work", """
let items=[];
async function load(){
  let r=await fe('/api/wip'); items=r.items||[];
  render();
}
function daysBetween(d){
  if(!d)return null;
  let parts=d.split('/'); if(parts.length!==3)return null;
  let dt=new Date(parts[2],parts[0]-1,parts[1]);
  return Math.floor((new Date()-dt)/(1000*60*60*24));
}
function render(){
  // Stage bottlenecks
  let sc={}; items.forEach(i=>{let s=i.stage||'unknown';if(!sc[s])sc[s]=[];sc[s].push(i);});
  let sorted=Object.entries(sc).sort((a,b)=>b[1].length-a[1].length);
  let maxCount=sorted.length?sorted[0][1].length:1;
  let h='<div class="card" style="margin-bottom:20px"><div class="card-header">Stage Heatmap</div>';
  h+='<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">';
  sorted.forEach(([s,arr])=>{
    let intensity=Math.round(arr.length/maxCount*255);
    let bg='rgba('+intensity+','+(80-intensity*0.3)+',60,0.4)';
    if(arr.length<maxCount*0.3)bg='rgba(79,209,197,0.15)';
    else if(arr.length<maxCount*0.6)bg='rgba(236,201,75,0.2)';
    else bg='rgba(229,62,62,0.25)';
    h+='<div style="padding:12px 20px;border-radius:8px;background:'+bg+';border:1px solid #2a3a4a;text-align:center;min-width:120px"><div style="font-size:24px;font-weight:800;color:#e0e0e0">'+arr.length+'</div><div style="font-size:12px;color:#8a9bb0;margin-top:4px">'+s+'</div></div>';
  });
  h+='</div></div>';
  // Overdue / aging
  let overdue=items.filter(i=>{let d=daysBetween(i.due);return d!==null&&d>0;}).sort((a,b)=>daysBetween(b.due)-daysBetween(a.due));
  h+='<div class="card"><div class="card-header">Overdue Items ('+overdue.length+')</div>';
  if(overdue.length){
    h+='<table><thead><tr><th>Piece</th><th>Customer</th><th>Stage</th><th>Due Date</th><th>Days Overdue</th></tr></thead><tbody>';
    overdue.slice(0,50).forEach(i=>{
      let d=daysBetween(i.due);
      let cls=d>60?'overdue-severe':d>30?'overdue-high':'overdue-medium';
      h+='<tr class="'+cls+'"><td><a href="/piece/'+i.pieceId+'">'+i.name+'</a></td><td>'+i.customer+'</td><td>'+i.stage+'</td><td>'+i.due+'</td><td style="font-weight:700;color:'+(d>60?'#fc8181':d>30?'#f6ad55':'#ecc94b')+'">'+d+' days</td></tr>';
    });
    h+='</tbody></table>';
  }else{h+='<p style="color:#68d391">No overdue items!</p>';}
  h+='</div>';
  $('#app').innerHTML=h;
}
load();
""")

@app.route('/bottlenecks')
def bottlenecks_page():
    return Response(BOTTLENECKS_HTML, content_type='text/html')


DUE_DATES_HTML = _page_html("Due Date Manager", "Track &amp; manage production deadlines", """
let items=[], sortCol='due', sortDir=1, filterStage='all';
async function load(){
  let r=await fe('/api/wip'); items=r.items||[]; render();
}
function parseDue(d){
  if(!d)return new Date('2099-01-01');
  let p=d.split('/'); return new Date(p[2],p[0]-1,p[1]);
}
function daysBetween(d){
  if(!d)return null;
  let p=d.split('/'); if(p.length!==3)return null;
  let dt=new Date(p[2],p[0]-1,p[1]);
  return Math.floor((dt-new Date())/(1000*60*60*24));
}
function render(){
  let f=items.slice();
  if(filterStage!=='all')f=f.filter(i=>i.stage===filterStage);
  f.sort((a,b)=>{
    if(sortCol==='due')return sortDir*(parseDue(a.due)-parseDue(b.due));
    if(sortCol==='name')return sortDir*a.name.localeCompare(b.name);
    if(sortCol==='customer')return sortDir*a.customer.localeCompare(b.customer);
    return 0;
  });
  let stages=[...new Set(items.map(i=>i.stage))].sort();
  let h='<div style="display:flex;gap:12px;align-items:center;margin-bottom:16px;flex-wrap:wrap">';
  h+='<select id="sf" onchange="filterStage=this.value;render()" style="width:auto"><option value="all">All Stages</option>';
  stages.forEach(s=>{h+='<option value="'+s+'"'+(filterStage===s?' selected':'')+'>'+s+'</option>';});
  h+='</select>';
  // Stats
  let due7=items.filter(i=>{let d=daysBetween(i.due);return d!==null&&d>=0&&d<=7;}).length;
  let due30=items.filter(i=>{let d=daysBetween(i.due);return d!==null&&d>=0&&d<=30;}).length;
  let overdue=items.filter(i=>{let d=daysBetween(i.due);return d!==null&&d<0;}).length;
  h+='<span class="badge" style="background:rgba(229,62,62,0.2);color:#fc8181">'+overdue+' Overdue</span>';
  h+='<span class="badge" style="background:rgba(236,201,75,0.2);color:#ecc94b">'+due7+' Due This Week</span>';
  h+='<span class="badge" style="background:rgba(79,209,197,0.15);color:#4fd1c5">'+due30+' Due This Month</span>';
  h+='</div>';
  h+='<table><thead><tr><th style="cursor:pointer" onclick="sortCol=\'name\';sortDir=-sortDir;render()">Piece</th><th style="cursor:pointer" onclick="sortCol=\'customer\';sortDir=-sortDir;render()">Customer</th><th>Stage</th><th style="cursor:pointer" onclick="sortCol=\'due\';sortDir=-sortDir;render()">Due Date</th><th>Days Left</th><th>Price</th></tr></thead><tbody>';
  f.forEach(i=>{
    let d=daysBetween(i.due);
    let cls='', color='#68d391';
    if(d!==null&&d<0){cls='overdue-severe';color='#fc8181';}
    else if(d!==null&&d<=7){cls='overdue-high';color='#f6ad55';}
    else if(d!==null&&d<=30){cls='overdue-medium';color='#ecc94b';}
    h+='<tr class="'+cls+'"><td><a href="/piece/'+i.pieceId+'">'+i.name+'</a></td><td>'+i.customer+'</td><td><span class="badge" style="background:rgba(79,209,197,0.15);color:#4fd1c5">'+i.stage+'</span></td><td>'+(i.due||'N/A')+'</td><td style="font-weight:700;color:'+color+'">'+(d!==null?d+' days':'N/A')+'</td><td>$'+(parseFloat(i.price)||0).toLocaleString()+'</td></tr>';
  });
  h+='</tbody></table>';
  $('#app').innerHTML=h;
}
load();
""")

@app.route('/due-dates')
def due_dates_page():
    return Response(DUE_DATES_HTML, content_type='text/html')


TEAM_HTML = _page_html("Team Management", "Employee roster &amp; assignments", """
let team=[], items=[];
async function load(){
  let r=await fe('/api/team'); team=r.members||[];
  let w=await fe('/api/wip'); items=w.items||[];
  render();
}
function render(){
  let h='<div class="card" style="margin-bottom:20px"><div class="card-header">Add Team Member</div>';
  h+='<div style="display:flex;gap:8px"><input id="nm" placeholder="Name" style="width:200px"><input id="rl" placeholder="Role (e.g. Wax, Metal, Patina)" style="width:200px"><button class="btn btn-primary" onclick="addMember()">Add</button></div></div>';
  h+='<div class="grid grid-3">';
  team.forEach((m,idx)=>{
    let name=typeof m==='string'?m:m.name;
    let role=typeof m==='string'?'':m.role||'';
    h+='<div class="card"><div style="display:flex;justify-content:space-between;align-items:center"><div><span style="font-weight:700;font-size:16px;color:#e0e0e0">'+name+'</span>';
    if(role)h+='<br><span style="font-size:12px;color:#8a9bb0">'+role+'</span>';
    h+='</div><button class="btn btn-danger btn-sm" onclick="removeMember(\''+name+'\')">Remove</button></div></div>';
  });
  if(!team.length)h+='<div class="card"><p style="color:#8a9bb0">No team members added yet</p></div>';
  h+='</div>';
  $('#app').innerHTML=h;
}
async function addMember(){
  let n=$('#nm').value.trim(), r=$('#rl').value.trim();
  if(!n)return;
  await post('/api/team/add',{name:n,role:r}); load();
}
async function removeMember(name){
  await post('/api/team/remove',{name:name}); load();
}
load();
""")

@app.route('/team')
def team_page():
    return Response(TEAM_HTML, content_type='text/html')


QUALITY_HTML = _page_html("Quality & Rework", "Track quality holds, rework &amp; inspections", """
let items=[], reworkLog=[];
async function load(){
  let r=await fe('/api/wip'); items=r.items||[];
  render();
}
function render(){
  let q=($('#qq')||{}).value||'';
  let h='<div class="search-box"><input id="qq" placeholder="Search pieces to log quality issue..." oninput="render()" value="'+q+'"></div>';
  // Stats
  let holds=items.filter(i=>i.status==='hold'||i.status==='rework');
  h+='<div class="grid grid-3" style="margin-bottom:20px">';
  h+='<div class="card stat-card"><div class="stat-value">'+holds.length+'</div><div class="stat-label">On Hold / Rework</div></div>';
  let reworkPct=items.length?Math.round(holds.length/items.length*100):0;
  h+='<div class="card stat-card"><div class="stat-value">'+reworkPct+'%</div><div class="stat-label">Rework Rate</div></div>';
  let activeCount=items.filter(i=>i.status==='active'||i.status==='Active').length;
  h+='<div class="card stat-card"><div class="stat-value text-green">'+activeCount+'</div><div class="stat-label">Active / On Track</div></div>';
  h+='</div>';
  // Log rework form
  if(q){
    let matches=items.filter(i=>i.name.toLowerCase().includes(q.toLowerCase())||i.pieceId.toLowerCase().includes(q.toLowerCase())).slice(0,10);
    if(matches.length){
      h+='<div class="card" style="margin-bottom:16px"><div class="card-header">Log Quality Issue</div>';
      h+='<table><thead><tr><th>Piece</th><th>Customer</th><th>Stage</th><th>Action</th></tr></thead><tbody>';
      matches.forEach(i=>{
        h+='<tr><td>'+i.name+'</td><td>'+i.customer+'</td><td>'+i.stage+'</td><td><button class="btn btn-danger btn-sm" onclick="logRework(\''+i.pieceId+'\',\''+i.name.replace(/'/g,"")+'\')">Log Rework</button></td></tr>';
      });
      h+='</tbody></table></div>';
    }
  }
  // Current holds
  h+='<div class="card"><div class="card-header">Quality Holds &amp; Rework Items</div>';
  if(holds.length){
    h+='<table><thead><tr><th>Piece</th><th>Customer</th><th>Stage</th><th>Status</th><th>Detail</th></tr></thead><tbody>';
    holds.forEach(i=>{
      h+='<tr class="overdue-medium"><td><a href="/piece/'+i.pieceId+'">'+i.name+'</a></td><td>'+i.customer+'</td><td>'+i.stage+'</td><td><span class="badge" style="background:rgba(229,62,62,0.2);color:#fc8181">'+i.status+'</span></td><td><a href="/piece/'+i.pieceId+'">View</a></td></tr>';
    });
    h+='</tbody></table>';
  }else{h+='<p style="color:#68d391">No quality holds currently</p>';}
  h+='</div>';
  $('#app').innerHTML=h;
}
async function logRework(pid,name){
  let reason=prompt('Reason for rework on '+name+'?');
  if(!reason)return;
  await post('/api/rework/log',{piece_id:pid,reason:reason,logged_by:'Manager'});
  alert('Rework logged for '+name);
  load();
}
load();
""")

@app.route('/quality')
def quality_page():
    return Response(QUALITY_HTML, content_type='text/html')



# === MOBILE PAGES - AUTO GENERATED ===


_MOBILE_CSS = "*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}\nbody{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f1923;color:#e0e0e0;min-height:100vh;padding-bottom:70px;overflow-x:hidden}\na{color:#4fd1c5;text-decoration:none}\n\n/* Top bar */\n.topbar{position:fixed;top:0;left:0;right:0;height:56px;background:#1a2634;border-bottom:1px solid #2a3a4a;display:flex;align-items:center;padding:0 16px;z-index:1000}\n.topbar h1{font-size:16px;font-weight:600;color:#e0e0e0;flex:1;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.topbar .logo{font-size:14px;color:#4fd1c5;font-weight:700;margin-right:8px}\n.hamburger{background:none;border:none;color:#e0e0e0;font-size:24px;cursor:pointer;padding:8px;min-width:44px;min-height:44px;display:flex;align-items:center;justify-content:center}\n\n/* Side drawer */\n.drawer-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:1100;opacity:0;pointer-events:none;transition:opacity .25s}\n.drawer-overlay.open{opacity:1;pointer-events:auto}\n.drawer{position:fixed;top:0;left:-280px;width:280px;height:100%;background:#1a2634;z-index:1200;transition:transform .25s ease;overflow-y:auto;padding:20px 0}\n.drawer.open{transform:translateX(280px)}\n.drawer-header{padding:16px 20px;border-bottom:1px solid #2a3a4a;margin-bottom:8px}\n.drawer-header h2{font-size:18px;color:#4fd1c5}\n.drawer-header p{font-size:12px;color:#8a9ab0;margin-top:2px}\n.drawer a{display:flex;align-items:center;gap:12px;padding:14px 20px;color:#c0c8d4;font-size:15px;transition:background .15s}\n.drawer a:hover,.drawer a.active{background:#253344;color:#4fd1c5}\n.drawer .nav-section{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#5a6a7a;padding:16px 20px 8px;font-weight:600}\n\n/* Bottom tab bar */\n.bottombar{position:fixed;bottom:0;left:0;right:0;height:64px;background:#1a2634;border-top:1px solid #2a3a4a;display:flex;z-index:1000;padding-bottom:env(safe-area-inset-bottom)}\n.bottombar a{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:#6a7a8a;font-size:10px;text-decoration:none;transition:color .15s;min-height:44px}\n.bottombar a.active{color:#4fd1c5}\n.bottombar a .icon{font-size:20px}\n\n/* Content area */\n.content{padding:68px 12px 12px;max-width:100%}\n\n/* Cards */\n.card{background:#1a2634;border:1px solid #2a3a4a;border-radius:12px;padding:16px;margin-bottom:12px}\n.card-title{font-size:14px;font-weight:600;color:#8a9ab0;margin-bottom:8px;text-transform:uppercase;letter-spacing:.5px}\n\n/* Stat row */\n.stats-row{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}\n.stat-card{background:#1a2634;border:1px solid #2a3a4a;border-radius:10px;padding:12px;text-align:center}\n.stat-card .val{font-size:24px;font-weight:700;color:#4fd1c5}\n.stat-card .label{font-size:11px;color:#8a9ab0;margin-top:2px}\n\n/* List items (replaces tables) */\n.list-item{background:#1a2634;border:1px solid #2a3a4a;border-radius:10px;padding:14px;margin-bottom:8px;display:flex;flex-direction:column;gap:6px}\n.list-item .item-header{display:flex;justify-content:space-between;align-items:center}\n.list-item .item-title{font-size:15px;font-weight:600;color:#e0e0e0}\n.list-item .item-sub{font-size:13px;color:#8a9ab0}\n.list-item .item-meta{display:flex;gap:8px;flex-wrap:wrap}\n\n/* Badges */\n.badge{display:inline-block;padding:3px 8px;border-radius:6px;font-size:11px;font-weight:600}\n.badge-teal{background:rgba(79,209,197,0.15);color:#4fd1c5}\n.badge-amber{background:rgba(246,173,85,0.15);color:#f6ad55}\n.badge-red{background:rgba(252,129,129,0.15);color:#fc8181}\n.badge-green{background:rgba(104,211,145,0.15);color:#68d391}\n.badge-blue{background:rgba(99,179,237,0.15);color:#63b3ed}\n.badge-purple{background:rgba(183,148,244,0.15);color:#b794f4}\n.badge-gray{background:rgba(160,174,192,0.15);color:#a0aec0}\n\n/* Search */\n.search-box{width:100%;padding:12px 16px;background:#0f1923;border:1px solid #2a3a4a;border-radius:10px;color:#e0e0e0;font-size:15px;margin-bottom:12px;-webkit-appearance:none}\n.search-box:focus{outline:none;border-color:#4fd1c5}\n.search-box::placeholder{color:#5a6a7a}\n\n/* Buttons */\n.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 16px;border-radius:8px;font-size:14px;font-weight:600;border:none;cursor:pointer;min-height:44px}\n.btn-primary{background:#4fd1c5;color:#0f1923}\n.btn-secondary{background:#2a3a4a;color:#e0e0e0}\n.btn-danger{background:rgba(252,129,129,0.15);color:#fc8181}\n.btn-sm{padding:6px 12px;font-size:12px;min-height:36px}\n\n/* Form elements */\ninput,select,textarea{background:#0f1923;border:1px solid #2a3a4a;border-radius:8px;color:#e0e0e0;padding:10px 12px;font-size:15px;width:100%;min-height:44px;-webkit-appearance:none}\ninput:focus,select:focus,textarea:focus{outline:none;border-color:#4fd1c5}\nselect{background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%238a9ab0' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E\");background-repeat:no-repeat;background-position:right 12px center;padding-right:32px}\n\n/* Progress bars */\n.progress-bar{background:#0f1923;border-radius:6px;height:8px;overflow:hidden;margin-top:4px}\n.progress-fill{height:100%;border-radius:6px;background:#4fd1c5;transition:width .3s}\n\n/* Section headers */\n.section-title{font-size:13px;font-weight:700;color:#8a9ab0;text-transform:uppercase;letter-spacing:1px;margin:16px 0 8px;padding:0 4px}\n\n/* Chart container */\n.chart-wrap{background:#1a2634;border:1px solid #2a3a4a;border-radius:12px;padding:12px;margin-bottom:12px}\n.chart-wrap canvas{width:100%!important;height:250px!important}\n\n/* Overdue severity */\n.severity-high{border-left:3px solid #fc8181}\n.severity-med{border-left:3px solid #f6ad55}\n.severity-low{border-left:3px solid #ecc94b}\n\n/* Heatmap cells */\n.heat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(90px,1fr));gap:6px}\n.heat-cell{border-radius:8px;padding:10px;text-align:center}\n.heat-cell .stage-name{font-size:11px;color:#e0e0e0;font-weight:600}\n.heat-cell .stage-count{font-size:20px;font-weight:700}\n\n/* Toggle / tabs */\n.tabs{display:flex;gap:4px;overflow-x:auto;padding-bottom:8px;margin-bottom:12px;-webkit-overflow-scrolling:touch}\n.tabs::-webkit-scrollbar{display:none}\n.tab{padding:8px 14px;border-radius:8px;font-size:13px;white-space:nowrap;background:#1a2634;border:1px solid #2a3a4a;color:#8a9ab0;cursor:pointer;min-height:36px}\n.tab.active{background:#4fd1c5;color:#0f1923;border-color:#4fd1c5;font-weight:600}\n\n/* Empty state */\n.empty{text-align:center;padding:40px 20px;color:#5a6a7a}\n.empty .icon{font-size:40px;margin-bottom:12px}\n\n/* Utility */\n.mt-8{margin-top:8px}.mt-16{margin-top:16px}.mb-8{margin-bottom:8px}\n.text-teal{color:#4fd1c5}.text-red{color:#fc8181}.text-amber{color:#f6ad55}.text-green{color:#68d391}\n.text-sm{font-size:13px}.text-xs{font-size:11px}\n.flex{display:flex}.flex-between{display:flex;justify-content:space-between;align-items:center}\n.gap-8{gap:8px}.gap-4{gap:4px}\n.truncate{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n"
_MOBILE_NAV = "<div class=\"nav-section\">Original Pages</div>\n<a href=\"/m/\">&#x1F3ED; Dashboard</a>\n<a href=\"/m/schedule\">&#x1F4C5; Schedule</a>\n<a href=\"/m/kpi\">&#x1F4CA; KPI</a>\n<a href=\"/m/maintenance\">&#x1F527; Maintenance</a>\n<a href=\"/m/shipping\">&#x1F4E6; Shipping</a>\n<div class=\"nav-section\">Production Management</div>\n<a href=\"/m/clients\">&#x1F465; Clients</a>\n<a href=\"/m/reports\">&#x1F4C8; Reports</a>\n<a href=\"/m/bottlenecks\">&#x26A0; Bottlenecks</a>\n<a href=\"/m/due-dates\">&#x1F4C5; Due Dates</a>\n<a href=\"/m/team\">&#x1F477; Team</a>\n<a href=\"/m/quality\">&#x2705; Quality</a>"
_MOBILE_BOTTOM = "<a href=\"/m/\" id=\"tab-home\"><span class=\"icon\">&#x1F3ED;</span>Home</a>\n<a href=\"/m/clients\" id=\"tab-clients\"><span class=\"icon\">&#x1F465;</span>Clients</a>\n<a href=\"/m/reports\" id=\"tab-reports\"><span class=\"icon\">&#x1F4C8;</span>Reports</a>\n<a href=\"/m/due-dates\" id=\"tab-dates\"><span class=\"icon\">&#x1F4C5;</span>Dates</a>\n<a href=\"/m/quality\" id=\"tab-quality\"><span class=\"icon\">&#x2705;</span>Quality</a>"

def _mobile_html(title, body_content, active_tab="", extra_head=""):
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<title>""" + title + """ - Pyrology</title>
<style>""" + _MOBILE_CSS + """</style>
""" + extra_head + """
</head>
<body>
<div class="topbar">
  <button class="hamburger" onclick="toggleDrawer()" aria-label="Menu">&#9776;</button>
  <span class="logo">PYROLOGY</span>
  <h1>""" + title + """</h1>
  <a href="/" style="font-size:12px;color:#8a9ab0;padding:8px">Desktop</a>
</div>
<div class="drawer-overlay" id="overlay" onclick="toggleDrawer()"></div>
<div class="drawer" id="drawer">
  <div class="drawer-header"><h2>&#x1F525; Pyrology</h2><p>Production WIP</p></div>
  """ + _MOBILE_NAV + """
</div>
<div class="content">
""" + body_content + """
</div>
<div class="bottombar">
  """ + _MOBILE_BOTTOM + """
</div>
<script>
function toggleDrawer(){ var d=document.getElementById('drawer'),o=document.getElementById('overlay'); d.classList.toggle('open'); o.classList.toggle('open'); }
// Highlight active tab
document.querySelectorAll('.bottombar a').forEach(function(a){ if(a.getAttribute('href')===window.location.pathname) a.classList.add('active'); });
// Highlight active drawer link
document.querySelectorAll('.drawer a').forEach(function(a){ if(a.getAttribute('href')===window.location.pathname) a.classList.add('active'); });
</script>
"""


MOBILE_DASH_HTML = _mobile_html("Dashboard", "\n<div class=\"stats-row\" id=\"mStats\">\n  <div class=\"stat-card\"><div class=\"val\" id=\"mTotal\">--</div><div class=\"label\">Total Pieces</div></div>\n  <div class=\"stat-card\"><div class=\"val\" id=\"mActive\">--</div><div class=\"label\">Active</div></div>\n  <div class=\"stat-card\"><div class=\"val\" id=\"mOverdue\">--</div><div class=\"label\">Overdue</div></div>\n  <div class=\"stat-card\"><div class=\"val\" id=\"mClients\">--</div><div class=\"label\">Clients</div></div>\n</div>\n<div class=\"section-title\">Stage Breakdown</div>\n<div id=\"mStages\"></div>\n<div class=\"section-title\">Recent Items</div>\n<input class=\"search-box\" placeholder=\"Search pieces...\" oninput=\"filterItems(this.value)\" id=\"mSearch\">\n<div id=\"mItems\"></div>\n<script>\nfunction loadDash(){\n  fetch('/api/wip').then(r=>r.json()).then(function(d){\n    var items=d.items||d||[];\n    if(items.items) items=items.items;\n    document.getElementById('mTotal').textContent=items.length;\n    var clients={},stages={},overdue=0,now=new Date();\n    items.forEach(function(it){\n      clients[it.customer||'']=1;\n      var s=it.currentStage||it.stage||'Unknown';\n      stages[s]=(stages[s]||0)+1;\n      if(it.dueDate){var dd=new Date(it.dueDate);if(dd<now)overdue++;}\n    });\n    document.getElementById('mActive').textContent=items.filter(function(i){return(i.status||'').toLowerCase()!=='completed'}).length;\n    document.getElementById('mOverdue').textContent=overdue;\n    document.getElementById('mClients').textContent=Object.keys(clients).length;\n    var sh='';\n    Object.keys(stages).sort(function(a,b){return stages[b]-stages[a]}).forEach(function(s){\n      var pct=Math.round(stages[s]/items.length*100);\n      sh+='<div class=\"card\"><div class=\"flex-between\"><span>'+s+'</span><span class=\"text-teal\">'+stages[s]+' ('+pct+'%)</span></div><div class=\"progress-bar\"><div class=\"progress-fill\" style=\"width:'+pct+'%\"></div></div></div>';\n    });\n    document.getElementById('mStages').innerHTML=sh;\n    window._allItems=items;\n    showItems(items.slice(0,30));\n  });\n}\nfunction showItems(items){\n  var h='';\n  items.forEach(function(it){\n    var stage=it.currentStage||it.stage||'';\n    h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+((it.name||it.pieceName||'Piece #'+it.pieceId).substring(0,30))+'</span><span class=\"badge badge-teal\">'+stage+'</span></div><div class=\"item-sub\">'+((it.customer||''))+' &middot; Job '+(it.job||'')+'</div></a>';\n  });\n  if(!h) h='<div class=\"empty\"><div class=\"icon\">&#x1F50D;</div>No items found</div>';\n  document.getElementById('mItems').innerHTML=h;\n}\nfunction filterItems(q){\n  if(!window._allItems)return;\n  q=q.toLowerCase();\n  var f=window._allItems.filter(function(i){\n    return (i.name||'').toLowerCase().indexOf(q)>-1||(i.customer||'').toLowerCase().indexOf(q)>-1||(i.job||'').toLowerCase().indexOf(q)>-1||String(i.pieceId).indexOf(q)>-1;\n  });\n  showItems(f.slice(0,50));\n}\nloadDash();\n</script>\n", "home")

MOBILE_CLIENTS_HTML = _mobile_html("Clients", "\n<input class=\"search-box\" placeholder=\"Search clients...\" oninput=\"filterClients(this.value)\" id=\"cSearch\">\n<div class=\"stats-row\" id=\"cStats\">\n  <div class=\"stat-card\"><div class=\"val\" id=\"cTotal\">--</div><div class=\"label\">Clients</div></div>\n  <div class=\"stat-card\"><div class=\"val\" id=\"cPieces\">--</div><div class=\"label\">Pieces</div></div>\n</div>\n<div id=\"cList\"></div>\n<script>\nfunction loadClients(){\n  fetch('/api/wip').then(r=>r.json()).then(function(d){\n    var items=d.items||d||[];\n    if(items.items) items=items.items;\n    var clients={};\n    items.forEach(function(it){\n      var c=it.customer||'Unknown';\n      if(!clients[c])clients[c]={name:c,count:0,stages:{},value:0};\n      clients[c].count++;\n      var s=it.currentStage||it.stage||'Unknown';\n      clients[c].stages[s]=(clients[c].stages[s]||0)+1;\n      clients[c].value+=(parseFloat(it.price)||0);\n    });\n    var arr=Object.values(clients).sort(function(a,b){return b.count-a.count});\n    window._allClients=arr;\n    document.getElementById('cTotal').textContent=arr.length;\n    document.getElementById('cPieces').textContent=items.length;\n    showClients(arr);\n  });\n}\nfunction showClients(arr){\n  var h='';\n  arr.forEach(function(c){\n    var badges='';\n    Object.keys(c.stages).forEach(function(s){badges+='<span class=\"badge badge-teal\" style=\"margin:2px\">'+s+': '+c.stages[s]+'</span>';});\n    h+='<a href=\"/m/clients/'+encodeURIComponent(c.name)+'\" class=\"list-item\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title\">'+c.name+'</span><span class=\"badge badge-blue\">'+c.count+' pcs</span></div><div class=\"item-sub\">$'+c.value.toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0})+'</div><div class=\"item-meta\" style=\"margin-top:4px\">'+badges+'</div></a>';\n  });\n  if(!h) h='<div class=\"empty\"><div class=\"icon\">&#x1F465;</div>No clients yet</div>';\n  document.getElementById('cList').innerHTML=h;\n}\nfunction filterClients(q){\n  if(!window._allClients)return;\n  q=q.toLowerCase();\n  showClients(window._allClients.filter(function(c){return c.name.toLowerCase().indexOf(q)>-1}));\n}\nloadClients();\n</script>\n", "clients")

MOBILE_REPORTS_HTML = _mobile_html("Reports & Trends", "\n<div class=\"chart-wrap\"><canvas id=\"stageChart\"></canvas></div>\n<div class=\"chart-wrap\"><canvas id=\"clientChart\"></canvas></div>\n<div class=\"section-title\">Stage Summary</div>\n<div id=\"rStages\"></div>\n<script src=\"https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js\"></script>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var stages={},clients={};\n  items.forEach(function(it){\n    var s=it.currentStage||it.stage||'Unknown';\n    stages[s]=(stages[s]||0)+1;\n    var c=it.customer||'Unknown';\n    clients[c]=(clients[c]||0)+(parseFloat(it.price)||0);\n  });\n  var sKeys=Object.keys(stages).sort(function(a,b){return stages[b]-stages[a]});\n  var colors=['#4fd1c5','#63b3ed','#b794f4','#f6ad55','#fc8181','#68d391','#ecc94b','#e0e0e0'];\n  new Chart(document.getElementById('stageChart'),{type:'doughnut',data:{labels:sKeys,datasets:[{data:sKeys.map(function(k){return stages[k]}),backgroundColor:colors}]},options:{responsive:true,plugins:{legend:{position:'bottom',labels:{color:'#8a9ab0',font:{size:11}}}}}});\n  var cArr=Object.entries(clients).sort(function(a,b){return b[1]-a[1]}).slice(0,10);\n  new Chart(document.getElementById('clientChart'),{type:'bar',data:{labels:cArr.map(function(c){return c[0].substring(0,15)}),datasets:[{data:cArr.map(function(c){return c[1]}),backgroundColor:'#4fd1c5'}]},options:{indexAxis:'y',responsive:true,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#8a9ab0',callback:function(v){return'$'+v.toLocaleString()}}},y:{ticks:{color:'#8a9ab0',font:{size:11}}}}}});\n  var sh='';\n  sKeys.forEach(function(s){\n    var pct=Math.round(stages[s]/items.length*100);\n    sh+='<div class=\"card\"><div class=\"flex-between\"><span>'+s+'</span><span class=\"text-teal\">'+stages[s]+' ('+pct+'%)</span></div><div class=\"progress-bar\"><div class=\"progress-fill\" style=\"width:'+pct+'%\"></div></div></div>';\n  });\n  document.getElementById('rStages').innerHTML=sh;\n});\n</script>\n", "reports")

MOBILE_BOTTLENECKS_HTML = _mobile_html("Bottleneck Analysis", "\n<div class=\"section-title\">Stage Heatmap</div>\n<div class=\"heat-grid\" id=\"bHeat\"></div>\n<div class=\"section-title mt-16\">Overdue Items</div>\n<div id=\"bOverdue\"></div>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var stages={},now=new Date(),overdue=[];\n  items.forEach(function(it){\n    var s=it.currentStage||it.stage||'Unknown';\n    stages[s]=(stages[s]||0)+1;\n    if(it.dueDate){var dd=new Date(it.dueDate);if(dd<now){var days=Math.floor((now-dd)/(86400000));overdue.push({name:it.name||it.pieceName||'Piece',customer:it.customer||'',stage:s,days:days,pieceId:it.pieceId});}}\n  });\n  var max=Math.max.apply(null,Object.values(stages))||1;\n  var hh='';\n  Object.keys(stages).sort(function(a,b){return stages[b]-stages[a]}).forEach(function(s){\n    var ratio=stages[s]/max;\n    var bg=ratio>0.7?'rgba(252,129,129,0.25)':ratio>0.4?'rgba(246,173,85,0.2)':'rgba(104,211,145,0.15)';\n    var clr=ratio>0.7?'#fc8181':ratio>0.4?'#f6ad55':'#68d391';\n    hh+='<div class=\"heat-cell\" style=\"background:'+bg+'\"><div class=\"stage-name\">'+s+'</div><div class=\"stage-count\" style=\"color:'+clr+'\">'+stages[s]+'</div></div>';\n  });\n  document.getElementById('bHeat').innerHTML=hh;\n  overdue.sort(function(a,b){return b.days-a.days});\n  var oh='';\n  overdue.slice(0,30).forEach(function(it){\n    var sev=it.days>60?'severity-high':it.days>30?'severity-med':'severity-low';\n    oh+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item '+sev+'\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+it.name+'</span><span class=\"badge badge-red\">'+it.days+'d overdue</span></div><div class=\"item-sub\">'+it.customer+' &middot; '+it.stage+'</div></a>';\n  });\n  if(!oh) oh='<div class=\"empty\"><div class=\"icon\">&#x2705;</div>No overdue items!</div>';\n  document.getElementById('bOverdue').innerHTML=oh;\n});\n</script>\n", "bottlenecks")

MOBILE_DUEDATES_HTML = _mobile_html("Due Dates", "\n<div class=\"tabs\" id=\"ddTabs\"><div class=\"tab active\" onclick=\"filterDD('all')\">All</div><div class=\"tab\" onclick=\"filterDD('overdue')\">Overdue</div><div class=\"tab\" onclick=\"filterDD('week')\">This Week</div><div class=\"tab\" onclick=\"filterDD('month')\">This Month</div></div>\n<div class=\"stats-row\">\n  <div class=\"stat-card\"><div class=\"val text-red\" id=\"ddOverdue\">--</div><div class=\"label\">Overdue</div></div>\n  <div class=\"stat-card\"><div class=\"val text-amber\" id=\"ddWeek\">--</div><div class=\"label\">This Week</div></div>\n</div>\n<div id=\"ddList\"></div>\n<script>\nvar _ddItems=[];\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var now=new Date(),week=new Date(now.getTime()+7*86400000),month=new Date(now.getTime()+30*86400000);\n  _ddItems=items.filter(function(i){return i.dueDate}).map(function(it){\n    var dd=new Date(it.dueDate);\n    var days=Math.round((dd-now)/86400000);\n    return{name:it.name||it.pieceName||'Piece',customer:it.customer||'',stage:it.currentStage||it.stage||'',dueDate:it.dueDate,days:days,pieceId:it.pieceId,isOverdue:days<0,isWeek:days>=0&&days<=7,isMonth:days>=0&&days<=30};\n  }).sort(function(a,b){return a.days-b.days});\n  document.getElementById('ddOverdue').textContent=_ddItems.filter(function(i){return i.isOverdue}).length;\n  document.getElementById('ddWeek').textContent=_ddItems.filter(function(i){return i.isWeek}).length;\n  filterDD('all');\n});\nfunction filterDD(mode){\n  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});\n  event.target.classList.add('active');\n  var f=_ddItems;\n  if(mode==='overdue')f=_ddItems.filter(function(i){return i.isOverdue});\n  else if(mode==='week')f=_ddItems.filter(function(i){return i.isWeek});\n  else if(mode==='month')f=_ddItems.filter(function(i){return i.isMonth});\n  var h='';\n  f.slice(0,50).forEach(function(it){\n    var badge=it.isOverdue?'<span class=\"badge badge-red\">'+Math.abs(it.days)+'d overdue</span>':it.isWeek?'<span class=\"badge badge-amber\">'+it.days+'d left</span>':'<span class=\"badge badge-green\">'+it.days+'d left</span>';\n    var sev=it.isOverdue?(it.days<-30?'severity-high':'severity-med'):'';\n    h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item '+sev+'\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+it.name+'</span>'+badge+'</div><div class=\"item-sub\">'+it.customer+' &middot; '+it.stage+' &middot; '+it.dueDate+'</div></a>';\n  });\n  if(!h)h='<div class=\"empty\">No items in this category</div>';\n  document.getElementById('ddList').innerHTML=h;\n}\n</script>\n", "dates")

MOBILE_TEAM_HTML = _mobile_html("Team", "\n<div class=\"card\">\n  <div class=\"card-title\">Add Team Member</div>\n  <div style=\"display:flex;gap:8px;flex-wrap:wrap\">\n    <input id=\"tmName\" placeholder=\"Name\" style=\"flex:1;min-width:120px\">\n    <input id=\"tmRole\" placeholder=\"Role\" style=\"flex:1;min-width:120px\">\n    <button class=\"btn btn-primary\" onclick=\"addMember()\">Add</button>\n  </div>\n</div>\n<div class=\"section-title\">Team Members</div>\n<div id=\"tmList\"></div>\n<script>\nfunction loadTeam(){\n  fetch('/api/team').then(r=>r.json()).then(function(d){\n    var members=d.members||[];\n    var h='';\n    members.forEach(function(m){\n      h+='<div class=\"list-item\"><div class=\"item-header\"><span class=\"item-title\">'+m.name+'</span><button class=\"btn btn-danger btn-sm\" onclick=\"removeMember(\\''+m.name+'\\')\">Remove</button></div><div class=\"item-sub\">'+m.role+'</div></div>';\n    });\n    if(!h)h='<div class=\"empty\"><div class=\"icon\">&#x1F477;</div>No team members yet</div>';\n    document.getElementById('tmList').innerHTML=h;\n  });\n}\nfunction addMember(){\n  var n=document.getElementById('tmName').value.trim();\n  var r=document.getElementById('tmRole').value.trim();\n  if(!n)return;\n  fetch('/api/team/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,role:r||'General'})}).then(function(){document.getElementById('tmName').value='';document.getElementById('tmRole').value='';loadTeam();});\n}\nfunction removeMember(name){\n  if(!confirm('Remove '+name+'?'))return;\n  fetch('/api/team/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name})}).then(function(){loadTeam();});\n}\nloadTeam();\n</script>\n", "team")

MOBILE_QUALITY_HTML = _mobile_html("Quality & Rework", "\n<div class=\"stats-row\">\n  <div class=\"stat-card\"><div class=\"val text-red\" id=\"qHolds\">--</div><div class=\"label\">On Hold</div></div>\n  <div class=\"stat-card\"><div class=\"val text-amber\" id=\"qRework\">--</div><div class=\"label\">Rework Rate</div></div>\n</div>\n<div class=\"card\">\n  <div class=\"card-title\">Log Rework</div>\n  <input id=\"qSearch\" class=\"search-box\" placeholder=\"Search piece to log rework...\" style=\"margin-bottom:8px\" oninput=\"searchRework(this.value)\">\n  <div id=\"qResults\"></div>\n</div>\n<div class=\"section-title\">Quality Holds</div>\n<div id=\"qHoldsList\"></div>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  window._qItems=items;\n  var holds=items.filter(function(i){return(i.status||'').toLowerCase().indexOf('hold')>-1||(i.status||'').toLowerCase().indexOf('rework')>-1});\n  document.getElementById('qHolds').textContent=holds.length;\n  document.getElementById('qRework').textContent=(items.length?Math.round(holds.length/items.length*100):0)+'%';\n  var h='';\n  holds.forEach(function(it){\n    h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item severity-high\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+(it.name||it.pieceName||'Piece')+'</span><span class=\"badge badge-red\">'+(it.status||'Hold')+'</span></div><div class=\"item-sub\">'+(it.customer||'')+' &middot; '+(it.currentStage||it.stage||'')+'</div></a>';\n  });\n  if(!h)h='<div class=\"empty\"><div class=\"icon\">&#x2705;</div>No quality holds</div>';\n  document.getElementById('qHoldsList').innerHTML=h;\n});\nfunction searchRework(q){\n  if(!q||!window._qItems)return;\n  q=q.toLowerCase();\n  var f=window._qItems.filter(function(i){return(i.name||'').toLowerCase().indexOf(q)>-1||(i.customer||'').toLowerCase().indexOf(q)>-1}).slice(0,5);\n  var h='';\n  f.forEach(function(it){\n    h+='<div class=\"list-item\" style=\"cursor:pointer\" onclick=\"logRework(\\''+it.pieceId+'\\',\\''+(it.name||'Piece').replace(/'/g,'')+'\\')\"><div class=\"item-header\"><span class=\"item-title\">'+(it.name||'Piece')+'</span><span class=\"btn btn-danger btn-sm\">Log Rework</span></div></div>';\n  });\n  document.getElementById('qResults').innerHTML=h;\n}\nfunction logRework(pid,pname){\n  var reason=prompt('Rework reason for '+pname+':');\n  if(!reason)return;\n  fetch('/api/rework/log',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({piece_id:pid,reason:reason})}).then(function(){alert('Rework logged');document.getElementById('qSearch').value='';document.getElementById('qResults').innerHTML='';});\n}\n</script>\n", "quality")

MOBILE_SCHEDULE_HTML = _mobile_html("Schedule", "\n<div class=\"section-title\">Schedule by Stage</div>\n<div class=\"tabs\" id=\"schTabs\"></div>\n<div id=\"schList\"></div>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var stages={};\n  items.forEach(function(it){var s=it.currentStage||it.stage||'Unknown';if(!stages[s])stages[s]=[];stages[s].push(it);});\n  var sKeys=Object.keys(stages).sort(function(a,b){return stages[b].length-stages[a].length});\n  window._schStages=stages;window._schKeys=sKeys;\n  var tabs='<div class=\"tab active\" onclick=\"showStage(0)\">All ('+items.length+')</div>';\n  sKeys.forEach(function(s,i){tabs+='<div class=\"tab\" onclick=\"showStage('+(i+1)+')\">'+s+' ('+stages[s].length+')</div>';});\n  document.getElementById('schTabs').innerHTML=tabs;\n  showStageItems(items.slice(0,50));\n});\nfunction showStage(idx){\n  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});\n  event.target.classList.add('active');\n  if(idx===0){fetch('/api/wip').then(r=>r.json()).then(function(d){var items=d.items||d||[];if(items.items)items=items.items;showStageItems(items.slice(0,50));});return;}\n  var s=window._schKeys[idx-1];\n  showStageItems((window._schStages[s]||[]).slice(0,50));\n}\nfunction showStageItems(items){\n  var h='';\n  items.forEach(function(it){\n    h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+(it.name||it.pieceName||'Piece')+'</span><span class=\"badge badge-teal\">'+(it.currentStage||it.stage||'')+'</span></div><div class=\"item-sub\">'+(it.customer||'')+' &middot; '+(it.dueDate||'No date')+'</div></a>';\n  });\n  if(!h)h='<div class=\"empty\">No items</div>';\n  document.getElementById('schList').innerHTML=h;\n}\n</script>\n", "schedule")

MOBILE_KPI_HTML = _mobile_html("KPI", "\n<div class=\"stats-row\" id=\"kStats\"></div>\n<div class=\"section-title\">Stage Distribution</div>\n<div class=\"chart-wrap\"><canvas id=\"kChart\"></canvas></div>\n<div class=\"section-title\">Value by Client (Top 10)</div>\n<div id=\"kClients\"></div>\n<script src=\"https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js\"></script>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var total=items.length,totalVal=0,clients={},stages={},overdue=0,now=new Date();\n  items.forEach(function(it){\n    totalVal+=(parseFloat(it.price)||0);\n    var c=it.customer||'Unknown';clients[c]=(clients[c]||0)+(parseFloat(it.price)||0);\n    var s=it.currentStage||it.stage||'Unknown';stages[s]=(stages[s]||0)+1;\n    if(it.dueDate&&new Date(it.dueDate)<now)overdue++;\n  });\n  document.getElementById('kStats').innerHTML='<div class=\"stat-card\"><div class=\"val\">'+total+'</div><div class=\"label\">Total Pieces</div></div><div class=\"stat-card\"><div class=\"val\">$'+Math.round(totalVal).toLocaleString()+'</div><div class=\"label\">Total Value</div></div><div class=\"stat-card\"><div class=\"val text-red\">'+overdue+'</div><div class=\"label\">Overdue</div></div><div class=\"stat-card\"><div class=\"val\">'+Object.keys(clients).length+'</div><div class=\"label\">Clients</div></div>';\n  var sKeys=Object.keys(stages).sort(function(a,b){return stages[b]-stages[a]});\n  var colors=['#4fd1c5','#63b3ed','#b794f4','#f6ad55','#fc8181','#68d391','#ecc94b','#e0e0e0'];\n  new Chart(document.getElementById('kChart'),{type:'doughnut',data:{labels:sKeys,datasets:[{data:sKeys.map(function(k){return stages[k]}),backgroundColor:colors}]},options:{responsive:true,plugins:{legend:{position:'bottom',labels:{color:'#8a9ab0',font:{size:11}}}}}});\n  var cArr=Object.entries(clients).sort(function(a,b){return b[1]-a[1]}).slice(0,10);\n  var h='';\n  cArr.forEach(function(c,i){\n    var pct=Math.round(c[1]/totalVal*100);\n    h+='<div class=\"card\"><div class=\"flex-between\"><span>'+(i+1)+'. '+c[0]+'</span><span class=\"text-teal\">$'+Math.round(c[1]).toLocaleString()+' ('+pct+'%)</span></div><div class=\"progress-bar\"><div class=\"progress-fill\" style=\"width:'+pct+'%\"></div></div></div>';\n  });\n  document.getElementById('kClients').innerHTML=h;\n});\n</script>\n", "kpi")

MOBILE_MAINT_HTML = _mobile_html("Maintenance", "\n<div class=\"section-title\">Maintenance Overview</div>\n<div id=\"maintList\"></div>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var maint=items.filter(function(i){var s=(i.currentStage||i.stage||'').toLowerCase();return s.indexOf('maint')>-1||s.indexOf('repair')>-1||s.indexOf('touch')>-1;});\n  if(!maint.length)maint=items.filter(function(i){var s=(i.status||'').toLowerCase();return s.indexOf('maint')>-1||s.indexOf('repair')>-1;});\n  var h='<div class=\"stats-row\"><div class=\"stat-card\"><div class=\"val\">'+maint.length+'</div><div class=\"label\">Maintenance Items</div></div><div class=\"stat-card\"><div class=\"val\">'+items.length+'</div><div class=\"label\">Total WIP</div></div></div>';\n  if(maint.length){\n    maint.forEach(function(it){\n      h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+(it.name||it.pieceName||'Piece')+'</span><span class=\"badge badge-amber\">'+(it.currentStage||it.stage||'')+'</span></div><div class=\"item-sub\">'+(it.customer||'')+' &middot; '+(it.dueDate||'No date')+'</div></a>';\n    });\n  }else{\n    h+='<div class=\"empty\"><div class=\"icon\">&#x1F527;</div>No items in maintenance stages</div>';\n  }\n  document.getElementById('maintList').innerHTML=h;\n});\n</script>\n", "maintenance")

MOBILE_SHIPPING_HTML = _mobile_html("Shipping", "\n<div class=\"section-title\">Shipping Overview</div>\n<div id=\"shipList\"></div>\n<script>\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=d.items||d||[];\n  if(items.items)items=items.items;\n  var ship=items.filter(function(i){var s=(i.currentStage||i.stage||'').toLowerCase();return s.indexOf('ship')>-1||s.indexOf('pack')>-1||s.indexOf('deliver')>-1||s.indexOf('complete')>-1;});\n  var h='<div class=\"stats-row\"><div class=\"stat-card\"><div class=\"val\">'+ship.length+'</div><div class=\"label\">Ready / Shipping</div></div><div class=\"stat-card\"><div class=\"val\">'+items.length+'</div><div class=\"label\">Total WIP</div></div></div>';\n  if(ship.length){\n    ship.forEach(function(it){\n      h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+(it.name||it.pieceName||'Piece')+'</span><span class=\"badge badge-green\">'+(it.currentStage||it.stage||'')+'</span></div><div class=\"item-sub\">'+(it.customer||'')+' &middot; '+(it.dueDate||'No date')+'</div></a>';\n    });\n  }else{\n    h+='<div class=\"empty\"><div class=\"icon\">&#x1F4E6;</div>No items in shipping stages</div>';\n  }\n  document.getElementById('shipList').innerHTML=h;\n});\n</script>\n", "shipping")

MOBILE_CLIENT_DETAIL_HTML = _mobile_html("Client Detail", "\n<div id=\"cdHeader\"></div>\n<div id=\"cdList\"></div>\n<script>\nvar cname=decodeURIComponent(window.location.pathname.replace('/m/clients/',''));\ndocument.querySelector('.topbar h1').textContent=cname;\nfetch('/api/wip').then(r=>r.json()).then(function(d){\n  var items=(d.items||d||[]).filter(function(i){return(i.customer||'')==cname});\n  document.getElementById('cdHeader').innerHTML='<div class=\"stats-row\"><div class=\"stat-card\"><div class=\"val\">'+items.length+'</div><div class=\"label\">Pieces</div></div><div class=\"stat-card\"><div class=\"val\">$'+items.reduce(function(s,i){return s+(parseFloat(i.price)||0)},0).toLocaleString()+'</div><div class=\"label\">Value</div></div></div>';\n  var h='';\n  items.forEach(function(it){\n    h+='<a href=\"/m/piece/'+it.pieceId+'\" class=\"list-item\" style=\"text-decoration:none;color:inherit\"><div class=\"item-header\"><span class=\"item-title truncate\">'+(it.name||it.pieceName||'Piece')+'</span><span class=\"badge badge-teal\">'+(it.currentStage||it.stage||'')+'</span></div><div class=\"item-sub\">Job '+(it.job||'')+' &middot; '+(it.dueDate||'No due date')+'</div></a>';\n  });\n  document.getElementById('cdList').innerHTML=h||'<div class=\"empty\">No pieces for this client</div>';\n});\n</script>\n", "clients")

MOBILE_PIECE_DETAIL_HTML = _mobile_html("Piece Detail", "\n<div id=\"pdInfo\"></div>\n<div class=\"section-title\">Notes</div>\n<div style=\"display:flex;gap:8px;margin-bottom:8px\"><input id=\"noteInput\" class=\"search-box\" placeholder=\"Add a note...\" style=\"margin:0;flex:1\"><button class=\"btn btn-primary btn-sm\" onclick=\"addNote()\">Add</button></div>\n<div id=\"pdNotes\"></div>\n<div class=\"section-title\">History</div>\n<div id=\"pdHistory\"></div>\n<script>\nvar pid=window.location.pathname.replace('/m/piece/','');\nfunction loadPiece(){\n  fetch('/api/wip').then(r=>r.json()).then(function(d){\n    var items=d.items||d||[];\n    if(items.items)items=items.items;\n    var it=items.find(function(i){return String(i.pieceId)==pid})||{};\n    document.querySelector('.topbar h1').textContent=it.name||it.pieceName||'Piece #'+pid;\n    var h='<div class=\"card\">';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Customer</span><span class=\"text-teal\">'+(it.customer||'--')+'</span></div>';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Job</span><span>'+(it.job||'--')+'</span></div>';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Stage</span><span class=\"badge badge-teal\">'+(it.currentStage||it.stage||'--')+'</span></div>';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Status</span><span>'+(it.status||'--')+'</span></div>';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Due Date</span><span>'+(it.dueDate||'--')+'</span></div>';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Price</span><span>$'+(it.price||'0')+'</span></div>';\n    h+='<div class=\"flex-between mb-8\"><span class=\"text-sm\">Metal</span><span>'+(it.metal||'--')+'</span></div>';\n    h+='<div class=\"flex-between\"><span class=\"text-sm\">Tier</span><span>'+(it.tier||'--')+'</span></div>';\n    h+='</div>';\n    document.getElementById('pdInfo').innerHTML=h;\n  });\n}\nfunction loadNotes(){\n  fetch('/api/notes/'+pid).then(r=>r.json()).then(function(d){\n    var notes=d.notes||[];\n    var h='';\n    notes.forEach(function(n){\n      h+='<div class=\"list-item\"><div class=\"item-header\"><span class=\"text-sm text-teal\">'+(n.author||'System')+'</span><span class=\"text-xs\" style=\"color:#5a6a7a\">'+(n.timestamp||'')+'</span></div><div style=\"margin-top:4px\">'+n.text+'</div></div>';\n    });\n    if(!h)h='<div class=\"empty\" style=\"padding:20px\"><div class=\"text-sm\" style=\"color:#5a6a7a\">No notes yet</div></div>';\n    document.getElementById('pdNotes').innerHTML=h;\n  });\n}\nfunction addNote(){\n  var t=document.getElementById('noteInput').value.trim();\n  if(!t)return;\n  fetch('/api/notes/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({piece_id:pid,text:t,author:'Mobile'})}).then(function(){document.getElementById('noteInput').value='';loadNotes();});\n}\nfunction loadHistory(){\n  fetch('/api/history/'+pid).then(r=>r.json()).then(function(d){\n    var hist=d.history||[];\n    var h='';\n    hist.forEach(function(e){\n      h+='<div class=\"list-item\"><div class=\"item-header\"><span class=\"text-sm\">'+(e.action||'Event')+'</span><span class=\"text-xs\" style=\"color:#5a6a7a\">'+(e.timestamp||'')+'</span></div></div>';\n    });\n    if(!h)h='<div class=\"empty\" style=\"padding:20px\"><div class=\"text-sm\" style=\"color:#5a6a7a\">No history yet</div></div>';\n    document.getElementById('pdHistory').innerHTML=h;\n  });\n}\nloadPiece();loadNotes();loadHistory();\n</script>\n", "")

@app.route('/m/')
def mobile_dash():
    return Response(MOBILE_DASH_HTML, content_type='text/html')

@app.route('/m/clients')
def mobile_clients():
    return Response(MOBILE_CLIENTS_HTML, content_type='text/html')

@app.route('/m/reports')
def mobile_reports():
    return Response(MOBILE_REPORTS_HTML, content_type='text/html')

@app.route('/m/bottlenecks')
def mobile_bottlenecks():
    return Response(MOBILE_BOTTLENECKS_HTML, content_type='text/html')

@app.route('/m/due-dates')
def mobile_duedates():
    return Response(MOBILE_DUEDATES_HTML, content_type='text/html')

@app.route('/m/team')
def mobile_team():
    return Response(MOBILE_TEAM_HTML, content_type='text/html')

@app.route('/m/quality')
def mobile_quality():
    return Response(MOBILE_QUALITY_HTML, content_type='text/html')

@app.route('/m/schedule')
def mobile_schedule():
    return Response(MOBILE_SCHEDULE_HTML, content_type='text/html')

@app.route('/m/kpi')
def mobile_kpi():
    return Response(MOBILE_KPI_HTML, content_type='text/html')

@app.route('/m/maintenance')
def mobile_maint():
    return Response(MOBILE_MAINT_HTML, content_type='text/html')

@app.route('/m/shipping')
def mobile_shipping():
    return Response(MOBILE_SHIPPING_HTML, content_type='text/html')

@app.route('/m/clients/<path:cname>')
def mobile_client_detail(cname):
    return Response(MOBILE_CLIENT_DETAIL_HTML, content_type='text/html')

@app.route('/m/piece/<piece_id>')
def mobile_piece_detail(piece_id):
    return Response(MOBILE_PIECE_DETAIL_HTML, content_type='text/html')



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

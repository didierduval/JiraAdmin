#!/usr/bin/env python3
"""
Generate an interactive DPR dashboard with multi-select filters, date pickers,
text search, sortable columns — and publish it to Confluence as an attachment.

The HTML is fully self-contained (no external dependencies) and works both as a
local file and as a Confluence attachment opened in a new tab.

Usage:
    python _confluence_dashboard.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from jira import JIRA

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

# ── Config ───────────────────────────────────────────────────────────

JIRA_SERVER = os.environ.get('JIRA_SERVER', 'https://fifo24.atlassian.net')
JIRA_EMAIL  = os.environ.get('JIRA_EMAIL', '')
JIRA_TOKEN  = os.environ.get('JIRA_API_TOKEN', '')
PROJECT_KEY = os.environ.get('PROJECT_KEY', 'DPR')

WIKI_BASE   = f"{JIRA_SERVER}/wiki"
CONF_SPACE  = os.environ.get('CONFLUENCE_SPACE', 'FRACAS')
PAGE_TITLE  = os.environ.get('CONFLUENCE_PAGE', 'DPR Hierarchical Dashboard')
HTML_FILE   = 'dpr_dashboard.html'

# ── Connections ──────────────────────────────────────────────────────

j = JIRA(options={'server': JIRA_SERVER}, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))

session = requests.Session()
session.auth = (JIRA_EMAIL, JIRA_TOKEN)
session.headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})


# =====================================================================
#  1. Fetch ALL issues with all relevant fields
# =====================================================================

def fetch_all_issues():
    print("  Discovering custom fields...")
    dpr_type_id = None
    for f in j.fields():
        if f['name'] == 'DPR Type':
            dpr_type_id = f['id']
            break
    print(f"    DPR Type field: {dpr_type_id or '(not found)'}")

    fetch_fields = 'summary,status,assignee,issuetype,parent,labels,created,updated,priority,components,duedate'
    if dpr_type_id:
        fetch_fields += f',{dpr_type_id}'

    print(f"  Fetching all issues from {PROJECT_KEY}...")
    raw = []
    start = 0
    while True:
        batch = j.search_issues(
            f'project = {PROJECT_KEY} ORDER BY key ASC',
            startAt=start, maxResults=100, fields=fetch_fields,
        )
        raw.extend(batch)
        if start + len(batch) >= batch.total:
            break
        start += len(batch)
    print(f"    {len(raw)} issues fetched")

    issues = []
    meta = dict(statuses=set(), assignees=set(), components=set(),
                priorities=set(), labels=set(), types=set(),
                dpr_types=set(), parents={})

    for iss in raw:
        f = iss.fields
        assignee = f.assignee.displayName if f.assignee else None
        components = [c.name for c in f.components] if f.components else []
        labels = list(f.labels) if f.labels else []
        parent_key = f.parent.key if hasattr(f, 'parent') and f.parent else None
        parent_summary = None
        if hasattr(f, 'parent') and f.parent and hasattr(f.parent, 'fields'):
            parent_summary = f.parent.fields.summary
        dpr_type = None
        if dpr_type_id:
            val = getattr(f, dpr_type_id, None)
            if val and hasattr(val, 'value'):
                dpr_type = val.value

        d = dict(
            key=iss.key,
            summary=f.summary,
            type=f.issuetype.name,
            status=f.status.name,
            statusCat=f.status.statusCategory.key,
            assignee=assignee,
            components=components,
            priority=f.priority.name if f.priority else 'Medium',
            labels=labels,
            parent=parent_key,
            dprType=dpr_type,
            due=str(f.duedate) if f.duedate else None,
            created=str(f.created)[:10] if f.created else None,
            updated=str(f.updated)[:10] if f.updated else None,
        )
        issues.append(d)

        meta['statuses'].add(d['status'])
        if assignee:
            meta['assignees'].add(assignee)
        meta['components'].update(components)
        meta['priorities'].add(d['priority'])
        meta['labels'].update(labels)
        meta['types'].add(d['type'])
        if dpr_type:
            meta['dpr_types'].add(dpr_type)
        if parent_key:
            meta['parents'][parent_key] = parent_summary or parent_key

    for k in ('statuses', 'assignees', 'components', 'priorities', 'labels', 'types', 'dpr_types'):
        meta[k] = sorted(meta[k])

    print(f"    Statuses:   {meta['statuses']}")
    print(f"    Assignees:  {meta['assignees']}")
    print(f"    Components: {meta['components']}")
    print(f"    Priorities: {meta['priorities']}")
    print(f"    Labels:     {meta['labels']}")
    print(f"    Types:      {meta['types']}")
    print(f"    DPR Types:  {meta['dpr_types']}")
    print(f"    Parents:    {list(meta['parents'].keys())}")

    return issues, meta


# =====================================================================
#  2. Generate self-contained interactive HTML
# =====================================================================

def generate_html(issues, meta):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    today = datetime.now().strftime('%Y-%m-%d')
    issues_json = json.dumps(issues, ensure_ascii=False)
    meta_json = json.dumps(meta, ensure_ascii=False)

    # ── CSS ──────────────────────────────────────────────────────────
    css = r'''
:root {
  --bg: #f4f5f7; --white: #fff; --border: #dfe1e6; --text: #172b4d;
  --muted: #6b778c; --accent: #0052cc; --accent-hover: #0747a6;
  --green: #36b37e; --yellow: #ff991f; --red: #de350b; --purple: #6554c0;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg);color:var(--text);font-size:13px}

/* Header */
.header{background:var(--accent);color:#fff;padding:14px 24px;display:flex;
  justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.header h1{font-size:18px;font-weight:600}
.header-meta{font-size:12px;opacity:.8}
.header a{color:#bbdefb;text-decoration:none;font-size:13px}
.header a:hover{color:#fff;text-decoration:underline}

/* Filter Panel */
.filter-panel{background:var(--white);border-bottom:1px solid var(--border);padding:14px 24px}
.filter-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-bottom:10px}
.filter-dates{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:10px;align-items:end}
.date-group{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.date-group label{font-weight:600;color:var(--muted);min-width:60px;font-size:11px;
  text-transform:uppercase;letter-spacing:.3px}
.date-group input[type=date]{padding:5px 8px;border:1px solid var(--border);border-radius:4px;font-size:12px;width:130px}
.date-preset{padding:3px 8px;border:1px solid var(--border);border-radius:3px;background:var(--bg);
  font-size:11px;cursor:pointer;color:var(--muted)}
.date-preset:hover{background:#e4e6ea}
.filter-text{margin-bottom:10px}
.filter-text input{width:100%;max-width:500px;padding:7px 12px;border:1px solid var(--border);
  border-radius:4px;font-size:13px}
.filter-text input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px rgba(0,82,204,.2)}
.filter-actions{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.quick-btns{display:flex;gap:6px;flex-wrap:wrap}
.qbtn{padding:5px 12px;border:1px solid var(--border);border-radius:4px;background:var(--white);
  font-size:12px;cursor:pointer;color:var(--text);transition:all .15s}
.qbtn:hover{border-color:var(--accent);color:var(--accent)}
.qbtn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-reset{padding:5px 14px;border:none;border-radius:4px;background:var(--bg);
  color:var(--muted);font-size:12px;cursor:pointer}
.btn-reset:hover{background:#ddd;color:var(--text)}
.btn-export{padding:5px 14px;border:1px solid var(--border);border-radius:4px;background:var(--white);
  color:var(--text);font-size:12px;cursor:pointer}
.btn-export:hover{background:var(--bg)}
.jql-bar{margin-top:8px;padding:6px 10px;background:#f0f4ff;border:1px solid #c3d4f7;
  border-radius:4px;font-family:'SFMono-Regular',Consolas,monospace;font-size:11px;
  word-break:break-all;display:none;cursor:pointer;position:relative}
.jql-bar.visible{display:block}
.jql-bar .copy-hint{position:absolute;right:8px;top:6px;color:var(--accent);font-family:inherit;font-size:10px}

/* MultiSelect Component */
.ms{position:relative}
.ms-label{display:block;font-weight:600;color:var(--muted);margin-bottom:3px;font-size:11px;
  text-transform:uppercase;letter-spacing:.3px}
.ms-btn{display:flex;align-items:center;justify-content:space-between;width:100%;
  padding:6px 10px;border:1px solid var(--border);border-radius:4px;background:var(--white);
  cursor:pointer;font-size:13px;color:var(--text);min-height:32px}
.ms-btn:hover{border-color:var(--accent)}
.ms-btn .arrow{font-size:10px;color:var(--muted);margin-left:6px;transition:transform .15s}
.ms-btn.open .arrow{transform:rotate(180deg)}
.ms-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ms-drop{position:absolute;top:calc(100% + 2px);left:0;z-index:200;width:280px;
  background:var(--white);border:1px solid var(--border);border-radius:6px;
  box-shadow:0 8px 24px rgba(0,0,0,.15);display:none}
.ms-drop.open{display:block}
.ms-search{width:100%;padding:8px 10px;border:none;border-bottom:1px solid var(--border);
  font-size:13px;outline:none}
.ms-acts{display:flex;gap:12px;padding:4px 10px;border-bottom:1px solid var(--border);font-size:11px}
.ms-acts a{color:var(--accent);text-decoration:none;cursor:pointer}
.ms-acts a:hover{text-decoration:underline}
.ms-opts{max-height:220px;overflow-y:auto;padding:4px 0}
.ms-opt{display:flex;align-items:center;gap:6px;padding:4px 10px;cursor:pointer;user-select:none}
.ms-opt:hover{background:var(--bg)}
.ms-opt input{accent-color:var(--accent);cursor:pointer}
.ms-opt .cnt{margin-left:auto;color:var(--muted);font-size:11px}
.ms-drop .no-match{padding:8px 10px;color:var(--muted);font-style:italic;font-size:12px}

/* Stats Bar */
.stats-bar{padding:10px 24px;display:flex;align-items:center;gap:20px;flex-wrap:wrap;
  background:var(--white);border-bottom:1px solid var(--border);font-size:12px;color:var(--muted)}
.stats-bar b{color:var(--text)}
.view-toggle{margin-left:auto;display:flex;gap:0}
.view-toggle button{padding:4px 12px;border:1px solid var(--border);background:var(--white);
  font-size:12px;cursor:pointer;color:var(--muted)}
.view-toggle button:first-child{border-radius:4px 0 0 4px}
.view-toggle button:last-child{border-radius:0 4px 4px 0;border-left:none}
.view-toggle button.active{background:var(--accent);color:#fff;border-color:var(--accent)}

/* Table */
.table-wrap{overflow-x:auto;background:var(--white)}
table{width:100%;border-collapse:collapse}
thead th{position:sticky;top:0;z-index:100;background:#f4f5f7;padding:9px 12px;text-align:left;
  font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;
  border-bottom:2px solid var(--border);cursor:pointer;white-space:nowrap;user-select:none}
thead th:hover{color:var(--accent)}
thead th .sort-arrow{font-size:10px;margin-left:3px;opacity:.4}
thead th.sorted .sort-arrow{opacity:1;color:var(--accent)}
tbody tr{border-bottom:1px solid #f4f5f7;transition:background .1s}
tbody tr:hover{background:#f8f9fb}
tbody tr.dpr-row{background:#fafbfc;font-weight:500}
tbody tr.dpr-row td{padding-top:12px;padding-bottom:12px;border-top:2px solid var(--border)}
tbody tr.done-row{opacity:.5}
tbody tr.overdue-row td.col-due{color:var(--red);font-weight:600}
td{padding:7px 12px;font-size:13px;vertical-align:middle}
td a{color:var(--accent);text-decoration:none;font-weight:500}
td a:hover{text-decoration:underline}
.col-key{white-space:nowrap;min-width:80px}
.col-summary{max-width:380px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.col-status{white-space:nowrap}
.col-assignee{white-space:nowrap}
.col-comp{white-space:nowrap;font-size:12px;color:var(--muted)}
.col-labels{white-space:nowrap}
.col-parent{white-space:nowrap}
.badge{display:inline-block;padding:2px 8px;border-radius:3px;color:#fff;font-size:10px;
  font-weight:700;text-transform:uppercase;letter-spacing:.3px}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;background:#dfe1e6;color:#42526e;
  font-size:11px;margin-right:2px}
.unassigned{color:#b3bac5;font-style:italic}
.empty-row td{text-align:center;padding:40px;color:var(--muted);font-size:14px;font-style:italic}

/* Hierarchical view */
.hier-group{margin:0}
.hier-header{display:flex;align-items:center;gap:10px;padding:10px 24px;background:#fafbfc;
  border-bottom:2px solid var(--border);cursor:pointer;user-select:none}
.hier-header:hover{background:#f0f1f3}
.hier-header .arrow{transition:transform .2s;font-size:12px;color:var(--muted)}
.hier-header.collapsed .arrow{transform:rotate(-90deg)}
.hier-header .dpr-key{font-weight:600}
.hier-header .dpr-key a{color:var(--accent);text-decoration:none}
.hier-children{overflow:hidden;transition:max-height .3s ease}
.hier-children.collapsed{max-height:0!important;overflow:hidden}

/* Footer */
.footer{padding:14px 24px;text-align:center;font-size:12px;color:var(--muted)}
.footer a{color:var(--accent);text-decoration:none}

@media(max-width:900px){
  .filter-grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}
  .col-summary{max-width:200px}
  .col-comp,.col-labels,.col-created,.col-updated{display:none}
}
.overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;z-index:150}
'''

    # ── HTML Body ────────────────────────────────────────────────────
    html_body = f'''
<div class="overlay" id="overlay"></div>

<div class="header">
  <div>
    <h1>\U0001f527 FRACAS DPR Dashboard</h1>
    <span class="header-meta">Project: {PROJECT_KEY} \u00b7 Data snapshot: {now}</span>
  </div>
  <a href="{JIRA_SERVER}/jira/software/c/projects/{PROJECT_KEY}/list" target="_blank">Open in Jira \u2197</a>
</div>

<div class="filter-panel">
  <div class="filter-grid">
    <div class="ms" id="ms-status"></div>
    <div class="ms" id="ms-assignee"></div>
    <div class="ms" id="ms-component"></div>
    <div class="ms" id="ms-priority"></div>
    <div class="ms" id="ms-type"></div>
    <div class="ms" id="ms-label"></div>
    <div class="ms" id="ms-dprtype"></div>
    <div class="ms" id="ms-parent"></div>
  </div>

  <div class="filter-dates">
    <div class="date-group">
      <label>Due</label>
      <input type="date" id="due-from" onchange="applyFilters()">
      <span>\u2014</span>
      <input type="date" id="due-to" onchange="applyFilters()">
      <button class="date-preset" onclick="setDuePreset('overdue')">Past due</button>
      <button class="date-preset" onclick="setDuePreset('week')">This week</button>
      <button class="date-preset" onclick="setDuePreset('30d')">Next 30d</button>
      <button class="date-preset" onclick="setDuePreset('nodue')">No due date</button>
    </div>
    <div class="date-group">
      <label>Created</label>
      <input type="date" id="created-from" onchange="applyFilters()">
      <span>\u2014</span>
      <input type="date" id="created-to" onchange="applyFilters()">
    </div>
    <div class="date-group">
      <label>Updated</label>
      <input type="date" id="updated-from" onchange="applyFilters()">
      <span>\u2014</span>
      <input type="date" id="updated-to" onchange="applyFilters()">
    </div>
  </div>

  <div class="filter-text">
    <input type="text" id="text-search" placeholder="\U0001f50d Search by key or summary\u2026" oninput="applyFilters()">
  </div>

  <div class="filter-actions">
    <div class="quick-btns">
      <button class="qbtn active" data-preset="all" onclick="quickFilter('all')">All Issues</button>
      <button class="qbtn" data-preset="open" onclick="quickFilter('open')">Open Only</button>
      <button class="qbtn" data-preset="overdue" onclick="quickFilter('overdue')">Past Due</button>
      <button class="qbtn" data-preset="unassigned" onclick="quickFilter('unassigned')">Unassigned</button>
      <button class="qbtn" data-preset="stale" onclick="quickFilter('stale')">Stale 30d+</button>
    </div>
    <div style="display:flex;gap:6px;align-items:center">
      <button class="btn-export" onclick="toggleJql()">JQL</button>
      <button class="btn-export" onclick="exportCsv()">Export CSV</button>
      <button class="btn-reset" onclick="resetFilters()">\u2715 Reset All</button>
    </div>
  </div>

  <div class="jql-bar" id="jql-bar" onclick="copyJql()">
    <span id="jql-text"></span>
    <span class="copy-hint">click to copy</span>
  </div>
</div>

<div class="stats-bar">
  <span id="stat-show">Showing <b>0</b> of <b>0</b></span>
  <span id="stat-extra"></span>
  <div class="view-toggle">
    <button id="vt-flat" class="active" onclick="setView('flat')">Flat</button>
    <button id="vt-hier" onclick="setView('hier')">Grouped by DPR</button>
  </div>
</div>

<div class="table-wrap" id="flat-view">
  <table>
    <thead><tr>
      <th data-col="key" onclick="sortBy('key')">Key <span class="sort-arrow">\u2195</span></th>
      <th data-col="summary" onclick="sortBy('summary')">Summary <span class="sort-arrow">\u2195</span></th>
      <th data-col="type" onclick="sortBy('type')">Type <span class="sort-arrow">\u2195</span></th>
      <th data-col="status" onclick="sortBy('status')">Status <span class="sort-arrow">\u2195</span></th>
      <th data-col="assignee" onclick="sortBy('assignee')">Assignee <span class="sort-arrow">\u2195</span></th>
      <th data-col="components" onclick="sortBy('components')">Component <span class="sort-arrow">\u2195</span></th>
      <th data-col="priority" onclick="sortBy('priority')">Priority <span class="sort-arrow">\u2195</span></th>
      <th class="col-labels" data-col="labels" onclick="sortBy('labels')">Labels <span class="sort-arrow">\u2195</span></th>
      <th data-col="due" onclick="sortBy('due')">Due <span class="sort-arrow">\u2195</span></th>
      <th data-col="parent" onclick="sortBy('parent')">Parent <span class="sort-arrow">\u2195</span></th>
      <th class="col-created" data-col="created" onclick="sortBy('created')">Created <span class="sort-arrow">\u2195</span></th>
      <th class="col-updated" data-col="updated" onclick="sortBy('updated')">Updated <span class="sort-arrow">\u2195</span></th>
    </tr></thead>
    <tbody id="tbody-flat"></tbody>
  </table>
</div>

<div id="hier-view" style="display:none"></div>

<div class="footer">
  FRACAS Engineering ({PROJECT_KEY}) \u00b7 <a href="{JIRA_SERVER}/jira/software/c/projects/{PROJECT_KEY}/list" target="_blank">Open in Jira</a>
  \u00b7 Snapshot: {now}
</div>
'''

    # ── JavaScript ───────────────────────────────────────────────────
    js = r'''
/* ── Status colors ──────────────────────────────────────────────── */
const STATUS_COLOR = {
  'Open':'#4A6785','In Progress':'#0052CC','Done':'#36B37E',
  'Approved':'#00875A','Root Cause Analysis (RCA)':'#FF991F',
  'Corrective Action (CA)':'#FF8B00','Verification (VR)':'#6554C0',
  'Close-Out (CO)':'#0065FF','Closed (CL)':'#36B37E','Backlog':'#6B778C',
};
const CAT_COLOR = {new:'#4A6785',indeterminate:'#0052CC',done:'#36B37E'};
function statusColor(s,cat){return STATUS_COLOR[s]||CAT_COLOR[cat]||'#6B778C';}

const TYPE_ICON = {DPR:'\uD83D\uDD34',Task:'\u2705','Sub-task':'\uD83D\uDCCB',
  Story:'\uD83D\uDCD6',Bug:'\uD83D\uDC1B'};
function typeIcon(t){return TYPE_ICON[t]||'\uD83D\uDCC4';}
function esc(s){if(!s)return'';const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

/* ── MultiSelect class ──────────────────────────────────────────── */
const ALL_MS=[];

class MultiSelect{
  constructor(id,label,options){
    this.el=document.getElementById(id);this.label=label;
    this.options=options;this.selected=new Set(options.map(o=>o.value));
    this.isOpen=false;this.build();ALL_MS.push(this);
  }
  build(){
    this.el.innerHTML=`
      <span class="ms-label">${esc(this.label)}</span>
      <button class="ms-btn" type="button">
        <span class="ms-text"></span><span class="arrow">\u25BE</span>
      </button>
      <div class="ms-drop">
        <input class="ms-search" type="text" placeholder="Search\u2026">
        <div class="ms-acts">
          <a class="sel-all">Select All</a>
          <a class="sel-none">Clear All</a>
        </div>
        <div class="ms-opts"></div>
      </div>`;
    this.btn=this.el.querySelector('.ms-btn');
    this.drop=this.el.querySelector('.ms-drop');
    this.optsEl=this.el.querySelector('.ms-opts');
    this.searchEl=this.el.querySelector('.ms-search');
    this.textEl=this.el.querySelector('.ms-text');
    this.renderOpts();this.updText();
    this.btn.addEventListener('click',e=>{e.stopPropagation();this.toggle();});
    this.el.querySelector('.sel-all').addEventListener('click',e=>{e.preventDefault();this.selAll();});
    this.el.querySelector('.sel-none').addEventListener('click',e=>{e.preventDefault();this.clrAll();});
    this.searchEl.addEventListener('input',()=>this.filterOpts());
    this.drop.addEventListener('click',e=>e.stopPropagation());
  }
  renderOpts(){
    if(!this.options.length){this.optsEl.innerHTML='<div class="no-match">No options</div>';return;}
    this.optsEl.innerHTML=this.options.map(o=>{
      const chk=this.selected.has(o.value)?'checked':'';
      return`<label class="ms-opt" data-v="${esc(o.value)}">
        <input type="checkbox" value="${esc(o.value)}" ${chk}>
        <span>${esc(o.label)}</span><span class="cnt">${o.count}</span></label>`;
    }).join('');
    this.optsEl.querySelectorAll('input[type=checkbox]').forEach(cb=>{
      cb.addEventListener('change',()=>{
        if(cb.checked)this.selected.add(cb.value);else this.selected.delete(cb.value);
        this.updText();applyFilters();
      });
    });
  }
  toggle(){
    this.isOpen=!this.isOpen;
    this.drop.classList.toggle('open',this.isOpen);
    this.btn.classList.toggle('open',this.isOpen);
    if(this.isOpen){this.searchEl.value='';this.filterOpts();this.searchEl.focus();
      ALL_MS.forEach(m=>{if(m!==this&&m.isOpen)m.close();});}
  }
  close(){this.isOpen=false;this.drop.classList.remove('open');this.btn.classList.remove('open');}
  selAll(){this.selected=new Set(this.options.map(o=>o.value));
    this.optsEl.querySelectorAll('input').forEach(cb=>cb.checked=true);this.updText();applyFilters();}
  clrAll(){this.selected.clear();
    this.optsEl.querySelectorAll('input').forEach(cb=>cb.checked=false);this.updText();applyFilters();}
  updText(){
    const n=this.selected.size,t=this.options.length;
    if(n===t)this.textEl.textContent=`All (${t})`;
    else if(n===0)this.textEl.textContent='None';
    else this.textEl.textContent=`${n} of ${t}`;
  }
  filterOpts(){
    const q=this.searchEl.value.toLowerCase();let any=false;
    this.optsEl.querySelectorAll('.ms-opt').forEach(el=>{
      const show=el.dataset.v.toLowerCase().includes(q);
      el.style.display=show?'':'none';if(show)any=true;});
    let nm=this.optsEl.querySelector('.no-match');
    if(!any&&!nm){nm=document.createElement('div');nm.className='no-match';
      nm.textContent='No matching options';this.optsEl.appendChild(nm);}
    else if(any&&nm)nm.remove();
  }
  getSelected(){return this.selected;}
  setValues(vals){this.selected=new Set(vals);
    this.optsEl.querySelectorAll('input').forEach(cb=>cb.checked=this.selected.has(cb.value));
    this.updText();}
}
document.addEventListener('click',()=>ALL_MS.forEach(m=>m.close()));

/* ── Count helpers ──────────────────────────────────────────────── */
function countBy(arr,fn){const m={};arr.forEach(i=>{const k=fn(i);m[k]=(m[k]||0)+1;});return m;}

/* ── Build multiselects ─────────────────────────────────────────── */
const statusCounts=countBy(ISSUES,i=>i.status);
const assigneeCounts=countBy(ISSUES,i=>i.assignee||'(Unassigned)');
const compCounts=(()=>{const m={};ISSUES.forEach(i=>{if(!i.components.length)m['(None)']=(m['(None)']||0)+1;
  else i.components.forEach(c=>m[c]=(m[c]||0)+1);});return m;})();
const prioCounts=countBy(ISSUES,i=>i.priority);
const typeCounts=countBy(ISSUES,i=>i.type);
const labelCounts=(()=>{const m={};ISSUES.forEach(i=>{if(!i.labels.length)m['(None)']=(m['(None)']||0)+1;
  else i.labels.forEach(l=>m[l]=(m[l]||0)+1);});return m;})();
const dprTypeCounts=countBy(ISSUES,i=>i.dprType||'(None)');
const parentCounts=countBy(ISSUES,i=>i.parent||'(Top Level)');

function makeOpts(counts,order){
  if(order){const keys=[...order];Object.keys(counts).forEach(k=>{if(!keys.includes(k))keys.push(k);});
    return keys.filter(k=>counts[k]).map(k=>({value:k,label:k,count:counts[k]||0}));}
  return Object.keys(counts).sort().map(k=>({value:k,label:k,count:counts[k]}));
}

const msStatus=new MultiSelect('ms-status','Status',makeOpts(statusCounts,META.statuses));
const msAssignee=new MultiSelect('ms-assignee','Assignee',makeOpts(assigneeCounts));
const msComponent=new MultiSelect('ms-component','Component',makeOpts(compCounts));
const msPriority=new MultiSelect('ms-priority','Priority',
  makeOpts(prioCounts,['Highest','High','Medium','Low','Lowest']));
const msType=new MultiSelect('ms-type','Type',makeOpts(typeCounts));
const msLabel=new MultiSelect('ms-label','Label',makeOpts(labelCounts));
const msDprType=new MultiSelect('ms-dprtype','DPR Type',makeOpts(dprTypeCounts));
const msParent=new MultiSelect('ms-parent','Parent',makeOpts(parentCounts));

/* ── State ──────────────────────────────────────────────────────── */
let currentSort={col:'key',asc:true};
let currentView='flat';
let filtered=[...ISSUES];
let noDueOnly=false;

/* ── Apply Filters ──────────────────────────────────────────────── */
function applyFilters(){
  const text=document.getElementById('text-search').value.toLowerCase().trim();
  const dueFrom=document.getElementById('due-from').value;
  const dueTo=document.getElementById('due-to').value;
  const creFrom=document.getElementById('created-from').value;
  const creTo=document.getElementById('created-to').value;
  const updFrom=document.getElementById('updated-from').value;
  const updTo=document.getElementById('updated-to').value;

  const sSt=msStatus.getSelected(),sAs=msAssignee.getSelected();
  const sCo=msComponent.getSelected(),sPr=msPriority.getSelected();
  const sTy=msType.getSelected(),sLa=msLabel.getSelected();
  const sDt=msDprType.getSelected(),sPa=msParent.getSelected();

  filtered=ISSUES.filter(iss=>{
    if(!sSt.has(iss.status))return false;
    if(!sAs.has(iss.assignee||'(Unassigned)'))return false;
    if(!iss.components.length){if(!sCo.has('(None)'))return false;}
    else{if(!iss.components.some(c=>sCo.has(c)))return false;}
    if(!sPr.has(iss.priority))return false;
    if(!sTy.has(iss.type))return false;
    if(!iss.labels.length){if(!sLa.has('(None)'))return false;}
    else{if(!iss.labels.some(l=>sLa.has(l)))return false;}
    if(!sDt.has(iss.dprType||'(None)'))return false;
    if(!sPa.has(iss.parent||'(Top Level)'))return false;
    if(text&&!iss.key.toLowerCase().includes(text)&&!iss.summary.toLowerCase().includes(text))return false;
    if(noDueOnly){if(iss.due)return false;}
    else{
      if(dueFrom&&(!iss.due||iss.due<dueFrom))return false;
      if(dueTo&&(!iss.due||iss.due>dueTo))return false;
    }
    if(creFrom&&(!iss.created||iss.created<creFrom))return false;
    if(creTo&&(!iss.created||iss.created>creTo))return false;
    if(updFrom&&(!iss.updated||iss.updated<updFrom))return false;
    if(updTo&&(!iss.updated||iss.updated>updTo))return false;
    return true;
  });
  sortFiltered();render();updateStats();updateJql();
}

/* ── Sorting ────────────────────────────────────────────────────── */
function sortFiltered(){
  const c=currentSort.col,asc=currentSort.asc;
  filtered.sort((a,b)=>{
    let va=a[c],vb=b[c];
    if(va==null)va='';if(vb==null)vb='';
    if(Array.isArray(va))va=va.join(', ');if(Array.isArray(vb))vb=vb.join(', ');
    if(c==='key'){const na=parseInt(va.split('-')[1])||0,nb=parseInt(vb.split('-')[1])||0;
      return asc?na-nb:nb-na;}
    return asc?String(va).localeCompare(String(vb)):String(vb).localeCompare(String(va));
  });
}
function sortBy(col){
  if(currentSort.col===col)currentSort.asc=!currentSort.asc;
  else{currentSort.col=col;currentSort.asc=true;}
  document.querySelectorAll('thead th').forEach(th=>{
    th.classList.toggle('sorted',th.dataset.col===col);
    const arr=th.querySelector('.sort-arrow');
    if(arr&&th.dataset.col===col)arr.textContent=currentSort.asc?'\u25B2':'\u25BC';
    else if(arr)arr.textContent='\u2195';
  });
  sortFiltered();render();
}

/* ── Render ─────────────────────────────────────────────────────── */
function renderRow(iss){
  const overdue=iss.due&&iss.due<TODAY&&iss.statusCat!=='done';
  const cls=[iss.type==='DPR'?'dpr-row':'',iss.statusCat==='done'?'done-row':'',
    overdue?'overdue-row':''].filter(Boolean).join(' ');
  const bg=statusColor(iss.status,iss.statusCat);
  const comp=iss.components.length?esc(iss.components.join(', ')):'\u2014';
  const lbl=iss.labels.length?iss.labels.map(l=>`<span class="tag">${esc(l)}</span>`).join(' '):'\u2014';
  const assignee=iss.assignee?esc(iss.assignee):'<span class="unassigned">Unassigned</span>';
  const parent=iss.parent?`<a href="${JIRA_URL}/browse/${iss.parent}" target="_blank">${iss.parent}</a>`:'\u2014';
  return`<tr class="${cls}">
    <td class="col-key"><a href="${JIRA_URL}/browse/${iss.key}" target="_blank">${iss.key}</a></td>
    <td class="col-summary" title="${esc(iss.summary)}">${esc(iss.summary)}</td>
    <td>${typeIcon(iss.type)} ${esc(iss.type)}</td>
    <td class="col-status"><span class="badge" style="background:${bg}">${esc(iss.status)}</span></td>
    <td class="col-assignee">${assignee}</td>
    <td class="col-comp">${comp}</td>
    <td>${esc(iss.priority)}</td>
    <td class="col-labels">${lbl}</td>
    <td class="col-due">${iss.due||'\u2014'}</td>
    <td class="col-parent">${parent}</td>
    <td class="col-created">${iss.created||'\u2014'}</td>
    <td class="col-updated">${iss.updated||'\u2014'}</td></tr>`;
}
function render(){if(currentView==='flat')renderFlat();else renderHier();}
function renderFlat(){
  document.getElementById('flat-view').style.display='';
  document.getElementById('hier-view').style.display='none';
  const tbody=document.getElementById('tbody-flat');
  if(!filtered.length){tbody.innerHTML='<tr class="empty-row"><td colspan="12">No issues match the current filters</td></tr>';return;}
  tbody.innerHTML=filtered.map(renderRow).join('');
}
function renderHier(){
  document.getElementById('flat-view').style.display='none';
  const hv=document.getElementById('hier-view');hv.style.display='';
  const groups={},topLevel=[];
  filtered.forEach(iss=>{if(iss.parent){if(!groups[iss.parent])groups[iss.parent]=[];groups[iss.parent].push(iss);}
    else topLevel.push(iss);});
  const dprs=topLevel.filter(i=>i.type==='DPR'),nonDprs=topLevel.filter(i=>i.type!=='DPR');
  let html='';
  dprs.forEach(dpr=>{
    const children=groups[dpr.key]||[];const bg=statusColor(dpr.status,dpr.statusCat);
    html+=`<div class="hier-group">
      <div class="hier-header" onclick="toggleHier(this)">
        <span class="arrow">\u25BC</span>
        <span class="dpr-key"><a href="${JIRA_URL}/browse/${dpr.key}" target="_blank">${dpr.key}</a></span>
        <span class="badge" style="background:${bg}">${esc(dpr.status)}</span>
        <span class="dpr-summary">${esc(dpr.summary)}</span>
        <span style="color:var(--muted);font-size:11px">(${children.length} children)</span>
      </div>
      <div class="hier-children"><table><thead><tr>
        <th>Key</th><th>Summary</th><th>Type</th><th>Status</th><th>Assignee</th>
        <th>Component</th><th>Priority</th><th>Labels</th><th>Due</th>
      </tr></thead><tbody>${children.map(renderRow).join('')}</tbody></table></div></div>`;
    delete groups[dpr.key];
  });
  Object.keys(groups).forEach(pk=>{
    html+=`<div class="hier-group">
      <div class="hier-header" onclick="toggleHier(this)">
        <span class="arrow">\u25BC</span>
        <span class="dpr-key"><a href="${JIRA_URL}/browse/${pk}" target="_blank">${pk}</a></span>
        <span style="color:var(--muted);font-size:11px">(${groups[pk].length} children)</span>
      </div>
      <div class="hier-children"><table><thead><tr>
        <th>Key</th><th>Summary</th><th>Type</th><th>Status</th><th>Assignee</th>
        <th>Component</th><th>Priority</th><th>Labels</th><th>Due</th>
      </tr></thead><tbody>${groups[pk].map(renderRow).join('')}</tbody></table></div></div>`;
  });
  if(nonDprs.length){
    html+=`<div class="hier-group">
      <div class="hier-header" onclick="toggleHier(this)">
        <span class="arrow">\u25BC</span>
        <span class="dpr-key">Top-Level Tasks</span>
        <span style="color:var(--muted);font-size:11px">(${nonDprs.length})</span>
      </div>
      <div class="hier-children"><table><thead><tr>
        <th>Key</th><th>Summary</th><th>Type</th><th>Status</th><th>Assignee</th>
        <th>Component</th><th>Priority</th><th>Labels</th><th>Due</th>
      </tr></thead><tbody>${nonDprs.map(renderRow).join('')}</tbody></table></div></div>`;
  }
  if(!html)html='<div style="padding:40px;text-align:center;color:var(--muted)">No issues match the current filters</div>';
  hv.innerHTML=html;
}
function toggleHier(header){header.classList.toggle('collapsed');
  header.nextElementSibling.classList.toggle('collapsed');}

/* ── Stats ──────────────────────────────────────────────────────── */
function updateStats(){
  const shown=filtered.length,total=ISSUES.length;
  const openN=filtered.filter(i=>i.statusCat!=='done').length;
  const doneN=shown-openN;
  const dprN=filtered.filter(i=>i.type==='DPR').length;
  const overdueN=filtered.filter(i=>i.due&&i.due<TODAY&&i.statusCat!=='done').length;
  document.getElementById('stat-show').innerHTML=`Showing <b>${shown}</b> of <b>${total}</b>`;
  let extra=`<b>${dprN}</b> DPRs \u00b7 <b>${openN}</b> open \u00b7 <b>${doneN}</b> done`;
  if(overdueN)extra+=` \u00b7 <b style="color:var(--red)">${overdueN}</b> overdue`;
  document.getElementById('stat-extra').innerHTML=extra;
}

/* ── JQL Generator ──────────────────────────────────────────────── */
function updateJql(){
  const parts=[`project = ${PROJECT}`];
  if(msStatus.selected.size<msStatus.options.length&&msStatus.selected.size>0)
    parts.push(`status IN (${[...msStatus.selected].map(s=>`"${s}"`).join(', ')})`);
  if(msType.selected.size<msType.options.length&&msType.selected.size>0)
    parts.push(`issuetype IN (${[...msType.selected].map(s=>`"${s}"`).join(', ')})`);
  const allA=msAssignee.options.map(o=>o.value);
  if(msAssignee.selected.size<allA.length&&msAssignee.selected.size>0){
    const hasU=msAssignee.selected.has('(Unassigned)');
    const named=[...msAssignee.selected].filter(a=>a!=='(Unassigned)');
    if(hasU&&!named.length)parts.push('assignee IS EMPTY');
    else if(!hasU&&named.length)parts.push(`assignee IN (${named.map(a=>`"${a}"`).join(', ')})`);
  }
  const allC=msComponent.options.map(o=>o.value);
  if(msComponent.selected.size<allC.length&&msComponent.selected.size>0){
    const named=[...msComponent.selected].filter(c=>c!=='(None)');
    if(named.length)parts.push(`component IN (${named.map(c=>`"${c}"`).join(', ')})`);
  }
  if(msPriority.selected.size<msPriority.options.length&&msPriority.selected.size>0)
    parts.push(`priority IN (${[...msPriority.selected].map(s=>`"${s}"`).join(', ')})`);
  const dueFrom=document.getElementById('due-from').value;
  const dueTo=document.getElementById('due-to').value;
  if(dueFrom)parts.push(`due >= "${dueFrom}"`);
  if(dueTo)parts.push(`due <= "${dueTo}"`);
  if(noDueOnly)parts.push('due IS EMPTY');
  const text=document.getElementById('text-search').value.trim();
  if(text)parts.push(`(summary ~ "${text}" OR key = "${text}")`);
  document.getElementById('jql-text').textContent=parts.join(' AND ');
}
function toggleJql(){document.getElementById('jql-bar').classList.toggle('visible');}
function copyJql(){
  const t=document.getElementById('jql-text').textContent;
  navigator.clipboard.writeText(t).then(()=>{
    const hint=document.querySelector('#jql-bar .copy-hint');
    hint.textContent='Copied!';setTimeout(()=>hint.textContent='click to copy',1500);
  });
}

/* ── View toggle ────────────────────────────────────────────────── */
function setView(v){currentView=v;
  document.getElementById('vt-flat').classList.toggle('active',v==='flat');
  document.getElementById('vt-hier').classList.toggle('active',v==='hier');
  render();}

/* ── Date presets ───────────────────────────────────────────────── */
function setDuePreset(p){
  const f=document.getElementById('due-from'),t=document.getElementById('due-to');
  noDueOnly=false;
  if(p==='overdue'){f.value='';t.value=TODAY;}
  else if(p==='week'){
    const d=new Date(),day=d.getDay();
    const mon=new Date(d);mon.setDate(d.getDate()-(day===0?6:day-1));
    const sun=new Date(mon);sun.setDate(mon.getDate()+6);
    f.value=mon.toISOString().slice(0,10);t.value=sun.toISOString().slice(0,10);
  }else if(p==='30d'){
    f.value=TODAY;const d=new Date();d.setDate(d.getDate()+30);
    t.value=d.toISOString().slice(0,10);
  }else if(p==='nodue'){f.value='';t.value='';noDueOnly=true;}
  applyFilters();
}

/* ── Quick filter presets ───────────────────────────────────────── */
function quickFilter(preset){
  document.querySelectorAll('.qbtn').forEach(b=>b.classList.toggle('active',b.dataset.preset===preset));
  resetFiltersQuiet();
  if(preset==='open'){
    const open=ISSUES.filter(i=>i.statusCat!=='done').map(i=>i.status);
    msStatus.setValues([...new Set(open)]);
  }else if(preset==='overdue'){
    const open=ISSUES.filter(i=>i.statusCat!=='done').map(i=>i.status);
    msStatus.setValues([...new Set(open)]);
    document.getElementById('due-to').value=TODAY;
  }else if(preset==='unassigned'){
    msAssignee.setValues(['(Unassigned)']);
    const open=ISSUES.filter(i=>i.statusCat!=='done').map(i=>i.status);
    msStatus.setValues([...new Set(open)]);
  }else if(preset==='stale'){
    const cutoff=new Date();cutoff.setDate(cutoff.getDate()-30);
    const open=ISSUES.filter(i=>i.statusCat!=='done').map(i=>i.status);
    msStatus.setValues([...new Set(open)]);
    document.getElementById('updated-to').value=cutoff.toISOString().slice(0,10);
  }
  applyFilters();
}
function resetFiltersQuiet(){
  noDueOnly=false;ALL_MS.forEach(m=>m.selAll());
  document.getElementById('text-search').value='';
  ['due-from','due-to','created-from','created-to','updated-from','updated-to']
    .forEach(id=>document.getElementById(id).value='');
}
function resetFilters(){resetFiltersQuiet();
  document.querySelectorAll('.qbtn').forEach(b=>b.classList.toggle('active',b.dataset.preset==='all'));
  applyFilters();}

/* ── Export CSV ─────────────────────────────────────────────────── */
function exportCsv(){
  const hdr=['Key','Summary','Type','Status','Assignee','Component','Priority','Labels','Due','Parent','Created','Updated'];
  const rows=filtered.map(i=>[i.key,`"${(i.summary||'').replace(/"/g,'""')}"`,i.type,i.status,
    i.assignee||'',i.components.join('; '),i.priority,i.labels.join('; '),i.due||'',i.parent||'',i.created||'',i.updated||'']);
  const csv=[hdr.join(','),...rows.map(r=>r.join(','))].join('\n');
  const blob=new Blob([csv],{type:'text/csv'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=`${PROJECT}_issues_${TODAY}.csv`;a.click();
}

/* ── Init ───────────────────────────────────────────────────────── */
applyFilters();
'''

    # ── Assemble ─────────────────────────────────────────────────────
    html = '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
    html += '<meta charset="UTF-8">\n'
    html += '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    html += f'<title>{PROJECT_KEY} Interactive Dashboard</title>\n'
    html += f'<style>{css}</style>\n'
    html += '</head>\n<body>\n'
    html += html_body
    html += '\n<script>\n'
    html += f'const ISSUES = {issues_json};\n'
    html += f'const META = {meta_json};\n'
    html += f'const JIRA_URL = {json.dumps(JIRA_SERVER)};\n'
    html += f'const PROJECT = {json.dumps(PROJECT_KEY)};\n'
    html += f'const TODAY = "{today}";\n'
    html += js
    html += '\n</script>\n</body>\n</html>'

    return html


# =====================================================================
#  3. Confluence page body — links to the interactive HTML attachment
# =====================================================================

def build_confluence_body(total, open_count, page_id):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    done = total - open_count
    P = PROJECT_KEY

    jira_server_id = ''
    try:
        r = session.get(f'{WIKI_BASE}/rest/applinks/1.0/listApplicationlinks')
        if r.status_code == 200:
            for link in r.json().get('list', []):
                app = link.get('application', {})
                if 'jira' in app.get('typeId', '').lower():
                    jira_server_id = app.get('id', '')
                    break
    except Exception:
        pass

    def _esc(t):
        if not t: return ''
        return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    def _jira_macro(jql, columns='key,summary,type,status,assignee,priority', max_issues=50):
        sid = ''
        if jira_server_id:
            sid = f'<ac:parameter ac:name="serverId">{_esc(jira_server_id)}</ac:parameter>\n'
        return (f'<ac:structured-macro ac:name="jira">\n{sid}'
                f'<ac:parameter ac:name="jqlQuery">{_esc(jql)}</ac:parameter>\n'
                f'<ac:parameter ac:name="columns">{columns}</ac:parameter>\n'
                f'<ac:parameter ac:name="maximumIssues">{max_issues}</ac:parameter>\n'
                f'</ac:structured-macro>')

    attachment_url = f'{WIKI_BASE}/download/attachments/{page_id}/{HTML_FILE}'

    body = f'''<ac:structured-macro ac:name="info">
<ac:rich-text-body>
<p><strong>FRACAS Engineering ({P})</strong> \u2014 Live Dashboard</p>
<p><strong>{open_count}</strong> open \u00b7 <strong>{done}</strong> done \u00b7 <strong>{total}</strong> total</p>
<p><em>Page updated: {now}</em></p>
</ac:rich-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="note">
<ac:rich-text-body>
<p><strong>\U0001f527 <a href="{_esc(attachment_url)}">Open the Interactive Dashboard \u2192</a></strong></p>
<p>The interactive version features <strong>multi-select dropdown filters</strong> for every dimension
(status, assignee, component, priority, type, label, DPR type, parent),
<strong>date range pickers</strong> with presets, <strong>free-text search</strong>,
<strong>sortable columns</strong>, <strong>CSV export</strong>, <strong>JQL generator</strong>,
and a <strong>hierarchical grouped view</strong>.</p>
<p>Download the attached <code>{HTML_FILE}</code> and open in any browser, or click the link above.</p>
</ac:rich-text-body>
</ac:structured-macro>

<h1>Quick Views (Live from Jira)</h1>

<h2>All Open DPRs</h2>
{_jira_macro(f'project = {P} AND issuetype = DPR AND statusCategory != Done ORDER BY key ASC',
             'key,summary,status,assignee,components,priority,due')}

<h2>All Open Action Items</h2>
{_jira_macro(f'project = {P} AND issuetype in (Task, Sub-task) AND statusCategory != Done ORDER BY assignee ASC, key ASC',
             'key,summary,type,status,assignee,parent,components,priority,due', 100)}

<h2>Past Due</h2>
{_jira_macro(f'project = {P} AND statusCategory != Done AND due < now() ORDER BY due ASC',
             'key,summary,type,status,assignee,due', 50)}

<h2>My Open Items</h2>
<p><em>Shows issues assigned to the currently logged-in Confluence user.</em></p>
{_jira_macro(f'project = {P} AND assignee = currentUser() AND statusCategory != Done ORDER BY priority DESC, due ASC',
             'key,summary,type,status,priority,parent,due', 50)}
'''
    return body


# =====================================================================
#  4. Confluence CRUD
# =====================================================================

def find_space():
    r = session.get(f'{WIKI_BASE}/rest/api/space/{CONF_SPACE}')
    if r.status_code == 200:
        print(f"  Space: {r.json().get('name')} ({CONF_SPACE})")
        return CONF_SPACE
    r2 = session.get(f'{WIKI_BASE}/rest/api/space', params={'limit': 25})
    if r2.status_code == 200:
        spaces = r2.json().get('results', [])
        if spaces:
            key = spaces[0]['key']
            print(f"  Using space: {spaces[0]['name']} ({key})")
            return key
    print("  ERROR: No Confluence spaces found!")
    sys.exit(1)


def find_page(space_key, title):
    r = session.get(f'{WIKI_BASE}/rest/api/content', params={
        'spaceKey': space_key, 'title': title, 'expand': 'version',
    })
    if r.status_code == 200:
        results = r.json().get('results', [])
        if results:
            return results[0]
    return None


def create_page(space_key, title, body):
    payload = {
        'type': 'page', 'title': title,
        'space': {'key': space_key},
        'body': {'storage': {'value': body, 'representation': 'storage'}},
    }
    r = session.post(f'{WIKI_BASE}/rest/api/content', json=payload)
    if r.status_code in (200, 201):
        page = r.json()
        print(f"  Page CREATED: {page.get('title')} (id: {page.get('id')})")
        return page
    print(f"  CREATE failed: HTTP {r.status_code}\n  {r.text[:500]}")
    return None


def update_page(page_id, title, body, current_version):
    payload = {
        'type': 'page', 'title': title,
        'version': {'number': current_version + 1},
        'body': {'storage': {'value': body, 'representation': 'storage'}},
    }
    r = session.put(f'{WIKI_BASE}/rest/api/content/{page_id}', json=payload)
    if r.status_code == 200:
        print(f"  Page UPDATED: v{current_version + 1} (id: {page_id})")
        return r.json()
    print(f"  UPDATE failed: HTTP {r.status_code}\n  {r.text[:500]}")
    return None


def upload_attachment(page_id, file_path):
    """Upload (or update) an HTML attachment on the Confluence page."""
    url = f'{WIKI_BASE}/rest/api/content/{page_id}/child/attachment'
    filename = Path(file_path).name

    r = session.get(url)
    existing_id = None
    if r.status_code == 200:
        for att in r.json().get('results', []):
            if att.get('title') == filename:
                existing_id = att['id']
                break

    headers = {'X-Atlassian-Token': 'nocheck'}
    with open(file_path, 'rb') as fp:
        files = {'file': (filename, fp, 'text/html')}
        if existing_id:
            upload_url = f'{url}/{existing_id}/data'
            resp = requests.post(upload_url, auth=(JIRA_EMAIL, JIRA_TOKEN),
                                 headers=headers, files=files)
        else:
            resp = requests.post(url, auth=(JIRA_EMAIL, JIRA_TOKEN),
                                 headers=headers, files=files)

    if resp.status_code in (200, 201):
        att = resp.json()
        if isinstance(att, dict) and 'results' in att:
            att = att['results'][0]
        print(f"  Attachment {'UPDATED' if existing_id else 'UPLOADED'}: {filename} (id: {att.get('id', '?')})")
        return att
    else:
        print(f"  Attachment upload failed: HTTP {resp.status_code}\n  {resp.text[:500]}")
        return None


# =====================================================================
#  Main
# =====================================================================

def main():
    print("=" * 60)
    print("  Interactive DPR Dashboard Publisher")
    print("=" * 60)

    # 1. Fetch all issues
    print("\n[1] Fetching all issues with full field data...")
    issues, meta = fetch_all_issues()

    # 2. Generate interactive HTML
    print("\n[2] Generating interactive HTML dashboard...")
    html = generate_html(issues, meta)
    local_path = Path(__file__).with_name(HTML_FILE)
    local_path.write_text(html, encoding='utf-8')
    print(f"  Saved locally: {local_path}")
    print(f"  Size: {len(html):,} chars")
    print(f"  Open in browser: file:///{local_path.as_posix()}")

    # 3. Find Confluence space & page
    print("\n[3] Finding Confluence space...")
    space_key = find_space()

    print(f"\n[4] Finding/creating page '{PAGE_TITLE}'...")
    existing = find_page(space_key, PAGE_TITLE)
    if existing:
        page_id = existing['id']
        version = existing['version']['number']
        print(f"  Existing page: id={page_id}, v{version}")
    else:
        page = create_page(space_key, PAGE_TITLE, '<p>Initializing...</p>')
        if not page:
            sys.exit(1)
        page_id = page['id']
        version = page['version']['number']

    # 4. Upload HTML attachment
    print(f"\n[5] Uploading interactive dashboard as attachment...")
    upload_attachment(page_id, str(local_path))

    # 5. Build and update page body
    print(f"\n[6] Updating Confluence page body...")
    total = len(issues)
    open_count = sum(1 for i in issues if i['statusCat'] != 'done')
    body = build_confluence_body(total, open_count, page_id)
    print(f"  Content: {len(body):,} chars")

    latest = find_page(space_key, PAGE_TITLE)
    if latest:
        version = latest['version']['number']

    result = update_page(page_id, PAGE_TITLE, body, version)

    if result:
        webui = result.get('_links', {}).get('webui', '')
        page_url = f"{WIKI_BASE}{webui}" if webui else f"{WIKI_BASE}/spaces/{space_key}/pages/{page_id}"
        print(f"\n{'=' * 60}")
        print(f"  DONE!")
        print(f"  Confluence page: {page_url}")
        print(f"  Local HTML:      file:///{local_path.as_posix()}")
        print(f"\n  The Confluence page links to the interactive HTML attachment.")
        print(f"  The HTML dashboard has filters, sorting, search, CSV export.")
        print(f"  Re-run this script to refresh the data snapshot.")
        print(f"{'=' * 60}")
    else:
        print("\n  FAILED to update page.")
        sys.exit(1)


if __name__ == '__main__':
    main()


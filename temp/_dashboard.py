#!/usr/bin/env python3
"""
Generate an interactive hierarchical HTML dashboard for the DPR project.

Shows:
  DPR-33: Escalated: dira          [Close-Out (CO)]   Didier Duval
    ├─ DPR-34: Perform RCA...      [Done]
    ├─ DPR-35: Implement CA...     [Done]
    ├─ DPR-94: Base Approval: ...  [Open]             Didier Duval
    └─ DPR-99: Supplier Approval.. [Open]             Didier Duval

Features:
  - Collapsible tree with expand/collapse all
  - Color-coded status badges
  - Filter by assignee (everyone sees their items)
  - Links to Jira issues
  - Auto-refresh button
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

from jira import JIRA

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

JIRA_SERVER = os.environ.get('JIRA_SERVER', 'https://fifo24.atlassian.net')
JIRA_EMAIL  = os.environ.get('JIRA_EMAIL', '')
JIRA_TOKEN  = os.environ.get('JIRA_API_TOKEN', '')
PROJECT_KEY = os.environ.get('PROJECT_KEY', 'DPR')

j = JIRA(options={'server': JIRA_SERVER}, basic_auth=(JIRA_EMAIL, JIRA_TOKEN))

# ── Gather data ──────────────────────────────────────────────────────

print(f"Fetching issues from {PROJECT_KEY}...")

# 1. Get ALL issues in one query, sorted by key
all_issues = []
start = 0
while True:
    batch = j.search_issues(
        f'project = {PROJECT_KEY} ORDER BY key ASC',
        startAt=start, maxResults=100,
        fields='summary,status,assignee,issuetype,parent,labels,created,updated,priority,components',
    )
    all_issues.extend(batch)
    if start + len(batch) >= batch.total:
        break
    start += len(batch)

print(f"  {len(all_issues)} issues fetched")

# 2. Build lookup and tree
issues_by_key = {}
children_of = defaultdict(list)  # parent_key -> [child_keys]
root_keys = []                    # top-level DPR issues

for iss in all_issues:
    key = iss.key
    f = iss.fields
    parent_key = None
    if hasattr(f, 'parent') and f.parent:
        parent_key = f.parent.key

    assignee = f.assignee.displayName if f.assignee else None
    components = [c.name for c in f.components] if f.components else []

    issues_by_key[key] = {
        'key': key,
        'summary': f.summary,
        'status': f.status.name,
        'status_cat': f.status.statusCategory.key,  # 'done', 'indeterminate', 'new'
        'assignee': assignee,
        'type': f.issuetype.name,
        'parent': parent_key,
        'labels': list(f.labels) if f.labels else [],
        'priority': f.priority.name if f.priority else 'Medium',
        'components': components,
        'created': str(f.created)[:10],
        'updated': str(f.updated)[:10],
    }

    if parent_key:
        children_of[parent_key].append(key)
    else:
        root_keys.append(key)

# Sort children by key number
def sort_key(k):
    try:
        return int(k.split('-')[1])
    except (IndexError, ValueError):
        return 0

root_keys.sort(key=sort_key)
for parent in children_of:
    children_of[parent].sort(key=sort_key)

# 3. Collect all assignees for the filter dropdown
all_assignees = sorted(set(
    d['assignee'] for d in issues_by_key.values() if d['assignee']
))

print(f"  {len(root_keys)} top-level issues, {len(all_assignees)} assignees")

# ── Status color mapping ─────────────────────────────────────────────

STATUS_COLORS = {
    'Open': '#4A6785',
    'In Progress': '#0052CC',
    'Done': '#36B37E',
    'Approved': '#00875A',
    'Root Cause Analysis (RCA)': '#FF991F',
    'Corrective Action (CA)': '#FF8B00',
    'Verification (VR)': '#6554C0',
    'Close-Out (CO)': '#0065FF',
    'Closed (CL)': '#36B37E',
    'Backlog': '#6B778C',
}

STATUS_CAT_COLORS = {
    'new': '#4A6785',
    'indeterminate': '#0052CC',
    'done': '#36B37E',
}

# ── Generate HTML ────────────────────────────────────────────────────

def status_badge(status, status_cat):
    color = STATUS_COLORS.get(status, STATUS_CAT_COLORS.get(status_cat, '#6B778C'))
    return f'<span class="badge" style="background:{color}">{escape(status)}</span>'

def issue_type_icon(itype):
    icons = {
        'DPR': '🔴',
        'Task': '✅',
        'Sub-task': '📋',
        'Story': '📖',
        'Bug': '🐛',
    }
    return icons.get(itype, '📄')

def render_issue_row(key, depth=0):
    """Render one issue row + its children recursively."""
    d = issues_by_key.get(key)
    if not d:
        return ''

    kids = children_of.get(key, [])
    has_kids = len(kids) > 0
    indent = depth * 28
    assignee = escape(d['assignee']) if d['assignee'] else '<span class="unassigned">Unassigned</span>'
    assignee_data = d['assignee'] or ''
    icon = issue_type_icon(d['type'])
    badge = status_badge(d['status'], d['status_cat'])
    comps = ', '.join(d['components']) if d['components'] else ''
    labels = ' '.join(f'<span class="label">{escape(l)}</span>' for l in d['labels'])
    url = f'{JIRA_SERVER}/browse/{key}'

    toggle = ''
    if has_kids:
        toggle = f'<span class="toggle" onclick="toggleRow(this)">▼</span>'
    else:
        toggle = '<span class="toggle-placeholder"></span>'

    row_class = 'root-row' if depth == 0 else f'child-row depth-{depth}'
    done_class = ' done-row' if d['status_cat'] == 'done' else ''

    html = f'''<tr class="{row_class}{done_class}" data-assignee="{escape(assignee_data)}" data-depth="{depth}" data-key="{key}">
  <td style="padding-left:{indent + 8}px" class="key-cell">
    {toggle}{icon} <a href="{url}" target="_blank">{key}</a>
  </td>
  <td class="summary-cell">{escape(d['summary'])}</td>
  <td class="status-cell">{badge}</td>
  <td class="assignee-cell">{assignee}</td>
  <td class="comp-cell">{escape(comps)}</td>
  <td class="labels-cell">{labels}</td>
</tr>\n'''

    if has_kids:
        for child_key in kids:
            html += render_issue_row(child_key, depth + 1)

    return html

# Build all rows
all_rows = ''
for rk in root_keys:
    all_rows += render_issue_row(rk)

# Count stats
total = len(all_issues)
open_count = sum(1 for d in issues_by_key.values() if d['status_cat'] != 'done')
done_count = sum(1 for d in issues_by_key.values() if d['status_cat'] == 'done')
dpr_count = sum(1 for d in issues_by_key.values() if d['type'] == 'DPR')

now = datetime.now().strftime('%Y-%m-%d %H:%M')

html_page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DPR Hierarchical Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f4f5f7; color: #172B4D; }}
  .header {{ background: #0052CC; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; }}
  .header h1 {{ font-size: 20px; font-weight: 600; }}
  .header .subtitle {{ font-size: 13px; opacity: 0.85; }}
  .toolbar {{ background: white; padding: 12px 24px; border-bottom: 1px solid #DFE1E6; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .toolbar label {{ font-size: 13px; color: #6B778C; }}
  .toolbar select, .toolbar input {{ padding: 6px 10px; border: 1px solid #DFE1E6; border-radius: 3px; font-size: 13px; }}
  .toolbar button {{ padding: 6px 14px; border: none; border-radius: 3px; font-size: 13px; cursor: pointer; background: #0052CC; color: white; }}
  .toolbar button:hover {{ background: #0747A6; }}
  .toolbar button.secondary {{ background: #F4F5F7; color: #42526E; border: 1px solid #DFE1E6; }}
  .toolbar button.secondary:hover {{ background: #EBECF0; }}
  .stats {{ display: flex; gap: 20px; margin-left: auto; font-size: 13px; color: #6B778C; }}
  .stats b {{ color: #172B4D; }}
  table {{ width: 100%; border-collapse: collapse; background: white; }}
  thead th {{ background: #F4F5F7; padding: 10px 12px; text-align: left; font-size: 12px; font-weight: 600; color: #6B778C; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #DFE1E6; position: sticky; top: 0; z-index: 10; }}
  tbody tr {{ border-bottom: 1px solid #F4F5F7; transition: background 0.15s; }}
  tbody tr:hover {{ background: #F4F5F7; }}
  tbody tr.root-row {{ background: #FAFBFC; font-weight: 500; }}
  tbody tr.root-row td {{ padding-top: 14px; padding-bottom: 14px; border-top: 2px solid #DFE1E6; }}
  tbody tr.done-row {{ opacity: 0.55; }}
  td {{ padding: 8px 12px; font-size: 13px; vertical-align: middle; }}
  .key-cell {{ white-space: nowrap; min-width: 180px; }}
  .key-cell a {{ color: #0052CC; text-decoration: none; font-weight: 500; }}
  .key-cell a:hover {{ text-decoration: underline; }}
  .summary-cell {{ max-width: 400px; }}
  .status-cell {{ white-space: nowrap; }}
  .assignee-cell {{ white-space: nowrap; }}
  .comp-cell {{ white-space: nowrap; font-size: 12px; color: #6B778C; }}
  .labels-cell {{ white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; color: white; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.3px; }}
  .label {{ display: inline-block; padding: 1px 6px; border-radius: 3px; background: #DFE1E6; color: #42526E; font-size: 11px; margin-right: 3px; }}
  .unassigned {{ color: #B3BAC5; font-style: italic; }}
  .toggle {{ cursor: pointer; display: inline-block; width: 18px; text-align: center; font-size: 11px; color: #6B778C; user-select: none; margin-right: 4px; }}
  .toggle:hover {{ color: #0052CC; }}
  .toggle-placeholder {{ display: inline-block; width: 22px; }}
  tr.collapsed {{ display: none; }}
  .footer {{ padding: 12px 24px; font-size: 12px; color: #6B778C; text-align: center; }}
  @media (max-width: 900px) {{
    .summary-cell {{ max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .comp-cell, .labels-cell {{ display: none; }}
  }}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>FRACAS Engineering — DPR Dashboard</h1>
    <div class="subtitle">Hierarchical view · Generated {now}</div>
  </div>
</div>

<div class="toolbar">
  <label for="assigneeFilter">Assignee:</label>
  <select id="assigneeFilter" onchange="filterByAssignee()">
    <option value="">Everyone</option>
    {"".join(f'<option value="{escape(a)}">{escape(a)}</option>' for a in all_assignees)}
  </select>

  <label for="statusFilter">Status:</label>
  <select id="statusFilter" onchange="filterByAssignee()">
    <option value="">All</option>
    <option value="open">Open / In Progress</option>
    <option value="done">Done</option>
  </select>

  <button class="secondary" onclick="expandAll()">Expand All</button>
  <button class="secondary" onclick="collapseAll()">Collapse All</button>

  <div class="stats">
    <span><b>{dpr_count}</b> DPRs</span>
    <span><b>{open_count}</b> open</span>
    <span><b>{done_count}</b> done</span>
    <span><b>{total}</b> total</span>
  </div>
</div>

<table>
<thead>
  <tr>
    <th style="min-width:200px">Key</th>
    <th>Summary</th>
    <th>Status</th>
    <th>Assignee</th>
    <th>Component</th>
    <th>Labels</th>
  </tr>
</thead>
<tbody>
{all_rows}
</tbody>
</table>

<div class="footer">
  FRACAS Engineering ({PROJECT_KEY}) · {total} issues · <a href="{JIRA_SERVER}/jira/software/c/projects/{PROJECT_KEY}/list" target="_blank">Open in Jira</a>
</div>

<script>
function toggleRow(el) {{
  const row = el.closest('tr');
  const key = row.dataset.key;
  const depth = parseInt(row.dataset.depth);
  const isCollapsed = el.textContent.trim() === '▶';

  // Find all direct & indirect children
  let next = row.nextElementSibling;
  while (next && parseInt(next.dataset.depth) > depth) {{
    if (isCollapsed) {{
      // Expanding: show direct children only (depth+1)
      if (parseInt(next.dataset.depth) === depth + 1) {{
        next.classList.remove('collapsed');
        // Reset nested toggles to collapsed
        const toggle = next.querySelector('.toggle');
        if (toggle) toggle.textContent = '▶';
      }}
    }} else {{
      // Collapsing: hide all descendants
      next.classList.add('collapsed');
    }}
    next = next.nextElementSibling;
  }}

  el.textContent = isCollapsed ? '▼' : '▶';
}}

function expandAll() {{
  document.querySelectorAll('tbody tr').forEach(r => r.classList.remove('collapsed'));
  document.querySelectorAll('.toggle').forEach(t => t.textContent = '▼');
  filterByAssignee(); // reapply filters
}}

function collapseAll() {{
  document.querySelectorAll('tbody tr').forEach(r => {{
    if (parseInt(r.dataset.depth) > 0) r.classList.add('collapsed');
  }});
  document.querySelectorAll('.toggle').forEach(t => t.textContent = '▶');
}}

function filterByAssignee() {{
  const assignee = document.getElementById('assigneeFilter').value;
  const statusVal = document.getElementById('statusFilter').value;
  const rows = document.querySelectorAll('tbody tr');

  // First, reset visibility
  rows.forEach(r => r.style.display = '');

  // Track which root rows have visible children
  const visibleRoots = new Set();

  rows.forEach(r => {{
    const a = r.dataset.assignee;
    const isDone = r.classList.contains('done-row');
    const depth = parseInt(r.dataset.depth);
    let visible = true;

    // Assignee filter
    if (assignee && a !== assignee) {{
      // For root rows (DPR), show if any child matches
      if (depth === 0) {{
        // Check children
        let next = r.nextElementSibling;
        let hasMatch = false;
        while (next && parseInt(next.dataset.depth) > 0) {{
          if (next.dataset.assignee === assignee) hasMatch = true;
          next = next.nextElementSibling;
        }}
        if (!hasMatch) visible = false;
      }} else {{
        visible = false;
      }}
    }}

    // Status filter
    if (statusVal === 'open' && isDone) visible = false;
    if (statusVal === 'done' && !isDone) visible = false;

    if (!visible) {{
      r.style.display = 'none';
    }} else if (depth === 0) {{
      visibleRoots.add(r.dataset.key);
    }}
  }});
}}

// Start with all expanded
expandAll();
</script>

</body>
</html>'''

# ── Write output ─────────────────────────────────────────────────────

out_path = Path(__file__).with_name('dpr_dashboard.html')
out_path.write_text(html_page, encoding='utf-8')
print(f"\n✅ Dashboard written to: {out_path}")
print(f"   Open in browser: file:///{out_path.as_posix()}")


import json
import os
import threading
import webbrowser
from collections import defaultdict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import quote as url_quote, urlencode, parse_qs, urlparse

import requests
from jira import JIRA

# Load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

# ==========================================
# 1. Connect to your Jira Instance
# ==========================================
# Secrets are read from environment variables or a .env file.
# Copy .env.example → .env and fill in your values.
JIRA_SERVER     = os.environ.get('JIRA_SERVER', 'https://fifo24.atlassian.net')
JIRA_EMAIL      = os.environ.get('JIRA_EMAIL', '')
JIRA_API_TOKEN  = os.environ.get('JIRA_API_TOKEN', '')
PROJECT_KEY     = os.environ.get('PROJECT_KEY', 'DPR')

# ── Atlassian login for automation page (Selenium) ────────────────────
JIRA_PASSWORD   = os.environ.get('JIRA_PASSWORD', '')

# ── OAuth 2.0 (3LO) — needed for Automation API ──────────────────────
OAUTH_CLIENT_ID     = os.environ.get('OAUTH_CLIENT_ID', '')
OAUTH_CLIENT_SECRET = os.environ.get('OAUTH_CLIENT_SECRET', '')
OAUTH_REDIRECT_URI  = 'http://localhost:8080/callback'
OAUTH_SCOPES        = ' '.join([
    'read:jira-work',
    'read:jira-user',
    'manage:jira-configuration',
    'read:automation:jira',       # granular scope — required for Automation API
    'offline_access',
])
_TOKEN_CACHE        = Path(__file__).with_name('.oauth_token.json')

# Initialize Jira connection (basic auth — used for everything except automation)
jira = JIRA(
    options={'server': JIRA_SERVER},
    basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN)
)


# ──────────────────────────────────────────
# OAuth 2.0 helper
# ──────────────────────────────────────────

def _get_oauth_token() -> str | None:
    """Return a valid OAuth 2.0 access token, or None if OAuth is not configured.
    Uses a cached refresh token when available; otherwise opens the browser
    for the consent flow (runs a tiny localhost server to catch the callback).
    """
    if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
        return None

    # 1. Try cached token
    if _TOKEN_CACHE.exists():
        cached = json.loads(_TOKEN_CACHE.read_text())
        # Try refreshing with the refresh_token
        if cached.get('refresh_token'):
            r = requests.post('https://auth.atlassian.com/oauth/token', json={
                'grant_type':    'refresh_token',
                'client_id':     OAUTH_CLIENT_ID,
                'client_secret': OAUTH_CLIENT_SECRET,
                'refresh_token': cached['refresh_token'],
            })
            if r.status_code == 200:
                tokens = r.json()
                tokens.setdefault('refresh_token', cached['refresh_token'])
                _TOKEN_CACHE.write_text(json.dumps(tokens))
                print("    ✓ OAuth token refreshed from cache")
                return tokens['access_token']
            else:
                print(f"    ⚠ Token refresh failed (HTTP {r.status_code}), re-authorizing…")

    # 2. Full consent flow — open browser, wait for callback
    print("    ℹ Opening browser for OAuth consent…")
    auth_url = ('https://auth.atlassian.com/authorize?' + urlencode({
        'audience':      'api.atlassian.com',
        'client_id':     OAUTH_CLIENT_ID,
        'scope':         OAUTH_SCOPES,
        'redirect_uri':  OAUTH_REDIRECT_URI,
        'response_type': 'code',
        'prompt':        'consent',
    }))

    auth_code = None

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            qs = parse_qs(urlparse(self.path).query)
            auth_code = qs.get('code', [None])[0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h2>Done - you can close this tab.</h2>')

        def log_message(self, *_):
            pass   # silence server logs

    parsed = urlparse(OAUTH_REDIRECT_URI)
    server = HTTPServer((parsed.hostname, parsed.port), _Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(auth_url)
    thread.join(timeout=120)
    server.server_close()

    if not auth_code:
        print("    ⚠ OAuth consent timed out or was cancelled")
        return None

    # 3. Exchange code for tokens
    r = requests.post('https://auth.atlassian.com/oauth/token', json={
        'grant_type':    'authorization_code',
        'client_id':     OAUTH_CLIENT_ID,
        'client_secret': OAUTH_CLIENT_SECRET,
        'code':          auth_code,
        'redirect_uri':  OAUTH_REDIRECT_URI,
    })
    if r.status_code != 200:
        print(f"    ⚠ Token exchange failed: HTTP {r.status_code}")
        try:
            print(f"      {r.json()}")
        except Exception:
            print(f"      {r.text[:200]}")
        return None

    tokens = r.json()
    _TOKEN_CACHE.write_text(json.dumps(tokens))
    print("    ✓ OAuth token obtained and cached")
    return tokens['access_token']


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────

def _bar(value: int, total: int, width: int = 20) -> str:
    """Return a simple ASCII progress bar."""
    filled = round(width * value / total) if total else 0
    return f"[{'█' * filled}{'░' * (width - filled)}] {value}"


def _md_table(headers: list[str], rows: list[list]) -> str:
    """Render a markdown table from headers and rows.
    Always includes a leading blank line so the table renders correctly
    even when preceded by inline text (markdown requires a blank line
    before a table).
    """
    if not rows:
        return ""
    PIPE = chr(124)  # '|' — using chr() to survive editor save
    col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
                  for i, h in enumerate(headers)]
    sep  = PIPE + " " + (" " + PIPE + " ").join("-" * w for w in col_widths) + " " + PIPE
    head = PIPE + " " + (" " + PIPE + " ").join(str(h).ljust(col_widths[i]) for i, h in enumerate(headers)) + " " + PIPE
    body = "\n".join(
        PIPE + " " + (" " + PIPE + " ").join(str(c).ljust(col_widths[i]) for i, c in enumerate(row)) + " " + PIPE
        for row in rows
    )
    return f"\n{head}\n{sep}\n{body}\n"


import re as _re

# Patterns for Jira automation UI button labels that leak into scraped text
_UI_NOISE_PHRASES = _re.compile(
    r'\s*\b(?:Duplicate\s+Delete|Change\s+trigger|Add\s+component)\b\s*', _re.I)
_UI_NOISE_TRAILING = _re.compile(
    r'(?:\s+(?:Duplicate|Delete|Edit|Copy|Move))+\s*$', _re.I)
_UI_NOISE_SOLO = _re.compile(
    r'(?:^|\s)(?:Duplicate|Delete|Edit|Copy|Move)(?:\s|$)', _re.I)


_DEDUP_PHRASES = _re.compile(
    r'\b([\w][\w-]*(?:\s+[\w][\w-]*){0,2})\s+\1\b', _re.I)

def _clean_ui_noise(text: str) -> str:
    """Strip Jira UI button labels (Duplicate, Delete, Change trigger, etc.)
    that bleed into scraped automation rule text, and remove consecutive
    duplicate phrases like 'Sub-task Sub-task' → 'Sub-task'."""
    if not text or text == '—':
        return text
    text = _UI_NOISE_PHRASES.sub(' ', text)
    text = _UI_NOISE_TRAILING.sub('', text)
    text = _UI_NOISE_SOLO.sub(' ', text)
    # Remove consecutive duplicate 1-3 word phrases caused by
    # TreeWalker picking up icon labels alongside their text content
    text = _DEDUP_PHRASES.sub(r'\1', text)
    return ' '.join(text.split()).strip()


def _safe(obj, attr, default="N/A"):
    val = getattr(obj, attr, default)
    return val if val else default


def _extract_condition_names(tree: dict) -> list[str]:
    """Recursively extract human-readable condition names from a Jira conditionsTree."""
    names = []
    if not tree:
        return names
    if 'type' in tree:
        names.append(tree['type'].split('.')[-1])
    for child in tree.get('conditions', []):
        names.extend(_extract_condition_names(child))
    return names


def _api3(path: str, params=None) -> dict | list:
    """GET /rest/api/3/{path} using the shared jira session.
    params can be a dict or a list-of-tuples (for multi-value keys like expand).
    """
    url = f"{JIRA_SERVER}/rest/api/3/{path}"
    r = jira._session.get(url, params=params or {})
    r.raise_for_status()
    return r.json()


# ──────────────────────────────────────────
# Data Fetching
# ──────────────────────────────────────────

def fetch_project_architecture(project_key: str) -> dict | None:
    """Fetches ALL configuration layers and issue statistics for a Jira project."""
    data = {}

    try:
        # ── A. Basic project info ──────────────────────────────────────────
        print("  → Fetching basic project info…")
        project = jira.project(project_key)
        data['name']        = project.name
        data['key']         = project.key
        data['type']        = _safe(project, 'projectTypeKey')
        data['lead']        = _safe(project.lead, 'displayName')
        data['description'] = _safe(project, 'description')
        data['url']         = f"{JIRA_SERVER}/jira/software/projects/{project_key}/boards"

        # Field-id → human name lookup (populated once in section M, reused in N)
        fid_to_name: dict[str, str] = {}

        # ── B. Issue Types ────────────────────────────────────────────────
        print("  → Fetching issue types…")
        data['issue_types'] = [
            {'name': it.name, 'description': _safe(it, 'description', '')}
            for it in project.issueTypes
        ]

        # Detect the "Epic-equivalent" issue type for this project.
        # Jira Software creates a custom Epic type (e.g. named after the project key)
        # whose description contains "big user story" / "broken down".
        # Fall back to the literal name "Epic".
        _EPIC_KEYWORDS = ('big user story', 'broken down', 'epic')
        epic_type_name = 'Epic'          # safe default
        for it in project.issueTypes:
            desc = (it.description or '').lower()
            name = it.name.lower()
            if any(kw in desc or kw in name for kw in _EPIC_KEYWORDS):
                epic_type_name = it.name
                break
        data['epic_type'] = epic_type_name
        print(f"  → Epic issue type detected as: '{epic_type_name}'")

        # ── C. Components ─────────────────────────────────────────────────
        print("  → Fetching components…")
        components = jira.project_components(project_key)
        data['components'] = [
            {
                'name':        comp.name,
                'lead':        _safe(comp, 'lead', {}).get('displayName', 'Unassigned')
                               if isinstance(_safe(comp, 'lead', {}), dict)
                               else _safe(getattr(comp, 'lead', None), 'displayName', 'Unassigned'),
                'description': _safe(comp, 'description', '') or ''
            }
            for comp in components
        ]

        # ── D. Versions / Releases ────────────────────────────────────────
        print("  → Fetching versions / releases…")
        versions = jira.project_versions(project_key)
        data['versions'] = [
            {
                'name':         v.name,
                'released':     '✅ Released' if getattr(v, 'released', False) else '🔄 Unreleased',
                'release_date': _safe(v, 'releaseDate'),
                'description':  _safe(v, 'description', '') or ''
            }
            for v in reversed(versions)          # most recent first
        ]

        # ── E. Epics ──────────────────────────────────────────────────────
        print(f"  → Fetching epics (issuetype='{epic_type_name}')…")
        epics = jira.search_issues(
            f'project={project_key} AND issuetype="{epic_type_name}" ORDER BY created DESC',
            maxResults=200,
            fields='summary,status,priority,assignee,created,updated'
        )
        data['epics'] = [
            {
                'key':      e.key,
                'summary':  e.fields.summary,
                'status':   str(e.fields.status),
                'priority': str(e.fields.priority),
                'assignee': _safe(e.fields.assignee, 'displayName', 'Unassigned')
                            if e.fields.assignee else 'Unassigned',
            }
            for e in epics
        ]

        # ── F. Issue Statistics ───────────────────────────────────────────
        print("  → Counting issues (may take a moment)…")
        probe   = jira.search_issues(f'project={project_key}', maxResults=0)
        total   = probe.total
        data['total_issues'] = total

        type_counts     = defaultdict(int)
        status_counts   = defaultdict(int)
        priority_counts = defaultdict(int)
        assignee_counts = defaultdict(int)

        fetched = 0
        batch   = 100
        while fetched < min(total, 2000):
            issues = jira.search_issues(
                f'project={project_key}',
                startAt=fetched, maxResults=batch,
                fields='issuetype,status,priority,assignee'
            )
            for issue in issues:
                type_counts[str(issue.fields.issuetype)]   += 1
                status_counts[str(issue.fields.status)]    += 1
                priority_counts[str(issue.fields.priority)] += 1
                assignee_counts[
                    _safe(issue.fields.assignee, 'displayName', 'Unassigned')
                    if issue.fields.assignee else 'Unassigned'
                ] += 1
            fetched += batch

        data['stats'] = {
            'by_type':     dict(sorted(type_counts.items(),     key=lambda x: -x[1])),
            'by_status':   dict(sorted(status_counts.items(),   key=lambda x: -x[1])),
            'by_priority': dict(sorted(priority_counts.items(), key=lambda x: -x[1])),
            'by_assignee': dict(sorted(assignee_counts.items(), key=lambda x: -x[1])),
        }

        # ── G. Boards & Sprints ───────────────────────────────────────────
        print("  → Fetching boards and sprints…")
        data['boards'] = []
        try:
            boards = jira.boards(projectKeyOrID=project_key)
            for board in boards:
                board_info = {'name': board.name, 'type': board.type, 'sprints': []}
                if board.type == 'scrum':
                    try:
                        sprints = jira.sprints(board.id, state='active,future,closed')
                        for sprint in sprints[-10:]:        # last 10
                            board_info['sprints'].append({
                                'name':  sprint.name,
                                'state': sprint.state,
                                'start': getattr(sprint, 'startDate', 'N/A'),
                                'end':   getattr(sprint, 'endDate',   'N/A'),
                            })
                    except Exception:
                        pass
                data['boards'].append(board_info)
        except Exception as e:
            print(f"    ⚠ Could not fetch boards: {e}")

        # ── H. Project Roles & Members ────────────────────────────────────
        print("  → Fetching project roles…")
        data['roles'] = {}
        try:
            roles = jira.project_roles(project_key)
            for role_name, role_meta in roles.items():
                try:
                    role_detail = jira.project_role(project_key, role_meta['id'])
                    data['roles'][role_name] = [
                        a.displayName for a in getattr(role_detail, 'actors', [])
                    ]
                except Exception:
                    data['roles'][role_name] = []
        except Exception as e:
            print(f"    ⚠ Could not fetch roles: {e}")

        # ── I. Permission Scheme ──────────────────────────────────────────
        print("  → Fetching permission scheme…")
        data['permission_scheme'] = 'N/A'
        try:
            perm = jira._get_json(f'project/{project_key}/permissionscheme')
            data['permission_scheme'] = perm.get('name', 'N/A')
        except Exception:
            pass

        # ── J. Notification Scheme ────────────────────────────────────────
        print("  → Fetching notification scheme…")
        data['notification_scheme'] = 'N/A'
        try:
            notif = jira._get_json(f'project/{project_key}/notificationscheme')
            data['notification_scheme'] = notif.get('name', 'N/A')
        except Exception:
            pass

        # ── K. Issue Security Scheme ──────────────────────────────────────
        print("  → Fetching issue security scheme…")
        data['security_scheme'] = 'N/A'
        try:
            sec = jira._get_json(f'project/{project_key}/issuesecuritylevelscheme')
            data['security_scheme'] = sec.get('name', 'N/A')
        except Exception:
            pass

        # ── L. Workflow Scheme & Full Workflow Details ─────────────────────
        print("  → Fetching workflow scheme and workflow details…")
        data['workflow_scheme_name']    = 'N/A'
        data['issue_type_workflow_map'] = []
        data['workflows']               = {}
        try:
            project_id   = project.id
            it_id_to_name = {it.id: it.name for it in project.issueTypes}

            ws_json = _api3('workflowscheme/project', {'projectId': project_id})
            if ws_json.get('values'):
                scheme = ws_json['values'][0].get('workflowScheme', {})
                data['workflow_scheme_name'] = scheme.get('name', 'N/A')
                default_wf  = scheme.get('defaultWorkflow', '')
                it_mappings = scheme.get('issueTypeMappings', {})  # {issueTypeId: wfName}

                wf_names = set()
                for it_id, it_name in it_id_to_name.items():
                    wf_name = it_mappings.get(it_id, default_wf) or 'Unknown'
                    data['issue_type_workflow_map'].append((it_name, wf_name))
                    wf_names.add(wf_name)

                for wf_name in wf_names:
                    if not wf_name or wf_name == 'Unknown':
                        continue
                    try:
                        # v2 /workflow only returns metadata on Cloud; use v3 search with expand.
                        # Pass expand as list-of-tuples so requests sends
                        # ?expand=transitions&expand=statuses&… instead of %2C-encoding commas.
                        wf_search = _api3('workflow/search', [
                            ('queryString', wf_name),
                            ('expand', 'transitions'),
                            ('expand', 'statuses'),
                            ('expand', 'transitions.rules'),
                            ('expand', 'transitions.properties'),
                        ])
                        if not isinstance(wf_search, dict):
                            print(f"    ⚠ workflow/search returned unexpected "
                                  f"{type(wf_search).__name__}: {str(wf_search)[:120]}")
                            continue
                        wf_entry = next(
                            (w for w in wf_search.get('values', [])
                             if w.get('id', {}).get('name') == wf_name),
                            None
                        )
                        if not wf_entry:
                            print(f"    ⚠ Workflow '{wf_name}' not found in search results")
                            continue
                        statuses_raw = wf_entry.get('statuses', [])
                        # Build id→name map; statuses are {id, name} dicts
                        sid_to_name  = {s['id']: s['name'] for s in statuses_raw
                                        if isinstance(s, dict)}
                        status_names = [s.get('name', s.get('id', ''))
                                        for s in statuses_raw if isinstance(s, dict)]
                        transitions = []
                        for t in wf_entry.get('transitions', []):
                            # In Jira Cloud v3 API: 'to'/'from' are status ID strings, not dicts
                            raw_to  = t.get('to', '')
                            raw_frm = t.get('from', [])
                            to  = (sid_to_name.get(raw_to,  raw_to)  if raw_to  else '—') or '—'
                            frm = [sid_to_name.get(s, s) for s in raw_frm] or ['(Initial)']
                            rules      = t.get('rules', {})
                            conditions = _extract_condition_names(rules.get('conditionsTree', {}))
                            validators = [v.get('type', '').split('.')[-1]
                                          for v in rules.get('validators', [])]
                            post_fns   = [pf.get('type', '').split('.')[-1]
                                          for pf in rules.get('postFunctions', [])]
                            transitions.append({
                                'name':       t.get('name', ''),
                                'type':       t.get('type', ''),
                                'from':       ', '.join(frm),
                                'to':         to,
                                'conditions': ', '.join(conditions) if conditions else '—',
                                'validators': ', '.join(validators) if validators else '—',
                                'post_fns':   ', '.join(post_fns)   if post_fns   else '—',
                            })
                        data['workflows'][wf_name] = {
                            'statuses':    status_names,
                            'transitions': transitions,
                        }
                        print(f"    ✓ Workflow '{wf_name}': "
                              f"{len(status_names)} statuses, {len(transitions)} transitions")
                    except Exception as e:
                        print(f"    ⚠ Workflow '{wf_name}': {e}")
        except Exception as e:
            print(f"    ⚠ Could not fetch workflow scheme: {e}")

        # ── M. Screens & Fields ───────────────────────────────────────────
        print("  → Fetching screen configuration…")
        data['screen_scheme_name'] = 'N/A'
        data['screens']            = {}
        try:
            project_id = project.id
            itss_json  = _api3('issuetypescreenscheme/project', {'projectId': project_id})

            # Resolve all field IDs → human names once
            all_fields_raw = _api3('field')
            fid_to_name.update({f['id']: f['name'] for f in
                                (all_fields_raw if isinstance(all_fields_raw, list) else [])})

            for entry in itss_json.get('values', []):
                itss_id   = entry.get('issueTypeScreenScheme', {}).get('id')
                itss_name = entry.get('issueTypeScreenScheme', {}).get('name', 'N/A')
                data['screen_scheme_name'] = itss_name
                if not itss_id:
                    continue

                # Step 2: resolve ITSS → actual Screen Scheme IDs via the mappings sub-API
                # Correct endpoint: /issuetypescreenscheme/mapping?issueTypeScreenSchemeId=
                # (NOT /issuetypescreenscheme/{id}/mappings — that path doesn't exist)
                mappings = _api3('issuetypescreenscheme/mapping',
                                 {'issueTypeScreenSchemeId': itss_id})
                ss_ids = {m['screenSchemeId'] for m in mappings.get('values', [])
                          if m.get('screenSchemeId')}
                print(f"    → ITSS '{itss_name}': {len(ss_ids)} screen scheme(s)")

                for ss_id in ss_ids:
                    ss_json = _api3('screenscheme', {'id': ss_id})
                    for ss in ss_json.get('values', []):
                        ss_name = ss.get('name', str(ss_id))
                        data['screens'][ss_name] = {}
                        for operation, screen_id in ss.get('screens', {}).items():
                            # Screen name
                            try:
                                sc = jira._session.get(
                                    f"{JIRA_SERVER}/rest/api/3/screens/{screen_id}"
                                ).json()
                                screen_name = sc.get('name', str(screen_id))
                            except Exception:
                                screen_name = str(screen_id)

                            # All fields across all tabs
                            fields = []
                            try:
                                tabs = _api3(f'screens/{screen_id}/tabs')
                                for tab in (tabs if isinstance(tabs, list) else []):
                                    tab_id = tab.get('id')
                                    tab_fields = _api3(
                                        f'screens/{screen_id}/tabs/{tab_id}/fields'
                                    )
                                    for f in (tab_fields if isinstance(tab_fields, list) else []):
                                        fname = (f.get('name') or
                                                 fid_to_name.get(f.get('id'), f.get('id', '')))
                                        if fname:
                                            fields.append(fname)
                            except Exception:
                                pass

                            data['screens'][ss_name][operation] = {
                                'screen': screen_name,
                                'fields': fields,
                            }
                            print(f"    ✓ Screen '{screen_name}' ({operation}): {len(fields)} fields")
        except Exception as e:
            print(f"    ⚠ Could not fetch screen config: {e}")

        # ── N. Field Configuration ────────────────────────────────────────
        print("  → Fetching field configuration scheme…")
        data['field_config_scheme'] = 'N/A'
        data['field_configs']       = {}
        try:
            project_id = project.id
            fcs_json   = _api3('fieldconfigurationscheme/project', {'projectId': project_id})

            # Ensure fid_to_name is populated (section M may have been skipped)
            if not fid_to_name:
                all_fields_raw2 = _api3('field')
                fid_to_name.update({f['id']: f['name'] for f in
                                    (all_fields_raw2 if isinstance(all_fields_raw2, list) else [])})

            def _load_fc(fc_id: str) -> list[dict]:
                """Fetch field-level details for one field configuration."""
                rows, start = [], 0
                while True:
                    page = _api3(f'fieldconfiguration/{fc_id}/fields',
                                 {'startAt': start, 'maxResults': 100})
                    for f in page.get('values', []):
                        fid = f.get('id', '')
                        rows.append({
                            'name':     fid_to_name.get(fid, fid),
                            'required': '✅' if f.get('isRequired') else '—',
                            'hidden':   '🙈' if f.get('isHidden')   else '—',
                        })
                    if page.get('isLast', True):
                        break
                    start += 100
                return rows

            # Check if the project has an explicit field config scheme assigned.
            # When using the system default, the API returns values with only
            # projectIds (no fieldConfigurationScheme key).
            has_explicit_scheme = any(
                entry.get('fieldConfigurationScheme') is not None
                for entry in fcs_json.get('values', [])
            )

            if has_explicit_scheme:
                # Project has a specific field config scheme
                for entry in fcs_json['values']:
                    fc_scheme = entry.get('fieldConfigurationScheme')
                    if not fc_scheme:
                        continue
                    fc_scheme_id = fc_scheme.get('id')
                    data['field_config_scheme'] = fc_scheme.get('name', 'N/A')
                    if not fc_scheme_id:
                        continue

                    mapping_json = _api3('fieldconfigurationscheme/mapping',
                                         {'fieldConfigurationSchemeId': fc_scheme_id})
                    fc_ids = {item.get('fieldConfigurationId')
                              for item in mapping_json.get('values', [])
                              if item.get('fieldConfigurationId')}

                    for fc_id in fc_ids:
                        try:
                            fc_meta = jira._session.get(
                                f"{JIRA_SERVER}/rest/api/3/fieldconfiguration/{fc_id}"
                            ).json()
                            fc_name = fc_meta.get('name', str(fc_id))
                            fields  = _load_fc(fc_id)
                            data['field_configs'][fc_name] = fields
                            print(f"    ✓ Field config '{fc_name}': {len(fields)} fields")
                        except Exception as e:
                            print(f"    ⚠ Field config {fc_id}: {e}")
            else:
                # Project uses the global default field configuration scheme
                data['field_config_scheme'] = 'Default Field Configuration Scheme'
                all_fc = _api3('fieldconfiguration')
                for fc in all_fc.get('values', []):
                    if fc.get('isDefault'):
                        fc_id   = fc['id']
                        fc_name = fc.get('name', 'Default Field Configuration')
                        fields  = _load_fc(fc_id)
                        data['field_configs'][fc_name] = fields
                        print(f"    ✓ Field config '{fc_name}' (default): {len(fields)} fields")
                        break
        except Exception as e:
            print(f"    ⚠ Could not fetch field config: {e}")

        # ── O. Automations ────────────────────────────────────────────────
        # The Jira Cloud Automation API is an internal microservice that
        # requires browser-session authentication (no public OAuth scope).
        # We use Selenium to get a valid session, then call the internal API.
        print("  → Fetching automation rules…")
        data['automations'] = []
        data['automation_url'] = (
            f"{JIRA_SERVER}/jira/software/c/projects/{project_key}/settings/automate"
        )
        project_id = project.id

        def _parse_auto_rule(rule: dict) -> dict:
            trigger = rule.get('trigger') or {}
            tname = ((trigger.get('component') or {}).get('value', '')
                     or (trigger.get('component') or {}).get('label', '')
                     or trigger.get('type', 'N/A'))
            # Collect action labels
            action_labels = []
            for a in rule.get('actions', []):
                lbl = (a.get('component') or {}).get('value', '')
                if not lbl:
                    lbl = (a.get('component') or {}).get('label', a.get('type', ''))
                if lbl:
                    action_labels.append(lbl)
            # Collect condition labels
            cond_labels = []
            for c in rule.get('conditions', []):
                lbl = (c.get('component') or {}).get('value', '')
                if not lbl:
                    lbl = (c.get('component') or {}).get('label', c.get('type', ''))
                if lbl:
                    cond_labels.append(lbl)
            return {
                'name':       rule.get('name', 'N/A'),
                'enabled':    '✅' if rule.get('state') in ('ENABLED', 'enabled') else '❌',
                'trigger':    tname,
                'conditions': '; '.join(cond_labels) if cond_labels else '—',
                'actions':    '; '.join(action_labels) if action_labels else '—',
            }

        # Resolve cloud id
        _cloud_id = ''
        try:
            _cloud_id = jira._session.get(
                f"{JIRA_SERVER}/_edge/tenant_info"
            ).json().get('cloudId', '')
        except Exception:
            pass

        if _cloud_id:
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options as ChromeOptions
                from selenium.webdriver.chrome.service import Service as ChromeService
                from selenium.webdriver.common.by import By
                from selenium.webdriver.common.keys import Keys
                from selenium.webdriver.common.action_chains import ActionChains
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC
                from webdriver_manager.chrome import ChromeDriverManager
                import time

                def _make_driver(headless=False):
                    opts = ChromeOptions()
                    if headless:
                        opts.add_argument('--headless=new')
                        opts.add_argument('--no-sandbox')
                        opts.add_argument('--disable-dev-shm-usage')
                    opts.add_argument('--disable-gpu')
                    opts.add_argument('--window-size=1280,900')
                    return webdriver.Chrome(
                        service=ChromeService(ChromeDriverManager().install()),
                        options=opts
                    )

                def _login_with_password(drv):
                    """Try Atlassian email+password login. Returns True on success."""
                    if not JIRA_EMAIL or not JIRA_PASSWORD:
                        print(f"    ⚠ Missing credentials: "
                              f"email={'set' if JIRA_EMAIL else 'EMPTY'}, "
                              f"password={'set' if JIRA_PASSWORD else 'EMPTY'}")
                        return False

                    jira_host = urlparse(JIRA_SERVER).hostname.lower()
                    print(f"    → Navigating to {JIRA_SERVER}/login")
                    drv.get(f"{JIRA_SERVER}/login")
                    try:
                        wait = WebDriverWait(drv, 30)

                        # ── Email step ─────────────────────────────
                        print(f"    → Waiting for username field…")
                        # Jira uses random-suffixed IDs like "username-uid1"
                        # so we match on data-testid or name instead.
                        email_el = wait.until(EC.element_to_be_clickable(
                            (By.CSS_SELECTOR,
                             '[data-testid="username"], '
                             'input[name="username"], '
                             'input[autocomplete="username"], '
                             '#username')))

                        # Click to focus, then type
                        email_el.click()
                        email_el.clear()
                        print(f"    → Typing email: {JIRA_EMAIL[:5]}***")
                        email_el.send_keys(JIRA_EMAIL)

                        # Verify the value was typed
                        typed = email_el.get_attribute('value') or ''
                        if not typed:
                            # Fallback: use ActionChains for key input
                            print("    → send_keys empty, retrying with ActionChains…")
                            email_el.click()
                            ActionChains(drv).pause(0.2)\
                                .send_keys(JIRA_EMAIL).perform()
                            time.sleep(0.3)
                            typed = email_el.get_attribute('value') or ''

                        if not typed:
                            print(f"    ⚠ Could not type email into username field")
                            return False
                        print(f"    → Email field value: {typed[:5]}***")

                        # Submit email
                        print("    → Submitting email…")
                        try:
                            drv.find_element(
                                By.CSS_SELECTOR,
                                '[data-testid="login-submit"], '
                                '#login-submit, '
                                'button[type="submit"]'
                            ).click()
                        except Exception:
                            email_el.send_keys(Keys.RETURN)

                        # ── Password step ──────────────────────────
                        # WebDriverWait handles the page transition — no fixed sleep needed
                        print("    → Waiting for password field…")
                        pwd_el = wait.until(EC.element_to_be_clickable(
                            (By.CSS_SELECTOR,
                             '[data-testid="password"], '
                             'input[name="password"], '
                             'input[autocomplete="current-password"], '
                             '#password')))

                        pwd_el.click()
                        pwd_el.clear()
                        print("    → Typing password…")
                        pwd_el.send_keys(JIRA_PASSWORD)

                        # Verify
                        typed_pw = pwd_el.get_attribute('value') or ''
                        if not typed_pw:
                            print("    → send_keys empty, retrying with ActionChains…")
                            pwd_el.click()
                            ActionChains(drv).pause(0.2)\
                                .send_keys(JIRA_PASSWORD).perform()
                            time.sleep(0.3)

                        # Submit password
                        print("    → Submitting password…")
                        try:
                            drv.find_element(
                                By.CSS_SELECTOR,
                                '[data-testid="login-submit"], '
                                '#login-submit, '
                                'button[type="submit"]'
                            ).click()
                        except Exception:
                            pwd_el.send_keys(Keys.RETURN)

                        # ── Wait for Jira redirect ─────────────────
                        print("    → Waiting for Jira redirect…")
                        wait = WebDriverWait(drv, 30)
                        wait.until(lambda d: (
                            urlparse(d.current_url).hostname or ''
                        ).lower() == jira_host)
                        # Wait for Jira SPA to render (lightweight check)
                        try:
                            WebDriverWait(drv, 10).until(
                                lambda d: d.execute_script(
                                    "return document.readyState") == 'complete')
                        except Exception:
                            time.sleep(1)
                        return True
                    except Exception as e:
                        print(f"    ⚠ Password login failed: "
                              f"{str(e).splitlines()[0][:80]}")
                        try:
                            print(f"    ⚠ Current URL: {drv.current_url[:80]}")
                        except Exception:
                            pass
                        return False

                def _wait_for_manual_login(drv):
                    """Wait for user to complete login manually (Google SSO etc.)."""
                    jira_host = urlparse(JIRA_SERVER).hostname.lower()
                    start = time.time()
                    while time.time() - start < 180:
                        try:
                            cur_url = drv.current_url.lower()
                            cur_host = urlparse(cur_url).hostname or ''
                            # Must be on the actual Jira site, NOT on login/SSO pages
                            if cur_host != jira_host:
                                time.sleep(2)
                                continue
                            # Must have been at least 10s (avoid false-positive before
                            # the SSO redirect kicks in)
                            if time.time() - start < 10:
                                time.sleep(2)
                                continue
                            # Check for Jira-specific content that only exists post-login
                            # (the Atlassian ID login page also has [data-testid])
                            is_jira = drv.execute_script("""
                                return !!(
                                    document.querySelector('[data-testid="atlassian-navigation"]') ||
                                    document.querySelector('[data-testid="navigation-apps"]') ||
                                    document.querySelector('#jira-frontend') ||
                                    document.querySelector('header nav') ||
                                    document.querySelector('[data-testid="global-pages.directories.board-directory-page"]') ||
                                    (document.title && document.title.includes('Jira'))
                                );
                            """)
                            if is_jira:
                                time.sleep(1)  # brief SPA settle
                                return True
                        except Exception:
                            pass
                        time.sleep(1.5)
                    return False

                # ── Attempt login ─────────────────────────────────────
                # Always use a visible browser (Atlassian blocks headless)
                driver = _make_driver(headless=False)
                logged_in = False

                if JIRA_PASSWORD:
                    print("    → Trying email+password login…")
                    logged_in = _login_with_password(driver)
                    if logged_in:
                        print("    ✓ Password login successful")

                if not logged_in:
                    print("    → Please log in manually in the browser…")
                    print("    ℹ You have up to 3 minutes to complete login.")
                    driver.get(data['automation_url'])
                    logged_in = _wait_for_manual_login(driver)

                if not logged_in:
                    print("    ⚠ Login timed out")
                    driver.quit()
                else:
                    try:
                        # Navigate to automation page
                        print("    → Navigating to automation page…")
                        driver.get(data['automation_url'])

                        # Poll until the rules table actually renders (up to 30s)
                        print("    ⏳ Waiting for rules table to load…")
                        try:
                            WebDriverWait(driver, 30).until(
                                lambda d: (d.execute_script(
                                    "return document.querySelectorAll("
                                    "'table tbody tr').length") or 0) > 0)
                        except Exception:
                            pass
                        row_count = driver.execute_script(
                            "return document.querySelectorAll("
                            "'table tbody tr').length") or 0
                        print(f"    → On: {driver.current_url[:80]}")
                        print(f"    → Table rows in DOM: {row_count}")

                        # ── Scrape rule list ──────────────────────────
                        list_js = r"""
                        const rules = [];
                        const seen = new Set();
                        document.querySelectorAll('table tbody tr').forEach(row => {
                            const cells = row.querySelectorAll('td');
                            if (cells.length < 2) return;
                            const link = cells[0].querySelector('a');
                            const name = link ? link.textContent.trim()
                                             : cells[0].textContent.trim();
                            if (!name || seen.has(name)) return;
                            seen.add(name);
                            // Detect enabled: look for toggle switch in any cell
                            let enabled = 'UNKNOWN';
                            row.querySelectorAll('[role="switch"]').forEach(tog => {
                                enabled = tog.getAttribute('aria-checked') === 'true'
                                    ? 'ENABLED' : 'DISABLED';
                            });
                            rules.push({
                                name, href: link ? link.href : '', enabled
                            });
                        });
                        return rules;
                        """
                        list_rules = driver.execute_script(list_js) or []
                        print(f"    → {len(list_rules)} unique rule(s) found")

                        # ── JS for extracting config from opened panel ─
                        # Targets actual Jira Automation DOM containers:
                        #   [class*="component-form"] form  (Edit work item)
                        #   [class*="create-issue-config"]  (Create work item)
                        #   [class*="rule-component-configure"] (config header)
                        EXTRACT_PANEL_JS = r"""
                        return (function() {
                            const result = {fields:[], raw_text:'', debug:''};
                            let panel = null;

                            // P1: Form inside the component-form / create-issue
                            // containers — these are the EXACT wrappers Jira uses
                            panel = document.querySelector(
                                '[class*="component-form"] form,' +
                                '[class*="FormContainer"] form,' +
                                '[class*="create-issue-config"] form');

                            // P2: form near the rule-component-configure header
                            if (!panel) {
                                const hdr = document.querySelector(
                                    '[class*="rule-component-configure"]');
                                if (hdr) {
                                    const sec = hdr.closest('section');
                                    if (sec) panel = sec.querySelector('form')
                                                     || sec;
                                }
                            }

                            // P3: form inside independent-scrolling section
                            if (!panel) {
                                document.querySelectorAll(
                                    'section[class*="independent-scrolling"]'
                                ).forEach(s => {
                                    if (panel) return;
                                    const f = s.querySelector('form');
                                    if (f) {
                                        const r = f.getBoundingClientRect();
                                        if (r.width>100 && r.height>100)
                                            panel = f;
                                    }
                                });
                            }

                            // P4: any visible form with ≥1 label
                            if (!panel) {
                                document.querySelectorAll('form').forEach(f=>{
                                    if (panel) return;
                                    if (f.querySelectorAll('label').length>=1){
                                        const r = f.getBoundingClientRect();
                                        if (r.width>150 && r.height>100)
                                            panel = f;
                                    }
                                });
                            }

                            // P5: the section itself (for non-form panels)
                            if (!panel) {
                                document.querySelectorAll(
                                    'section[class*="independent-scrolling"]'
                                ).forEach(s => {
                                    if (panel) return;
                                    const r = s.getBoundingClientRect();
                                    if (r.width>200 && r.height>200)
                                        panel = s;
                                });
                            }

                            if (!panel) {
                                result.raw_text = '(panel not found)';
                                result.debug = 'NO_PANEL';
                                return result;
                            }

                            result.debug = panel.tagName + '.' +
                                (panel.className||'').substring(0,60);

                            // ── EXPAND collapsed "More options" ──
                            try {
                                panel.querySelectorAll(
                                    '[aria-expanded="false"]'
                                ).forEach(exp => {
                                    const t = (exp.textContent||'').trim();
                                    if (/more\s*options/i.test(t))
                                        exp.click();
                                });
                            } catch(e) {}

                            const seen = new Set();
                            const DEDUP = /\b([\w][\w-]*(?:\s+[\w][\w-]*){0,2})\s+\1\b/gi;

                            // ── STRATEGY A: label → parentElement ──
                            panel.querySelectorAll('label').forEach(lbl => {
                                let lt = lbl.textContent.trim()
                                    .replace(/\s+/g, ' ')
                                    .replace(/\s*\*\s*(\(required\))?/gi,'')
                                    .replace(/\s*\(optional\)/gi,'')
                                    .trim();
                                if (!lt || lt.length>60 ||
                                    seen.has(lt.toLowerCase())) return;

                                const ctr = lbl.parentElement;
                                if (!ctr) return;

                                let val = '';

                                // 1. Text input (skip hidden/checkbox/combobox)
                                if (!val) {
                                    const inp = ctr.querySelector(
                                        'input' +
                                        ':not([type="hidden"])' +
                                        ':not([type="checkbox"])' +
                                        ':not([role="combobox"])' +
                                        ':not([class*="ak-select"])');
                                    if (inp) val = inp.value
                                        || inp.getAttribute('value') || '';
                                }

                                // 1b. Checkbox / toggle
                                if (!val) {
                                    const cb = ctr.querySelector(
                                        'input[type="checkbox"]');
                                    if (cb) val = cb.checked ? 'Yes' : 'No';
                                }

                                // 2. Textarea
                                if (!val) {
                                    const ta = ctr.querySelector('textarea');
                                    if (ta) val = ta.value
                                        || ta.textContent.trim()
                                        || ta.getAttribute('placeholder')
                                        || '';
                                }

                                // 3. Contenteditable
                                if (!val) {
                                    const ce = ctr.querySelector(
                                        '[contenteditable="true"]');
                                    if (ce) val = ce.textContent.trim();
                                }

                                // 4. ADS dropdown single value
                                if (!val) {
                                    const sv = ctr.querySelector(
                                        '.ak-select__single-value,' +
                                        '[class*="singleValue"],' +
                                        '[class*="SingleValue"]');
                                    if (sv) {
                                        const lc = sv.querySelector(
                                            '[class*="LabelContainer"]');
                                        val = lc
                                            ? lc.textContent.trim()
                                            : sv.textContent.trim();
                                    }
                                }

                                // 5. ADS multi-value tags
                                if (!val) {
                                    const tags = ctr.querySelectorAll(
                                        '.ak-select__multi-value__label,' +
                                        '[class*="multiValue__label"]');
                                    if (tags.length)
                                        val = Array.from(tags)
                                            .map(t=>t.textContent.trim())
                                            .filter(t=>t).join(', ');
                                }

                                // 6. Hidden input
                                if (!val) {
                                    const h = ctr.querySelector(
                                        'input[type="hidden"]');
                                    if (h && h.value) val = h.value;
                                }

                                // 7. Buttons showing selected value
                                if (!val) {
                                    ctr.querySelectorAll('button').forEach(
                                        btn => {
                                        if (val) return;
                                        const t = (btn.textContent||'').trim();
                                        const fl = (t.split('\n')[0]||'').trim();
                                        if (fl && fl.length>1 && fl.length<100
                                            && !/^(Add|Remove|Clear|×|X|Save|Cancel|Delete|Duplicate|Choose|Select|Show|Back|Next|Configure|open)/i.test(fl)
                                            && !btn.contains(lbl))
                                            val = fl;
                                    });
                                }

                                // 8. Sibling text
                                if (!val) {
                                    for (const ch of ctr.children) {
                                        if (ch===lbl||ch.contains(lbl)
                                            ||lbl.contains(ch)) continue;
                                        const t = ch.textContent.trim();
                                        if (t && t.length>0 && t.length<200
                                            && !/^(Required|Optional|\*|Choose fields)/i.test(t)){
                                            val = t; break;
                                        }
                                    }
                                }

                                val = val.replace(DEDUP, '$1').trim();
                                if (val) {
                                    seen.add(lt.toLowerCase());
                                    result.fields.push(
                                        {field: lt, value: val});
                                }
                            });

                            // ── STRATEGY A2: orphan inputs (no <label> parent) ──
                            // Condition blocks have text inputs with
                            // aria-labelledby or name but no wrapping <label>.
                            panel.querySelectorAll(
                                'input[data-ds--text-field--input],' +
                                'input[type="text"]:not([role="combobox"])' +
                                ':not([class*="ak-select"])'
                            ).forEach(inp => {
                                const val = (inp.value || '').trim();
                                if (!val || val.length > 500) return;
                                // Skip if already captured
                                if (result.fields.some(
                                    f => f.value === val)) return;

                                // Determine label
                                let lt = '';
                                const lblId = inp.getAttribute(
                                    'aria-labelledby');
                                if (lblId) {
                                    const lblEl = document.getElementById(
                                        lblId);
                                    if (lblEl) lt = lblEl.textContent.trim()
                                        .replace(/\s*\*\s*(\(required\))?/gi,'')
                                        .replace(/\s*\(optional\)/gi,'')
                                        .trim();
                                }
                                // If inside a tabpanel, use the tab label
                                if (!lt) {
                                    const tp = inp.closest(
                                        '[role="tabpanel"]');
                                    if (tp) {
                                        const tid = tp.getAttribute(
                                            'aria-labelledby');
                                        if (tid) {
                                            const tab = document.getElementById(
                                                tid);
                                            if (tab) lt =
                                                tab.textContent.trim();
                                        }
                                    }
                                }
                                if (!lt) {
                                    lt = inp.getAttribute('name')
                                        || inp.getAttribute('placeholder')
                                        || '';
                                    // Clean prefixes like "condition-"
                                    lt = lt.replace(/^condition-/i, '');
                                }
                                if (lt) lt = lt.charAt(0).toUpperCase()
                                    + lt.slice(1);
                                if (!lt || seen.has(lt.toLowerCase())) return;
                                seen.add(lt.toLowerCase());
                                result.fields.push({field: lt,
                                    value: val.replace(DEDUP, '$1').trim()});
                            });

                            // ── Condition block: match type selector ──
                            panel.querySelectorAll(
                                '[class*="MatchTypeSelector"]'
                            ).forEach(sel => {
                                // The active button has a visually distinct
                                // class; detect via aria-pressed or by
                                // comparing CSS classes (active = different)
                                const btns = sel.querySelectorAll('button');
                                if (btns.length >= 2) {
                                    // First button is the primary style when
                                    // active; pick the one whose class list
                                    // differs (Jira sets a different class)
                                    const classes = Array.from(btns).map(
                                        b => b.className);
                                    let activeText = '';
                                    for (const b of btns) {
                                        // Check for aria-pressed or bold style
                                        if (b.getAttribute('aria-pressed')
                                            === 'true') {
                                            activeText = b.textContent.trim();
                                            break;
                                        }
                                    }
                                    if (!activeText) {
                                        // Fallback: distinct class = active
                                        const freq = {};
                                        classes.forEach(c => {
                                            freq[c] = (freq[c]||0) + 1;});
                                        for (let i=0; i<btns.length; i++) {
                                            if (freq[classes[i]] === 1) {
                                                activeText =
                                                    btns[i].textContent.trim();
                                                break;
                                            }
                                        }
                                    }
                                    if (!activeText)
                                        activeText =
                                            btns[0].textContent.trim();
                                    const mk = 'match type';
                                    if (!seen.has(mk)) {
                                        seen.add(mk);
                                        result.fields.push({
                                            field: 'Match type',
                                            value: activeText});
                                    }
                                }
                            });

                            // ── STRATEGY B: <p> headers ──
                            // (skip long descriptions / Jira help text)
                            const DESC_NOISE = /^(Checks whether|Find out about|The else block|executes the|This condition|This action|This trigger|Restrict the|Limit the|Choose what|Specify the|Select the|Creates? a |Transitions? |Sets? the |Sends? |Logs? )/i;
                            panel.querySelectorAll('p').forEach(p => {
                                const raw = p.textContent.trim();
                                const mm = raw.match(
                                    /^(.+?)[\s*]*(?:\(required\)|\(optional\))?$/i);
                                if (!mm) return;
                                const fn = mm[1].replace(/\s*\*\s*$/,'').trim();
                                if (!fn||fn.length<2||fn.length>50
                                    ||seen.has(fn.toLowerCase())) return;
                                if (DESC_NOISE.test(fn)) return;
                                let next = p.nextElementSibling;
                                let att = 0;
                                while (next && att<3) {
                                    const el = next.querySelector(
                                        'input,textarea,[contenteditable],select');
                                    if (el) {
                                        let v = el.value||el.textContent.trim();
                                        if (v && v.length<300) {
                                            v = v.replace(DEDUP,'$1');
                                            seen.add(fn.toLowerCase());
                                            result.fields.push(
                                                {field:fn, value:v});
                                            break;
                                        }
                                    }
                                    const sv = next.querySelector(
                                        '.ak-select__single-value,' +
                                        '[class*="singleValue"]');
                                    if (sv) {
                                        const lc = sv.querySelector(
                                            '[class*="LabelContainer"]');
                                        const v = lc
                                            ? lc.textContent.trim()
                                            : sv.textContent.trim();
                                        if (v) {
                                            seen.add(fn.toLowerCase());
                                            result.fields.push(
                                                {field:fn, value:v});
                                            break;
                                        }
                                    }
                                    next = next.nextElementSibling;
                                    att++;
                                }
                            });

                            // ── STRATEGY C: Code / JSON editors ──
                            panel.querySelectorAll(
                                'textarea, pre, code, [class*="code" i]'
                            ).forEach(el => {
                                const text = (el.value
                                    ||el.textContent||'').trim();
                                if (!text||text.length<5) return;
                                if (result.fields.some(
                                    f=>f.value.includes(
                                        text.substring(0,20)))) return;
                                let label = 'Additional fields';
                                let prev = el.parentElement;
                                let at2 = 0;
                                while (prev && at2<5) {
                                    const ps = prev.previousElementSibling;
                                    if (ps) {
                                        const t = ps.textContent.trim();
                                        if (t && t.length<40) {
                                            label = t; break;
                                        }
                                    }
                                    prev = prev.parentElement;
                                    at2++;
                                }
                                if (!seen.has(label.toLowerCase())) {
                                    seen.add(label.toLowerCase());
                                    result.fields.push({field:label,
                                        value:text.substring(0,500)});
                                }
                            });

                            // ── STRATEGY D: TreeWalker fallback ──
                            if (result.fields.length === 0) {
                                const wk = document.createTreeWalker(
                                    panel, NodeFilter.SHOW_TEXT);
                                const pts = [];
                                let nd;
                                while (nd = wk.nextNode()) {
                                    const t = nd.textContent.trim();
                                    if (t && t.length>0) pts.push(t);
                                }
                                const noise = /^(Save|Cancel|Close|Duplicate|Delete|Edit|Back|OK|Done|Next|Change trigger|Add component|More options|Choose fields to set|Show smart value panel|Select operation|open|Configure|Learn More|Expand|Send an email)$/i;
                                const ln = pts.filter(
                                    t=>t.length>1 && !noise.test(t));
                                const usedIdx = new Set();
                                for (let i=0;i<ln.length;i++) {
                                    const mx = ln[i].match(
                                        /^([A-Z][^:]{1,50}):\s*(.+)$/i);
                                    if (mx) {
                                        result.fields.push({
                                            field:mx[1].trim(),
                                            value:mx[2].trim()});
                                        usedIdx.add(i);
                                    }
                                }
                                for (let i=0;i<ln.length-1;i++) {
                                    if (usedIdx.has(i)) continue;
                                    if (ln[i].length<=40 &&
                                        ln[i+1].length>=ln[i].length) {
                                        result.fields.push({
                                            field:ln[i],value:ln[i+1]});
                                        usedIdx.add(i);
                                        usedIdx.add(i+1);
                                        i++;
                                    }
                                }
                                let di = 1;
                                for (let i=0;i<ln.length;i++) {
                                    if (usedIdx.has(i)) continue;
                                    if (ln[i].length>3 && ln[i].length<300){
                                        result.fields.push({
                                            field:'Detail '+di,
                                            value:ln[i]});
                                        di++;
                                    }
                                }
                                result.raw_text = ln.join(' | ');
                            }

                            return result;
                        })()
                        """

                        # ── Click into each rule for deep extraction ──
                        for ri in list_rules:
                            rname = ri.get('name', '?')
                            rhref = ri.get('href', '')
                            enabled = ri.get('enabled', '')
                            steps = []

                            if rhref:
                                try:
                                    driver.get(rhref)

                                    # Wait until workflow components render (up to 16s)
                                    try:
                                        WebDriverWait(driver, 16, poll_frequency=0.5).until(
                                            lambda d: (d.execute_script(
                                                "return document.querySelectorAll("
                                                "'[data-testid=\"rule-workflow-component\"]'"
                                                ").length") or 0) > 0)
                                    except Exception:
                                        pass
                                    comp_count = driver.execute_script(
                                        "return document.querySelectorAll("
                                        "'[data-testid=\"rule-workflow-component\"]'"
                                        ").length") or 0

                                    # Phase 1: enabled + component overview
                                    overview = driver.execute_script(r"""
                                    const res = {enabled:'', components:[]};
                                    const tog = document.querySelector(
                                        '[role="switch"]');
                                    if (tog) res.enabled =
                                        tog.getAttribute('aria-checked')==='true'
                                        ? 'ENABLED' : 'DISABLED';
                                    if (!res.enabled) {
                                        const bt = document.body.innerText||'';
                                        if (/\bENABLED\b/.test(bt))
                                            res.enabled = 'ENABLED';
                                        else if (/\bDISABLED\b/.test(bt))
                                            res.enabled = 'DISABLED';
                                    }
                                    function nodeText(el) {
                                        if (!el) return '';
                                        const parts = [];
                                        const w = document.createTreeWalker(
                                            el, NodeFilter.SHOW_TEXT);
                                        let n;
                                        while (n = w.nextNode()) {
                                            const t = n.textContent.trim();
                                            if (t) parts.push(t);
                                        }
                                        let text = parts.join(' ');
                                        text = text.replace(
                                            /\b(Duplicate|Delete|Edit|Copy|Move|Change trigger|Add component)\b/gi,
                                            ' ');
                                        text = text.replace(
                                            /\b([\w][\w-]*(?:\s+[\w][\w-]*){0,2})\s+\1\b/gi,
                                            '$1');
                                        return text.replace(/\s+/g,' ').trim();
                                    }
                                    document.querySelectorAll(
                                        '[data-testid="rule-workflow-component"]'
                                    ).forEach((comp, idx) => {
                                        const btn = comp.querySelector(
                                            'button[data-testid]');
                                        const tid = btn
                                            ? btn.getAttribute('data-testid')
                                            : '';
                                        let type = 'ACTION';
                                        if (/trigger/i.test(tid))
                                            type = 'TRIGGER';
                                        else if (/condition/i.test(tid))
                                            type = 'CONDITION';
                                        else if (/branch/i.test(tid))
                                            type = 'BRANCH';
                                        res.components.push({
                                            index: idx, type: type,
                                            testId: tid,
                                            summary: nodeText(btn || comp)
                                        });
                                    });
                                    if (res.components.length === 0) {
                                        const items = document.querySelectorAll(
                                            '[class*="rule-workflow"] li, ' +
                                            '[class*="RuleWorkflow"] li');
                                        items.forEach((li, idx) => {
                                            const t = nodeText(li);
                                            const lo = t.toLowerCase();
                                            let type = 'ACTION';
                                            if (lo.startsWith('when'))
                                                type = 'TRIGGER';
                                            else if (lo.startsWith('if') ||
                                                     lo.startsWith('condition'))
                                                type = 'CONDITION';
                                            res.components.push({
                                                index: idx, type,
                                                testId: '', summary: t
                                            });
                                        });
                                    }
                                    return res;
                                    """) or {}

                                    if overview.get('enabled'):
                                        enabled = overview['enabled']
                                    components = overview.get('components', [])

                                    # If 0 components found, the page may
                                    # still be loading — retry once
                                    if not components:
                                        print(f"    → '{rname}': 0 components,"
                                              f" retrying…")
                                        try:
                                            WebDriverWait(driver, 8, poll_frequency=0.5).until(
                                                lambda d: (d.execute_script(
                                                    "return document.querySelectorAll("
                                                    "'[data-testid=\"rule-workflow-component\"]'"
                                                    ").length") or 0) > 0)
                                        except Exception:
                                            pass
                                        overview = driver.execute_script(
                                            r"""
                                            const res = {components:[]};
                                            """ + r"""
                                            function nodeText(el) {
                                                if (!el) return '';
                                                const parts = [];
                                                const w = document.createTreeWalker(
                                                    el, NodeFilter.SHOW_TEXT);
                                                let n;
                                                while (n = w.nextNode()) {
                                                    const t = n.textContent.trim();
                                                    if (t) parts.push(t);
                                                }
                                                let text = parts.join(' ');
                                                text = text.replace(
                                                    /\b(Duplicate|Delete|Edit|Copy|Move|Change trigger|Add component)\b/gi,
                                                    ' ');
                                                text = text.replace(
                                                    /\b([\w][\w-]*(?:\s+[\w][\w-]*){0,2})\s+\1\b/gi,
                                                    '$1');
                                                return text.replace(/\s+/g,' ').trim();
                                            }
                                            document.querySelectorAll(
                                                '[data-testid="rule-workflow-component"]'
                                            ).forEach((comp, idx) => {
                                                const btn = comp.querySelector(
                                                    'button[data-testid]')
                                                    || comp.querySelector('button');
                                                const tid = btn
                                                    ? btn.getAttribute('data-testid') || ''
                                                    : '';
                                                let type = 'ACTION';
                                                if (/trigger/i.test(tid))
                                                    type = 'TRIGGER';
                                                else if (/condition/i.test(tid))
                                                    type = 'CONDITION';
                                                else if (/branch/i.test(tid))
                                                    type = 'BRANCH';
                                                res.components.push({
                                                    index: idx, type: type,
                                                    testId: tid,
                                                    summary: nodeText(btn || comp)
                                                });
                                            });
                                            return res;
                                            """) or {}
                                        components = overview.get(
                                            'components', [])

                                    print(f"    → '{rname}': "
                                          f"{len(components)} component(s)")

                                    # Phase 2: Click each component
                                    for ci, comp in enumerate(components):
                                        comp_summary = _clean_ui_noise(
                                            comp.get('summary', ''))
                                        step = {
                                            'order':   ci + 1,
                                            'type':    comp.get('type',
                                                               'UNKNOWN'),
                                            'summary': comp_summary,
                                            'config':  [],
                                        }

                                        try:
                                            # Click the component button.
                                            # Use broad selectors: any button
                                            # inside a workflow component, not
                                            # just button[data-testid].
                                            click_result = driver.execute_script(
                                                """
                                                const idx = arguments[0];
                                                const btns = [];
                                                document.querySelectorAll(
                                                    '[data-testid="rule-workflow-component"]'
                                                ).forEach(c => {
                                                    // Try button with data-testid first
                                                    let b = c.querySelector(
                                                        'button[data-testid]');
                                                    // Fallback: any button
                                                    if (!b) b = c.querySelector('button');
                                                    if (b) btns.push(b);
                                                });
                                                if (idx < btns.length) {
                                                    btns[idx].scrollIntoView(
                                                        {block:'center'});
                                                    btns[idx].click();
                                                    return {ok:true, total:btns.length};
                                                }
                                                return {ok:false, total:btns.length};
                                                """, ci)

                                            clicked = (click_result or {}).get('ok', False)
                                            btn_count = (click_result or {}).get('total', 0)

                                            if ci == 0:
                                                print(f"      click: {clicked}, "
                                                      f"{btn_count} btns found")

                                            if clicked:
                                                # Wait for config panel to
                                                # appear (form or section)
                                                try:
                                                    WebDriverWait(driver, 4, poll_frequency=0.3).until(
                                                        lambda d: d.execute_script("""
                                                            return !!(
                                                                document.querySelector('[class*="component-form"] form') ||
                                                                document.querySelector('[class*="FormContainer"] form') ||
                                                                document.querySelector('[class*="create-issue-config"] form') ||
                                                                document.querySelector('[class*="rule-component-configure"]') ||
                                                                document.querySelector('section[class*="independent-scrolling"] form')
                                                            );
                                                        """))
                                                except Exception:
                                                    pass

                                                # Extract panel fields — try
                                                # twice (second after expand)
                                                config = {}
                                                for _att in range(2):
                                                    config = (
                                                        driver.execute_script(
                                                            EXTRACT_PANEL_JS
                                                        ) or {})
                                                    if config.get('fields'):
                                                        break
                                                    if config.get(
                                                        'debug') == 'NO_PANEL':
                                                        time.sleep(1)
                                                        continue
                                                    break

                                                dbg = config.get('debug','')
                                                nf = len(config.get(
                                                    'fields', []))
                                                if ci < 3:
                                                    print(
                                                        f"      step{ci+1}: "
                                                        f"{dbg[:40]} "
                                                        f"| {nf} flds")

                                                if config.get('fields'):
                                                    step['config'] = [
                                                        {
                                                          'field':
                                                            _clean_ui_noise(
                                                              f.get('field','')),
                                                          'value':
                                                            _clean_ui_noise(
                                                              f.get('value','')),
                                                        }
                                                        for f in config[
                                                            'fields']
                                                        if f.get('field')
                                                        and f.get('value')
                                                    ]

                                                if (not step['config']
                                                      and config.get(
                                                          'raw_text')
                                                      and config['raw_text']
                                                      != '(panel not found)'):
                                                    step['config'] = [{
                                                      'field': 'Details',
                                                      'value':
                                                        _clean_ui_noise(
                                                          config['raw_text'
                                                            ][:500]),
                                                    }]

                                            else:
                                                if ci == 0:
                                                    print(f"      ⚠ click failed, "
                                                          f"idx={ci}")

                                        except Exception as e:
                                            print(f"      ⚠ Step {ci+1}: "
                                                  f"{str(e).splitlines()[0][:60]}")

                                        steps.append(step)

                                    nc = sum(1 for s in steps
                                             if s.get('config'))
                                    print(
                                        f"    ✓ '{rname}': "
                                        f"[{'ON' if enabled=='ENABLED' else 'OFF'}] "
                                        f"{len(steps)} steps, "
                                        f"{nc} with deep config")

                                    driver.get(data['automation_url'])
                                    try:
                                        WebDriverWait(driver, 15, poll_frequency=0.5).until(
                                            lambda d: (d.execute_script(
                                                "return document.querySelectorAll("
                                                "'table tbody tr').length") or 0) > 0)
                                    except Exception:
                                        time.sleep(1)
                                except Exception as e:
                                    print(f"    ⚠ '{rname}': "
                                          f"{str(e).splitlines()[0][:80]}")
                                    driver.get(data['automation_url'])
                                    try:
                                        WebDriverWait(driver, 15, poll_frequency=0.5).until(
                                            lambda d: (d.execute_script(
                                                "return document.querySelectorAll("
                                                "'table tbody tr').length") or 0) > 0)
                                    except Exception:
                                        time.sleep(1)

                            data['automations'].append({
                                'name': rname,
                                'enabled': ('✅' if enabled == 'ENABLED'
                                           else '❌' if enabled == 'DISABLED'
                                           else '—'),
                                'steps': steps,
                                'url': rhref,
                            })

                        if data['automations']:
                            print(f"    ✓ {len(data['automations'])} rule(s) with details")
                    finally:
                        driver.quit()

            except ImportError:
                print("    ⚠ selenium not installed — pip install selenium webdriver-manager")
            except Exception as e:
                print(f"    ⚠ Browser failed: {str(e).splitlines()[0][:120]}")

        if not data['automations']:
            print(f"    ℹ View rules at: {data['automation_url']}")

        print(f"    → {len(data['automations'])} automation rule(s) collected")

        return data

    except Exception as e:
        print(f"❌ Fatal error fetching data: {e}")
        return None


# ──────────────────────────────────────────
# Markdown Report Generator
# ──────────────────────────────────────────

def generate_markdown(data: dict, project_key: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    def h(level, text):
        lines.append(f"\n{'#' * level} {text}\n")

    def p(text=""):
        lines.append(text)

    # ── Title ──────────────────────────────────────────────────────────────
    lines.append(f"# 📋 Jira Project Report — {data['name']} (`{data['key']}`)")
    p(f"> Generated on **{now}** · [Open in Jira]({data['url']})")

    # ── 1. Project Overview ────────────────────────────────────────────────
    h(2, "1. Project Overview")
    p(_md_table(
        ["Field", "Value"],
        [
            ["Name",                  data['name']],
            ["Key",                   data['key']],
            ["Type",                  data['type']],
            ["Lead",                  data['lead']],
            ["Description",           data['description']],
            ["Total Issues",          data['total_issues']],
            ["Permission Scheme",     data['permission_scheme']],
            ["Notification Scheme",   data['notification_scheme']],
            ["Security Scheme",       data['security_scheme']],
        ]
    ))

    # ── 2. Issue Types ─────────────────────────────────────────────────────
    h(2, "2. Issue Types")
    if data['issue_types']:
        rows = [[it['name'], it['description'] or '—'] for it in data['issue_types']]
        p(_md_table(["Issue Type", "Description"], rows))
    else:
        p("*No issue types configured.*")

    # ── 3. Issue Statistics ────────────────────────────────────────────────
    h(2, "3. Issue Statistics")
    stats = data['stats']
    total = data['total_issues'] or 1   # avoid div/0

    h(3, "3.1 By Type")
    p(_md_table(
        ["Type", "Count", "Share"],
        [[k, v, _bar(v, total)] for k, v in stats['by_type'].items()]
    ))

    h(3, "3.2 By Status")
    p(_md_table(
        ["Status", "Count", "Share"],
        [[k, v, _bar(v, total)] for k, v in stats['by_status'].items()]
    ))

    h(3, "3.3 By Priority")
    p(_md_table(
        ["Priority", "Count", "Share"],
        [[k, v, _bar(v, total)] for k, v in stats['by_priority'].items()]
    ))

    h(3, "3.4 By Assignee  *(top 15)*")
    top_assignees = list(stats['by_assignee'].items())[:15]
    p(_md_table(
        ["Assignee", "Count", "Share"],
        [[k, v, _bar(v, total)] for k, v in top_assignees]
    ))

    # ── 4. Epics ───────────────────────────────────────────────────────────
    epic_type = data.get('epic_type', 'Epic')
    h(2, f"4. {epic_type}s  (High-Level Architecture)")
    if data['epics']:
        rows = [
            [e['key'], e['summary'], e['status'], e['priority'], e['assignee']]
            for e in data['epics']
        ]
        p(_md_table(["Key", "Summary", "Status", "Priority", "Assignee"], rows))
    else:
        p("*No epics found.*")

    # ── 5. Components ──────────────────────────────────────────────────────
    h(2, "5. Components")
    if data['components']:
        rows = [[c['name'], c['lead'], c['description'] or '—'] for c in data['components']]
        p(_md_table(["Component", "Lead", "Description"], rows))
    else:
        p("*No components configured.*")

    # ── 6. Versions / Releases ─────────────────────────────────────────────
    h(2, "6. Versions / Releases")
    if data['versions']:
        rows = [
            [v['name'], v['released'], v['release_date'], v['description'] or '—']
            for v in data['versions']
        ]
        p(_md_table(["Version", "State", "Release Date", "Description"], rows))
    else:
        p("*No versions configured.*")

    # ── 7. Boards & Sprints ────────────────────────────────────────────────
    h(2, "7. Boards & Sprints")
    if data['boards']:
        for board in data['boards']:
            h(3, f"Board: {board['name']}  `[{board['type'].upper()}]`")
            if board['sprints']:
                rows = [
                    [s['name'], s['state'].upper(), s['start'][:10] if s['start'] != 'N/A' else 'N/A',
                     s['end'][:10] if s['end'] != 'N/A' else 'N/A']
                    for s in board['sprints']
                ]
                p(_md_table(["Sprint", "State", "Start", "End"], rows))
            else:
                p("*No sprints (Kanban board or none found).*")
    else:
        p("*No boards found.*")

    # ── 8. Project Roles & Members ─────────────────────────────────────────
    h(2, "8. Project Roles & Members")
    if data['roles']:
        rows = [
            [role, ", ".join(members) if members else "*(empty)*"]
            for role, members in data['roles'].items()
        ]
        p(_md_table(["Role", "Members"], rows))
    else:
        p("*No role data available.*")

    # ── 9. Workflow Configuration ──────────────────────────────────────────
    h(2, "9. Workflow Configuration")
    p(f"**Workflow Scheme:** `{data.get('workflow_scheme_name', 'N/A')}`\n")

    if data.get('issue_type_workflow_map'):
        h(3, "9.1 Issue Type → Workflow Mapping")
        p(_md_table(
            ["Issue Type", "Workflow"],
            [[it, wf] for it, wf in data['issue_type_workflow_map']]
        ))

    for wf_name, wf in data.get('workflows', {}).items():
        h(3, f"9.x Workflow: `{wf_name}`")

        # Statuses
        p("**Statuses:**")
        p("  " + " → ".join(f"`{s}`" for s in wf['statuses']) if wf['statuses'] else "*none*")
        p()

        # Transitions table
        if wf['transitions']:
            h(4, "Transitions")
            rows = [
                [t['name'], t['type'], t['from'], t['to'],
                 t['conditions'], t['validators'], t['post_fns']]
                for t in wf['transitions']
            ]
            p(_md_table(
                ["Transition", "Type", "From", "To", "Conditions", "Validators", "Post-Functions"],
                rows
            ))
        else:
            p("*No transitions found.*")

    # ── 10. Screens & Fields ───────────────────────────────────────────────
    h(2, "10. Screens & Fields")
    p(f"**Screen Scheme:** `{data.get('screen_scheme_name', 'N/A')}`\n")

    screens = data.get('screens', {})
    if screens:
        for scheme_name, ops in screens.items():
            h(3, f"Scheme: `{scheme_name}`")
            for operation, info in sorted(ops.items()):
                h(4, f"Operation: `{operation}` → Screen: `{info['screen']}`")
                if info['fields']:
                    # Render as a 3-column grid for readability
                    chunk = 3
                    field_list = info['fields']
                    rows = []
                    for i in range(0, len(field_list), chunk):
                        row = field_list[i:i+chunk]
                        while len(row) < chunk:
                            row.append('')
                        rows.append(row)
                    p(_md_table(["Field", "Field", "Field"], rows))
                else:
                    p("*No fields found.*")
    else:
        p("*No screen configuration found.*")

    # ── 11. Field Configuration ────────────────────────────────────────────
    h(2, "11. Field Configuration")
    p(f"**Field Config Scheme:** `{data.get('field_config_scheme', 'N/A')}`\n")

    field_configs = data.get('field_configs', {})
    if field_configs:
        for cfg_name, fields in field_configs.items():
            h(3, f"Config: `{cfg_name}`")
            # Only show non-default rows (required or hidden)
            notable = [f for f in fields if f['required'] == '✅' or f['hidden'] == '🙈']
            if notable:
                p("**Required / Hidden fields:**")
                p(_md_table(
                    ["Field", "Required", "Hidden"],
                    [[f['name'], f['required'], f['hidden']] for f in notable]
                ))
            # Full field list
            p(f"**All fields ({len(fields)}):**")
            p(_md_table(
                ["Field", "Required", "Hidden"],
                [[f['name'], f['required'], f['hidden']] for f in fields]
            ))
    else:
        p("*No field configuration found.*")

    # ── 12. Automation Rules ───────────────────────────────────────────────
    h(2, "12. Automation Rules")
    automations = data.get('automations', [])
    auto_url    = data.get('automation_url', '')
    if auto_url:
        p(f"> 📎 [View all rules in Jira]({auto_url})\n")

    _STEP_ICONS = {
        'TRIGGER': '🔔', 'CONDITION': '🔀',
        'BRANCH': '🔀',  'ACTION': '⚡',
    }

    if automations:
        for ai, rule in enumerate(automations, 1):
            rname   = rule['name']
            badge   = rule['enabled']
            steps   = rule.get('steps', [])

            h(3, f"12.{ai} Rule: `{rname}`  {badge}")

            # ── Legacy flat format (backward compat with old JSON) ──
            if not steps and (rule.get('trigger') or rule.get('conditions')
                              or rule.get('actions')):
                rows = []
                if rule.get('trigger') and rule['trigger'] != '—':
                    rows.append(['🔔 TRIGGER', rule['trigger']])
                if rule.get('conditions') and rule['conditions'] != '—':
                    for c in rule['conditions'].split('; '):
                        rows.append(['🔀 CONDITION', c])
                if rule.get('actions') and rule['actions'] != '—':
                    for a in rule['actions'].split('; '):
                        rows.append(['⚡ ACTION', a])
                if rows:
                    p(_md_table(["Type", "Description"], rows))
                else:
                    p("*No details available.*")
                continue

            if not steps:
                rule_url = rule.get('url', '')
                if rule_url:
                    p(f"*Could not extract details — "
                      f"[view rule in Jira]({rule_url})*")
                else:
                    p("*No details available.*")
                continue

            # ── Step overview table ──
            summary_rows = [
                [
                    s['order'],
                    f"{_STEP_ICONS.get(s['type'], '❓')} {s['type']}",
                    s.get('summary', '—') or '—',
                ]
                for s in steps
            ]
            p(_md_table(["#", "Type", "Component"], summary_rows))

            # ── Per-step configuration details ──
            for s in steps:
                if not s.get('config'):
                    continue
                icon = _STEP_ICONS.get(s['type'], '❓')
                summary_short = (s.get('summary', '') or '')[:100]
                p(f"\n**Step {s['order']} — {icon} {s['type']}:"
                  f" {summary_short}**")
                p(_md_table(
                    ["Field", "Value"],
                    [[c['field'], c['value']] for c in s['config']]
                ))
    else:
        p("*No automation rules found or could not be retrieved.*")
        if auto_url:
            p(f"\n> 📎 **View rules directly:** [{auto_url}]({auto_url})")

    p(f"\n---\n*Report generated by `extract_project.py` on {now}*")
    return "\n".join(lines)


# ──────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n🔍 Fetching full configuration for project: {PROJECT_KEY}")
    print("=" * 55)

    project_data = fetch_project_architecture(PROJECT_KEY)

    if project_data:
        output_file = 'jira_current_state.md'
        md = generate_markdown(project_data, PROJECT_KEY)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(md)

        # Also dump the raw data as JSON for further programmatic use
        json_file = 'jira_current_state.json'
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(project_data, f, indent=2, default=str)

        print(f"\n✅  Markdown report  → {output_file}")
        print(f"✅  Raw JSON data    → {json_file}")
        print("\nOpen the markdown file in PyCharm and ask Copilot Chat to analyse it.")
    else:
        print("\n❌  Failed to fetch project data. Check credentials and project key.")

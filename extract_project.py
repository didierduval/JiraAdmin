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
                    jira_host = urlparse(JIRA_SERVER).hostname.lower()
                    drv.get(f"{JIRA_SERVER}/login")
                    try:
                        wait = WebDriverWait(drv, 20)
                        # Email field
                        email_el = wait.until(EC.presence_of_element_located(
                            (By.ID, 'username')))
                        email_el.clear()
                        email_el.send_keys(JIRA_EMAIL)
                        drv.find_element(By.ID, 'login-submit').click()
                        # Password field
                        pwd_el = wait.until(EC.presence_of_element_located(
                            (By.ID, 'password')))
                        pwd_el.clear()
                        pwd_el.send_keys(JIRA_PASSWORD)
                        drv.find_element(By.ID, 'login-submit').click()
                        # Wait until we're on the actual Jira site (not login pages)
                        wait = WebDriverWait(drv, 30)
                        wait.until(lambda d: urlparse(d.current_url).hostname == jira_host)
                        time.sleep(3)
                        return True
                    except Exception as e:
                        print(f"    ⚠ Password login failed: {str(e).splitlines()[0][:80]}")
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
                                time.sleep(3)  # let the SPA settle
                                return True
                        except Exception:
                            pass
                        time.sleep(2)
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
                        table_deadline = time.time() + 30
                        row_count = 0
                        while time.time() < table_deadline:
                            row_count = driver.execute_script(
                                "return document.querySelectorAll('table tbody tr').length"
                            ) or 0
                            if row_count > 0:
                                break
                            time.sleep(2)
                        time.sleep(2)  # extra settle time
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

                        # ── Click into each rule for details ──────────
                        for ri in list_rules:
                            rname = ri.get('name', '?')
                            rhref = ri.get('href', '')
                            enabled = ri.get('enabled', '')
                            trigger = '—'
                            conditions = '—'
                            actions = '—'

                            if rhref:
                                try:
                                    driver.get(rhref)
                                    time.sleep(5)

                                    detail = driver.execute_script(r"""
                                    const res = {trigger:'', conditions:[], actions:[], enabled:''};

                                    // Clean UI noise from scraped text
                                    function clean(text) {
                                        return text
                                            .replace(/\s*(Duplicate|Delete|Change trigger|Add component|Edit|Copy|Move)\s*/gi, ' ')
                                            .replace(/\s+/g, ' ')
                                            .trim();
                                    }

                                    // Detect enabled from the badge/label on the header
                                    const header = document.body.innerText;
                                    if (/\bENABLED\b/.test(header)) res.enabled = 'ENABLED';
                                    else if (/\bDISABLED\b/.test(header)) res.enabled = 'DISABLED';
                                    const tog = document.querySelector('[role="switch"]');
                                    if (tog) res.enabled = tog.getAttribute('aria-checked')==='true' ? 'ENABLED':'DISABLED';

                                    // Use the actual DOM structure
                                    const components = document.querySelectorAll(
                                        '[data-testid="rule-workflow-component"]');

                                    components.forEach(comp => {
                                        const btn = comp.querySelector('button[data-testid]');
                                        const testId = btn ? btn.getAttribute('data-testid') : '';
                                        // Get text only from the button content, not sibling menus
                                        const text = clean(btn ? btn.innerText : comp.innerText);

                                        if (testId.includes('TRIGGER') || testId.toLowerCase().includes('trigger')) {
                                            // Remove "Condition applied ..." that bleeds in from a sibling
                                            let t = text.replace(/^When:?\s*/i, '');
                                            t = t.replace(/\s*Condition applied\b.*/i, '').trim();
                                            res.trigger = t;
                                        } else if (testId.includes('CONDITION') || testId.toLowerCase().includes('condition')) {
                                            let c = text.replace(/^(If:?\s*|Condition applied:?\s*)/i, '').trim();
                                            if (c) res.conditions.push(c);
                                        } else {
                                            let a = text.replace(/^(Then|And):?\s*/i, '').trim();
                                            if (a) res.actions.push(a);
                                        }
                                    });

                                    // Fallback: if no components found via data-testid,
                                    // try the workflow list items
                                    if (!res.trigger && components.length === 0) {
                                        const items = document.querySelectorAll(
                                            '[class*="rule-workflow"] li, [class*="RuleWorkflow"] li, ' +
                                            'ol[class*="rule-workflow"] > li');
                                        items.forEach(li => {
                                            const t = li.innerText.replace(/\s+/g, ' ').trim();
                                            const lo = t.toLowerCase();
                                            if (lo.startsWith('when')) {
                                                res.trigger = t.replace(/^When:?\s*/i, '').trim();
                                            } else if (lo.startsWith('if') || lo.startsWith('condition')) {
                                                res.conditions.push(t.replace(/^(If|Condition[^:]*):?\s*/i, '').trim());
                                            } else if (lo.startsWith('then') || lo.startsWith('and')) {
                                                res.actions.push(t.replace(/^(Then|And):?\s*/i, '').trim());
                                            }
                                        });
                                    }

                                    // Last fallback: parse innerText line by line
                                    if (!res.trigger) {
                                        const lines = document.body.innerText.split('\n').map(l=>l.trim()).filter(l=>l);
                                        let phase = '';
                                        for (const l of lines) {
                                            const lo = l.toLowerCase();
                                            if (/^when[:\s]/i.test(l) || lo === 'when') { phase='T'; if(l.length>6) res.trigger=l.replace(/^when:?\s*/i,''); continue; }
                                            if (/^(if[:\s]|condition)/i.test(l)) { phase='C'; let v=l.replace(/^(if|condition[^:]*):?\s*/i,''); if(v) res.conditions.push(v); continue; }
                                            if (/^(then|and)[:\s]/i.test(l) || lo==='then') { phase='A'; let v=l.replace(/^(then|and):?\s*/i,''); if(v) res.actions.push(v); continue; }
                                            if (phase==='T' && !res.trigger && l.length>3 && l.length<200) res.trigger=l;
                                            else if (phase==='C' && l.length>3 && l.length<200) res.conditions.push(l);
                                            else if (phase==='A' && l.length>3 && l.length<200) res.actions.push(l);
                                        }
                                    }

                                    return res;
                                    """) or {}

                                    trigger = detail.get('trigger','') or '—'
                                    conds = [c for c in detail.get('conditions',[]) if c]
                                    acts = [a for a in detail.get('actions',[]) if a]
                                    conditions = '; '.join(conds[:5]) if conds else '—'
                                    actions = '; '.join(acts[:5]) if acts else '—'
                                    if detail.get('enabled'):
                                        enabled = detail['enabled']

                                    print(f"    ✓ '{rname}': "
                                          f"[{'ON' if enabled=='ENABLED' else 'OFF'}] "
                                          f"T={trigger[:40]} "
                                          f"| {len(conds)}C {len(acts)}A")

                                    driver.get(data['automation_url'])
                                    time.sleep(3)
                                except Exception as e:
                                    print(f"    ⚠ '{rname}': {str(e).splitlines()[0][:80]}")
                                    driver.get(data['automation_url'])
                                    time.sleep(3)

                            data['automations'].append({
                                'name': rname,
                                'enabled': ('✅' if enabled == 'ENABLED'
                                           else '❌' if enabled == 'DISABLED'
                                           else '—'),
                                'trigger': trigger[:150],
                                'conditions': conditions[:300],
                                'actions': actions[:300],
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
    if automations:
        p(_md_table(
            ["Rule Name", "Enabled", "Trigger", "Conditions", "Actions"],
            [[a['name'], a['enabled'], a['trigger'], a['conditions'], a['actions']]
             for a in automations]
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

#!/usr/bin/env python3
"""
setup_approval_matrix.py
========================
Automated setup of the **Approval Matrix** utility project in Jira Cloud.

This script:
  1. Creates (or reuses) a Jira Business project (key: AM)
  2. Reads the DPR project's components dynamically
  3. Creates mapping tickets  "[Component] - [Role]" → Assignee
  4. Generates step-by-step instructions for wiring the Lookup Issues
     action into the "DPR: Auto-Generate Close-Out Approvals" automation rule

Usage:
  1. Ensure your .env file has JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN set
  2. Edit the APPROVAL_MATRIX dict below with real email addresses
  3. Run:  python setup_approval_matrix.py
"""

import json
import os
import sys
from pathlib import Path
from textwrap import dedent

import requests
from jira import JIRA, JIRAError

# ── Load .env ────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

JIRA_SERVER    = os.environ.get('JIRA_SERVER', 'https://fifo24.atlassian.net')
JIRA_EMAIL     = os.environ.get('JIRA_EMAIL', '')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN', '')
DPR_KEY        = os.environ.get('PROJECT_KEY', 'DPR')     # source project
AM_KEY         = 'AM'                                       # approval matrix key
AM_NAME        = 'Approval Matrix'

# ── Jira connection ──────────────────────────────────────────────────────
jira = JIRA(
    options={'server': JIRA_SERVER},
    basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN),
)


# =====================================================================
#  CONFIGURATION — edit this section
# =====================================================================

# Base approval roles — these are always created for EVERY component.
# (Derived from the existing "DPR: Auto-Generate Close-Out Approvals" rule)
BASE_ROLES = [
    'DPR Owner',
    'Reliability Engineer',
    'Quality Engineer',
    'System Engineer',
    'Project Lead',
]

# Conditional approval roles — created only for specific DPR Types.
# The key is the DPR-Type value, the value is the role label used in
# the sub-task summary.
CONDITIONAL_ROLES = {
    'Supplier': 'Supplier',
    # Add more as needed, e.g.:
    # 'Manufacturing': 'Manufacturing',
}

# ── Approval matrix ──────────────────────────────────────────────────
# Maps  "Component - Role"  →  approver email.
# Populate with your actual team members.
#
# The script auto-discovers components from the DPR project, so you
# only need to fill in the email addresses below.  Any entry set to
# None or '' will be created as UNASSIGNED (you can fix it later in
# the AM project board).
#
# Example:
#   APPROVAL_MATRIX = {
#       'Nexus - DPR Owner':            'john.doe@company.com',
#       'Nexus - Reliability Engineer': 'jane.smith@company.com',
#       'Nexus - Quality Engineer':     'bob@company.com',
#       'Nexus - Supplier':             'supplier.lead@company.com',
#       ...
#   }
#
# If you leave this dict EMPTY, the script will auto-generate all
# Component × Role combinations with unassigned tickets — you can
# then assign them manually in Jira.

APPROVAL_MATRIX: dict[str, str | None] = {
    # ── Nexus ──
    # 'Nexus - DPR Owner':            'person@company.com',
    # 'Nexus - Reliability Engineer': 'person@company.com',
    # 'Nexus - Quality Engineer':     'person@company.com',
    # 'Nexus - System Engineer':      'person@company.com',
    # 'Nexus - Project Lead':         'person@company.com',
    # 'Nexus - Supplier':             'person@company.com',
    # ── Spectrum ──
    # 'Spectrum - DPR Owner':            'person@company.com',
    # 'Spectrum - Reliability Engineer': 'person@company.com',
    # 'Spectrum - Quality Engineer':     'person@company.com',
    # 'Spectrum - System Engineer':      'person@company.com',
    # 'Spectrum - Project Lead':         'person@company.com',
    # 'Spectrum - Supplier':             'person@company.com',
}


# =====================================================================
#  HELPERS
# =====================================================================

def _api3_get(path: str, params=None):
    """GET /rest/api/3/{path}."""
    r = jira._session.get(f"{JIRA_SERVER}/rest/api/3/{path}", params=params or {})
    r.raise_for_status()
    return r.json()


def _api3_post(path: str, payload: dict):
    """POST /rest/api/3/{path} with JSON body."""
    r = jira._session.post(
        f"{JIRA_SERVER}/rest/api/3/{path}",
        json=payload,
    )
    if r.status_code not in (200, 201):
        print(f"  ⚠ POST {path} → HTTP {r.status_code}")
        try:
            print(f"    {json.dumps(r.json(), indent=2)[:500]}")
        except Exception:
            print(f"    {r.text[:500]}")
    r.raise_for_status()
    return r.json()


def _find_user(email: str) -> dict | None:
    """Search for a Jira user by email. Returns {accountId, displayName} or None."""
    if not email:
        return None
    try:
        users = jira._session.get(
            f"{JIRA_SERVER}/rest/api/3/user/search",
            params={'query': email, 'maxResults': 1},
        ).json()
        if users:
            return {
                'accountId':   users[0]['accountId'],
                'displayName': users[0].get('displayName', email),
            }
    except Exception as e:
        print(f"  ⚠ User lookup failed for '{email}': {e}")
    return None


def _project_exists(key: str) -> bool:
    """Check if a project with the given key exists."""
    try:
        jira.project(key)
        return True
    except JIRAError:
        return False


def _get_current_user_account_id() -> str:
    """Return the accountId of the authenticated user."""
    me = _api3_get('myself')
    return me['accountId']


# =====================================================================
#  STEP 1 — Create or reuse the AM project
# =====================================================================

def create_am_project() -> str:
    """Create the Approval Matrix project if it doesn't exist.
    Returns the project key.
    """
    if _project_exists(AM_KEY):
        print(f"  ✓ Project '{AM_KEY}' already exists — reusing it.")
        return AM_KEY

    print(f"  → Creating project '{AM_NAME}' (key: {AM_KEY})…")

    lead_id = _get_current_user_account_id()

    # Try multiple template keys — Jira Cloud may accept different ones
    # depending on the license type (Jira Software vs Jira Work Management).
    templates = [
        # Jira Work Management / Business
        'com.atlassian.jira-core-project-templates:jira-core-simplified-task-tracking',
        'com.atlassian.jira-core-project-templates:jira-core-simplified-project-management',
        # Jira Software
        'com.pyxis.greenhopper.jira:gh-simplified-kanban-classic',
        'com.pyxis.greenhopper.jira:gh-simplified-basic',
    ]

    last_err = None
    for tmpl in templates:
        payload = {
            'key':                AM_KEY,
            'name':               AM_NAME,
            'projectTypeKey':     'business',
            'projectTemplateKey': tmpl,
            'description':        (
                'Approval Matrix — maps Component + Role to Approver '
                'for DPR Close-Out automation.'
            ),
            'leadAccountId':      lead_id,
            'assigneeType':       'UNASSIGNED',
        }
        try:
            result = _api3_post('project', payload)
            print(f"  ✓ Project created: {JIRA_SERVER}/jira/core/projects/{AM_KEY}/board")
            return result.get('key', AM_KEY)
        except requests.HTTPError as e:
            last_err = e
            # 'business' type may not be available — retry with 'software'
            try:
                sw_payload = {**payload, 'projectTypeKey': 'software'}
                result = _api3_post('project', sw_payload)
                print(f"  ✓ Project created (as software): "
                      f"{JIRA_SERVER}/jira/software/projects/{AM_KEY}/board")
                return result.get('key', AM_KEY)
            except requests.HTTPError:
                pass
            continue

    print(f"\n  ⚠ Could not create project '{AM_KEY}' with any template.")
    print(f"    Last error: {last_err}")
    print(f"\n  → Please create it manually:")
    print(f"    1. Go to {JIRA_SERVER} → Projects → Create project")
    print(f"    2. Choose Business → Task tracking (company-managed)")
    print(f"    3. Name: {AM_NAME}, Key: {AM_KEY}")
    print(f"    4. Re-run this script after creating the project.\n")
    sys.exit(1)


# =====================================================================
#  STEP 2 — Discover components & build full matrix
# =====================================================================

def discover_matrix() -> list[dict]:
    """Build the full list of matrix entries from the DPR project's
    components × roles.  Each entry is {summary, email, role, component}.
    """
    print(f"\n  → Reading components from project '{DPR_KEY}'…")
    components = jira.project_components(DPR_KEY)
    comp_names = sorted(c.name for c in components)
    print(f"  → Components found: {', '.join(comp_names)}")

    entries: list[dict] = []

    for comp in comp_names:
        # Base roles (always)
        for role in BASE_ROLES:
            key = f"{comp} - {role}"
            entries.append({
                'summary':   key,
                'email':     APPROVAL_MATRIX.get(key, None),
                'role':      role,
                'component': comp,
            })
        # Conditional roles (DPR-Type-specific)
        for dpr_type, role_label in CONDITIONAL_ROLES.items():
            key = f"{comp} - {role_label}"
            entries.append({
                'summary':   key,
                'email':     APPROVAL_MATRIX.get(key, None),
                'role':      role_label,
                'component': comp,
                'dpr_type':  dpr_type,
            })

    print(f"  → Matrix entries to create: {len(entries)}")
    return entries


# =====================================================================
#  STEP 3 — Create matrix tickets
# =====================================================================

def create_matrix_tickets(entries: list[dict]) -> list[dict]:
    """Create (or skip existing) tickets in the AM project.
    Returns the list of created/existing issues.
    """
    print(f"\n  → Checking for existing tickets in '{AM_KEY}'…")

    # Fetch all existing AM issues to avoid duplicates
    existing: dict[str, str] = {}   # summary → issue key
    start_at = 0
    while True:
        results = jira.search_issues(
            f'project = {AM_KEY} ORDER BY created ASC',
            startAt=start_at, maxResults=100,
            fields='summary',
        )
        for iss in results:
            existing[iss.fields.summary.strip()] = iss.key
        if start_at + len(results) >= results.total:
            break
        start_at += len(results)

    print(f"  → Existing tickets in AM: {len(existing)}")

    # Resolve issue type — prefer "Task"
    am_project = jira.project(AM_KEY)
    task_type = None
    for it in am_project.issueTypes:
        if it.name.lower() == 'task':
            task_type = it
            break
    if not task_type:
        # Fallback: use first available type
        task_type = am_project.issueTypes[0]
    print(f"  → Using issue type: {task_type.name} (id: {task_type.id})")

    created = []
    skipped = 0

    for entry in entries:
        summary = entry['summary']

        if summary in existing:
            skipped += 1
            entry['issue_key'] = existing[summary]
            created.append(entry)
            continue

        # Resolve assignee
        assignee_id = None
        email = entry.get('email') or ''
        if email:
            user = _find_user(email)
            if user:
                assignee_id = user['accountId']
                print(f"    → {summary} → {user['displayName']}")
            else:
                print(f"    ⚠ {summary} → user '{email}' not found, leaving unassigned")

        # Build issue fields
        fields = {
            'project':   {'key': AM_KEY},
            'issuetype': {'id': task_type.id},
            'summary':   summary,
            'description': (
                f"Approval Matrix entry: {entry['component']} — "
                f"{entry['role']}.\n\n"
                f"Assign this ticket to the person responsible "
                f"for approving this role."
            ),
        }
        if assignee_id:
            fields['assignee'] = {'accountId': assignee_id}

        # Add label for easy filtering
        fields['labels'] = ['approval-matrix']

        try:
            issue = jira.create_issue(fields=fields)
            entry['issue_key'] = issue.key
            created.append(entry)
            status = f"assigned to {email}" if email else "UNASSIGNED"
            print(f"    ✓ {issue.key}: {summary} ({status})")
        except JIRAError as e:
            print(f"    ⚠ Failed to create '{summary}': {e.text[:120]}")
            # If labels not allowed, retry without
            if 'labels' in str(e.text).lower() or 'field' in str(e.text).lower():
                try:
                    fields.pop('labels', None)
                    issue = jira.create_issue(fields=fields)
                    entry['issue_key'] = issue.key
                    created.append(entry)
                    print(f"    ✓ {issue.key}: {summary} (retry without labels)")
                except JIRAError as e2:
                    print(f"    ⚠ Retry also failed: {e2.text[:120]}")

    if skipped:
        print(f"  → Skipped {skipped} ticket(s) that already exist")
    print(f"  ✓ {len(created)} matrix ticket(s) ready in project {AM_KEY}")
    return created


# =====================================================================
#  STEP 4 — Generate automation wiring instructions
# =====================================================================

def generate_instructions(entries: list[dict]) -> str:
    """Generate markdown instructions for wiring the Lookup Issues
    action into the DPR automation rule.
    """
    comp_names = sorted(set(e['component'] for e in entries))
    role_names = sorted(set(e['role'] for e in entries))

    # Build the instruction document
    doc = dedent(f"""\
    # 🔧 Approval Matrix — Automation Wiring Guide

    > Generated by `setup_approval_matrix.py`
    > Approval Matrix project: [{AM_KEY}]({JIRA_SERVER}/jira/core/projects/{AM_KEY}/board)

    ## ✅ What was created

    **Project:** {AM_NAME} (`{AM_KEY}`)

    **Components:** {', '.join(comp_names)}

    **Roles:** {', '.join(role_names)}

    ### Matrix Tickets

    | Ticket | Summary | Assignee |
    | ------ | ------- | -------- |
    """)

    for e in entries:
        key = e.get('issue_key', '—')
        email = e.get('email') or '_unassigned_'
        doc += f"| {key} | {e['summary']} | {email} |\n"

    doc += dedent(f"""

    ---

    ## 🔌 How to wire this into your automation

    ### Step A: Open the automation rule

    1. Go to **{JIRA_SERVER}/jira/software/c/projects/{DPR_KEY}/settings/automate**
    2. Open the rule: **DPR: Auto-Generate Close-Out Approvals**

    ### Step B: Add "Lookup Issues" before each "Create Issue" step

    For **each** "Create issue" action that creates an approval sub-task, you need
    to insert a **Lookup Issues** step immediately before it.

    #### For the Base Approvals (Steps 2–6 in the current rule)

    Each step creates a sub-task like:
    - "Base Approval: **DPR Owner** for {{{{issue.key}}}}"
    - "Base Approval: **Reliability Engineer** for {{{{issue.key}}}}"
    - etc.

    For each one:

    1. Click **+ Add component** (the "+" icon just above the Create Issue step)
    2. Select **New action** → **Lookup issues**
    3. In the **JQL** box, enter:

       ```
       project = {AM_KEY} AND summary ~ "\\"{{{{issue.components.first.name}}}} - <ROLE>\\""
       ```

       Replace `<ROLE>` with the specific role name. For example, for the
       "DPR Owner" approval step:

       ```
       project = {AM_KEY} AND summary ~ "\\"{{{{issue.components.first.name}}}} - DPR Owner\\""
       ```

    4. Click **Save**

    #### For the Conditional Approval (Supplier — Step 8)

    Same process, but the JQL combines Component + DPR Type:

    ```
    project = {AM_KEY} AND summary ~ "\\"{{{{issue.components.first.name}}}} - {{{{issue.DPR Type.value}}}}\\""
    ```

    ### Step C: Set dynamic Assignee on each "Create Issue" step

    After adding the Lookup step, click the **Create issue** step (immediately
    after the Lookup) and use **one** of these two methods:

    #### Method 1: Smart Value icon (recommended)

    1. Click **Choose fields to set…** → select **Assignee** (if not already shown)
    2. Look for the **`{{}}`** (curly braces) icon to the **right** of the Assignee
       field (next to the `···` menu) — this opens the smart-value editor
    3. Click **`{{}}`** and type exactly:

       ```
       {{{{lookupIssues.first.assignee.accountId}}}}
       ```

    4. Click **Save**

    > **Note:** The `{{}}` icon appears on most fields and lets you inject
    > smart values (dynamic variables) directly into that field.

    #### Method 2: Additional Fields JSON (alternative)

    If the `{{}}` icon is not available on the Assignee field, you can set
    the assignee via the **Additional fields** JSON textarea at the bottom
    of the Create Issue form instead:

    1. Scroll down to the **Additional fields** section (or click
       "You may specify additional field values to be set using a JSON")
    2. Add or merge the following into the existing JSON:

       ```json
       {{{{
         "fields": {{{{
           "assignee": {{{{
             "accountId": "{{{{lookupIssues.first.assignee.accountId}}}}"
           }}}}
         }}}}
       }}}}
       ```

       If there is already an `"fields"` object (e.g. with `"environment"`),
       just add the `"assignee"` key inside it:

       ```json
       {{{{
         "fields": {{{{
           "environment": "Thanks for raising {{{{issue.key}}}}.",
           "assignee": {{{{
             "accountId": "{{{{lookupIssues.first.assignee.accountId}}}}"
           }}}}
         }}}}
       }}}}
       ```

       > ⚠️ **Important:** If you see `"Custom Field Name": {{{{"value": "red"}}}}` in the
       > existing JSON, **remove it** — that is a placeholder that doesn't correspond
       > to a real field and will cause a validation error.

    3. Click **Save**

    Repeat for every Create Issue step.

    ### Step D: Publish

    Click the **Update** button (top-right) to publish the rule changes.

    ---

    ## 📋 Complete JQL Reference

    | Approval Step | Lookup JQL |
    | ------------- | ---------- |
    """)

    for role in BASE_ROLES:
        jql = (f'project = {AM_KEY} AND summary ~ '
               f'"\\"{{{{issue.components.first.name}}}} - {role}\\""')
        doc += f"| Base: {role} | `{jql}` |\n"

    for dpr_type, role_label in CONDITIONAL_ROLES.items():
        jql = (f'project = {AM_KEY} AND summary ~ '
               f'"\\"{{{{issue.components.first.name}}}} - '
               f'{{{{issue.DPR Type.value}}}}\\""')
        doc += f"| Conditional: {role_label} (DPR Type = {dpr_type}) | `{jql}` |\n"

    doc += dedent(f"""

    ---

    ## 💡 Maintenance

    **To change an approver:** go to the [{AM_KEY} project board]({JIRA_SERVER}/jira/core/projects/{AM_KEY}/board),
    find the ticket (e.g., "Nexus - Supplier"), and change the **Assignee**.
    No automation changes needed!

    **To add a new component:** create new tickets in {AM_KEY} following the
    naming pattern `[Component] - [Role]` for every role.

    **To add a new role:** create tickets for all components and add a new
    Lookup Issues + Create Issue pair in the automation rule.
    """)

    return doc


# =====================================================================
#  MAIN
# =====================================================================

def main():
    print("=" * 60)
    print("  Approval Matrix Setup")
    print("=" * 60)

    # Validate connection
    print(f"\n  → Connecting to {JIRA_SERVER}…")
    try:
        me = _api3_get('myself')
        print(f"  ✓ Connected as: {me.get('displayName', me.get('emailAddress', '?'))}")
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        sys.exit(1)

    # Step 1: Create project
    print(f"\n{'─' * 60}")
    print("  STEP 1: Create Approval Matrix project")
    print(f"{'─' * 60}")
    create_am_project()

    # Step 2: Discover components & build matrix
    print(f"\n{'─' * 60}")
    print("  STEP 2: Discover components & build matrix")
    print(f"{'─' * 60}")
    entries = discover_matrix()

    # Step 3: Create matrix tickets
    print(f"\n{'─' * 60}")
    print("  STEP 3: Create matrix tickets")
    print(f"{'─' * 60}")
    created = create_matrix_tickets(entries)

    # Step 4: Generate instructions
    print(f"\n{'─' * 60}")
    print("  STEP 4: Generate automation wiring guide")
    print(f"{'─' * 60}")
    instructions = generate_instructions(created)

    out_path = Path(__file__).with_name('approval_matrix_guide.md')
    out_path.write_text(instructions, encoding='utf-8')
    print(f"  ✓ Guide written to: {out_path}")

    # Also dump the matrix as JSON for reference
    json_path = Path(__file__).with_name('approval_matrix.json')
    json_data = []
    for e in created:
        json_data.append({
            'issue_key': e.get('issue_key', ''),
            'summary':   e['summary'],
            'component': e['component'],
            'role':      e['role'],
            'assignee':  e.get('email') or None,
        })
    json_path.write_text(json.dumps(json_data, indent=2), encoding='utf-8')
    print(f"  ✓ Matrix JSON written to: {json_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print("  ✅ DONE!")
    print(f"{'=' * 60}")
    print(f"""
  Project:      {JIRA_SERVER}/jira/core/projects/{AM_KEY}/board
  Tickets:      {len(created)} matrix entries
  Guide:        {out_path.name}

  Next steps:
    1. Open {out_path.name} for the automation wiring instructions
    2. Assign unassigned tickets in the AM project to real people
    3. Follow the guide to add Lookup Issues to the automation rule
    """)


if __name__ == '__main__':
    main()





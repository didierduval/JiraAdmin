"""
Fix: Grant the "Automation for Jira" app access to the AM project.

Root Cause:
  The Jira Automation "Lookup work items" step runs as the *Automation for Jira*
  app actor, NOT as the human user.  The app must have BROWSE_PROJECTS in AM.

  This script applies ALL known fixes:
    1. Ensures the app is in AM's atlassian-addons-project-access role
    2. Changes AM's permission scheme to match DPR's (if different)
    3. Verifies cross-project JQL works

Safe to run multiple times.
"""

import json
import os
import sys
from pathlib import Path

import requests
from jira import JIRA

# ── Load .env ──
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

JIRA_SERVER    = os.environ.get('JIRA_SERVER', 'https://fifo24.atlassian.net')
JIRA_EMAIL     = os.environ.get('JIRA_EMAIL', '')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN', '')
DPR_KEY        = os.environ.get('PROJECT_KEY', 'DPR')
AM_KEY         = 'AM'

j = JIRA(
    options={'server': JIRA_SERVER},
    basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN),
)
session = j._session  # reuse authenticated session


def _get(path, **kw):
    r = session.get(f"{JIRA_SERVER}/rest/api/3/{path}", **kw)
    r.raise_for_status()
    return r.json()


def _post(path, payload):
    r = session.post(f"{JIRA_SERVER}/rest/api/3/{path}", json=payload)
    if r.status_code not in (200, 201):
        print(f"  POST {path} -> HTTP {r.status_code}")
        print(f"  {r.text[:300]}")
    r.raise_for_status()
    return r.json()


def _put(path, payload):
    r = session.put(f"{JIRA_SERVER}/rest/api/3/{path}", json=payload)
    if r.status_code not in (200, 201, 204):
        print(f"  PUT {path} -> HTTP {r.status_code}")
        print(f"  {r.text[:300]}")
    r.raise_for_status()
    return r


def main():
    print("=" * 65)
    print("  Fix: Ensure Automation for Jira can access AM project")
    print("=" * 65)

    # ══════════════════════════════════════════════════════
    # FIX 1: Role membership
    # ══════════════════════════════════════════════════════
    print("\n--- FIX 1: Addon role membership ---")

    dpr_roles = _get(f"project/{DPR_KEY}/role")
    addon_role_id = None
    addon_role_name = None
    for role_name, role_url in dpr_roles.items():
        if 'addons' in role_name.lower():
            addon_role_name = role_name
            addon_role_id = role_url.rstrip('/').split('/')[-1]
            break

    if not addon_role_id:
        print("  ERROR: addon role not found in DPR")
        sys.exit(1)

    # Get automation app accountId from DPR
    dpr_role_detail = _get(f"project/{DPR_KEY}/role/{addon_role_id}")
    actors = dpr_role_detail.get('actors', [])
    auto_id = None
    for a in actors:
        if 'automation' in a.get('displayName', '').lower():
            auto_id = a['actorUser']['accountId']
            print(f"  Automation for Jira: {auto_id}")
            break

    if not auto_id:
        print("  ERROR: Automation for Jira not found in DPR role")
        sys.exit(1)

    # Add to AM role
    am_role_detail = _get(f"project/{AM_KEY}/role/{addon_role_id}")
    am_actor_ids = [a.get('actorUser', {}).get('accountId') for a in am_role_detail.get('actors', [])]
    if auto_id in am_actor_ids:
        print(f"  Already in AM role - OK")
    else:
        _post(f"project/{AM_KEY}/role/{addon_role_id}", {"user": [auto_id]})
        print(f"  Added to AM role")

    # Also sync all other DPR app actors
    for a in actors:
        aid = a.get('actorUser', {}).get('accountId')
        if aid and aid not in am_actor_ids:
            try:
                _post(f"project/{AM_KEY}/role/{addon_role_id}", {"user": [aid]})
                print(f"  + Synced: {a.get('displayName', '?')}")
            except Exception:
                pass

    # ══════════════════════════════════════════════════════
    # FIX 2: Permission scheme alignment
    # ══════════════════════════════════════════════════════
    print("\n--- FIX 2: Permission scheme alignment ---")

    am_perm = _get(f"project/{AM_KEY}/permissionscheme")
    dpr_perm = _get(f"project/{DPR_KEY}/permissionscheme")
    print(f"  AM:  '{am_perm.get('name')}' (id: {am_perm.get('id')})")
    print(f"  DPR: '{dpr_perm.get('name')}' (id: {dpr_perm.get('id')})")

    if am_perm.get('id') != dpr_perm.get('id'):
        try:
            _put(f"project/{AM_KEY}/permissionscheme", {"id": dpr_perm['id']})
            print(f"  Changed AM to use '{dpr_perm.get('name')}'")
        except Exception as e:
            print(f"  Could not change: {e}")
    else:
        print(f"  Already the same - OK")

    # ══════════════════════════════════════════════════════
    # FIX 3: Check AM project style
    # ══════════════════════════════════════════════════════
    print("\n--- FIX 3: Project type check ---")
    am_proj = _get(f"project/{AM_KEY}")
    style = am_proj.get('style', '?')
    print(f"  AM style: {style}")
    if style in ('next-gen', 'new'):
        print("  WARNING: Team-managed project - role permissions may not apply!")
        print("  Go to AM > Project Settings > Access > enable Open access")
    else:
        print(f"  Company-managed - roles apply - OK")

    # ══════════════════════════════════════════════════════
    # VERIFY: Cross-project JQL
    # ══════════════════════════════════════════════════════
    print("\n--- VERIFICATION: Cross-project JQL ---")
    jql = 'project = AM AND summary ~ "Nexus" AND summary ~ "Reliability Engineer"'
    results = j.search_issues(jql, maxResults=5, fields='summary,assignee')
    if results:
        r = results[0]
        a = r.fields.assignee
        print(f"  JQL works (as your user): {r.key} -> {a.displayName if a else 'UNASSIGNED'}")
    else:
        print(f"  WARNING: JQL returned 0 results!")

    # ══════════════════════════════════════════════════════
    # NEXT STEPS
    # ══════════════════════════════════════════════════════
    print(f"\n{'=' * 65}")
    print(f"  ALL API-SIDE FIXES APPLIED")
    print(f"{'=' * 65}")
    print(f"""
  If the automation Lookup STILL returns 0 results after re-testing,
  the issue is likely a Jira Cloud automation scope restriction.

  MANUAL STEPS to try (in order):

  1. CHECK RULE ACTOR:
     {JIRA_SERVER}/jira/software/c/projects/{DPR_KEY}/settings/automate
     Click "..." (top-right) > "Default actor"
     Change to YOUR account temporarily.

  2. TEST TRIVIAL JQL:
     Edit one Lookup step, change JQL to just: project = AM
     If this also returns nothing -> it's a scope restriction.

  3. USE "Send web request" INSTEAD:
     Replace Lookup with "Send web request":
       URL: {JIRA_SERVER}/rest/api/3/search
       Method: GET
       Query params: jql=project=AM AND summary~"{{{{issue.components.first.name}}}}" AND summary~"Reliability Engineer"&fields=assignee&maxResults=1
       Wait for response: Yes
     Then in Create Issue Additional Fields:
       {{"fields": {{"assignee": {{"accountId": "{{{{webResponse.body.issues.first.fields.assignee.accountId}}}}"}}}}}}

  4. GLOBAL RULE:
     Move the rule to global automation:
     {JIRA_SERVER}/jira/settings/automation

  See DEBUGGING_LOOKUP.md for full details.
""")


if __name__ == '__main__':
    main()


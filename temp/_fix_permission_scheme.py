"""
Fix the REAL root cause: AM project uses a different permission scheme
that doesn't grant BROWSE_PROJECTS to the Automation for Jira app.

DPR uses 'Default software scheme' (id 10035)
AM  uses 'Default Permission Scheme' (id 0)

This script either:
  A) Changes AM's permission scheme to match DPR's, OR
  B) Adds BROWSE_PROJECTS grant for the addon role to AM's scheme
"""

import json
import os
import sys
from pathlib import Path

import requests
from jira import JIRA

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

session = requests.Session()
session.auth = (JIRA_EMAIL, JIRA_API_TOKEN)
session.headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})
BASE = f"{JIRA_SERVER}/rest/api/3"


def get(path, params=None):
    r = session.get(f"{BASE}/{path}", params=params or {})
    r.raise_for_status()
    return r.json()


def put(path, payload):
    r = session.put(f"{BASE}/{path}", json=payload)
    if r.status_code not in (200, 201, 204):
        print(f"  PUT {path} -> HTTP {r.status_code}")
        print(f"  {r.text[:500]}")
    r.raise_for_status()
    return r


def main():
    print("=" * 65)
    print("  Fix: AM Permission Scheme for Automation Access")
    print("=" * 65)

    # ── 1. Get both permission schemes ──
    print("\n[1] Current permission schemes:")
    am_perm = get(f"project/{AM_KEY}/permissionscheme")
    dpr_perm = get(f"project/{DPR_KEY}/permissionscheme")

    am_scheme_id = am_perm.get('id')
    am_scheme_name = am_perm.get('name')
    dpr_scheme_id = dpr_perm.get('id')
    dpr_scheme_name = dpr_perm.get('name')

    print(f"    AM  -> '{am_scheme_name}' (id: {am_scheme_id})")
    print(f"    DPR -> '{dpr_scheme_name}' (id: {dpr_scheme_id})")

    if am_scheme_id == dpr_scheme_id:
        print("\n    Both projects already use the same scheme!")
        print("    The permission scheme is NOT the problem.")
        print("    Try changing the automation rule actor instead.")
        return

    # ── 2. Examine AM's scheme BROWSE_PROJECTS grants ──
    print(f"\n[2] BROWSE_PROJECTS grants in AM scheme (id {am_scheme_id}):")
    try:
        scheme_detail = get(f"permissionscheme/{am_scheme_id}", params={'expand': 'permissions'})
        perms = scheme_detail.get('permissions', [])
        browse_grants = [p for p in perms if p.get('permission') == 'BROWSE_PROJECTS']
        if browse_grants:
            for g in browse_grants:
                holder = g.get('holder', {})
                htype = holder.get('type', '?')
                param = holder.get('parameter', '')
                role = holder.get('projectRole', {}).get('name', '') if 'projectRole' in holder else ''
                print(f"    - type: {htype}, param: {param} {role}")
        else:
            print(f"    No specific grants found (may use defaults)")
    except Exception as e:
        print(f"    Error fetching details: {e}")

    print(f"\n[2b] BROWSE_PROJECTS grants in DPR scheme (id {dpr_scheme_id}):")
    try:
        scheme_detail = get(f"permissionscheme/{dpr_scheme_id}", params={'expand': 'permissions'})
        perms = scheme_detail.get('permissions', [])
        browse_grants = [p for p in perms if p.get('permission') == 'BROWSE_PROJECTS']
        if browse_grants:
            for g in browse_grants:
                holder = g.get('holder', {})
                htype = holder.get('type', '?')
                param = holder.get('parameter', '')
                role_name = ''
                if htype == 'projectRole':
                    role_name = holder.get('projectRole', {}).get('name', '')
                print(f"    - type: {htype}, param: {param} {role_name}")
        else:
            print(f"    No specific grants found")
    except Exception as e:
        print(f"    Error: {e}")

    # ── 3. Fix: Assign DPR's permission scheme to AM ──
    print(f"\n[3] Changing AM permission scheme to '{dpr_scheme_name}' (id {dpr_scheme_id})...")
    print(f"    This ensures the Automation for Jira app can browse AM")
    print(f"    just like it can browse DPR.")

    try:
        resp = put(
            f"project/{AM_KEY}/permissionscheme",
            {"id": dpr_scheme_id}
        )
        print(f"    SUCCESS! AM now uses '{dpr_scheme_name}'")
    except requests.HTTPError as e:
        print(f"    FAILED: {e}")
        print(f"\n    MANUAL FIX:")
        print(f"    1. Go to: {JIRA_SERVER}/plugins/servlet/project-config/{AM_KEY}/permissions")
        print(f"    2. Click 'Actions' > 'Use a different scheme'")
        print(f"    3. Select '{dpr_scheme_name}'")
        print(f"    4. Click 'Associate'")
        return

    # ── 4. Verify ──
    print(f"\n[4] Verifying...")
    new_perm = get(f"project/{AM_KEY}/permissionscheme")
    print(f"    AM now uses: '{new_perm.get('name')}' (id: {new_perm.get('id')})")

    if new_perm.get('id') == dpr_scheme_id:
        print(f"\n    CONFIRMED! AM and DPR now share the same permission scheme.")
        print(f"\n    Next: Transition a DPR to Close-Out (CO) to test the rule.")
    else:
        print(f"    WARNING: Scheme change may not have taken effect.")

    print(f"\n{'=' * 65}")
    print(f"  DONE")
    print(f"{'=' * 65}")


if __name__ == '__main__':
    main()


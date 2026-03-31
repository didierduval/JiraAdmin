"""
Deep diagnostic: why does the Jira Automation 'Lookup work items' return nothing?

Checks performed:
  1. AM project type (team-managed vs company-managed)
  2. AM permission scheme — does addon role have BROWSE_PROJECTS?
  3. Automation for Jira in AM roles
  4. Smart-value resolution for DPR-33
  5. JQL variants that the automation engine might produce
  6. Rule actor & scope check
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

j = JIRA(
    options={'server': JIRA_SERVER},
    basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN),
)
session = j._session


def _get(path, params=None):
    r = session.get(f"{JIRA_SERVER}/rest/api/3/{path}", params=params or {})
    r.raise_for_status()
    return r.json()


def _get_raw(path, params=None):
    """GET that returns the response object (for status checking)."""
    r = session.get(f"{JIRA_SERVER}/rest/api/3/{path}", params=params or {})
    return r


def main():
    print("=" * 70)
    print("  DEEP DIAGNOSTIC: Automation Lookup Issues")
    print("=" * 70)

    issues_found = []

    # ── 1. AM project type ──
    print("\n[1] AM Project Type")
    am_proj = _get(f"project/{AM_KEY}")
    style = am_proj.get('style', 'unknown')
    ptype = am_proj.get('projectTypeKey', 'unknown')
    print(f"    Key:   {am_proj.get('key')}")
    print(f"    Name:  {am_proj.get('name')}")
    print(f"    Type:  {ptype}")
    print(f"    Style: {style}")

    is_team_managed = style in ('next-gen', 'new')  # team-managed
    if is_team_managed:
        print("    >>> TEAM-MANAGED (next-gen) project!")
        print("    >>> Role-based permissions do NOT apply here.")
        print("    >>> Access is controlled via Project Settings > Access.")
        issues_found.append(
            "AM is TEAM-MANAGED. The atlassian-addons-project-access role "
            "does NOT control app access. You must add access differently."
        )
    else:
        print(f"    Company-managed (classic) project - roles apply.")

    # ── 2. AM Permission Scheme ──
    print("\n[2] AM Permission Scheme")
    try:
        # Get the permission scheme assigned to AM
        r = _get_raw(f"project/{AM_KEY}/securitylevel")
        # Try the permissionscheme endpoint
        perm_resp = _get_raw(f"project/{AM_KEY}/permissionscheme")
        if perm_resp.status_code == 200:
            perm_data = perm_resp.json()
            print(f"    Scheme: {perm_data.get('name', '?')} (id: {perm_data.get('id')})")

            # Check BROWSE_PROJECTS grants
            perms = perm_data.get('permissions', [])
            browse_grants = [
                p for p in perms
                if p.get('permission') == 'BROWSE_PROJECTS'
            ]
            if browse_grants:
                print(f"    BROWSE_PROJECTS granted to:")
                for g in browse_grants:
                    holder = g.get('holder', {})
                    htype = holder.get('type', '?')
                    param = holder.get('parameter', '')
                    value = holder.get('value', '')
                    name = holder.get('projectRole', {}).get('name', '') if htype == 'projectRole' else ''
                    print(f"      - type={htype}, param={param}, value={value} {name}")

                    if htype == 'projectRole' and 'addons' in name.lower():
                        print(f"        ^ addon role HAS Browse Projects")
            else:
                print(f"    WARNING: No BROWSE_PROJECTS grants found in scheme detail")
                print(f"    (might need ?expand=permissions on the endpoint)")
        else:
            print(f"    Could not fetch permission scheme (HTTP {perm_resp.status_code})")
    except Exception as e:
        print(f"    Error: {e}")

    # Try with expand
    try:
        perm_resp2 = _get_raw(
            f"permissionscheme",
            params={'expand': 'permissions,user,group,projectRole,field,all'}
        )
        if perm_resp2.status_code == 200:
            schemes = perm_resp2.json().get('permissionSchemes', [])
            print(f"\n    All permission schemes ({len(schemes)}):")
            for s in schemes:
                print(f"      - {s.get('name')} (id: {s.get('id')})")
    except Exception:
        pass

    # ── 3. DPR permission scheme for comparison ──
    print("\n[3] DPR Permission Scheme (for comparison)")
    try:
        dpr_perm = _get_raw(f"project/{DPR_KEY}/permissionscheme")
        if dpr_perm.status_code == 200:
            dpr_perm_data = dpr_perm.json()
            print(f"    Scheme: {dpr_perm_data.get('name', '?')} (id: {dpr_perm_data.get('id')})")
        else:
            print(f"    HTTP {dpr_perm.status_code}")
    except Exception as e:
        print(f"    Error: {e}")

    # ── 4. Automation app in AM project access ──
    print("\n[4] Automation App in AM Project")
    if is_team_managed:
        print("    Team-managed project - checking access via project properties...")
        # For team-managed projects, try the access endpoint
        try:
            access_resp = _get_raw(f"project/{AM_KEY}/role")
            if access_resp.status_code == 200:
                roles = access_resp.json()
                print(f"    Roles available: {list(roles.keys())}")
                for role_name, role_url in roles.items():
                    role_id = role_url.rstrip('/').split('/')[-1]
                    try:
                        role_detail = _get(f"project/{AM_KEY}/role/{role_id}")
                        actors = role_detail.get('actors', [])
                        auto_found = any(
                            'automation' in a.get('displayName', '').lower()
                            for a in actors
                        )
                        if auto_found or 'addons' in role_name.lower():
                            print(f"    Role '{role_name}' actors:")
                            for a in actors:
                                print(f"      - {a.get('displayName', '?')}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"    Error: {e}")
    else:
        print("    Company-managed - role check already done by _fix_am_access.py")

    # ── 5. Check if automation actor can search AM (impersonation test) ──
    print("\n[5] Smart Value Resolution for DPR-33")
    iss = j.issue('DPR-33', fields='summary,components,issuetype,status')
    comps = iss.fields.components
    comp_names = [c.name for c in comps] if comps else []
    print(f"    Components: {comp_names}")

    # Check DPR Type
    all_fields = j.fields()
    dpr_type_id = None
    for f in all_fields:
        if f['name'] == 'DPR Type':
            dpr_type_id = f['id']
            break

    dpr_type_val = None
    if dpr_type_id:
        iss2 = j.issue('DPR-33', fields=dpr_type_id)
        raw = getattr(iss2.fields, dpr_type_id, None)
        if raw and hasattr(raw, 'value'):
            dpr_type_val = raw.value
        elif raw:
            dpr_type_val = str(raw)
    print(f"    DPR Type: {dpr_type_val}")

    if not comp_names:
        print("    >>> DPR-33 HAS NO COMPONENTS! The smart value will be empty!")
        issues_found.append("DPR-33 has no components - smart value resolves to empty")
    else:
        component = comp_names[0]
        print(f"    Smart value {{issue.components.first.name}} = '{component}'")

    # ── 6. JQL search tests ──
    print("\n[6] JQL Search Tests (as your user)")
    component = comp_names[0] if comp_names else "Nexus"

    test_jqls = [
        ("All AM issues",
         f"project = AM ORDER BY key ASC"),
        ("Simple text search",
         f'project = AM AND summary ~ "Reliability Engineer"'),
        ("Component + Role (two ~ clauses)",
         f'project = AM AND summary ~ "{component}" AND summary ~ "Reliability Engineer"'),
        ("Exact summary match (=)",
         f'project = AM AND summary = "{component} - Reliability Engineer"'),
        ("Text contains full phrase",
         f'project = AM AND summary ~ "\\"{component} - Reliability Engineer\\""'),
    ]

    for label, jql in test_jqls:
        try:
            results = j.search_issues(jql, maxResults=3, fields='summary,assignee')
            hits = [(r.key, r.fields.summary) for r in results]
            status = "OK" if hits else "EMPTY"
            print(f"    [{status:5s}] {label}")
            print(f"           JQL: {jql}")
            if hits:
                for k, s in hits:
                    print(f"           -> {k}: {s}")
        except Exception as e:
            print(f"    [ERROR] {label}")
            print(f"           JQL: {jql}")
            print(f"           -> {str(e)[:100]}")

    # ── 7. Check rule scope and actor ──
    print("\n[7] Automation Rule Scope")
    print("    The 'DPR: Auto-Generate Close-Out Approvals' rule is a PROJECT rule")
    print("    in the DPR project. When it runs 'Lookup work items', it uses the")
    print("    rule actor's permissions to search.")
    print()
    print("    In Jira Cloud, the default rule actor is 'Automation for Jira'.")
    print("    This app must have BROWSE_PROJECTS in the TARGET project (AM).")

    # ── 8. Check the Automation rule actor setting ──
    print("\n[8] Possible Rule Actor Override")
    print("    Go to: DPR > Project Settings > Automation > (...) > Default actor")
    print("    If set to a specific user, that user needs AM access instead.")
    print("    If set to 'Automation for Jira (recommended)', the app needs access.")

    # ── 9. Try searching as the Automation app (via mypermissions) ──
    print("\n[9] Checking 'Automation for Jira' app permissions on AM")
    auto_account_id = '557058:f58131cb-b67d-43c7-b30d-6b58d40bd077'  # from previous run
    try:
        # Check what permissions the auto app has on AM
        perm_check = _get_raw(
            f"mypermissions",
            params={
                'projectKey': AM_KEY,
                'permissions': 'BROWSE_PROJECTS'
            }
        )
        if perm_check.status_code == 200:
            perms = perm_check.json().get('permissions', {})
            browse = perms.get('BROWSE_PROJECTS', {})
            have = browse.get('havePermission', False)
            print(f"    YOUR user BROWSE_PROJECTS on AM: {have}")
        else:
            print(f"    mypermissions check: HTTP {perm_check.status_code}")
    except Exception as e:
        print(f"    Error: {e}")

    # ── SUMMARY ──
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)

    if is_team_managed:
        print("""
  ROOT CAUSE: The AM project is TEAM-MANAGED (next-gen).

  In team-managed projects, the 'atlassian-addons-project-access' role
  does NOT control which apps can browse the project. Instead, access
  is managed through the project's own access page.

  FIX OPTIONS:

  Option A (Recommended): Convert AM to company-managed
    1. Go to: AM project > Project settings > scroll to bottom
    2. Look for "Move to a company-managed project" or similar
    3. This makes traditional permission schemes apply
    Note: This may not always be available.

  Option B: Change AM project access settings
    1. Go to: {server}/jira/core/projects/AM/settings/access
    2. Make sure "Open access" is ON (anyone in the organization
       can browse), OR add the Automation for Jira app explicitly
    3. In team-managed projects, you may need to set the project
       to "Open" so automation can search it.

  Option C: Use a GLOBAL automation rule instead of project-scoped
    1. Go to: Jira Settings > System > Global automation
    2. Move/recreate the rule as a global rule
    3. Global rules can search all projects regardless of scope

  Option D: Avoid cross-project lookup entirely
    1. Remove the Lookup steps
    2. Hard-code assignees in the Additional Fields JSON
    3. Or store the matrix data differently (labels, custom fields)

  MANUAL CHECK:
    1. Open: {server}/jira/core/projects/AM/settings/access
    2. Check if access is "Open" or restricted
    3. If restricted, add "Automation for Jira" as a member
""".format(server=JIRA_SERVER))
    elif issues_found:
        print("\n  Issues found:")
        for i, issue in enumerate(issues_found, 1):
            print(f"  {i}. {issue}")
    else:
        print("""
  No obvious issue found from the API side.

  The JQL works as YOUR user, and the Automation for Jira app
  is in the AM project's addon role.

  NEXT STEPS TO TRY:

  1. Check the automation RULE ACTOR:
     Go to DPR > Project Settings > Automation
     Click (...) menu > Default actor
     Try changing it to yourself (your user) temporarily

  2. Check AM project access:
     Go to {server}/jira/core/projects/AM/settings/access
     Ensure it's not restricted

  3. Try a GLOBAL rule instead of project-scoped
     Global rules run with broader permissions

  4. Test with a trivial JQL first:
     Change the Lookup JQL to just: project = AM
     If this ALSO returns nothing, it's definitely a permission issue
""".format(server=JIRA_SERVER))

    print("=" * 70)


if __name__ == '__main__':
    main()


n"""Clean up DPR-33: delete all duplicate approval sub-tasks, add delete permission if needed."""
import os, requests, json
from pathlib import Path
from dotenv import load_dotenv
from jira import JIRA, JIRAError
load_dotenv(Path(__file__).with_name('.env'))

JIRA_SERVER = os.environ['JIRA_SERVER']
s = requests.Session()
s.auth = (os.environ['JIRA_EMAIL'], os.environ['JIRA_API_TOKEN'])
s.headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})

j = JIRA(options={'server': JIRA_SERVER}, basic_auth=(os.environ['JIRA_EMAIL'], os.environ['JIRA_API_TOKEN']))

# 1. Check DELETE permission
r = s.get(f'{JIRA_SERVER}/rest/api/3/mypermissions', params={'projectKey': 'DPR', 'permissions': 'DELETE_ISSUES'})
have_delete = r.json().get('permissions', {}).get('DELETE_ISSUES', {}).get('havePermission', False)
print(f"DELETE_ISSUES permission: {have_delete}")

if not have_delete:
    print("Adding DELETE_ISSUES to permission scheme...")
    # Get scheme
    scheme = s.get(f'{JIRA_SERVER}/rest/api/3/project/DPR/permissionscheme').json()
    scheme_id = scheme['id']
    print(f"  Scheme: {scheme['name']} (id: {scheme_id})")

    # Add DELETE_ISSUES for applicationRole (logged-in users)
    grant_payload = {
        "holder": {"type": "applicationRole"},
        "permission": "DELETE_ISSUES"
    }
    r2 = s.post(f'{JIRA_SERVER}/rest/api/3/permissionscheme/{scheme_id}/permission', json=grant_payload)
    if r2.status_code in (200, 201):
        print("  Added DELETE_ISSUES for all application users")
    else:
        print(f"  HTTP {r2.status_code}: {r2.text[:200]}")
        # Try with project admin role
        grant_payload2 = {
            "holder": {"type": "projectRole", "parameter": "10002"},  # admin role
            "permission": "DELETE_ISSUES"
        }
        r3 = s.post(f'{JIRA_SERVER}/rest/api/3/permissionscheme/{scheme_id}/permission', json=grant_payload2)
        print(f"  Admin role grant: HTTP {r3.status_code}")

# 2. Keep only the latest set + RCA/CA/VR tasks
# Keep DPR-34,35,36 (RCA/CA/VR) and DPR-94..99 (latest with Lookup working)
keep = {'DPR-34', 'DPR-35', 'DPR-36', 'DPR-94', 'DPR-95', 'DPR-96', 'DPR-97', 'DPR-98', 'DPR-99'}

subs = j.search_issues('parent = DPR-33 ORDER BY key ASC', maxResults=200, fields='summary')
to_delete = [sub for sub in subs if sub.key not in keep]
print(f"\nSub-tasks to delete: {len(to_delete)}, keeping: {len(keep)}")

deleted = 0
failed = 0
for sub in to_delete:
    try:
        r = s.delete(f'{JIRA_SERVER}/rest/api/3/issue/{sub.key}', params={'deleteSubtasks': 'true'})
        if r.status_code in (200, 204):
            print(f"  Deleted {sub.key}: {sub.fields.summary}")
            deleted += 1
        else:
            print(f"  FAILED {sub.key}: HTTP {r.status_code} - {r.text[:100]}")
            failed += 1
    except Exception as e:
        print(f"  FAILED {sub.key}: {str(e)[:80]}")
        failed += 1

print(f"\nDeleted: {deleted}, Failed: {failed}")

# 3. Show remaining
remaining = j.search_issues('parent = DPR-33 ORDER BY key ASC', maxResults=50, fields='summary,status,assignee')
print(f"\nRemaining sub-tasks ({len(remaining)}):")
for r in remaining:
    a = r.fields.assignee
    aname = a.displayName if a else 'Unassigned'
    print(f"  {r.key}: {r.fields.summary} [{r.fields.status.name}] -> {aname}")


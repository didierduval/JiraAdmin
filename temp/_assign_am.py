"""Assign all unassigned AM tickets to Didier Duval (current user)."""
from jira import JIRA
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

j = JIRA(
    options={'server': os.environ['JIRA_SERVER']},
    basic_auth=(os.environ['JIRA_EMAIL'], os.environ['JIRA_API_TOKEN']),
)

# Get current user's accountId
me = j._session.get(f"{os.environ['JIRA_SERVER']}/rest/api/3/myself").json()
my_id = me['accountId']
my_name = me.get('displayName', '?')
print(f"Assigning all unassigned AM tickets to: {my_name} ({my_id})\n")

all_am = j.search_issues('project = AM ORDER BY key ASC', maxResults=50, fields='summary,assignee')
assigned = 0
for iss in all_am:
    a = iss.fields.assignee
    if not a:
        try:
            j.assign_issue(iss.key, my_id)
            print(f"  ✅ {iss.key}: {iss.fields.summary} → {my_name}")
            assigned += 1
        except Exception as e:
            print(f"  ❌ {iss.key}: {str(e)[:80]}")
    else:
        print(f"  ── {iss.key}: {iss.fields.summary} (already: {a.displayName})")

print(f"\n✅ Assigned {assigned} tickets. All AM tickets now have an assignee.")


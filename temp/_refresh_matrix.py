"""Refresh approval_matrix.json from live Jira AM project."""
from jira import JIRA
import os, json
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

issues = j.search_issues(
    'project = AM ORDER BY key ASC', maxResults=100,
    fields='summary,assignee,status',
)

data = []
for iss in issues:
    a = iss.fields.assignee
    name = a.displayName if a else 'UNASSIGNED'
    acct = a.accountId if a else None
    data.append({
        'issue_key': iss.key,
        'summary':   iss.fields.summary,
        'status':    iss.fields.status.name,
        'assignee':  name if a else None,
        'accountId': acct,
    })
    print(f"  {iss.key:8s} | {iss.fields.summary:40s} | {name}")

out = Path(__file__).with_name('approval_matrix.json')
out.write_text(json.dumps(data, indent=2), encoding='utf-8')
print(f"\n✓ {len(data)} tickets written to {out.name}")


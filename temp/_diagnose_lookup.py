"""Diagnose why the Lookup JQL fails for DPR-33."""
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

# 1. Check DPR-33
iss = j.issue('DPR-33', fields='summary,components,issuetype,status')
comps = iss.fields.components
comp_names = [c.name for c in comps] if comps else []
print(f"DPR-33: {iss.fields.summary}")
print(f"  Type:       {iss.fields.issuetype.name}")
print(f"  Status:     {iss.fields.status.name}")
print(f"  Components: {comp_names if comp_names else 'NONE!'}")

# 2. Test JQL variations
test_jqls = []
if comp_names:
    cn = comp_names[0]
    test_jqls.append(('Exact phrase (escaped quotes)', f'project = AM AND summary ~ "\\"{cn} - Reliability Engineer\\""'))
    test_jqls.append(('Simple phrase (no inner quotes)', f'project = AM AND summary ~ "{cn} - Reliability Engineer"'))
    test_jqls.append(('Tilde without quotes', f'project = AM AND summary ~ "{cn} Reliability Engineer"'))

test_jqls.append(('All AM issues', 'project = AM ORDER BY key ASC'))
test_jqls.append(('Simple text search', 'project = AM AND summary ~ "Reliability Engineer"'))

for label, jql in test_jqls:
    try:
        results = j.search_issues(jql, maxResults=5, fields='summary')
        hits = [(r.key, r.fields.summary) for r in results]
        print(f"\n  JQL [{label}]:")
        print(f"    {jql}")
        print(f"    → {len(hits)} result(s): {hits}")
    except Exception as e:
        print(f"\n  JQL [{label}]: ERROR")
        print(f"    {jql}")
        print(f"    → {str(e)[:120]}")


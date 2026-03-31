"""
Diagnose ALL Lookup issues for the DPR: Auto-Generate Close-Out Approvals rule.
Tests cross-project search, smart value resolution, and every JQL variant.
"""
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

print("=" * 60)
print("  DIAGNOSTIC: Approval Matrix Lookup")
print("=" * 60)

# 1. Verify DPR-33 fields
print("\n── DPR-33 Fields ──")
iss = j.issue('DPR-33', fields='summary,components,issuetype,status,customfield_10118')
comps = iss.fields.components
comp_names = [c.name for c in comps] if comps else []
print(f"  Summary:    {iss.fields.summary}")
print(f"  Components: {comp_names}")

# Find DPR Type field (it's a custom field)
# Try to find it by checking all fields
all_fields = j.fields()
dpr_type_id = None
for f in all_fields:
    if f['name'] == 'DPR Type':
        dpr_type_id = f['id']
        break
print(f"  DPR Type field ID: {dpr_type_id}")

if dpr_type_id:
    iss2 = j.issue('DPR-33', fields=dpr_type_id)
    dpr_type_val = getattr(iss2.fields, dpr_type_id, None)
    if dpr_type_val:
        if hasattr(dpr_type_val, 'value'):
            print(f"  DPR Type value: {dpr_type_val.value}")
        else:
            print(f"  DPR Type raw: {dpr_type_val}")
    else:
        print(f"  DPR Type: None")

# 2. Test cross-project search
print("\n── Cross-project search test ──")
try:
    results = j.search_issues('project = AM', maxResults=3, fields='summary')
    print(f"  project = AM → {len(results)} results (total: {results.total})")
    for r in results:
        print(f"    {r.key}: {r.fields.summary}")
except Exception as e:
    print(f"  ❌ FAILED: {e}")

# 3. Test EXACT JQL for each Lookup step
print("\n── Exact JQL tests (simulating smart values) ──")
component = comp_names[0] if comp_names else "Nexus"

test_cases = [
    ("DPR Owner",            f'project = AM AND summary ~ "{component}" AND summary ~ "DPR Owner"'),
    ("Reliability Engineer",  f'project = AM AND summary ~ "{component}" AND summary ~ "Reliability Engineer"'),
    ("Quality Engineer",      f'project = AM AND summary ~ "{component}" AND summary ~ "Quality Engineer"'),
    ("System Engineer",       f'project = AM AND summary ~ "{component}" AND summary ~ "System Engineer"'),
    ("Project Lead",          f'project = AM AND summary ~ "{component}" AND summary ~ "Project Lead"'),
    ("Supplier",              f'project = AM AND summary ~ "{component}" AND summary ~ "Supplier"'),
]

all_ok = True
for role, jql in test_cases:
    try:
        results = j.search_issues(jql, maxResults=5, fields='summary,assignee')
        if len(results) == 1:
            r = results[0]
            a = r.fields.assignee
            aname = a.displayName if a else "⚠️ UNASSIGNED"
            print(f"  ✅ {role:25s} → {r.key} ({aname})")
        elif len(results) == 0:
            print(f"  ❌ {role:25s} → 0 results!")
            all_ok = False
        else:
            print(f"  ⚠️  {role:25s} → {len(results)} results (ambiguous)")
            all_ok = False
    except Exception as e:
        print(f"  ❌ {role:25s} → ERROR: {str(e)[:80]}")
        all_ok = False

# 4. Test with OLD JQL format (escaped quotes) to confirm it's different
print("\n── Old JQL format test (escaped quotes) ──")
old_jql = f'project = AM AND summary ~ "\\"{component} - Reliability Engineer\\""'
try:
    results = j.search_issues(old_jql, maxResults=5, fields='summary')
    print(f"  Escaped quotes JQL → {len(results)} results")
    for r in results:
        print(f"    {r.key}: {r.fields.summary}")
except Exception as e:
    print(f"  Escaped quotes JQL → ERROR: {str(e)[:80]}")

# 5. Generate copy-paste JQL for each step
print("\n" + "=" * 60)
print("  COPY-PASTE JQL for Jira Automation")
print("=" * 60)
print("""
For each Lookup step, copy the EXACT JQL below into the JQL field.
Do NOT add escaped quotes or extra characters.

── Step: DPR Owner Lookup ──
project = AM AND summary ~ "{{issue.components.first.name}}" AND summary ~ "DPR Owner"

── Step: Reliability Engineer Lookup ──
project = AM AND summary ~ "{{issue.components.first.name}}" AND summary ~ "Reliability Engineer"

── Step: Quality Engineer Lookup ──
project = AM AND summary ~ "{{issue.components.first.name}}" AND summary ~ "Quality Engineer"

── Step: System Engineer Lookup ──
project = AM AND summary ~ "{{issue.components.first.name}}" AND summary ~ "System Engineer"

── Step: Project Lead Lookup ──
project = AM AND summary ~ "{{issue.components.first.name}}" AND summary ~ "Project Lead"

── Step: Supplier Lookup (conditional) ──
project = AM AND summary ~ "{{issue.components.first.name}}" AND summary ~ "Supplier"

NOTE: For the Supplier step, we use the literal word "Supplier" instead of
{{issue.DPR Type.value}} to avoid potential smart-value resolution issues.
You can change it to {{issue.DPR Type.value}} later once the basic flow works.
""")

# 6. Check AM assignee coverage
print("── AM Ticket Assignee Status ──")
all_am = j.search_issues('project = AM ORDER BY key ASC', maxResults=50, fields='summary,assignee')
unassigned = []
for r in all_am:
    a = r.fields.assignee
    if not a:
        unassigned.append(f"{r.key}: {r.fields.summary}")
        
if unassigned:
    print(f"  ⚠️  {len(unassigned)} tickets are UNASSIGNED (will cause errors!):")
    for u in unassigned:
        print(f"    {u}")
else:
    print(f"  ✅ All {len(all_am)} AM tickets have assignees")

print("\n" + "=" * 60)
if all_ok and not unassigned:
    print("  ✅ Everything looks good — apply the JQL and JSON changes")
else:
    if not all_ok:
        print("  ❌ Some JQL tests failed")
    if unassigned:
        print(f"  ❌ {len(unassigned)} AM tickets need assignees")
print("=" * 60)


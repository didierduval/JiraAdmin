"""
Fix the automation rule via the Jira Automation API.
Replaces {{issue.components.first.name}} with {{issue.components.name}}
in all Lookup JQLs, as .first might not work in JQL context.
"""
import json, os, requests
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

JIRA_SERVER = os.environ['JIRA_SERVER']
JIRA_EMAIL  = os.environ['JIRA_EMAIL']
JIRA_TOKEN  = os.environ['JIRA_API_TOKEN']
AUTH = (JIRA_EMAIL, JIRA_TOKEN)

# Get cloud ID
r = requests.get(f"{JIRA_SERVER}/_edge/tenant_info")
cloud_id = r.json().get('cloudId', '')
print(f"Cloud ID: {cloud_id}")

# Load the exported rule
rule_file = Path(__file__).with_name(
    'automation-rule-019d3589-c3eb-7b91-a469-9f349468664e-202603292353.json')
export = json.loads(rule_file.read_text())
rule = export['rules'][0]
rule_id = rule['id']
print(f"Rule: {rule['name']} (ID: {rule_id})")

# Try different smart value variants for components
VARIANTS = [
    '{{issue.components.name}}',           # .name on collection (might work for single)
    '{{triggerIssue.components.first.name}}',  # triggerIssue prefix
    '{{issue.component.name}}',            # singular
]

# First, let's test which smart value syntax Jira Automation supports
# by trying to read the rule via the API
api_bases = [
    f"{JIRA_SERVER}/gateway/api/automation/internal-api/jira/{cloud_id}/pro/rest",
    f"{JIRA_SERVER}/rest/cb-automation/latest/project/rule",
]

print("\n── Testing Automation API access ──")
for base in api_bases:
    try:
        url = f"{base}/{rule_id}"
        r = requests.get(url, auth=AUTH, timeout=10)
        print(f"  GET {url[:80]}... → {r.status_code}")
        if r.status_code == 200:
            print(f"  ✅ API accessible!")
            api_base = base
            break
    except Exception as e:
        print(f"  ❌ {str(e)[:60]}")

# Since we may not have API write access, let's create modified JSONs
# for manual import
print("\n── Creating fixed rule JSON files ──")

for i, variant in enumerate(VARIANTS):
    fixed = json.loads(json.dumps(export))  # deep copy
    rule_copy = fixed['rules'][0]
    
    # Fix all lookup JQLs
    changes = [0]
    def fix_lookups(components):
        for comp in components:
            if comp.get('type') == 'jira.lookup.issues':
                q = comp.get('value', {}).get('query', {})
                old_val = q.get('value', '')
                if '{{issue.components.first.name}}' in old_val:
                    new_val = old_val.replace(
                        '{{issue.components.first.name}}', variant)
                    q['value'] = new_val
                    changes[0] += 1
                    print(f"  Fixed: ...{variant}... AND ...")
            # Recurse into children (for IF blocks)
            for child in comp.get('children', []):
                fix_lookups(child.get('children', []))
    
    fix_lookups(rule_copy.get('components', []))
    
    # Also fix trigger children
    trigger = rule_copy.get('trigger', {})
    for child in trigger.get('children', []):
        fix_lookups(child.get('children', []))
    
    out_name = f'automation-rule-fix-{i+1}.json'
    out_path = Path(__file__).with_name(out_name)
    out_path.write_text(json.dumps(fixed, indent=2), encoding='utf-8')
    print(f"  → {out_name}: {changes[0]} lookups fixed with {variant}")

print(f"""
── What to try ──

Option 1 (recommended): Manually update the 3 Lookup JQLs in Jira:
  
  Change:  {{{{issue.components.first.name}}}}
  To:      {{{{issue.components.name}}}}

  Just remove ".first" from the smart value in each Lookup step.

Option 2: If Option 1 doesn't work, try:
  
  {{{{triggerIssue.components.first.name}}}}

Option 3: If neither works, try:
  
  {{{{issue.component.name}}}}

The JQL for each Lookup should be:
  project = AM AND summary ~ "<SMART_VALUE>" AND summary ~ "<ROLE>"
""")




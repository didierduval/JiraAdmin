"""
Generate a complete fixed automation rule JSON that uses IF/ELSE blocks
for each component with hardcoded JQL (since smart values don't resolve
in Lookup JQL for the components field).

The user can import this JSON via:
  Jira → Project Settings → Automation → "..." menu → Import rules
"""
import json, os, copy
from pathlib import Path
from jira import JIRA

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

JIRA_SERVER = os.environ['JIRA_SERVER']
j = JIRA(options={'server': JIRA_SERVER},
         basic_auth=(os.environ['JIRA_EMAIL'], os.environ['JIRA_API_TOKEN']))

# Discover components from DPR project
DPR_KEY = os.environ.get('PROJECT_KEY', 'DPR')
components = [c.name for c in j.project_components(DPR_KEY)]
print(f"Components: {components}")

# Load the exported rule as base template
rule_file = Path(__file__).with_name(
    'automation-rule-019d3589-c3eb-7b91-a469-9f349468664e-202603292353.json')
export = json.loads(rule_file.read_text())
base_rule = export['rules'][0]

# Get the component field ID for conditions
fields = j.fields()
comp_field_id = None
for f in fields:
    if f['name'] == 'Component/s' or f['id'] == 'components':
        comp_field_id = f['id']
        break
if not comp_field_id:
    comp_field_id = 'components'
print(f"Component field ID: {comp_field_id}")

# ── Helper: create a Lookup action ──
def make_lookup(comp_name, role, sv_id):
    return {
        "id": None,
        "parentId": None,
        "conditionParentId": None,
        "component": "ACTION",
        "type": "jira.lookup.issues",
        "value": {
            "id": sv_id,
            "name": {"type": "FREE", "value": "lookupIssues"},
            "type": "JQL",
            "query": {
                "type": "SMART",
                "value": f'project = AM AND summary ~ "{comp_name}" AND summary ~ "{role}"'
            },
            "lazy": False
        },
        "conditions": [],
        "children": [],
        "schemaVersion": 1,
        "connectionId": None,
        "checksum": None
    }

# ── Helper: create a Create-subtask action with lookup assignee ──
def make_create(summary_template, with_assignee=True):
    ops = [
        {
            "field": {"type": "ID", "value": "summary"},
            "fieldType": "summary",
            "type": "SET",
            "value": summary_template
        },
        {
            "field": {"type": "ID", "value": "description"},
            "fieldType": "description",
            "type": "SET",
            "value": None
        },
        {
            "field": {"type": "ID", "value": "project"},
            "fieldType": "project",
            "type": "SET",
            "value": {"value": "current", "type": "COPY"}
        },
        {
            "field": {"type": "ID", "value": "issuetype"},
            "fieldType": "issuetype",
            "type": "SET",
            "value": {"type": "ID", "value": "10013"}
        },
        {
            "field": {"type": "ID", "value": "parent"},
            "fieldType": "parent",
            "type": "SET",
            "value": {"type": "COPY", "value": "current"}
        },
        {
            "field": {"type": "ID", "value": "labels"},
            "fieldType": "labels",
            "type": "SET",
            "value": [{"type": "FREE", "value": "Approval"}]
        }
    ]
    
    adv = None
    if with_assignee:
        adv = '{"fields":{"assignee":{"accountId":"{{lookupIssues.first.assignee.accountId}}"}}}'
    
    return {
        "id": None,
        "parentId": None,
        "conditionParentId": None,
        "component": "ACTION",
        "type": "jira.issue.create",
        "value": {
            "operations": ops,
            "advancedFields": adv,
            "sendNotifications": False
        },
        "conditions": [],
        "children": [],
        "schemaVersion": 12,
        "connectionId": None,
        "checksum": None
    }

# ── Helper: create a component condition ──
def make_component_condition(comp_name, parent_id):
    return {
        "id": None,
        "parentId": None,
        "conditionParentId": parent_id,
        "component": "CONDITION",
        "type": "jira.issue.condition",
        "value": {
            "selectedField": {"type": "ID", "value": "components"},
            "selectedFieldType": "components",
            "comparison": "EQUALS",
            "compareValue": {
                "type": "NAME",
                "modifier": None,
                "value": comp_name,
                "multiValue": False,
                "source": None
            }
        },
        "conditions": [],
        "children": [],
        "schemaVersion": 3,
        "connectionId": None,
        "checksum": None
    }

# ── Helper: create an IF/ELSE-IF block for a role ──
def make_role_block(role, summary_template, sv_id):
    """Create an IF (Nexus) / ELSE-IF (Spectrum) block for one role."""
    if_blocks = []
    for i, comp in enumerate(components):
        block_type = "jira.condition.if.block" if i == 0 else "jira.condition.else.block"
        
        block = {
            "id": None,
            "parentId": None,
            "conditionParentId": None,
            "component": "CONDITION_BLOCK",
            "type": block_type,
            "value": {"conditionMatchType": "ALL"},
            "conditions": [make_component_condition(comp, None)],
            "children": [
                make_lookup(comp, role, sv_id),
                make_create(summary_template, with_assignee=True),
            ],
            "schemaVersion": 1,
            "connectionId": None,
            "checksum": None
        }
        
        # ELSE-IF blocks also need a condition
        if i > 0:
            block["conditions"] = [make_component_condition(comp, None)]
        
        if_blocks.append(block)
    
    container = {
        "id": None,
        "parentId": None,
        "conditionParentId": None,
        "component": "CONDITION",
        "type": "jira.condition.container.block",
        "value": None,
        "conditions": [],
        "children": if_blocks,
        "schemaVersion": 1,
        "connectionId": None,
        "checksum": None
    }
    return container

# ── Build the complete rule ──
print("\nBuilding fixed rule...")

new_rule = copy.deepcopy(base_rule)

# Keep the trigger as-is
# Rebuild the components list

new_components = []

# 1. DPR Owner - no lookup needed (uses trigger assignee)
dpr_owner_create = copy.deepcopy(base_rule['components'][0])  # existing DPR Owner create
dpr_owner_create['value']['advancedFields'] = None  # clean junk
new_components.append(dpr_owner_create)
print("  1. Create DPR Owner (from trigger assignee)")

# 2-5. Other base roles with IF/ELSE per component
roles_needing_lookup = [
    ('Reliability Engineer', 'Base Approval: Reliability Engineer for {{issue.key}}', '_sv_re'),
    ('Quality Engineer',     'Base Approval: Quality Engineer for {{issue.key}}',     '_sv_qe'),
    ('System Engineer',      'Base Approval: System Engineer for {{issue.key}}',      '_sv_se'),
    ('Project Lead',         'Base Approval: Project Lead for {{issue.key}}',         '_sv_pl'),
]

for role, summary, sv_id in roles_needing_lookup:
    block = make_role_block(role, summary, sv_id)
    new_components.append(block)
    print(f"  +  IF/ELSE block: {role} (Lookup per component)")

# 6. Supplier conditional (IF DPR Type = Supplier, then IF/ELSE per component)
supplier_inner_blocks = []
for i, comp in enumerate(components):
    block_type = "jira.condition.if.block" if i == 0 else "jira.condition.else.block"
    block = {
        "id": None,
        "parentId": None,
        "conditionParentId": None,
        "component": "CONDITION_BLOCK",
        "type": block_type,
        "value": {"conditionMatchType": "ALL"},
        "conditions": [make_component_condition(comp, None)],
        "children": [
            make_lookup(comp, "Supplier", "_sv_sup"),
            make_create("Supplier Approval Required for {{issue.key}}", with_assignee=True),
        ],
        "schemaVersion": 1,
        "connectionId": None,
        "checksum": None
    }
    supplier_inner_blocks.append(block)

supplier_component_block = {
    "id": None,
    "parentId": None,
    "conditionParentId": None,
    "component": "CONDITION",
    "type": "jira.condition.container.block",
    "value": None,
    "conditions": [],
    "children": supplier_inner_blocks,
    "schemaVersion": 1,
    "connectionId": None,
    "checksum": None
}

# Wrap in DPR Type = Supplier condition
supplier_condition = copy.deepcopy(base_rule['components'][-1])  # existing IF block
# Replace children with our new component block
supplier_condition['children'][0]['children'] = [supplier_component_block]
new_components.append(supplier_condition)
print("  +  IF DPR Type=Supplier → IF/ELSE per component")

new_rule['components'] = new_components

# Remove rule ID so import creates a new rule
del new_rule['id']
new_rule['name'] = 'DPR: Auto-Generate Close-Out Approvals (Fixed)'
new_rule['state'] = 'DISABLED'  # start disabled for safety

output = {"cloud": True, "rules": [new_rule]}
out_path = Path(__file__).with_name('automation-rule-FIXED.json')
out_path.write_text(json.dumps(output, indent=2), encoding='utf-8')

print(f"\n✅ Fixed rule written to: {out_path.name}")
print(f"""
Structure:
  1. TRIGGER: Work item transitioned → Close-Out (CO), Issue Type = DPR
  2. ACTION:  Create DPR Owner sub-task (assignee from trigger)
  3. IF component = {components[0]}:
       Lookup "...{components[0]}...Reliability Engineer" → Create with assignee
     ELSE IF component = {components[1]}:
       Lookup "...{components[1]}...Reliability Engineer" → Create with assignee
  4. Same pattern for Quality Engineer
  5. Same pattern for System Engineer
  6. Same pattern for Project Lead
  7. IF DPR Type = Supplier:
       IF component = {components[0]}:
         Lookup "...{components[0]}...Supplier" → Create with assignee
       ELSE IF component = {components[1]}:
         Lookup "...{components[1]}...Supplier" → Create with assignee

To import:
  1. Go to DPR project → Project Settings → Automation
  2. Click "..." (three-dot menu, top-right) → Import rules
  3. Select {out_path.name}
  4. The rule imports as DISABLED — review it, then enable
  5. Disable or delete the old broken rule
""")


# Automation Lookup & Duplicate Prevention — RESOLVED

## Confirmed Root Cause (Lookup failure)

**Jira Cloud project-scoped automation rules CANNOT search other projects
via "Lookup work items"**. The fix was changing the rule scope to **Global**
in the global automation administration.

---

## Preventing Duplicate Approval Sub-Tasks

When a DPR transitions to Close-Out (CO) multiple times (e.g., gets sent
back to CA/VR and returns to CO), the automation must NOT create duplicate
approval sub-tasks.

### How to configure:

1. Open the rule **"DPR: Auto-Generate Close-Out Approvals"**
2. Click **"+"** between the **Trigger** and the first **Create Sub-task**
3. Add: **New condition** → **Related issues condition**
4. Configure:
   - **Related work items**: `Children`
   - **Condition**: `None match specified JQL`
   - **JQL**: `labels = Approval`
5. **Move ALL** existing Create/Lookup steps **inside** this If block
6. Click **Update** to publish

### How it works:
- All approval sub-tasks are created with label `Approval`
- The condition checks: "Do any children have label=Approval?"
- If YES → skip (approvals already exist)
- If NO → create all approval sub-tasks

### Updated Rule Structure:

```
1.  TRIGGER:    When: Work item transitioned → Close-Out (CO)
                Condition: Issue Type equals DPR

2.  CONDITION:  If: No Children match  labels = Approval
                (everything below is INSIDE this condition)

3.    ACTION:   Create Sub-task "Base Approval: DPR Owner for {{issue.key}}"
                Assignee: from trigger work item
                Labels: Approval

4.    ACTION:   Lookup work items
                JQL: project = AM AND summary ~ "{{issue.components.first.name}}"
                     AND summary ~ "Reliability Engineer"

5.    ACTION:   Create Sub-task "Base Approval: Reliability Engineer for {{issue.key}}"
                Additional Fields: {"fields":{"assignee":{"accountId":
                  "{{lookupIssues.first.assignee.accountId}}"}}}
                Labels: Approval

6.    ACTION:   Create Sub-task "Base Approval: Quality Engineer for {{issue.key}}"
                Labels: Approval

7.    ACTION:   Create Sub-task "Base Approval: System Engineer for {{issue.key}}"
                Labels: Approval

8.    ACTION:   Lookup work items
                JQL: project = AM AND summary ~ "{{issue.components.first.name}}"
                     AND summary ~ "Project Lead"

9.    ACTION:   Create Sub-task "Base Approval: Project Lead for {{issue.key}}"
                Additional Fields: {"fields":{"assignee":{"accountId":
                  "{{lookupIssues.first.assignee.accountId}}"}}}
                Labels: Approval

10.   CONDITION: If DPR Type equals Supplier

11.     ACTION: Lookup work items
                JQL: project = AM AND summary ~ "{{issue.components.first.name}}"
                     AND summary ~ "{{issue.DPR Type.value}}"

12.     ACTION: Create Sub-task "Supplier Approval Required for {{issue.key}}"
                Additional Fields: {"fields":{"assignee":{"accountId":
                  "{{lookupIssues.first.assignee.accountId}}"}}}
                Labels: Approval
```

---

## Cleanup Script

To clean up duplicate sub-tasks created during testing:
```
python _cleanup_dpr33.py
```

---

## Key Lessons

1. **Scope matters**: Project-scoped automation rules cannot search other projects
   via Lookup. Set scope to **Global** for cross-project lookups.
2. **Always add a guard condition**: Check for existing approval sub-tasks
   before creating new ones to prevent duplicates on re-transition.
3. **Labels as markers**: Using `labels = Approval` on all approval sub-tasks
   makes it easy to check for their existence.

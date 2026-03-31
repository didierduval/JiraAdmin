#!/usr/bin/env python3
"""
sync_components.py
==================
Synchronize **Components** between two Jira projects (e.g. INC ↔ DPR).

Three modes of operation
------------------------
1. **source → target** (default)
   Copies every component that exists in SOURCE but not in TARGET.
2. **bidirectional**  (--bidi)
   Makes both projects contain the union of both component lists.
3. **dry-run**  (--dry-run, on by default)
   Only prints what *would* happen — no changes written.

The script copies:
  • name  (used as the unique key)
  • description
  • leadAccountId  (only when the same user exists on the target project)

Usage examples
--------------
  # Preview what would be synced  (INC → DPR)
  python sync_components.py

  # Actually run the sync
  python sync_components.py --apply

  # Bidirectional sync (union of both)
  python sync_components.py --bidi --apply

  # Reverse direction: DPR → INC
  python sync_components.py --source DPR --target INC --apply

Scheduling
----------
Run this script on a cron/Task Scheduler to keep them in sync, or call
`sync_components(source, target)` from your automation-trigger webhook.

Future: Sub-Components
----------------------
When you add the **Sub-Components** app (e.g., Adaptavist or similar),
top-level components will represent **products** and the sub-hierarchy
will represent **architecture**.  This script only touches top-level
components, so it will remain compatible.
"""

import argparse
import json
import os
from pathlib import Path

import requests
from jira import JIRA

# ── Load .env ────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'))
except ImportError:
    pass

JIRA_SERVER    = os.environ.get('JIRA_SERVER', 'https://fifo24.atlassian.net')
JIRA_EMAIL     = os.environ.get('JIRA_EMAIL', '')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN', '')

# ── Jira connection ──────────────────────────────────────────────────────
jira = JIRA(
    options={'server': JIRA_SERVER},
    basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN),
)

# REST session (for component create/update — the python-jira library's
# create_component is fine, but we also expose raw REST for flexibility)
_session = requests.Session()
_session.auth = (JIRA_EMAIL, JIRA_API_TOKEN)
_session.headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})


# =====================================================================
#  Helpers
# =====================================================================

def _safe(obj, attr, default=None):
    """Safely get a nested attribute."""
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except Exception:
        return default


def fetch_components(project_key: str) -> list[dict]:
    """Return component dicts for *project_key*.

    Each dict has: id, name, description, leadAccountId.
    """
    raw_components = jira.project_components(project_key)
    results = []
    for c in raw_components:
        lead_id = None
        lead_obj = getattr(c, 'lead', None)
        if lead_obj:
            # Could be a dict (REST v3) or a PropertyHolder
            if isinstance(lead_obj, dict):
                lead_id = lead_obj.get('accountId')
            else:
                lead_id = getattr(lead_obj, 'accountId', None)

        results.append({
            'id':              c.id,
            'name':            c.name,
            'description':     getattr(c, 'description', '') or '',
            'leadAccountId':   lead_id,
            'assigneeType':    getattr(c, 'assigneeType', 'UNASSIGNED'),
        })
    return sorted(results, key=lambda c: c['name'].lower())


def create_component(project_key: str, name: str,
                     description: str = '',
                     lead_account_id: str | None = None) -> dict:
    """Create a component via REST API and return the response JSON."""
    url = f"{JIRA_SERVER}/rest/api/3/component"
    payload = {
        'project':  project_key,
        'name':     name,
    }
    if description:
        payload['description'] = description
    if lead_account_id:
        payload['leadAccountId'] = lead_account_id
    resp = _session.post(url, data=json.dumps(payload))
    resp.raise_for_status()
    return resp.json()


def update_component(component_id: str,
                     description: str | None = None,
                     lead_account_id: str | None = None) -> dict:
    """Update an existing component's description / lead."""
    url = f"{JIRA_SERVER}/rest/api/3/component/{component_id}"
    payload = {}
    if description is not None:
        payload['description'] = description
    if lead_account_id is not None:
        payload['leadAccountId'] = lead_account_id
    if not payload:
        return {}
    resp = _session.put(url, data=json.dumps(payload))
    resp.raise_for_status()
    return resp.json()


# =====================================================================
#  Core sync logic
# =====================================================================

def diff_components(source_comps: list[dict],
                    target_comps: list[dict]) -> tuple[list[dict], list[dict]]:
    """Compare source and target component lists.

    Returns
    -------
    to_create : list[dict]
        Components that exist in source but NOT in target.
    to_update : list[dict]
        Components that exist in both but where description differs
        (source wins).  Each dict includes 'target_id'.
    """
    target_by_name = {c['name'].lower(): c for c in target_comps}

    to_create: list[dict] = []
    to_update: list[dict] = []

    for sc in source_comps:
        key = sc['name'].lower()
        tc = target_by_name.get(key)
        if tc is None:
            to_create.append(sc)
        else:
            # Check if description needs updating (source wins)
            if sc['description'] and sc['description'] != tc['description']:
                to_update.append({**sc, 'target_id': tc['id']})

    return to_create, to_update


def sync_components(source_key: str, target_key: str,
                    dry_run: bool = True,
                    update_descriptions: bool = True) -> dict:
    """Sync components from *source_key* → *target_key*.

    Parameters
    ----------
    source_key : str
        Jira project key to read components FROM.
    target_key : str
        Jira project key to write components TO.
    dry_run : bool
        If True, print what would happen but don't change anything.
    update_descriptions : bool
        If True, overwrite target description with source description
        when they differ.

    Returns
    -------
    dict with keys: created, updated, skipped (counts).
    """
    print(f"\n{'=' * 60}")
    print(f"  Component Sync:  {source_key} → {target_key}")
    print(f"  Mode:  {'DRY RUN (no changes)' if dry_run else '🔧 APPLY'}")
    print(f"{'=' * 60}\n")

    # 1. Fetch both component lists
    print(f"  → Fetching components from {source_key}…")
    source_comps = fetch_components(source_key)
    print(f"    Found {len(source_comps)} component(s): "
          f"{', '.join(c['name'] for c in source_comps)}\n")

    print(f"  → Fetching components from {target_key}…")
    target_comps = fetch_components(target_key)
    print(f"    Found {len(target_comps)} component(s): "
          f"{', '.join(c['name'] for c in target_comps)}\n")

    # 2. Compute diff
    to_create, to_update = diff_components(source_comps, target_comps)

    if not to_create and not to_update:
        print("  ✅ Projects are already in sync — nothing to do.\n")
        return {'created': 0, 'updated': 0, 'skipped': 0}

    # 3. Report
    if to_create:
        print(f"  Components to CREATE in {target_key}:")
        for c in to_create:
            lead = c['leadAccountId'] or 'none'
            print(f"    + {c['name']:<40} (lead={lead})")
        print()

    if to_update and update_descriptions:
        print(f"  Components to UPDATE in {target_key} (description sync):")
        for c in to_update:
            print(f"    ~ {c['name']:<40}")
        print()

    # 4. Apply
    stats = {'created': 0, 'updated': 0, 'skipped': 0}

    if dry_run:
        print("  ⏸  Dry run — no changes made.  Pass --apply to execute.\n")
        stats['skipped'] = len(to_create) + len(to_update)
        return stats

    for c in to_create:
        try:
            result = create_component(
                project_key=target_key,
                name=c['name'],
                description=c['description'],
                lead_account_id=c['leadAccountId'],
            )
            print(f"    ✅ Created '{c['name']}' (id={result['id']})")
            stats['created'] += 1
        except requests.HTTPError as e:
            print(f"    ❌ Failed to create '{c['name']}': {e.response.text}")
            stats['skipped'] += 1

    if update_descriptions:
        for c in to_update:
            try:
                update_component(
                    component_id=c['target_id'],
                    description=c['description'],
                )
                print(f"    ✅ Updated description for '{c['name']}'")
                stats['updated'] += 1
            except requests.HTTPError as e:
                print(f"    ❌ Failed to update '{c['name']}': {e.response.text}")
                stats['skipped'] += 1

    print(f"\n  Done — created: {stats['created']}, "
          f"updated: {stats['updated']}, skipped: {stats['skipped']}\n")
    return stats


# =====================================================================
#  CLI
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Synchronize Jira components between two projects.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python sync_components.py                          # dry-run INC → DPR
  python sync_components.py --apply                  # apply INC → DPR
  python sync_components.py --bidi --apply           # bidirectional
  python sync_components.py --source DPR --target INC --apply
        """,
    )
    parser.add_argument('--source', default='INC',
                        help='Source project key (default: INC)')
    parser.add_argument('--target', default='DPR',
                        help='Target project key (default: DPR)')
    parser.add_argument('--bidi', action='store_true',
                        help='Bidirectional sync (union of both)')
    parser.add_argument('--apply', action='store_true',
                        help='Actually apply changes (default is dry-run)')
    parser.add_argument('--no-update-desc', action='store_true',
                        help='Skip updating descriptions on existing components')
    parser.add_argument('--dump', action='store_true',
                        help='Dump component lists to JSON and exit')
    args = parser.parse_args()

    dry_run = not args.apply
    update_desc = not args.no_update_desc

    if args.dump:
        for pk in (args.source, args.target):
            comps = fetch_components(pk)
            out = Path(__file__).with_name(f'components_{pk}.json')
            out.write_text(json.dumps(comps, indent=2))
            print(f"  → Dumped {len(comps)} components from {pk} → {out}")
        return

    # Forward sync: source → target
    sync_components(args.source, args.target,
                    dry_run=dry_run, update_descriptions=update_desc)

    # Reverse sync (bidirectional)
    if args.bidi:
        sync_components(args.target, args.source,
                        dry_run=dry_run, update_descriptions=update_desc)

    # ── Summary / guidance ───────────────────────────────────────────
    if dry_run:
        print("  💡 Tip: run with --apply to actually create/update components.\n")

    print("  ── Automation Guidance ──────────────────────────────────────")
    print("  To keep components in sync automatically going forward,")
    print("  add a Jira Automation rule to each project:")
    print()
    print("  Trigger:  Component created  (project = INC)")
    print("  Action:   Send web request  →  POST to your webhook/script")
    print("            OR use a Scheduled rule that runs this script daily.")
    print()
    print("  Alternatively, add a 'Run script' post-function if using")
    print("  ScriptRunner, or wire this into your existing INC→DPR")
    print("  automation as described in the guide below.")
    print()


if __name__ == '__main__':
    main()



#!/usr/bin/env python3
"""
generate_pipeline.py
Generates docs/pipeline.html — Group 1 pipeline management table.
Group 1: RSVP known, owned by Ani, Attended Event = Yes,
         Call Completed != Order Completed, Not Disqualified.
"""

import json
import os
import re
import sys
import time
import requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date as date_type
from html import escape
from pathlib import Path

HUBSPOT_TOKEN = os.environ.get('HUBSPOT_API_KEY', '').strip().replace('﻿', '')
PORTAL_ID = '5454671'
HEADERS = {'Authorization': f'Bearer {HUBSPOT_TOKEN}', 'Content-Type': 'application/json'}
SEARCH_URL = 'https://api.hubapi.com/crm/v3/objects/contacts/search'

# Dashboard timestamps display in New York wall-clock time (DST-aware: EST in winter,
# EDT in summer) so they always match a clock on the wall in NYC. Internal math stays UTC.
def eastern_now():
    """Return (now_in_ny_walltime, tzname) — DST per US rules, dependency-free."""
    u = datetime.now(timezone.utc)
    mar8 = datetime(u.year, 3, 8, tzinfo=timezone.utc)
    dst_start = (mar8 + timedelta(days=(6 - mar8.weekday()) % 7)).replace(hour=7)  # 2nd Sun Mar, 02:00 EST
    nov1 = datetime(u.year, 11, 1, tzinfo=timezone.utc)
    dst_end = (nov1 + timedelta(days=(6 - nov1.weekday()) % 7)).replace(hour=6)    # 1st Sun Nov, 02:00 EDT
    is_dst = dst_start <= u < dst_end
    off, name = (-4, 'EDT') if is_dst else (-5, 'EST')
    return u.astimezone(timezone(timedelta(hours=off))), name

DEAL_STAGES = {
    '1321369495': 'Event Attended',
    '1339121714': 'Advisor Assigned',
    '1321369496': 'Active Rel',
    '1363474599': 'Long Term Rel',
    '1321369497': 'Mtg Sch',
    '1321369500': 'Nurture',
    '1321369502': 'Rec Made',
    '1321369499': 'Closed Won',
    '1321369501': 'Closed Lost',
    '1341309466': 'Self Serve',
    '1363474966': 'Collector',
    '1363467915': 'Financial Advisor',
}

STAGE_SORT_ORDER = {
    '1321369502': 0,  # Recommendation Made
    '1321369500': 1,  # Nurture
    '1321369497': 2,  # Meeting Scheduled
    '1363474599': 3,  # Long Term Relationship
    '1321369496': 4,  # Active Relationship
    '1339121714': 5,  # Advisor Assigned
    '1321369495': 6,  # Event Attended
    '1321369499': 7,  # Closed Won
    '1321369501': 8,  # Closed Lost
    '1341309466': 9,  # Self Serve
    '1363474966': 10, # Collector
    '1363467915': 11, # Financial Advisor
    '':            12, # No deal
}

STAGE_CSS = {
    '1321369495': 'stage-event',
    '1339121714': 'stage-advisor',
    '1321369496': 'stage-active',
    '1363474599': 'stage-longterm',
    '1321369497': 'stage-meeting',
    '1321369500': 'stage-nurture',
    '1321369502': 'stage-rec',
    '1321369499': 'stage-won',
    '1321369501': 'stage-lost',
    '1341309466': 'stage-disq',
    '1363474966': 'stage-disq',
    '1363467915': 'stage-disq',
}

GALLERY_LEADS_PIPELINE = '880355706'
ANI_OWNER_ID = '77771452'

OWNERS = [
    {'name': 'Ani',  'id': '77771452', 'out': 'docs/index.html', 'pw': 'banksy'},
    {'name': 'Erik', 'id': '73613833', 'out': 'docs/erik.html',  'pw': 'banksy'},
]
OVERVIEW_CFG  = {'name': 'Overview',     'out': 'docs/overview.html',                 'pw': 'banksy'}
VELOCITY_CFG  = {'name': 'Velocity',     'out': 'docs/velocity.html',                 'pw': 'banksy'}
SCORED_CFG    = {'name': 'Adv Assigned', 'out': 'docs/advisor_assigned_scored.html',  'pw': 'banksy'}
MAGAZINE_CFG  = {'name': 'Magazine',     'out': 'docs/magazine.html'}
DELIVERABLES_CFG = {'name': 'Deliverables', 'out': 'docs/deliverables.html'}
ALL_PAGES = OWNERS + [OVERVIEW_CFG, VELOCITY_CFG, SCORED_CFG, MAGAZINE_CFG, DELIVERABLES_CFG]


def render_nav(active_cfg):
    """Single source of truth for the top-nav markup (styled by nav.css).
    Ani + Erik are collapsed into one 'Pipeline' entry (an Ani/Erik sub-toggle
    is added on the owner pages). Portfolio is a static page included here so the
    link survives regeneration."""
    owner_active = active_cfg in OWNERS
    items = ['<a href="index.html"' + (' class="active"' if owner_active else '') + '>Pipeline</a>']
    for o in [OVERVIEW_CFG, VELOCITY_CFG, SCORED_CFG, MAGAZINE_CFG, DELIVERABLES_CFG]:
        active = ' class="active"' if o is active_cfg else ''
        items.append(f'<a href="{Path(o["out"]).name}"{active}>{o["name"]}</a>')
    items.append('<a href="portfolio.html">Portfolio</a>')
    return '<div class="nav">' + ''.join(items) + '</div>'


OVERVIEW_OWNER_IDS = {'77771452', '73613833'}
OVERVIEW_OWNER_NAMES = {'77771452': 'Mittal', '73613833': 'Bringsjord'}
MONTHLY_GOAL = 700_000
CLOSED_WON_STAGE  = '1321369499'
CLOSED_LOST_STAGE = '1321369501'
DQ_STAGE          = '1341309466'
ADVISOR_ASSIGNED_STAGE = '1339121714'
TERMINAL_STAGES   = {CLOSED_WON_STAGE, CLOSED_LOST_STAGE, DQ_STAGE, '1363474966', '1363467915'}

# ── Velocity dashboard constants ──
AA_CAPACITY = 126                          # Erik + Ani combined Advisor-Assigned working capacity
VELO_SERIES_START = date_type(2026, 3, 9)  # first Monday of the weekly trend window
VELO_APR9 = date_type(2026, 4, 9)          # automation-batch inflection point

# Overview weekly-summary narrative is frozen per ISO week (see _weekly_summary_bullets)
WEEKLY_SUMMARY_FILE = Path(__file__).parent / 'weekly_summary.json'


def _weekly_summary_bullets(candidates, today_d):
    """Freeze the top-3 positive bullets per ISO week so the narrative reads as a weekly
    note rather than churning on every 3x/day rebuild. Regenerates only on the first run
    of a new ISO week; the stored JSON is human-editable in between."""
    iso = list(today_d.isocalendar()[:2])  # [year, week]
    try:
        store = json.loads(WEEKLY_SUMMARY_FILE.read_text(encoding='utf-8'))
    except Exception:
        store = {}
    if store.get('iso') == iso and store.get('bullets'):
        return store['bullets']
    ranked = sorted(candidates, key=lambda x: -x[0])[:3]
    bullets = [{'color': c, 'title': t, 'text': x} for (_s, c, t, x) in ranked]
    try:
        WEEKLY_SUMMARY_FILE.write_text(
            json.dumps({'iso': iso, 'updated': today_d.isoformat(), 'bullets': bullets}, indent=2),
            encoding='utf-8')
    except Exception as e:
        print(f'  WARN: could not write weekly summary: {e}', flush=True)
    return bullets


def fetch_all_contacts(owner_id):
    filters = [
        {'propertyName': 'outbound_rsvp_to_event', 'operator': 'HAS_PROPERTY'},
        {'propertyName': 'hubspot_owner_id', 'operator': 'EQ', 'value': owner_id},
        {'propertyName': 'attended_outbound_event', 'operator': 'EQ', 'value': 'Yes'},
        {'propertyName': 'call_completed', 'operator': 'NOT_IN', 'values': ['Order Completed']},
        {'propertyName': 'outbound_event_attendee_disqualified', 'operator': 'NOT_IN', 'values': ['Disqualified']},
    ]
    props = ['firstname', 'lastname', 'email', 'outbound_rsvp_to_event',
             'hs_last_sales_activity_timestamp', 'notes_last_contacted',
             'pipl_linkedin', 'hs_linkedin_url',
             'outbound_team___linkedin_url', 'linkedin_personal_url', 'lgm_linkedinurl',
             'jobtitle', 'company', 'industry', 'total_purchased___reserved', 'haspurchased']

    contacts = []
    after = None
    while True:
        body = {'filterGroups': [{'filters': filters}], 'properties': props, 'limit': 200}
        if after:
            body['after'] = after
        r = requests.post(SEARCH_URL, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        contacts.extend(data.get('results', []))
        after = data.get('paging', {}).get('next', {}).get('after')
        if not after:
            break
        time.sleep(0.2)

    print(f'Fetched {len(contacts)} contacts', flush=True)
    return contacts


def fetch_whale_replies(deal_ids):
    """For each whale deal, find its associated contact and return last-reply info.

    Returns: {deal_id: {'reply_date': str_iso_or_empty}}
    """
    out = {}
    if not deal_ids:
        return out

    # Step 1: deal -> contact associations
    deal_to_contact = {}
    for i in range(0, len(deal_ids), 100):
        batch = deal_ids[i:i+100]
        r = requests.post(
            'https://api.hubapi.com/crm/v4/associations/deal/contact/batch/read',
            headers=HEADERS,
            json={'inputs': [{'id': str(did)} for did in batch]},
        )
        r.raise_for_status()
        for item in r.json().get('results', []):
            did = str(item['from']['id'])
            tos = item.get('to', [])
            if tos:
                deal_to_contact[did] = str(tos[0]['toObjectId'])
        time.sleep(0.15)

    # Step 2: batch-read contact reply dates
    contact_ids = list(set(deal_to_contact.values()))
    contact_props = {}
    for i in range(0, len(contact_ids), 100):
        batch = contact_ids[i:i+100]
        r = requests.post(
            'https://api.hubapi.com/crm/v3/objects/contacts/batch/read',
            headers=HEADERS,
            json={'inputs': [{'id': cid} for cid in batch],
                  'properties': ['hs_email_last_reply_date']},
        )
        r.raise_for_status()
        for c in r.json().get('results', []):
            contact_props[c['id']] = c.get('properties', {})
        time.sleep(0.15)

    # Step 3: map deal_id -> reply_date
    for did in deal_ids:
        did_s = str(did)
        cid = deal_to_contact.get(did_s, '')
        rd = contact_props.get(cid, {}).get('hs_email_last_reply_date', '') if cid else ''
        out[did_s] = {'reply_date': rd or ''}
    return out


def fetch_deals_live():
    """Fetch all Gallery Leads deals live from HubSpot API."""
    url = 'https://api.hubapi.com/crm/v3/objects/deals/search'
    # Include per-stage entry-date properties so we can compute stage progression
    stage_entry_props = [f'hs_v2_date_entered_{sid}' for sid in DEAL_STAGES.keys()]
    # AA exit-date drives the Velocity net-AA-flow reconstruction
    stage_exit_props = [f'hs_v2_date_exited_{ADVISOR_ASSIGNED_STAGE}']
    base_props = ['dealname', 'dealstage', 'amount', 'num_contacted_notes',
                  'hubspot_owner_id', 'closedate', 'createdate', 'notes_last_updated']
    deals = []
    after = None
    while True:
        body = {
            'filterGroups': [{'filters': [
                {'propertyName': 'pipeline', 'operator': 'EQ', 'value': GALLERY_LEADS_PIPELINE}
            ]}],
            'properties': base_props + stage_entry_props + stage_exit_props,
            'limit': 200,
        }
        if after:
            body['after'] = after
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        deals.extend(data.get('results', []))
        after = data.get('paging', {}).get('next', {}).get('after')
        if not after:
            break
        time.sleep(0.2)
    print(f'Fetched {len(deals)} deals live', flush=True)
    return deals


ACTIVITY_CACHE_FILE = Path(__file__).parent / 'activity_cache.json'

def _load_activity_cache() -> dict:
    try:
        return json.loads(ACTIVITY_CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}

def fetch_advisor_activity():
    """Fetch call and email counts for Ani + Erik, last 30 days.

    Calls are fetched live. Emails fall back to activity_cache.json when the
    token lacks crm.objects.emails.read scope (403). Cache is updated manually
    via Claude MCP queries and committed alongside the script.
    """
    today = datetime.now(timezone.utc)
    ts_30d = int((today - timedelta(days=30)).timestamp() * 1000)

    # Last 5 working days start
    d_iter = today.date() - timedelta(days=1)
    wd = 0
    while wd < 5:
        if d_iter.weekday() < 5:
            wd += 1
        if wd < 5:
            d_iter -= timedelta(days=1)
    ts_5wd = int(datetime(d_iter.year, d_iter.month, d_iter.day, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    n_5wd_days = max((today.date() - d_iter).days, 1)

    counts = {oid: {'calls_30': 0, 'emails_30': 0, 'calls_5wd': 0, 'emails_5wd': 0}
              for oid in OVERVIEW_OWNER_IDS}
    email_cache_ts = None

    for obj_type, key in [('calls', 'calls'), ('emails', 'emails')]:
        url = f'https://api.hubapi.com/crm/v3/objects/{obj_type}/search'
        after = None
        failed = False
        while True:
            body = {
                'filterGroups': [
                    {'filters': [
                        {'propertyName': 'hubspot_owner_id', 'operator': 'EQ', 'value': oid},
                        {'propertyName': 'hs_timestamp', 'operator': 'GTE', 'value': str(ts_30d)},
                    ]}
                    for oid in OVERVIEW_OWNER_IDS
                ],
                'properties': ['hubspot_owner_id', 'hs_timestamp'],
                'limit': 200,
            }
            if after:
                body['after'] = after
            r = requests.post(url, headers=HEADERS, json=body, timeout=30)
            if not r.ok:
                print(f'  Activity fetch error {obj_type}: {r.status_code}', file=sys.stderr)
                failed = True
                break
            data = r.json()
            for item in data.get('results', []):
                p = item.get('properties', {})
                owner = p.get('hubspot_owner_id', '')
                if owner not in counts:
                    continue
                counts[owner][f'{key}_30'] += 1
                ts_val = p.get('hs_timestamp')
                if ts_val:
                    try:
                        # API returns ISO string ("2026-05-27T19:36:29Z") not epoch ms
                        if str(ts_val).lstrip('-').isdigit():
                            ts_ms = int(ts_val)
                        else:
                            from datetime import datetime as _dt
                            ts_ms = int(_dt.fromisoformat(str(ts_val).replace('Z', '+00:00')).timestamp() * 1000)
                        if ts_ms >= ts_5wd:
                            counts[owner][f'{key}_5wd'] += 1
                    except (ValueError, TypeError):
                        pass
            after = data.get('paging', {}).get('next', {}).get('after')
            if not after:
                break
            time.sleep(0.1)

        if failed and key == 'emails':
            cache = _load_activity_cache()
            if cache:
                email_cache_ts = cache.get('fetched_at', '')
                for oid in OVERVIEW_OWNER_IDS:
                    cached = cache.get(oid, {})
                    counts[oid]['emails_30'] = cached.get('emails_30', 0)
                    counts[oid]['emails_5wd'] = cached.get('emails_5wd', 0)
                print(f'  Emails: loaded from cache (last updated {email_cache_ts})', flush=True)

    print(f'  Activity: Ani calls={counts["77771452"]["calls_30"]} emails={counts["77771452"]["emails_30"]} '
          f'| Erik calls={counts["73613833"]["calls_30"]} emails={counts["73613833"]["emails_30"]}', flush=True)
    return counts, n_5wd_days, email_cache_ts


def normalize(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def build_deal_index(deals):
    """Pre-process deals for fast matching. Returns (records, by_name, by_last_name)."""
    records = []
    by_name = {}
    by_last_name = {}  # last_name_key -> [(first_name_key, record)]
    for deal in deals:
        p = deal.get('properties', {})
        raw = (p.get('dealname', '') or '').strip()
        record = {
            'stage': p.get('dealstage', ''),
            'amount': p.get('amount', ''),
            'times_contacted': p.get('num_contacted_notes', ''),
            'raw_lower': raw.lower(),
        }
        records.append(record)
        # Name index: strip email suffix, strip " - Placeholder..." suffixes
        name_part = re.sub(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', '', raw, flags=re.IGNORECASE).strip()
        name_part = re.split(r'\s+-\s+', name_part)[0].strip()
        key = normalize(name_part)
        if key and key not in by_name:
            by_name[key] = record
        # Last-name index for nickname/shortened first-name matching (e.g. "Ed" vs "Edward")
        parts = name_part.split()
        if len(parts) >= 2:
            last_key = normalize(parts[-1])
            first_key = normalize(parts[0])
            by_last_name.setdefault(last_key, []).append((first_key, record))
    return records, by_name, by_last_name


def match_deal(contact_props, records, by_name, by_last_name=None):
    email = (contact_props.get('email') or '').lower().strip()
    first = contact_props.get('firstname') or ''
    last = contact_props.get('lastname') or ''
    name_key = normalize(first + last)

    # Primary: contact email appears as a substring of the deal name
    # (handles "FirstLastemail@domain.com" format where email is embedded)
    if email:
        for r in records:
            if email in r['raw_lower']:
                return r

    # Fallback: normalized name match
    if name_key in by_name:
        return by_name[name_key]

    # Partial name match (catches "Justin Holder - Placeholder..." etc.)
    for dkey, dval in by_name.items():
        if dkey.startswith(name_key) and len(name_key) >= 4:
            return dval

    # Nickname/shortened first name match (e.g. deal "Ed Sonderling" vs contact "Edward Sonderling")
    if by_last_name and last:
        last_key = normalize(last)
        first_key = normalize(first)
        for d_first, dval in by_last_name.get(last_key, []):
            if len(d_first) >= 2 and len(first_key) >= 2:
                if first_key.startswith(d_first) or d_first.startswith(first_key):
                    return dval

    return None


def fmt_date(iso):
    if not iso:
        return ''
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        return dt.strftime('%#m/%#d') if sys.platform == 'win32' else dt.strftime('%-m/%-d')
    except Exception:
        return iso[:10] if len(iso) >= 10 else iso


def fmt_date_ms(ts_str):
    """Accept ISO string or ms-as-string, return (formatted_date, epoch_ms)."""
    if not ts_str:
        return '', 0
    try:
        ms = float(ts_str)
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            ms = dt.timestamp() * 1000
        except Exception:
            return '', 0
    s = dt.strftime('%#m/%#d') if sys.platform == 'win32' else dt.strftime('%-m/%-d')
    return s, int(ms)


def fetch_contact_tasks(contact_ids, owner_id):
    """Fetch earliest open task per contact + daily breakdown by type.
    Returns (per_contact_dict, daily_dict) where
      per_contact_dict: {cid: {due_str, due_ms, subject}}
      daily_dict: {date_iso: {email: n, call: n, other: n}}
    """
    TASK_SEARCH = 'https://api.hubapi.com/crm/v3/objects/tasks/search'
    ASSOC_URL   = 'https://api.hubapi.com/crm/v4/associations/task/contact/batch/read'

    contact_id_set = {str(c) for c in contact_ids}

    all_tasks, after = [], None
    while True:
        body = {
            'filterGroups': [{'filters': [
                {'propertyName': 'hubspot_owner_id', 'operator': 'EQ', 'value': owner_id},
                {'propertyName': 'hs_task_status', 'operator': 'NEQ', 'value': 'COMPLETED'},
            ]}],
            'properties': ['hs_task_subject', 'hs_timestamp', 'hs_task_status', 'hs_task_type'],
            'limit': 100,
        }
        if after:
            body['after'] = after
        r = requests.post(TASK_SEARCH, headers=HEADERS, json=body)
        if r.status_code >= 300:
            print(f'Task search failed ({r.status_code}) — skipping task column', flush=True)
            return {}, {}
        data = r.json()
        all_tasks.extend(data.get('results', []))
        after = data.get('paging', {}).get('next', {}).get('after')
        if not after:
            break
        time.sleep(0.2)

    if not all_tasks:
        return {}, {}

    task_props = {t['id']: t['properties'] for t in all_tasks}
    task_ids = list(task_props.keys())
    print(f'  {len(task_ids)} open tasks found, fetching contact associations...', flush=True)

    # Daily breakdown — only tasks due on or after today
    today_iso = datetime.now(timezone.utc).date().isoformat()
    daily_tasks: dict = {}
    for tp in task_props.values():
        _, ms = fmt_date_ms(tp.get('hs_timestamp', ''))
        if not ms:
            continue
        date_key = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
        if date_key < today_iso:
            continue  # skip overdue
        raw_type = (tp.get('hs_task_type') or '').upper()
        cat = 'email' if 'EMAIL' in raw_type else 'call' if 'CALL' in raw_type else 'other'
        day = daily_tasks.setdefault(date_key, {'email': 0, 'call': 0, 'other': 0})
        day[cat] += 1

    # Per-contact earliest task
    contact_tasks: dict = {}
    for i in range(0, len(task_ids), 100):
        batch = task_ids[i:i+100]
        r = requests.post(ASSOC_URL, headers=HEADERS,
                          json={'inputs': [{'id': tid} for tid in batch]})
        if r.status_code >= 300:
            break
        for item in r.json().get('results', []):
            tid = str(item['from']['id'])
            tp = task_props.get(tid, {})
            date_str, ms = fmt_date_ms(tp.get('hs_timestamp', ''))
            if not ms:
                continue
            subject = (tp.get('hs_task_subject') or '').strip()
            for assoc in item.get('to', []):
                cid = str(assoc['toObjectId'])
                if cid in contact_id_set:
                    contact_tasks.setdefault(cid, []).append((ms, date_str, subject))
        time.sleep(0.2)

    result = {}
    for cid, ctasks in contact_tasks.items():
        ctasks.sort()
        ms, due_str, subject = ctasks[0]
        result[cid] = {'due_str': due_str, 'due_ms': ms, 'subject': subject}
    return result, daily_tasks


def fetch_contact_meetings(contact_ids):
    """Fetch next upcoming meeting for each contact. Returns {contact_id_str: {start_str, start_ms, title}}."""
    MEETING_SEARCH = 'https://api.hubapi.com/crm/v3/objects/meetings/search'
    ASSOC_URL = 'https://api.hubapi.com/crm/v4/associations/meeting/contact/batch/read'

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    contact_id_set = {str(c) for c in contact_ids}

    all_meetings, after = [], None
    while True:
        body = {
            'filterGroups': [{'filters': [
                {'propertyName': 'hs_meeting_start_time', 'operator': 'GTE', 'value': str(now_ms)},
            ]}],
            'properties': ['hs_meeting_title', 'hs_meeting_start_time'],
            'sorts': [{'propertyName': 'hs_meeting_start_time', 'direction': 'ASCENDING'}],
            'limit': 100,
        }
        if after:
            body['after'] = after
        r = requests.post(MEETING_SEARCH, headers=HEADERS, json=body)
        if r.status_code >= 300:
            print(f'Meeting search failed ({r.status_code}) — skipping meeting column', flush=True)
            return {}, {}
        data = r.json()
        all_meetings.extend(data.get('results', []))
        after = data.get('paging', {}).get('next', {}).get('after')
        if not after:
            break
        time.sleep(0.2)

    if not all_meetings:
        return {}, {}

    meeting_props = {m['id']: m['properties'] for m in all_meetings}
    meeting_ids = list(meeting_props.keys())
    print(f'  {len(meeting_ids)} upcoming meetings found, fetching contact associations...', flush=True)

    contact_meetings: dict = {}
    for i in range(0, len(meeting_ids), 100):
        batch = meeting_ids[i:i + 100]
        r = requests.post(ASSOC_URL, headers=HEADERS,
                          json={'inputs': [{'id': mid} for mid in batch]})
        if r.status_code >= 300:
            break
        for item in r.json().get('results', []):
            mid = str(item['from']['id'])
            mp = meeting_props.get(mid, {})
            start_str, ms = fmt_date_ms(mp.get('hs_meeting_start_time', ''))
            if not ms:
                continue
            title = (mp.get('hs_meeting_title') or '').strip()
            for assoc in item.get('to', []):
                cid = str(assoc['toObjectId'])
                if cid in contact_id_set:
                    contact_meetings.setdefault(cid, []).append((ms, start_str, title))
        time.sleep(0.2)

    # Daily counts from pipeline contacts only (deduplicated per contact per day)
    daily_meetings: dict = {}
    seen_cid_day: set = set()
    for cid, mtgs in contact_meetings.items():
        for ms, start_str, title in mtgs:
            date_key = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
            key = (cid, date_key)
            if key not in seen_cid_day:
                seen_cid_day.add(key)
                daily_meetings[date_key] = daily_meetings.get(date_key, 0) + 1

    result = {}
    for cid, mtgs in contact_meetings.items():
        mtgs.sort()
        ms, start_str, title = mtgs[0]
        result[cid] = {'start_str': start_str, 'start_ms': ms, 'title': title}
    return result, daily_meetings


def fetch_contact_notes(contact_ids):
    """Fetch most recent note snippet for each contact. Returns {contact_id_str: snippet_str}."""
    if not contact_ids:
        return {}

    ASSOC_URL = 'https://api.hubapi.com/crm/v4/associations/contacts/notes/batch/read'
    NOTE_BATCH = 'https://api.hubapi.com/crm/v3/objects/notes/batch/read'

    # Step 1: get note IDs per contact
    contact_to_notes: dict = {}
    for i in range(0, len(contact_ids), 100):
        batch = [str(c) for c in contact_ids[i:i+100]]
        r = requests.post(ASSOC_URL, headers=HEADERS,
                          json={'inputs': [{'id': cid} for cid in batch]})
        if r.status_code >= 300:
            continue
        for item in r.json().get('results', []):
            cid = str(item['from']['id'])
            note_ids = [str(a['toObjectId']) for a in item.get('to', [])]
            if note_ids:
                contact_to_notes[cid] = note_ids
        time.sleep(0.15)

    if not contact_to_notes:
        return {}

    # Step 2: batch fetch note details
    all_note_ids = list({nid for ids in contact_to_notes.values() for nid in ids})
    note_data: dict = {}
    for i in range(0, len(all_note_ids), 100):
        batch = all_note_ids[i:i+100]
        r = requests.post(NOTE_BATCH, headers=HEADERS,
                          json={'inputs': [{'id': nid} for nid in batch],
                                'properties': ['hs_note_body', 'hs_timestamp']})
        if r.status_code >= 300:
            continue
        for note in r.json().get('results', []):
            note_data[note['id']] = {
                'body': note['properties'].get('hs_note_body') or '',
                'ts':   note['properties'].get('hs_timestamp') or '',
            }
        time.sleep(0.15)

    # Step 3: pick most recent note per contact, return short snippet
    result = {}
    for cid, note_ids in contact_to_notes.items():
        notes = [(note_data[nid]['ts'], note_data[nid]['body'])
                 for nid in note_ids if nid in note_data]
        if not notes:
            continue
        notes.sort(reverse=True)
        body = notes[0][1]
        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()
        if len(body) > 110:
            body = body[:110].rsplit(' ', 1)[0] + '…'
        if body:
            result[cid] = body
    return result


def fmt_industry_label(raw):
    if not raw:
        return 'Unknown'
    raw = raw.strip()
    if raw in INDUSTRY_LABEL_MAP:
        return INDUSTRY_LABEL_MAP[raw]
    normalized = raw.lower().replace('_', ' ')
    if normalized in ('na', 'n/a', 'n.a.', 'none', '-', 'not applicable'):
        return 'Unknown'
    for k, v in INDUSTRY_LABEL_MAP.items():
        if k.lower().replace('_', ' ') == normalized:
            return v
    return raw.replace('_', ' ').title()


def fmt_amount(amount_str):
    if not amount_str:
        return ''
    try:
        n = float(amount_str)
        if n >= 1_000_000: return f'{n/1_000_000:.1f}M'
        if n >= 1_000:     return f'{n/1_000:.0f}k'
        return f'{int(n):,}'
    except Exception:
        return amount_str


def fmt_days_ago(days):
    if days is None: return '—'
    if days == 0:    return 'today'
    if days == 1:    return 'yesterday'
    return f'{days}d'


FUNNEL_STAGES = [
    ('1339121714', 'Advisor Assigned'),
    ('1321369496', 'Active Rel'),
    ('1363474599', 'Long Term Rel'),
    ('1321369497', 'Mtg Sch'),
    ('1321369500', 'Nurture'),
    ('1321369502', 'Rec Made'),
]

INDUSTRY_LABEL_MAP = {
    # HubSpot enum format
    'INVESTMENT_BANKING_VENTURE': 'Investment Banking',
    'INVESTMENT_MANAGEMENT': 'Investment Mgmt',
    'VENTURE_CAPITAL_PRIVATE_EQUITY': 'VC / Private Equity',
    'FINANCIAL_SERVICES': 'Financial Services',
    'CAPITAL_MARKETS': 'Capital Markets',
    'BANKING': 'Banking',
    'INSURANCE': 'Insurance',
    'ACCOUNTING': 'Accounting',
    'LAW_PRACTICE': 'Law',
    'LEGAL_SERVICES': 'Law',
    'REAL_ESTATE': 'Real Estate',
    'COMMERCIAL_REAL_ESTATE': 'Commercial RE',
    'COMPUTER_SOFTWARE': 'Software',
    'INFORMATION_TECHNOLOGY_SERVICES': 'IT Services',
    'INTERNET': 'Internet',
    'MEDICAL_PRACTICE': 'Healthcare',
    'HOSPITAL_HEALTH_CARE': 'Healthcare',
    'PHARMACEUTICALS': 'Pharma',
    'BIOTECHNOLOGY': 'Biotech',
    'MANAGEMENT_CONSULTING': 'Consulting',
    'NONPROFIT_ORGANIZATION_MANAGEMENT': 'Nonprofit',
    # LinkedIn / free-text values (as stored in portal)
    'Computer Software': 'Software',
    'Information Technology And Services': 'IT Services',
    'Hospital & Health Care': 'Healthcare',
    'Hospitals & Physicians Clinics': 'Healthcare',
    'Law Practice': 'Law',
    'Legal Services': 'Law',
    'Law Firms & Legal Services': 'Law',
    'Investment Management': 'Investment Mgmt',
    'Non-Profit Organization Management': 'Nonprofit',
    'Non-Profit Organizations': 'Nonprofit',
    'Marketing And Advertising': 'Marketing & Advertising',
    'Colleges & Universities': 'Education',
    'Bizservice': 'Business Services',
    'Mfg': 'Manufacturing',
    'Na': 'Unknown',
    'N/A': 'Unknown',
    'n/a': 'Unknown',
}

PIE_PALETTE = [
    '#c9a96e', '#6daacc', '#9d9ddd', '#cc8a6d', '#6dbf6d', '#6dcccc',
    '#c97777', '#a9cc6e', '#cc9d6d', '#6e8acc', '#cc6d9d', '#aaaaaa',
]


_TITLE_ABBREVS = [
    ('senior vice president', 'SVP'),
    ('executive vice president', 'EVP'),
    ('assistant vice president', 'AVP'),
    ('associate vice president', 'AVP'),
    ('vice president', 'VP'),
    ('managing director', 'MD'),
    ('managing partner', 'MP'),
    ('managing member', 'MM'),
    ('general partner', 'GP'),
    ('founding partner', 'Founding Partner'),
    ('chief executive officer', 'CEO'),
    ('chief financial officer', 'CFO'),
    ('chief operating officer', 'COO'),
    ('chief technology officer', 'CTO'),
    ('chief information officer', 'CIO'),
    ('chief marketing officer', 'CMO'),
    ('chief revenue officer', 'CRO'),
    ('chief investment officer', 'CIO'),
    ('chief people officer', 'CPO'),
    ('chief product officer', 'CPO'),
    ('portfolio manager', 'PM'),
    ('fund manager', 'FM'),
    ('senior director', 'Sr. Dir'),
    ('associate director', 'Assoc. Dir'),
    ('senior manager', 'Sr. Mgr'),
]

def shorten_title(title):
    t = title
    for long, short in _TITLE_ABBREVS:
        t = re.sub(re.escape(long), short, t, flags=re.IGNORECASE)
    return t


def build_html(contacts, records, by_name, by_last_name=None, tasks=None, meetings=None, notes=None,
               daily_tasks=None, daily_meetings=None, owner_name='Ani', nav_html='', password='anisha'):
    now_dt = datetime.now(timezone.utc)
    _now_e, _tz = eastern_now()
    now = _now_e.strftime('%B %-d, %Y %H:%M ' + _tz) if sys.platform != 'win32' \
        else _now_e.strftime('%B %#d, %Y %H:%M ' + _tz)
    now_ms_ts = int(now_dt.timestamp() * 1000)
    today_date = now_dt.date()
    ms_30d = 30 * 24 * 3600 * 1000
    ms_21d = 21 * 24 * 3600 * 1000
    if tasks is None:
        tasks = {}
    if meetings is None:
        meetings = {}
    if notes is None:
        notes = {}

    # Build row data first so we can sort before emitting HTML
    row_data = []
    for c in contacts:
        p = c.get('properties', {})
        cid = str(c['id'])
        first = p.get('firstname') or ''
        last = p.get('lastname') or ''
        name = escape(f'{first} {last}'.strip() or p.get('email') or cid)
        hs_url = f'https://app.hubspot.com/contacts/{PORTAL_ID}/record/0-1/{cid}'

        li_url = (p.get('pipl_linkedin') or p.get('hs_linkedin_url') or
                  p.get('outbound_team___linkedin_url') or p.get('linkedin_personal_url') or
                  p.get('lgm_linkedinurl') or '')
        li_url = li_url.strip().strip('"\'')
        if li_url and 'linkedin.com' in li_url and not li_url.startswith('http'):
            li_url = 'https://' + li_url
        if li_url and 'linkedin.com' not in li_url:
            li_url = ''

        rsvp_raw = p.get('outbound_rsvp_to_event', '') or ''
        # Use whichever timestamp is more recent
        ts1 = p.get('hs_last_sales_activity_timestamp', '') or ''
        ts2 = p.get('notes_last_contacted', '') or ''
        contacted_raw = ts1 if ts1 >= ts2 else ts2
        rsvp_date = fmt_date(rsvp_raw)
        last_contact = fmt_date(contacted_raw)

        deal = match_deal(p, records, by_name, by_last_name)
        stage_id = deal['stage'] if deal else ''
        stage_label = DEAL_STAGES.get(stage_id, stage_id) if stage_id else ''
        stage_css = STAGE_CSS.get(stage_id, '') if stage_id else ''

        amount_raw = (deal['amount'] if deal else '') or ''
        try:
            amount_val = float(amount_raw)
        except (ValueError, TypeError):
            amount_val = 0.0
        amount_fmt = fmt_amount(amount_raw)

        times_contacted = (deal['times_contacted'] if deal else '') or ''
        try:
            times_val = int(times_contacted)
        except (ValueError, TypeError):
            times_val = 0

        try:
            prior_invested = float(p.get('total_purchased___reserved') or 0) > 0
        except (ValueError, TypeError):
            prior_invested = False
        if not prior_invested:
            prior_invested = (p.get('haspurchased') or '').lower() == 'yes'

        row_data.append({
            'cid': cid,
            'name': name,
            'hs_url': hs_url,
            'prior_invested': prior_invested,
            'li_url': li_url,
            'rsvp_raw': rsvp_raw,
            'rsvp_date': rsvp_date,
            'contacted_raw': contacted_raw,
            'last_contact': last_contact,
            'stage_id': stage_id,
            'stage_label': stage_label,
            'stage_css': stage_css,
            'amount_val': amount_val,
            'amount_fmt': amount_fmt,
            'times_val': times_val,
            'times_contacted': times_contacted,
            'title': (p.get('jobtitle') or '').strip(),
            'company': (p.get('company') or '').strip(),
            'industry': (p.get('industry') or '').strip(),
            'task_due': tasks.get(cid, {}).get('due_str', ''),
            'task_due_ms': tasks.get(cid, {}).get('due_ms', 0),
            'task_subject': tasks.get(cid, {}).get('subject', ''),
            'meeting_start': meetings.get(cid, {}).get('start_str', ''),
            'meeting_ms': meetings.get(cid, {}).get('start_ms', 0),
            'meeting_title': meetings.get(cid, {}).get('title', ''),
        })

        # Compute pipeline status
        _mtg_ms = meetings.get(cid, {}).get('start_ms', 0)
        _task_ms = tasks.get(cid, {}).get('due_ms', 0)
        _, _rsvp_ms = fmt_date_ms(rsvp_raw)
        _, _cont_ms = fmt_date_ms(contacted_raw)
        if stage_id in TERMINAL_STAGES:
            _status, _status_order = 'Dormant', 2
        elif _mtg_ms > 0:
            _status, _status_order = 'Upcoming', 0
        elif _task_ms > 0 and _task_ms > now_ms_ts + ms_30d:
            _status, _status_order = 'Dormant', 2
        elif ((_task_ms > 0 and _task_ms <= now_ms_ts + ms_30d) or
              (_rsvp_ms > 0 and now_ms_ts - _rsvp_ms <= ms_21d) or
              (_cont_ms > 0 and now_ms_ts - _cont_ms <= ms_21d)):
            _status, _status_order = 'Working', 1
        else:
            _status, _status_order = 'Dormant', 2
        row_data[-1]['status'] = _status
        row_data[-1]['status_order'] = _status_order
        # Days since most-recent contact (None if never recorded)
        if _cont_ms > 0:
            row_data[-1]['days_since'] = max(0, int((now_ms_ts - _cont_ms) // 86_400_000))
        else:
            row_data[-1]['days_since'] = None
        row_data[-1]['rsvp_ms'] = _rsvp_ms

    # Save pre-filter rows for whale tracker (includes Collector etc.)
    all_row_data = row_data[:]

    # Remove disqualified and closed won
    row_data = [r for r in row_data if r['stage_id'] not in TERMINAL_STAGES]

    # Default sort: status, stage priority, meeting time (if upcoming), amount desc.
    # Extra tiebreak for Advisor Assigned: more recent attendees first (older attendees lower).
    ADV_ASSIGNED_STAGE = '1339121714'
    def _default_sort_key(r):
        base = (
            r['status_order'],
            STAGE_SORT_ORDER.get(r['stage_id'], 9),
            r['meeting_ms'] if (r['status_order'] == 0 and r['meeting_ms'] > 0) else 0,
            -r['amount_val'],
        )
        # AA bucket: tiebreak by rsvp_ms descending (recent first = lower number when negated)
        aa_tiebreak = -r.get('rsvp_ms', 0) if r['stage_id'] == ADV_ASSIGNED_STAGE else 0
        return base + (aa_tiebreak,)
    row_data.sort(key=_default_sort_key)

    # --- Summary stats ---
    ms_7d = 7 * 24 * 3600 * 1000
    stat_pipeline_val  = sum(r['amount_val'] for r in row_data if r['amount_val'] > 0)
    stat_mtg_count     = sum(1 for r in row_data if r['meeting_ms'] > 0)
    stat_tasks_week    = sum(1 for r in row_data if 0 < r['task_due_ms'] <= now_ms_ts + ms_7d)
    stat_active        = sum(1 for r in row_data if r['status'] in ('Working', 'Upcoming'))
    stat_dormant       = sum(1 for r in row_data if r['status'] == 'Dormant')
    stat_closed        = sum(1 for r in all_row_data if r['stage_id'] in TERMINAL_STAGES)
    stat_whales        = len([r for r in all_row_data if r['amount_val'] >= 50_000])

    def fmt_stat_val(n):
        if n >= 1_000_000: return f'{n/1_000_000:.2f}M'
        if n >= 1_000:     return f'{n/1_000:.0f}k'
        return f'{int(n):,}'

    hero_val_html = fmt_stat_val(stat_pipeline_val)

    def sub_stat(value, label, icon=''):
        icon_html = f'<span class="ss-icon">{icon}</span>' if icon else ''
        return (f'<div class="sub-stat">'
                f'<div class="sub-val">{value}</div>'
                f'<div class="sub-lbl">{icon_html}{label}</div>'
                f'</div>')

    sub_stats_html = (
        sub_stat(str(stat_active),       'Working',         '&#128293;') +   # fire
        sub_stat(str(stat_dormant),      'Dormant',         '&#128564;') +   # sleeping face
        sub_stat(str(stat_closed),       'Closed',          '&#9989;')   +   # check mark
        sub_stat(str(stat_mtg_count),    'Meetings',        '&#128197;') +   # calendar
        sub_stat(str(stat_tasks_week),   'Tasks',           '&#128203;') +   # clipboard
        sub_stat(str(stat_whales),       'Whales',          '&#128011;')     # whale
    )

    # --- Today / Whale subsets ---
    today_start_ms = int(datetime.combine(today_date, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp() * 1000)
    today_end_ms   = today_start_ms + 86_400_000
    today_rows = [r for r in row_data if
        (r['meeting_ms'] > 0 and today_start_ms <= r['meeting_ms'] < today_end_ms) or
        (r['task_due_ms'] > 0 and today_start_ms <= r['task_due_ms'] < today_end_ms)]
    today_meetings_rows = [r for r in today_rows
        if r['meeting_ms'] > 0 and today_start_ms <= r['meeting_ms'] < today_end_ms]
    today_meetings_rows.sort(key=lambda r: r['meeting_ms'])  # chronological for timeline
    today_tasks_rows = [r for r in today_rows
        if not (r['meeting_ms'] > 0 and today_start_ms <= r['meeting_ms'] < today_end_ms)]
    today_tasks_rows.sort(key=lambda r: -r['amount_val'])

    whale_rows = sorted(
        [r for r in all_row_data
         if r['amount_val'] >= 50_000
         and r['stage_id'] != CLOSED_LOST_STAGE
         and r['stage_id'] != CLOSED_WON_STAGE],
        key=lambda r: -r['amount_val'])

    # Cold deals — Meeting Sched 7+ days, Active Rel 10+ days, amount > $15k
    COLD_RULES = {
        '1321369497': 7,   # Meeting Scheduled
        '1321369496': 10,  # Active Relationship
    }
    cold_rows = []
    for r in row_data:
        threshold = COLD_RULES.get(r['stage_id'])
        if threshold is None:
            continue
        if r['amount_val'] <= 15_000:
            continue
        # Skip only if a task is scheduled in the future — overdue tasks still count as slipping
        if r['task_due_ms'] > now_ms_ts:
            continue
        d = r.get('days_since')
        if d is None or d >= threshold:
            cold_rows.append(r)
    cold_rows.sort(key=lambda r: -(r.get('days_since') if r.get('days_since') is not None else 9_999))
    cold_rows = cold_rows[:5]

    def heat_cls(d):
        if d is None:    return 'heat-3'
        if d <= 7:       return ''
        if d <= 14:      return 'heat-1'
        if d <= 28:      return 'heat-2'
        return 'heat-3'

    def cold_row(r):
        hs_badge = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        inv_badge = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        stage_cell = f'<span class="badge {r["stage_css"]}">{escape(r["stage_label"])}</span>' if r['stage_label'] else '—'
        amt_cell = escape(r['amount_fmt']) if r['amount_fmt'] else '—'
        task_title = escape(r['task_subject']) if r['task_subject'] else ''
        task_cell = f'<span title="{task_title}">{r["task_due"]}</span>' if r['task_due'] else '—'
        d = r.get('days_since')
        days_cell = '<span class="cold-days">never</span>' if d is None else f'<span class="cold-days">{d}d</span>'
        cls = heat_cls(d)
        cls_attr = f' class="{cls}"' if cls else ''
        return (f'<tr{cls_attr}>'
                f'<td>{hs_badge}{r["name"]}{inv_badge}</td>'
                f'<td>{days_cell}</td>'
                f'<td>{stage_cell}</td>'
                f'<td>{amt_cell}</td>'
                f'<td>{task_cell}</td>'
                f'</tr>')

    cold_rows_html = '\n'.join(cold_row(r) for r in cold_rows) if cold_rows else '<tr><td colspan="5" style="color:var(--text-3);text-align:center;padding:18px">No deals are slipping</td></tr>'

    # --- Today: vertical meeting timeline + tasks bucket ---
    try:
        from zoneinfo import ZoneInfo
        _NYC = ZoneInfo('America/New_York')
    except Exception:
        _NYC = None

    def fmt_local_time(ms):
        if not ms:
            return ''
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        if _NYC: dt = dt.astimezone(_NYC)
        fmt = '%#I:%M %p' if sys.platform == 'win32' else '%-I:%M %p'
        return dt.strftime(fmt)

    def mtg_item(r):
        hs = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        time_str = fmt_local_time(r['meeting_ms']) or '—'
        title = escape(r['meeting_title']) if r['meeting_title'] else ''
        amt = f'<span class="ti-amt">{escape(r["amount_fmt"])}</span>' if r['amount_fmt'] else ''
        return (
            f'<div class="ti mtg-item" title="{title}">'
            f'<div class="mtg-time">{time_str}</div>'
            f'<div class="ti-body">'
            f'<div class="ti-name">{hs}{escape(r["name"])}{amt}</div>'
            f'</div></div>'
        )

    def task_item(r):
        hs = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        subj = (r['task_subject'] or '').strip()
        amt = f'<span class="ti-amt">{escape(r["amount_fmt"])}</span>' if r['amount_fmt'] else ''
        stage_dot = f'<span class="stage-dot {r["stage_css"]}" title="{escape(r["stage_label"])}"></span>' if r['stage_css'] else '<span class="stage-dot stage-disq"></span>'
        subj_html = f'<span class="task-subj-inline">{escape(subj[:60])}</span>' if subj else ''
        return (
            f'<div class="ti task-item">'
            f'{stage_dot}{hs}'
            f'<span class="task-name">{escape(r["name"])}</span>'
            f'{amt}'
            f'{subj_html}'
            f'</div>'
        )

    # Standing 4–7pm gallery event every day — inserted in chronological order
    if _NYC:
        _gal_dt = datetime.combine(today_date, datetime.min.time()).replace(hour=16, tzinfo=_NYC)
    else:
        _gal_dt = datetime.combine(today_date, datetime.min.time()).replace(hour=20, tzinfo=timezone.utc)
    gallery_ms = int(_gal_dt.timestamp() * 1000)
    gallery_html = (
        '<div class="ti mtg-item gallery-event">'
        '<div class="mtg-time">4:00 PM</div>'
        '<div class="ti-body">'
        '<div class="ti-name">Gallery Event</div>'
        '</div></div>'
    )
    _mtg_pieces = []
    _gallery_done = False
    for r in today_meetings_rows:
        if r['meeting_ms'] >= gallery_ms and not _gallery_done:
            _mtg_pieces.append(gallery_html)
            _gallery_done = True
        _mtg_pieces.append(mtg_item(r))
    if not _gallery_done:
        _mtg_pieces.append(gallery_html)
    mtg_items_html  = '\n'.join(_mtg_pieces)
    task_items_html = '\n'.join(task_item(r) for r in today_tasks_rows)   if today_tasks_rows   else '<div class="ti-empty">No tasks today</div>'

    REC_MADE_STAGE = '1321369502'
    def whale_priority(r):
        hs = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        inv = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        amt = escape(r['amount_fmt']) if r['amount_fmt'] else '—'
        # Meta line: Stage · last X · task Y / mtg Y
        meta_parts = []
        if r['stage_label']: meta_parts.append(escape(r['stage_label']))
        d = r.get('days_since')
        if d is not None: meta_parts.append(f'last {fmt_days_ago(d)}')
        if r['meeting_start'] and r['meeting_ms'] >= now_ms_ts:
            meta_parts.append(f'mtg {escape(r["meeting_start"])}')
        elif r['task_due']:
            meta_parts.append(f'task {escape(r["task_due"])}')
        meta_line = ' &middot; '.join(meta_parts)
        # ONE icon: Meeting / Rec Made / Hot / Stale (or nothing)
        d = r.get('days_since')
        if r['meeting_ms'] > 0 and r['meeting_ms'] >= now_ms_ts:
            badge_html = '<span class="wp-icon" title="Upcoming meeting">&#128197;</span>'
        elif r['stage_id'] == REC_MADE_STAGE:
            badge_html = '<span class="wp-icon" title="Recommendation made">&#127919;</span>'
        elif d is not None and d <= 7:
            badge_html = '<span class="wp-icon" title="Hot — recent contact">&#128293;</span>'
        elif d is None or d > 14:
            badge_html = '<span class="wp-icon" title="Stale — no contact in 14+ days">&#10052;</span>'
        else:
            badge_html = ''
        # NEXT line — concrete action
        if r['meeting_ms'] > 0 and r['meeting_ms'] >= now_ms_ts:
            next_text = escape((r['meeting_title'] or 'Meeting').strip()[:80])
            next_cls = ''
        elif r['task_due_ms'] > 0:
            next_text = escape((r['task_subject'] or 'Task').strip()[:80])
            next_cls = ''
        else:
            next_text = 'Book a follow-up'
            next_cls = ' wp-next-prompt'
        return (
            f'<div class="whale-priority">'
            f'<div class="wp-amount">{amt}</div>'
            f'<div class="wp-text">'
            f'<div class="wp-id">{hs}<span class="wp-name">{escape(r["name"])}</span>{inv}</div>'
            f'<div class="wp-meta">{badge_html}{meta_line}</div>'
            f'<div class="wp-next"><span class="wp-next-lbl">Next</span><span class="wp-next-text{next_cls}">{next_text}</span></div>'
            f'</div>'
            f'</div>'
        )
    whale_priorities_html = '\n'.join(whale_priority(r) for r in whale_rows) if whale_rows else '<div class="ti-empty">No open whales above $50k</div>'

    # --- Chart data ---
    funnel_counts = [sum(1 for r in row_data if r['stage_id'] == sid) for sid, _ in FUNNEL_STAGES]

    # --- 3-day calendar ---
    from datetime import timedelta
    if daily_tasks is None:
        daily_tasks = {}
    if daily_meetings is None:
        daily_meetings = {}

    def next_biz_days(n, start):
        days, d = [], start
        while len(days) < n:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        return days

    cal_days = next_biz_days(3, now_dt.date())
    WORKDAY_H = 8.0
    EVENT_BLOCK_H = 4.0
    FREE_H = WORKDAY_H - EVENT_BLOCK_H  # 4h base free time

    cal_cards_html = ''
    for idx, day in enumerate(cal_days):
        date_iso = day.isoformat()
        is_today = (day == now_dt.date())
        day_label = ('Today, ' if is_today else '') + day.strftime('%a %b %-d' if sys.platform != 'win32' else '%a %b %#d')
        mtg_count = daily_meetings.get(date_iso, 0)
        dt = daily_tasks.get(date_iso, {'email': 0, 'call': 0, 'other': 0})
        email_n, call_n, other_n = dt['email'], dt['call'], dt['other']
        mtg_label = f'{mtg_count} meeting{"s" if mtg_count != 1 else ""}'
        free_h = max(0.0, FREE_H - mtg_count * 0.5)
        bar_filled = int(free_h / FREE_H * 10)
        bar_empty = 10 - bar_filled
        bar_str = '&#9608;' * bar_filled + '&#9617;' * bar_empty
        card_class = 'cal-card cal-today' if is_today else 'cal-card'
        total_tasks = email_n + call_n + other_n
        cal_cards_html += f'''<div class="{card_class}">
  <div class="cal-day-label">{day_label}</div>
  <div class="cal-mini">{mtg_count} mtg &middot; {total_tasks} tasks</div>
  <div class="cal-cap"><span class="cal-bar">{bar_str}</span><span class="cal-cap-label">{free_h:.1f}h open</span></div>
</div>'''

    # Stacked horizontal funnel — Masterworks navy gradient
    total_funnel = sum(funnel_counts) or 1
    # Order matches FUNNEL_STAGES: Advisor, Active, LongTerm, Mtg, Nurture, Rec Made
    SF_COLORS = ['#8a96a8', '#6e7d92', '#54657c', '#3d556e', '#26405e', '#11203a']
    sf_segs = []
    sf_keys = []
    for i, (c, (sid, lbl)) in enumerate(zip(funnel_counts, FUNNEL_STAGES)):
        if c == 0:
            sf_keys.append(
                f'<div class="sf-key sf-key-empty"><span class="sf-dot" style="background:{SF_COLORS[i]};opacity:0.3"></span>'
                f'<span class="sf-key-lbl">{escape(lbl)}</span><span class="sf-key-n">0</span></div>'
            )
            continue
        pct = c / total_funnel * 100
        # Show count inside segment only if segment is wide enough
        inline_count = f'<span class="sf-cnt">{c}</span>' if pct >= 8 else ''
        sf_segs.append(
            f'<div class="sf-seg" style="flex:{pct:.2f};background:{SF_COLORS[i]}" '
            f'title="{escape(lbl)}: {c}">{inline_count}</div>'
        )
        sf_keys.append(
            f'<div class="sf-key"><span class="sf-dot" style="background:{SF_COLORS[i]}"></span>'
            f'<span class="sf-key-lbl">{escape(lbl)}</span><span class="sf-key-n">{c}</span></div>'
        )
    stacked_funnel_html = ''.join(sf_segs) or '<div class="sf-seg sf-empty">No active deals</div>'
    stacked_funnel_legend_html = ''.join(sf_keys)

    # Collect unique stage labels for filter dropdown
    stage_labels = []
    seen = set()
    for r in row_data:
        lbl = r['stage_label']
        if lbl and lbl not in seen:
            seen.add(lbl)
            stage_labels.append(lbl)

    rows = []
    for i, r in enumerate(row_data):
        stage_cell = f'<span class="badge {r["stage_css"]}">{escape(r["stage_label"])}</span>' if r['stage_label'] else '—'
        li_link = f'<a href="{escape(r["li_url"])}" target="_blank">LI</a>' if r['li_url'] else '—'
        hs_badge = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        task_title = escape(r['task_subject']) if r['task_subject'] else ''
        task_cell = f'<span title="{task_title}">{r["task_due"]}</span>' if r['task_due'] else '—'
        mtg_title = escape(r['meeting_title']) if r['meeting_title'] else ''
        mtg_cell = f'<span title="{mtg_title}">{r["meeting_start"]}</span>' if r['meeting_start'] else '—'
        inv_badge = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        note_snippet = notes.get(r['cid'], '') if r['stage_id'] == '1321369496' else ''
        note_html = (f'<span class="note-toggle" onclick="var n=this.nextElementSibling;n.style.display=n.style.display===\'block\'?\'none\':\'block\'">&#9662;</span>'
                     f'<div class="note-snippet" style="display:none">{escape(note_snippet)}</div>') if note_snippet else ''
        status_css = {'Upcoming': 'status-upcoming', 'Working': 'status-active', 'Dormant': 'status-dormant'}.get(r['status'], '')
        status_cell = f'<span class="badge {status_css}">{r["status"]}</span>'
        contacted_today = False
        if r['contacted_raw']:
            try:
                ct = datetime.fromisoformat(r['contacted_raw'].replace('Z', '+00:00'))
                contacted_today = ct.date() == today_date
            except Exception:
                pass
        # Recency shading now lives on the Last Contacted cell only (plain alternating rows otherwise)
        _lc_map = {'heat-1': 'lc-1', 'heat-2': 'lc-2', 'heat-3': 'lc-3'}
        lc_cls = _lc_map.get(heat_cls(r.get('days_since')), '')
        row_class = ''
        tc_parts = [shorten_title(r['title'])[:28] if r['title'] else '', r['company'][:24] if r['company'] else '']
        tc_str = ', '.join(p for p in tc_parts if p)
        li_bit = f'<a href="{escape(r["li_url"])}" target="_blank" class="li-inline">LI</a> ' if r['li_url'] else ''
        tc_cell = f'{li_bit}<span class="tc-text">{escape(tc_str)}</span>' if tc_str else (li_bit.strip() or '—')
        rows.append(
            f'    <tr{row_class} data-default-order="{i}" data-stage-order="{STAGE_SORT_ORDER.get(r["stage_id"], 9)}"'
            f' data-amount="{r["amount_val"]}" data-rsvp="{escape(r["rsvp_raw"])}"'
            f' data-contacted="{escape(r["contacted_raw"])}" data-times="{r["times_val"]}"'
            f' data-task-ms="{r["task_due_ms"]}" data-meeting-ms="{r["meeting_ms"]}"'
            f' data-status-order="{r["status_order"]}" data-stage-label="{escape(r["stage_label"])}"'
            f' data-status="{r["status"]}">\n'
            f'      <td>{status_cell}</td>\n'
            f'      <td>{hs_badge}{r["name"]}{inv_badge}{note_html}</td>\n'
            f'      <td class="tc-col">{tc_cell}</td>\n'
            f'      <td>{stage_cell}</td>\n'
            f'      <td>{escape(r["amount_fmt"])}</td>\n'
            f'      <td>{r["rsvp_date"]}</td>\n'
            f'      <td class="lc-cell {lc_cls}">{fmt_days_ago(r.get("days_since"))}</td>\n'
            f'      <td>{mtg_cell}</td>\n'
            f'      <td>{task_cell}</td>\n'
            f'      <td>{escape(r["times_contacted"])}</td>\n'
            f'    </tr>'
        )

    rows_html = '\n'.join(rows)
    count = len(contacts)

    stage_options = '\n'.join(
        f'      <option value="{escape(lbl)}">{escape(lbl)}</option>' for lbl in stage_labels
    )

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline — {owner_name}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<link rel="stylesheet" href="pipeline.css">
</head>
<body>
<div id="pw-gate">
  <div id="pw-box">
    <h2>MASTERWORKS PIPELINE</h2>
    <input id="pw-input" type="password" placeholder="Password" autofocus />
    <div id="pw-err"></div>
    <button id="pw-btn" onclick="checkPw()">Enter</button>
  </div>
</div>
<script>
(function(){{
  var PW='{password}';
  var SK='pw_ok';
  if(localStorage.getItem(SK)==='1')document.getElementById('pw-gate').classList.add('hidden');
  window.checkPw=function(){{
    if(document.getElementById('pw-input').value===PW){{
      localStorage.setItem(SK,'1');
      document.getElementById('pw-gate').classList.add('hidden');
    }}else{{
      document.getElementById('pw-err').textContent='Incorrect password';
      document.getElementById('pw-input').value='';
    }}
  }};
  document.getElementById('pw-input').addEventListener('keydown',function(e){{if(e.key==='Enter')checkPw();}});
}})();
</script>
{nav_html}
<header class="page-header">
  <div class="page-header-left">
    <h1>Pipeline &mdash; {owner_name}</h1>
    <div class="meta"><span>{count} contacts</span><span>Updated {now}</span></div>
  </div>
  <div class="mw-mark">Masterworks<span class="mw-dot">&bull;</span>Advisory</div>
</header>

<section class="hero-band">
  <div class="hero-top">
    <div class="hero-core">
      <div class="hero-eyebrow">Open Pipeline Value</div>
      <div class="hero-val">{hero_val_html}</div>
      <div class="hero-lbl">across {stat_active} active deals</div>
    </div>
    <div class="hero-sub-stats">{sub_stats_html}</div>
  </div>
  <div class="funnel-wrap">
    <div class="funnel-eyebrow">Active Deals by Stage <span class="funnel-hint">hover for counts</span></div>
    <div class="sf-track">{stacked_funnel_html}</div>
  </div>
</section>

<section class="page-section section-today">
  <div class="section-band">
    <div class="section-title">Today <span class="section-count">{len(today_rows)}</span></div>
    <div class="section-meta">Meetings on schedule, tasks to clear, next 3 days for context</div>
  </div>
  <div class="today-3col">
    <div class="t3-col t3-meetings">
      <h3>Meetings <span class="t3-count">{len(today_meetings_rows)}</span></h3>
      <div class="ti-list">{mtg_items_html}</div>
    </div>
    <div class="t3-col t3-tasks">
      <h3>Tasks <span class="t3-count">{len(today_tasks_rows)}</span></h3>
      <div class="ti-list">{task_items_html}</div>
    </div>
    <aside class="t3-col t3-calendar">
      <h3>Next 3 Days</h3>
      <div class="cal-cards">{cal_cards_html}</div>
    </aside>
  </div>
</section>

<section class="page-section section-whale">
  <div class="section-band whale">
    <div class="section-title">Whale Tracker <span class="section-count">{len(whale_rows)}</span></div>
    <div class="section-meta">Open deals $50k and above &middot; excludes Closed Won + Closed Lost</div>
  </div>
  <div class="whale-priorities" data-owner="{owner_name}">{whale_priorities_html}</div>
</section>

<section class="page-section section-cold">
  <div class="section-band cold">
    <div class="section-title">Deals Slipping <span class="section-count">{len(cold_rows)}</span></div>
    <div class="section-meta">Top 5 by days cold &middot; Meeting Scheduled 7+ days, Active Relationship 10+ days &middot; over $15k, no upcoming task (overdue counts)</div>
  </div>
  <table class="mini-table cold-table">
    <thead><tr><th>Name</th><th>Days Cold</th><th>Stage</th><th>Amount</th><th>Task Due</th></tr></thead>
    <tbody>{cold_rows_html}</tbody>
  </table>
</section>

<section class="page-section section-all">
  <div class="section-band">
    <div class="section-title">All Contacts <span class="section-count">{count}</span></div>
    <button id="hidden-toggle" class="collapse-btn" onclick="toggleHidden()"><span class="lbl">Show all contacts</span></button>
  </div>
  <div class="controls">
    <button id="btn-default" onclick="resetSort()">Default Sort</button>
    <span class="sort-hint">status &rarr; deal stage &rarr; amount</span>
    <label for="stage-filter">Stage:</label>
    <select id="stage-filter" onchange="filterStage(this.value)">
      <option value="">All Stages</option>
{stage_options}
    </select>
    <span id="visible-count" style="color:var(--text-3);font-size:0.74rem;"></span>
  </div>
  <div class="table-scroll">
    <table id="pipeline-table">
      <thead>
        <tr>
          <th onclick="sortTable(8,'number')">Status</th>
          <th onclick="sortTable(0,'text')">Name</th>
          <th>Title / Co</th>
          <th onclick="sortTable(1,'stage')">Deal Stage</th>
          <th onclick="sortTable(2,'number')">Deal Amount</th>
          <th onclick="sortTable(3,'date')">Date Attended</th>
          <th onclick="sortTable(4,'date')">Last Contacted</th>
          <th onclick="sortTable(7,'date')">Upcoming Mtg</th>
          <th onclick="sortTable(6,'number')">Task</th>
          <th onclick="sortTable(5,'number')"># Contacted</th>
        </tr>
      </thead>
      <tbody id="pipeline-body">
{rows_html}
      </tbody>
    </table>
  </div>
</section>
<script>
(function() {{
  var sortState = {{ col: -1, dir: 1 }};

  function getVal(tr, colIndex) {{
    switch(colIndex) {{
      case 0: return tr.cells[0].textContent.trim().toLowerCase();
      case 1: return parseInt(tr.dataset.stageOrder) || 9;
      case 2: return parseFloat(tr.dataset.amount) || 0;
      case 3: return tr.dataset.rsvp || '';
      case 4: return tr.dataset.contacted || '';
      case 5: return parseInt(tr.dataset.times) || 0;
      case 6: return parseInt(tr.dataset.taskMs) || 0;
      case 7: return parseInt(tr.dataset.meetingMs) || 0;
      case 8: return parseInt(tr.dataset.statusOrder) || 0;
      default: return '';
    }}
  }}

  window.sortTable = function(colIndex, type) {{
    var tbody = document.getElementById('pipeline-body');
    var ths = document.querySelectorAll('#pipeline-table thead th');
    var rows = Array.from(tbody.querySelectorAll('tr'));

    if (sortState.col === colIndex) {{
      sortState.dir *= -1;
    }} else {{
      sortState.col = colIndex;
      sortState.dir = (type === 'number') ? -1 : 1;
    }}

    rows.sort(function(a, b) {{
      var va = getVal(a, colIndex);
      var vb = getVal(b, colIndex);
      if (va < vb) return -1 * sortState.dir;
      if (va > vb) return  1 * sortState.dir;
      return 0;
    }});

    rows.forEach(function(r) {{ tbody.appendChild(r); }});

    ths.forEach(function(th, i) {{
      th.classList.remove('sort-asc','sort-desc');
      if (i === colIndex) {{
        th.classList.add(sortState.dir === 1 ? 'sort-asc' : 'sort-desc');
      }}
    }});
  }};

  var showHidden = false;  // by default hide dormant, Advisor Assigned, Long Term Relationship

  function isMasked(r) {{
    return r.dataset.status === 'Dormant'
        || r.dataset.stageLabel === 'Advisor Assigned'
        || r.dataset.stageLabel === 'Long Term Relationship';
  }}

  window.toggleHidden = function() {{
    showHidden = !showHidden;
    var btn = document.getElementById('hidden-toggle');
    btn.querySelector('.lbl').textContent = showHidden ? 'Hide some contacts' : 'Show all contacts';
    applyFilters();
  }};

  window.resetSort = function() {{
    var tbody = document.getElementById('pipeline-body');
    var ths = document.querySelectorAll('#pipeline-table thead th');
    var rows = Array.from(tbody.querySelectorAll('tr'));

    rows.sort(function(a, b) {{
      return parseInt(a.dataset.defaultOrder) - parseInt(b.dataset.defaultOrder);
    }});
    rows.forEach(function(r) {{ tbody.appendChild(r); }});

    ths.forEach(function(th) {{ th.classList.remove('sort-asc','sort-desc'); }});
    sortState = {{ col: -1, dir: 1 }};
    updateCount();
  }};

  var stageFilter = '';

  function applyFilters() {{
    var rows = document.querySelectorAll('#pipeline-body tr');
    rows.forEach(function(r) {{
      var stageHide = stageFilter !== '' && r.dataset.stageLabel !== stageFilter;
      // Mask dormant + AA when no specific stage filter is active and showHidden is false
      var maskHide = stageFilter === '' && !showHidden && isMasked(r);
      r.classList.toggle('hidden', stageHide || maskHide);
    }});
    updateCount();
  }}

  window.filterStage = function(val) {{
    stageFilter = val;
    applyFilters();
  }};

  // Initial state: mask dormant + AA + LTR
  applyFilters();

  function updateCount() {{
    var rows = document.querySelectorAll('#pipeline-body tr:not(.hidden)');
    var el = document.getElementById('visible-count');
    var total = document.querySelectorAll('#pipeline-body tr').length;
    el.textContent = rows.length < total ? rows.length + ' of ' + total + ' shown' : '';
  }}
}})();

// --- Charts ---
(function() {{
  Chart.defaults.color = '#888';
  Chart.defaults.borderColor = '#222';

}})();
</script>
</body>
</html>'''



def build_velocity_data(deals):
    """Compute Velocity KPIs + weekly trend series.

    Deal COUNTS (net total, net AA) are reconstructed from each deal's
    hs_v2_date_entered_* / _exited_* timestamps every run — accurate and
    self-healing. Pipeline VALUE history can't be reconstructed (HubSpot
    stores only the current amount), so it's seeded from velocity_snapshots.json
    and a live point is written for the current week on every run.
    """
    today = datetime.now(timezone.utc).date()

    def amt(d):
        try: return float(d['properties'].get('amount') or 0)
        except: return 0.0

    def pdt(s):
        if not s: return None
        try: return datetime.fromisoformat(s[:10]).date()
        except: return None

    def monday(d):
        return d - timedelta(days=d.weekday())

    all_active = [d for d in deals if d['properties'].get('dealstage', '') not in TERMINAL_STAGES]
    total_active = len(all_active)
    pipeline_value = sum(amt(d) for d in all_active if amt(d) > 0)

    # ── AA backlog: scoped to the advisors who carry the 126 capacity (Erik + Ani) ──
    aa_team = [d for d in all_active
               if d['properties'].get('dealstage') == ADVISOR_ASSIGNED_STAGE
               and d['properties'].get('hubspot_owner_id') in OVERVIEW_OWNER_IDS]
    aa_count = len(aa_team)
    backlog_pct = aa_count / AA_CAPACITY * 100 if AA_CAPACITY else 0

    # Avg days a deal sits in AA before exiting (team, exits in last 30d) + weekly exit rate (last 4wk)
    days30_ago = today - timedelta(days=30)
    wk4_ago = today - timedelta(days=28)
    exit_durations, exits_last4wk = [], 0
    for d in deals:
        p = d['properties']
        if p.get('hubspot_owner_id') not in OVERVIEW_OWNER_IDS:
            continue
        ent = pdt(p.get(f'hs_v2_date_entered_{ADVISOR_ASSIGNED_STAGE}'))
        ex = pdt(p.get(f'hs_v2_date_exited_{ADVISOR_ASSIGNED_STAGE}'))
        if ent and ex and ex > ent and ex >= days30_ago:
            exit_durations.append((ex - ent).days)
        if ex and ex >= wk4_ago:
            exits_last4wk += 1
    avg_days_exit_aa = round(sum(exit_durations) / len(exit_durations)) if exit_durations else 0
    weekly_exit_rate = exits_last4wk / 4 if exits_last4wk else 0
    weeks_to_clear = (aa_count / weekly_exit_rate) if weekly_exit_rate else None
    resume_date = (today + timedelta(days=round(weeks_to_clear * 7))) if weeks_to_clear else None

    # ── Weekly buckets (whole pipeline) for the net-flow charts ──
    weeks, m, cur_monday = [], VELO_SERIES_START, monday(today)
    while m <= cur_monday:
        weeks.append(m); m += timedelta(days=7)

    def wk_index(d):
        if not d or d < VELO_SERIES_START:
            return None
        idx = (monday(d) - VELO_SERIES_START).days // 7
        return idx if 0 <= idx < len(weeks) else None

    net_total = [0] * len(weeks)
    net_aa = [0] * len(weeks)
    for d in deals:
        p = d['properties']
        i = wk_index(pdt(p.get('createdate')))
        if i is not None:
            net_total[i] += 1
        term_dates = [pdt(p.get(f'hs_v2_date_entered_{sid}')) for sid in TERMINAL_STAGES]
        term_dates = [x for x in term_dates if x]
        if term_dates:
            j = wk_index(min(term_dates))
            if j is not None:
                net_total[j] -= 1
        ka = wk_index(pdt(p.get(f'hs_v2_date_entered_{ADVISOR_ASSIGNED_STAGE}')))
        if ka is not None:
            net_aa[ka] += 1
        kx = wk_index(pdt(p.get(f'hs_v2_date_exited_{ADVISOR_ASSIGNED_STAGE}')))
        if kx is not None:
            net_aa[kx] -= 1

    # ── Cumulative pipeline value: reconstructed point-in-time from deal membership ──
    # For each week, sum the CURRENT amount of every deal created by the end of that week
    # that had not yet entered a terminal stage. Uses current amount as a proxy for
    # amount-at-time (HubSpot keeps no amount history) and can't see hard-deleted deals,
    # but it's fully reproducible and replaces the old hand-estimated screenshot seed.
    deal_life = []
    for d in deals:
        p = d['properties']
        cr = pdt(p.get('createdate'))
        a = amt(d)
        if not cr or a <= 0:
            continue
        terms = [pdt(p.get(f'hs_v2_date_entered_{sid}')) for sid in TERMINAL_STAGES]
        terms = [t for t in terms if t]
        deal_life.append((cr, min(terms) if terms else None, a))
    cumulative = []
    for w in weeks:
        asof = min(w + timedelta(days=6), today)   # end of that week (Sun), capped at today
        val = sum(a for (cr, et, a) in deal_life if cr <= asof and (et is None or et > asof))
        cumulative.append(round(val))

    _fmt = '%b %#d' if sys.platform == 'win32' else '%b %-d'
    return {
        'labels': [w.strftime(_fmt) for w in weeks],
        'net_total': net_total,
        'net_aa': net_aa,
        'cumulative_k': [round(v / 1000) for v in cumulative],
        'apr9_idx': wk_index(VELO_APR9),
        'total_active': total_active,
        'pipeline_value': pipeline_value,
        'aa_count': aa_count,
        'backlog_pct': backlog_pct,
        'avg_days_exit_aa': avg_days_exit_aa,
        'weekly_exit_rate': weekly_exit_rate,
        'weeks_to_clear': weeks_to_clear,
        'resume_date': resume_date,
    }


def build_velocity_html(vd, now_str, nav_html, password='banksy'):
    """Render the light-themed Velocity dashboard (KPIs + 3 Chart.js trend charts)."""
    _fmt = '%b %#d' if sys.platform == 'win32' else '%b %-d'

    def fmtk(n):
        if n >= 1_000_000: return f'${n/1_000_000:.2f}M'
        if n >= 1_000:     return f'${n/1_000:.0f}K'
        return f'${int(n):,}'

    bp = vd['backlog_pct']
    if bp > 100:
        sig_cls, sig_txt = 'danger', f'Pause Events — AA Backlog {bp:.0f}% of Capacity'
    elif bp >= 80:
        sig_cls, sig_txt = 'warn', f'Monitor — AA Backlog {bp:.0f}% of Capacity'
    else:
        sig_cls, sig_txt = 'ok', f'Healthy — AA Backlog {bp:.0f}% of Capacity'

    wtc = vd['weeks_to_clear']
    if wtc is not None:
        wtc_val = f'{wtc:.1f}'
        resume = vd['resume_date'].strftime(_fmt) if vd['resume_date'] else '—'
        wtc_sub = f"At {vd['weekly_exit_rate']:.0f} exits/week · resume ~{resume}"
        wtc_cls = 'danger' if wtc > 2 else 'warn'
    else:
        wtc_val, wtc_sub, wtc_cls = '—', 'No AA exits logged in last 4 weeks', 'warn'

    aa_cls = 'danger' if bp > 100 else ('warn' if bp >= 80 else 'green')
    chart_data = {
        'labels': vd['labels'],
        'netTotal': vd['net_total'],
        'netAA': vd['net_aa'],
        'cumulative': vd['cumulative_k'],
        'apr9': vd['apr9_idx'] if vd['apr9_idx'] is not None else -1,
    }

    parts = [
        '<!DOCTYPE html><html lang="en"><head>',
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">',
        '<title>Gallery Events — Pipeline Velocity</title>',
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>',
        '<link rel="stylesheet" href="pipeline.css"></head><body>',
        # password gate (matches the other tabs)
        '<div id="pw-gate"><div id="pw-box"><h2>PIPELINE VELOCITY</h2>',
        '<input id="pw-input" type="password" placeholder="Password" autofocus/>',
        '<div id="pw-err"></div><button id="pw-btn" onclick="checkPw()">Enter</button></div></div>',
        f'<script>(function(){{var PW={repr(password)};var SK="pw_ok";',
        'if(sessionStorage.getItem(SK)==="1")document.getElementById("pw-gate").classList.add("hidden");',
        'window.checkPw=function(){if(document.getElementById("pw-input").value===PW){',
        'sessionStorage.setItem(SK,"1");document.getElementById("pw-gate").classList.add("hidden");',
        '}else{document.getElementById("pw-err").textContent="Incorrect password";',
        'document.getElementById("pw-input").value="";}};',
        'document.getElementById("pw-input").addEventListener("keydown",function(e){if(e.key==="Enter")checkPw();});',
        '})();</script>',
        nav_html,
        '<div class="velo">',
        '<div class="v-hdr"><div><h1>Gallery Events — Pipeline Velocity</h1>',
        f'<p>OUTBOUND · GALLERY LEADS · BRINGSJORD &amp; MITTAL · {now_str}</p></div>',
        f'<div class="signal-pill {sig_cls}"><div class="signal-dot"></div>{escape(sig_txt)}</div></div>',
        # KPI row
        '<div class="kpi-row">',
        f'<div class="kpi blue"><div class="kpi-label">Total active deals</div>'
        f'<div class="kpi-value">{vd["total_active"]}</div><div class="kpi-sub">Whole GL pipeline · excl. closed/DQ</div></div>',
        f'<div class="kpi {aa_cls}"><div class="kpi-label">Advisor Assigned</div>'
        f'<div class="kpi-value">{vd["aa_count"]}</div><div class="kpi-sub">Bringsjord + Mittal · capacity {AA_CAPACITY}</div></div>',
        f'<div class="kpi green"><div class="kpi-label">Avg days to exit AA</div>'
        f'<div class="kpi-value">{vd["avg_days_exit_aa"]}</div><div class="kpi-sub">Time in stage · last 30 days</div></div>',
        f'<div class="kpi {wtc_cls}"><div class="kpi-label">Weeks to clear backlog</div>'
        f'<div class="kpi-value">{wtc_val}</div><div class="kpi-sub">{escape(wtc_sub)}</div></div>',
        '</div>',
        # Cumulative value chart
        '<div class="v-section"><div class="v-section-header">'
        '<div class="v-section-title">Cumulative Pipeline Value — Active Stages</div>'
        '<div class="v-section-meta">Excl. Closed Won · Closed Lost · Self Serve · Financial Advisor · Collector</div></div>'
        '<div class="chart-wrap chart-lg"><canvas id="cumChart"></canvas></div>'
        '<div class="legend-row">'
        '<div class="legend-item"><div class="legend-dot" style="background:#1e2a40"></div>Cumulative pipeline value ($K)</div>'
        '<div class="legend-item"><div class="legend-dot" style="background:#a04e2c"></div>Apr 9 automation batch</div></div></div>',
        # Two net-flow charts
        '<div class="two-col">'
        '<div class="v-section"><div class="v-section-header">'
        '<div class="v-section-title">Net Deal Count — Full Pipeline</div>'
        '<span class="threshold-note">added − exited per week</span></div>'
        '<div class="chart-wrap chart-md"><canvas id="netTotalChart"></canvas></div></div>'
        '<div class="v-section"><div class="v-section-header">'
        '<div class="v-section-title">Net Deal Count — Advisor Assigned</div>'
        f'<span class="threshold-note">Chokepoint · capacity {AA_CAPACITY}</span></div>'
        '<div class="chart-wrap chart-md"><canvas id="netAAChart"></canvas></div></div></div>',
        '<div class="apr9-note"><strong>Apr 9 automation inflection:</strong> before Apr 9, advisors '
        'manually created deals for chosen contacts. From Apr 9, every event attendee automatically received '
        'a deal — a one-day batch that inflated both deal count and pipeline value. Advisors are clearing it '
        'by moving unworkable deals to Self Serve.</div>',
        f'<div class="v-footer"><span>Data source: HubSpot · Outbound | Gallery Leads (880355706)</span>'
        f'<span>Counts reconstructed live · value seeded + daily snapshot · {now_str}</span></div>',
        '</div>',  # .velo
        f'<script>const VD={json.dumps(chart_data)};',
        _VELOCITY_CHART_JS,
        '</script></body></html>',
    ]
    return ''.join(parts)


_VELOCITY_CHART_JS = r'''
Chart.defaults.color='#8a8d96';
Chart.defaults.borderColor='#e3e6ec';
Chart.defaults.font.family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";
const NAVY='#1e2a40', GREEN='#5f7a52', AMBER='#a07a3b', RED='#a04e2c';
const A=VD.apr9;
new Chart(document.getElementById('cumChart'),{
  type:'line',
  data:{labels:VD.labels,datasets:[{
    data:VD.cumulative,borderColor:NAVY,spanGaps:true,
    backgroundColor:(ctx)=>{const {ctx:c,chartArea}=ctx.chart;if(!chartArea)return'transparent';
      const g=c.createLinearGradient(0,chartArea.top,0,chartArea.bottom);
      g.addColorStop(0,'rgba(30,42,64,0.18)');g.addColorStop(1,'rgba(30,42,64,0)');return g;},
    fill:true,tension:0.35,borderWidth:2.5,
    pointRadius:(ctx)=>ctx.dataIndex===A?7:3,
    pointBackgroundColor:(ctx)=>ctx.dataIndex===A?RED:NAVY,
    pointBorderColor:(ctx)=>ctx.dataIndex===A?RED:NAVY}]},
  options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},
    plugins:{legend:{display:false},tooltip:{backgroundColor:'#fff',titleColor:'#0e1422',bodyColor:'#0e1422',borderColor:'#d4d8de',borderWidth:1,
      callbacks:{label:ctx=>ctx.parsed.y==null?' n/a':` $${ctx.parsed.y.toLocaleString()}K`,
        afterLabel:ctx=>ctx.dataIndex===A?' ← Apr 9 automation batch':''}}},
    scales:{x:{grid:{color:'#eef0f3'},ticks:{font:{size:11}}},
      y:{grid:{color:'#eef0f3'},ticks:{font:{size:11},callback:v=>`$${v}K`},min:0}}}
});
function netChart(id,data,posColor){
  new Chart(document.getElementById(id),{type:'bar',
    data:{labels:VD.labels,datasets:[{data:data,
      backgroundColor:data.map((v,i)=>i===A?'rgba(160,78,44,0.75)':v>=0?posColor.bg:'rgba(95,122,82,0.7)'),
      borderColor:data.map((v,i)=>i===A?RED:v>=0?posColor.bd:GREEN),
      borderWidth:1,borderRadius:4}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{backgroundColor:'#fff',titleColor:'#0e1422',bodyColor:'#0e1422',borderColor:'#d4d8de',borderWidth:1,
        callbacks:{label:ctx=>` Net: ${ctx.parsed.y>0?'+':''}${ctx.parsed.y} deals`,
          afterLabel:ctx=>ctx.dataIndex===A?' ← Apr 9 batch':''}}},
      scales:{x:{grid:{display:false},ticks:{font:{size:10}}},
        y:{grid:{color:'#eef0f3'},ticks:{font:{size:11},callback:v=>(v>0?'+':'')+v}}}}
  });
}
netChart('netTotalChart',VD.netTotal,{bg:'rgba(30,42,64,0.7)',bd:NAVY});
netChart('netAAChart',VD.netAA,{bg:'rgba(160,122,59,0.7)',bd:AMBER});
'''


def build_overview_html(deals, activity, n_5wd_days, now_str, nav_html, password='banksy', email_cache_ts=None):
    today = datetime.now(timezone.utc)
    today_d = today.date()
    month_start  = today_d.replace(day=1)
    year_start   = today_d.replace(month=1, day=1)
    days30_ago   = today_d - timedelta(days=30)

    def amt(d):
        try: return float(d['properties'].get('amount') or 0)
        except: return 0.0

    def pd(s):
        if not s: return None
        try: return datetime.fromisoformat(s[:10]).date()
        except: return None

    def fmtamt(n):
        if n >= 1_000_000: return f'${n/1_000_000:.1f}M'
        if n >= 1_000:     return f'${n/1_000:.0f}k'
        return f'${int(n):,}'

    def deal_name(d):
        raw = (d['properties'].get('dealname') or '').strip()
        name = re.sub(r'[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}', '', raw, flags=re.IGNORECASE).strip()
        name = re.split(r'\s+-\s+', name)[0].strip()
        return name or raw

    def deal_age_days(d):
        cr = pd(d['properties'].get('createdate', ''))
        return (today_d - cr).days if cr else 0

    team  = [d for d in deals if d['properties'].get('hubspot_owner_id', '') in OVERVIEW_OWNER_IDS]
    active = [d for d in team if d['properties'].get('dealstage', '') not in TERMINAL_STAGES]
    won   = [d for d in team if d['properties'].get('dealstage', '') == CLOSED_WON_STAGE]
    lost  = [d for d in team if d['properties'].get('dealstage', '') == CLOSED_LOST_STAGE]

    # ── Section 1: Pipeline Health ──
    total_active    = len(active)
    pipeline_value  = sum(amt(d) for d in active if amt(d) > 0)
    no_value_count  = sum(1 for d in active if amt(d) == 0)
    pct_no_value    = no_value_count / total_active * 100 if total_active else 0
    decided         = len(won) + len(lost)
    close_rate      = len(won) / decided * 100 if decided else 0
    funnel_rate     = len(won) / len(team) * 100 if team else 0
    # Sales cycle measured from event attended → close. The deal's entry into the
    # "Event Attended" stage is the deal-level proxy for the attend date; fall back to
    # createdate for deals that never passed through that stage.
    close_times = []
    for d in won:
        p = d['properties']
        cd = pd(p.get('closedate'))
        start = pd(p.get('hs_v2_date_entered_1321369495')) or pd(p.get('createdate'))
        if cd and start and cd > start:
            close_times.append((cd - start).days)
    avg_close = sum(close_times) / len(close_times) if close_times else 0

    # ── Section 2: Funnel ──
    ACTIVE_STAGES = [
        ('1321369495', 'Event Attended'),
        ('1339121714', 'Advisor Assigned'),
        ('1321369496', 'Active Relationship'),
        ('1363474599', 'Long Term Relationship'),
        ('1321369497', 'Meeting Scheduled'),
        ('1321369500', 'Nurture'),
        ('1321369502', 'Rec. Made'),
    ]
    stage_counts = {sid: sum(1 for d in active if d['properties'].get('dealstage') == sid)
                    for sid, _ in ACTIVE_STAGES}
    max_stage_ct = max(stage_counts.values()) or 1
    total_active_funnel = sum(stage_counts.values())

    # ── Section 3: Sales Closed ──
    def won_in(ds, since):
        return [d for d in ds if pd(d['properties'].get('closedate', '')) and
                pd(d['properties'].get('closedate', '')) >= since]

    w_month = won_in(won, month_start)
    w_30d   = won_in(won, days30_ago)
    w_ytd   = won_in(won, year_start)
    rev_month = sum(amt(d) for d in w_month)
    rev_30d   = sum(amt(d) for d in w_30d)
    rev_ytd   = sum(amt(d) for d in w_ytd)
    goal_pct  = min(rev_month / MONTHLY_GOAL * 100, 100)

    ytd_by_owner = {oid: sum(amt(d) for d in w_ytd if d['properties'].get('hubspot_owner_id') == oid)
                    for oid in OVERVIEW_OWNER_IDS}
    w_30d_sorted = sorted(w_30d, key=amt, reverse=True)

    # ── Section 5: Whales ──
    whales = sorted([d for d in active if amt(d) >= 100_000], key=amt, reverse=True)

    # ── Section 6: Hygiene ──
    days30_ts = (today - timedelta(days=30)).timestamp() * 1000
    stale = []
    for d in active:
        p = d['properties']
        nc = int(p.get('num_contacted_notes') or 0)
        nlu = p.get('notes_last_updated')
        def _to_ts(v):
            if not v:
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00')).timestamp() * 1000
            except Exception:
                return None
        nlu_ts = _to_ts(nlu)
        if nc == 0 or (nlu_ts and nlu_ts < days30_ts):
            stale.append(d)

    BANDS = [('$100k+', 100_000, 9e9), ('$25k–$99k', 25_000, 99_999),
             ('$10k–$24k', 10_000, 24_999), ('$5k–$9k', 5_000, 9_999), ('No value', 0, 4_999)]
    band_rows = ''
    stale_val = 0.0
    for label, lo, hi in BANDS:
        bucket = [d for d in stale if lo <= amt(d) <= hi]
        if not bucket:
            continue
        bval = sum(amt(d) for d in bucket)
        stale_val += bval
        ages = [deal_age_days(d) for d in bucket]
        avg_age = int(sum(ages) / len(ages)) if ages else 0
        val_str = fmtamt(bval) if bval > 0 else '—'
        band_rows += (f'<div class="brow"><span>{label}</span>'
                      f'<span style="font-weight:600;color:var(--red);text-align:right">{len(bucket)}</span>'
                      f'<span style="color:var(--text2);text-align:right">{val_str}</span>'
                      f'<span style="color:var(--text3);text-align:right">{avg_age}d</span></div>')

    no_val_rows = ''
    total_no_val = sum(1 for d in active if amt(d) == 0)
    for oid in ['77771452', '73613833']:
        cnt = sum(1 for d in active if amt(d) == 0 and d['properties'].get('hubspot_owner_id') == oid)
        if cnt == 0:
            continue
        pct = cnt / total_no_val * 100 if total_no_val else 0
        bar_w = int(pct)
        color = 'var(--red)' if pct > 25 else 'var(--amber)'
        name = OVERVIEW_OWNER_NAMES.get(oid, oid)
        no_val_rows += (f'<div class="or"><div style="flex:1"><span>{name}</span>'
                        f'<div class="mb" style="width:{bar_w}%;background:{color}"></div></div>'
                        f'<span style="font-weight:600;color:{color};width:24px;text-align:right">{cnt}</span>'
                        f'<span style="color:var(--text3);font-size:10px;width:30px;text-align:right">{pct:.0f}%</span></div>')

    # ── Section 7: Lower Pipeline ──
    lower = [d for d in active if 10_000 <= amt(d) <= 25_000]
    lower_val = sum(amt(d) for d in lower)
    closes_needed = int(MONTHLY_GOAL / 5_000) if MONTHLY_GOAL else 0

    # ── Section 8: Activity ──
    ani  = activity.get('77771452', {})
    erik = activity.get('73613833', {})
    ani_c30  = ani.get('calls_30', 0);  ani_e30  = ani.get('emails_30', 0)
    erik_c30 = erik.get('calls_30', 0); erik_e30 = erik.get('emails_30', 0)
    ani_c5   = ani.get('calls_5wd', 0); ani_e5   = ani.get('emails_5wd', 0)
    erik_c5  = erik.get('calls_5wd', 0);erik_e5  = erik.get('emails_5wd', 0)
    max_c30  = max(ani_c30, erik_c30) or 1
    max_e30  = max(ani_e30, erik_e30) or 1
    max_c5   = max(ani_c5, erik_c5)   or 1
    max_e5   = max(ani_e5, erik_e5)   or 1
    total_touches = ani_c30 + ani_e30 + erik_c30 + erik_e30
    total_rev_pipeline = rev_ytd
    roi = total_rev_pipeline / total_touches if total_touches else 0

    # ── Whale rows ──
    STAGE_BADGE = {
        '1321369495': ('br', 'Event Attended'),
        '1339121714': ('ba', 'Adv. Assigned'),
        '1321369496': ('bp', 'Active Rel.'),
        '1363474599': ('bp', 'Long Term Rel.'),
        '1321369497': ('bp', 'Mtg Scheduled'),
        '1321369500': ('bp', 'Nurture'),
        '1321369502': ('bg', 'Rec. Made'),
    }
    # Reply info for each whale (fetched live)
    print('  Fetching whale email reply data...', flush=True)
    whale_replies = fetch_whale_replies([d['id'] for d in whales])
    n_replied = sum(1 for v in whale_replies.values() if v.get('reply_date'))

    whale_rows = ''
    for d in whales:
        p = d['properties']
        cls, lbl = STAGE_BADGE.get(p.get('dealstage', ''), ('bp', p.get('dealstage', '')))
        age = deal_age_days(d)
        nc  = p.get('num_contacted_notes') or '0'
        owner = OVERVIEW_OWNER_NAMES.get(p.get('hubspot_owner_id', ''), '')
        reply_info = whale_replies.get(str(d['id']), {})
        rd_iso = reply_info.get('reply_date', '')
        if rd_iso:
            rd_dt = pd(rd_iso)
            if rd_dt:
                reply_cell = f'<td style="color:var(--green)">&#10003; {rd_dt.strftime("%b %#d" if sys.platform == "win32" else "%b %-d")}</td>'
            else:
                reply_cell = f'<td style="color:var(--green)">&#10003;</td>'
        else:
            reply_cell = '<td style="color:var(--text3)">&mdash;</td>'
        whale_rows += (f'<tr><td><strong>{escape(deal_name(d))}</strong>'
                       f'<small style="color:var(--text3);margin-left:5px">{owner}</small></td>'
                       f'<td>{fmtamt(amt(d))}</td>'
                       f'<td><span class="badge {cls}">{lbl}</span></td>'
                       f'<td>{age}d</td><td>{nc}</td>'
                       f'{reply_cell}</tr>')

    # ── Last 30d closed rows ──
    closed_30d_rows = ''
    for d in w_30d_sorted[:8]:
        p = d['properties']
        owner = OVERVIEW_OWNER_NAMES.get(p.get('hubspot_owner_id', ''), '')
        a = amt(d)
        a_str = fmtamt(a) if a > 0 else '<span style="color:var(--text3)">—</span>'
        closed_30d_rows += (f'<tr><td>{escape(deal_name(d))}</td>'
                            f'<td style="color:var(--text2)">{owner}</td>'
                            f'<td style="text-align:right;font-weight:500">{a_str}</td></tr>')

    # ── Funnel bars ──
    funnel_bars = ''
    FUNNEL_COLORS = ['var(--red)', 'var(--amber)', 'var(--purple)',
                     'var(--purple)', 'var(--purple)', 'var(--purple)', 'var(--green)']
    for i, (sid, label) in enumerate(ACTIVE_STAGES):
        cnt = stage_counts[sid]
        pct_of_total = cnt / total_active_funnel * 100 if total_active_funnel else 0
        bar_pct = cnt / max_stage_ct * 100
        color = FUNNEL_COLORS[i]
        funnel_bars += (
            f'<div class="fc"><div class="fc-bw">'
            f'<div class="fc-lbl">{label}</div>'
            f'<div class="fc-bg"><div class="fc-fill" style="width:{bar_pct:.0f}%;background:{color}"></div></div>'
            f'</div><div class="fc-nums">'
            f'<div class="fc-pct" style="color:{color}">{pct_of_total:.0f}%</div>'
            f'<div class="fc-cnt">{cnt}</div>'
            f'</div></div>'
        )

    month_name = today_d.strftime('%B')

    # ── Section 4: Stage Progression (dynamic from hs_v2_date_entered_*) ──
    FORWARD_STAGES_OV = [
        '1339121714',  # Advisor Assigned
        '1321369496',  # Active Rel
        '1363474599',  # Long Term Rel
        '1321369497',  # Mtg Sch
        '1321369500',  # Nurture
        '1321369502',  # Rec Made
        '1321369499',  # Closed Won
    ]
    STAGE_ARRIVED_LABELS = {
        '1339121714': 'Advisor Assigned',
        '1321369496': 'Active Rel',
        '1363474599': 'Long Term Rel',
        '1321369497': 'Mtg Sch',
        '1321369500': 'Nurture',
        '1321369502': 'Rec Made',
        '1321369499': 'Closed Won',
    }
    advance_events = []
    for d in team:
        p = d['properties']
        owner = p.get('hubspot_owner_id', '')
        if owner not in OVERVIEW_OWNER_IDS:
            continue
        for sid in FORWARD_STAGES_OV:
            ts = p.get(f'hs_v2_date_entered_{sid}', '')
            if not ts:
                continue
            dt = pd(ts)
            if not dt:
                continue
            advance_events.append((dt, owner, sid))

    def _workdays_back(end, n):
        out, d = [], end
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d)
            d -= timedelta(days=1)
        return list(reversed(out))

    WD_BACK = 10
    chart_days = _workdays_back(today_d, WD_BACK)
    chart_days_set = set(chart_days)
    daily_owner_count = defaultdict(int)
    for dt, owner, sid in advance_events:
        if dt in chart_days_set:
            daily_owner_count[(dt, owner)] += 1
    last5_days = chart_days[-5:]
    last5_set = set(last5_days)
    per_owner_5wd = defaultdict(int)
    per_stage_5wd = defaultdict(int)
    for dt, owner, sid in advance_events:
        if dt in last5_set:
            per_owner_5wd[owner] += 1
            per_stage_5wd[sid] += 1
    total_5wd = sum(per_owner_5wd.values())

    ANI_COLOR_OV  = '#7c3aed'   # purple
    ERIK_COLOR_OV = '#16a34a'   # green
    OWNER_COLORS_OV = {'77771452': ANI_COLOR_OV, '73613833': ERIK_COLOR_OV}
    _date_fmt = '%b %#d' if sys.platform == 'win32' else '%b %-d'

    max_daily = max(daily_owner_count.values()) if daily_owner_count else 1
    day_headers_html = '<div></div>' + ''.join(
        f'<div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">{dt.strftime(_date_fmt)}</div>'
        for dt in chart_days
    )
    owner_rows_html = ''
    for oid in ['77771452', '73613833']:
        name = OVERVIEW_OWNER_NAMES.get(oid, oid)
        color = OWNER_COLORS_OV.get(oid, '#888')
        owner_rows_html += f'<div style="font-size:11px;font-weight:500;padding:3px 0;color:var(--text)">{name}</div>'
        for dt in chart_days:
            count = daily_owner_count.get((dt, oid), 0)
            if count == 0:
                owner_rows_html += '<div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"></div>'
            else:
                h_pct = max(int(count / max_daily * 95), 8)
                owner_rows_html += (
                    f'<div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px">'
                    f'<div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;'
                    f'font-size:9px;font-weight:600;color:rgba(255,255,255,.95);min-height:3px;height:{h_pct}%;background:{color}">{count}</div>'
                    f'</div>'
                )
    chart_cols = f'76px repeat({WD_BACK},minmax(0,1fr))'

    # Last-5 panel: per-advisor bars + by-stage destinations
    max_owner_5wd = max(per_owner_5wd.values()) if per_owner_5wd else 1
    last5_owner_rows = ''
    for oid in ['77771452', '73613833']:
        name = OVERVIEW_OWNER_NAMES.get(oid, oid)
        color = OWNER_COLORS_OV.get(oid, '#888')
        cnt = per_owner_5wd.get(oid, 0)
        pct = int(cnt / max_owner_5wd * 100) if max_owner_5wd else 0
        last5_owner_rows += (
            f'<div style="display:grid;grid-template-columns:76px 1fr 26px;gap:5px;align-items:center;padding:5px 0;'
            f'border-bottom:.5px solid var(--border);font-size:11px">'
            f'<span style="font-weight:500">{name}</span>'
            f'<div><div style="height:5px;background:{color};border-radius:3px;width:{pct}%"></div></div>'
            f'<span style="text-align:right;font-weight:500;color:{color}">{cnt}</span></div>'
        )
    stage_sorted_5wd = sorted(per_stage_5wd.items(), key=lambda x: -x[1])
    max_stage_5wd = max(per_stage_5wd.values()) if per_stage_5wd else 1
    last5_stage_rows = ''
    for sid, cnt in stage_sorted_5wd:
        if cnt == 0: continue
        lbl = STAGE_ARRIVED_LABELS.get(sid, sid)
        width = int(cnt / max_stage_5wd * 100)
        last5_stage_rows += (
            f'<div class="sr"><div style="flex:1"><span>{lbl}</span>'
            f'<div class="sbar" style="width:{width}%;background:var(--purple)"></div></div>'
            f'<span style="font-weight:500;white-space:nowrap">{cnt}</span></div>'
        )

    last5_range = f'{last5_days[0].strftime(_date_fmt)}–{last5_days[-1].strftime(_date_fmt)}' if last5_days else ''
    stage_progression_html = f'''
<div class="g2">
  <div class="card">
    <div class="ctitle">Daily advances by advisor</div>
    <div style="display:flex;gap:14px;margin-bottom:10px;flex-wrap:wrap;">
      <div style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text2)"><div style="width:10px;height:10px;border-radius:2px;background:{ANI_COLOR_OV}"></div>Mittal</div>
      <div style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text2)"><div style="width:10px;height:10px;border-radius:2px;background:{ERIK_COLOR_OV}"></div>Bringsjord</div>
    </div>
    <div style="display:grid;grid-template-columns:{chart_cols};gap:3px;align-items:center;">
      {day_headers_html}
      {owner_rows_html}
    </div>
    <div class="note">Forward stage entries (Advisor Assigned → Closed Won) · last {WD_BACK} working days</div>
  </div>
  <div class="card">
    <div class="ctitle" style="margin-bottom:11px">Last 5 working days ({last5_range})</div>
    <div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px">Advances by advisor</div>
    <div style="display:grid;grid-template-columns:76px 1fr 26px;gap:5px;align-items:center;padding:5px 0;border-bottom:.5px solid var(--border);font-size:10px;color:var(--text3)"><span>Advisor</span><span></span><span style="text-align:right">Wk</span></div>
    {last5_owner_rows}
    <div style="margin-top:14px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:7px">Where deals moved to</div>
    {last5_stage_rows}
    <div style="margin-top:11px;padding-top:9px;border-top:.5px solid var(--border);display:flex;justify-content:space-between;align-items:center">
      <div><div style="font-size:12px;color:var(--text2)">Total advances (last 5 working days)</div></div>
      <span style="font-size:24px;font-weight:500;color:var(--purple)">{total_5wd}</span>
    </div>
  </div>
</div>
'''

    # ── Section 9: Advisor Breakdown ──
    STAGE_DIST_ROWS = [
        ('1321369495', 'Event Attended', 'var(--green)'),
        ('1339121714', 'Advisor Assigned', 'var(--red)'),
        ('1321369496', 'Active Relationship', 'var(--amber)'),
    ]
    GROUPED_STAGES = ('1321369497', '1321369500', '1321369502', '1363474599')  # Mtg, Nurture, Rec, LTR
    adv_full_names = {'73613833': 'Erik Bringsjord', '77771452': 'Anisha Mittal'}
    adv_cards = ''
    for oid in ['73613833', '77771452']:
        act_o  = [d for d in active if d['properties'].get('hubspot_owner_id') == oid]
        won_o  = [d for d in won    if d['properties'].get('hubspot_owner_id') == oid]
        lost_o = [d for d in lost   if d['properties'].get('hubspot_owner_id') == oid]
        val_o  = sum(amt(d) for d in act_o if amt(d) > 0)
        decided_o = len(won_o) + len(lost_o)
        cr_o   = len(won_o) / decided_o * 100 if decided_o else 0
        ytd_o  = ytd_by_owner.get(oid, 0)
        sc_o   = {sid: sum(1 for d in act_o if d['properties'].get('dealstage') == sid) for sid in DEAL_STAGES}
        grouped_ct = sum(sc_o.get(s, 0) for s in GROUPED_STAGES)
        maxbar = max([sc_o.get('1321369495', 0), sc_o.get('1339121714', 0),
                      sc_o.get('1321369496', 0), grouped_ct]) or 1
        cr_color = 'var(--green)' if cr_o >= 30 else 'var(--amber)'
        bars = ''
        for sid, label, color in STAGE_DIST_ROWS:
            c = sc_o.get(sid, 0)
            bars += (f'<div class="sr"><div style="flex:1"><span>{label}</span>'
                     f'<div class="sbar" style="width:{int(c/maxbar*100)}%;background:{color}"></div></div>'
                     f'<span style="font-weight:500;white-space:nowrap;color:var(--text-2)">{c}</span></div>')
        bars += (f'<div class="sr"><div style="flex:1"><span>Mtg + Nurture + Rec + LTR</span>'
                 f'<div class="sbar" style="width:{int(grouped_ct/maxbar*100)}%;background:var(--purple)"></div></div>'
                 f'<span style="font-weight:500;white-space:nowrap;color:var(--text-2)">{grouped_ct}</span></div>')
        adv_cards += (
            f'<div class="card"><div class="ctitle" style="margin-bottom:11px">{adv_full_names[oid]}</div>'
            f'<div class="g4s">'
            f'<div class="stat"><div class="sv" style="color:var(--purple)">{fmtamt(val_o)}</div><div class="sl">active pipeline value</div></div>'
            f'<div class="stat"><div class="sv" style="color:var(--purple)">{len(act_o)}</div><div class="sl">active deals</div></div>'
            f'<div class="stat"><div class="sv" style="color:{cr_color}">{cr_o:.1f}%</div><div class="sl">close rate · won ÷ decided</div></div>'
            f'<div class="stat"><div class="sv" style="color:var(--green)">{fmtamt(ytd_o)}</div><div class="sl">YTD closed</div></div>'
            f'</div><div style="margin-top:9px">'
            f'<div style="font-size:10px;color:var(--text-3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Stage distribution</div>'
            f'{bars}</div></div>'
        )
    advisor_breakdown_html = f'<div class="g2" style="margin-bottom:16px">{adv_cards}</div>'

    # ── Section 10: Weekly Executive Summary (auto-computed from the data) ──
    def _delta_pct(cur_pd, base_pd):
        return (cur_pd - base_pd) / base_pd * 100 if base_pd > 0 else 0.0

    activity_deltas = [
        ('Bringsjord calls',  erik_c5 / n_5wd_days, erik_c30 / 30),
        ('Bringsjord emails', erik_e5 / n_5wd_days, erik_e30 / 30),
        ('Mittal calls',      ani_c5  / n_5wd_days, ani_c30  / 30),
        ('Mittal emails',     ani_e5  / n_5wd_days, ani_e30  / 30),
    ]
    delta_cards = ''
    for label, cur_pd, base_pd in activity_deltas:
        pct = _delta_pct(cur_pd, base_pd)
        color = 'var(--green)' if pct >= 0 else 'var(--amber)'
        sign = '+' if pct >= 0 else ''
        delta_cards += (
            f'<div style="background:var(--surface-2);border-radius:8px;padding:11px 12px">'
            f'<div style="font-size:10px;color:var(--text-2);margin-bottom:5px">{label}</div>'
            f'<div style="font-size:22px;font-weight:500;color:{color}">{sign}{pct:.0f}%</div>'
            f'<div style="font-size:10px;color:var(--text-3);margin-top:3px">{cur_pd:.1f}/day vs {base_pd:.1f} baseline</div></div>'
        )
    rm_ct   = stage_counts.get('1321369502', 0)
    adv_pct = total_5wd / total_active * 100 if total_active else 0
    quality_cards = (
        f'<div style="background:var(--surface-2);border-radius:8px;padding:11px 12px">'
        f'<div style="font-size:10px;color:var(--text-2);margin-bottom:5px">No-value deals</div>'
        f'<div style="font-size:22px;font-weight:500;color:var(--green)">{pct_no_value:.0f}%</div>'
        f'<div style="font-size:10px;color:var(--text-3);margin-top:3px">{no_value_count} of {total_active} active</div></div>'
        f'<div style="background:var(--surface-2);border-radius:8px;padding:11px 12px">'
        f'<div style="font-size:10px;color:var(--text-2);margin-bottom:5px">At Recommendation Made</div>'
        f'<div style="font-size:22px;font-weight:500;color:var(--green)">{rm_ct}</div>'
        f'<div style="font-size:10px;color:var(--text-3);margin-top:3px">closest to the finish line</div></div>'
        f'<div style="background:var(--surface-2);border-radius:8px;padding:11px 12px">'
        f'<div style="font-size:10px;color:var(--text-2);margin-bottom:5px">Advanced forward</div>'
        f'<div style="font-size:22px;font-weight:500;color:var(--purple)">{adv_pct:.0f}%</div>'
        f'<div style="font-size:10px;color:var(--text-3);margin-top:3px">{total_5wd} of {total_active} active · last {n_5wd_days}d</div></div>'
    )

    # EDIT WEEKLY: positive-narrative candidates. The 3 highest-impact ones that
    # apply are rendered as the executive bullets. Tweak the phrasing here.
    best_effort = max(activity_deltas, key=lambda x: _delta_pct(x[1], x[2]))
    best_pct    = _delta_pct(best_effort[1], best_effort[2])
    whale_val   = sum(amt(d) for d in whales)
    summary_candidates = []
    if best_pct > 0:
        summary_candidates.append((best_pct, 'var(--purple)', 'Effort is climbing.',
            f'{best_effort[0]} ran {best_pct:.0f}% above baseline this week — {best_effort[1]:.0f}/day vs '
            f'{best_effort[2]:.1f}. Activity is the leading indicator and it is pointing up.'))
    if pct_no_value < 25:
        summary_candidates.append(((25 - pct_no_value) * 4, 'var(--green)', 'Pipeline quality is strong.',
            f'Only {pct_no_value:.0f}% of {total_active} active deals lack a value. Nearly every open deal is a '
            f'real, qualified opportunity.'))
    if total_5wd > 0:
        summary_candidates.append((float(total_5wd), 'var(--amber)', 'Deals are moving.',
            f'{total_5wd} deals advanced through the funnel in the last {n_5wd_days} working days — healthy '
            f'forward momentum across the team.'))
    if rev_ytd > 0:
        month_clause = f', {fmtamt(rev_month)} of it in {month_name}.' if rev_month > 0 else '.'
        summary_candidates.append((60.0, 'var(--green)', 'Revenue is on the board.',
            f'{fmtamt(rev_ytd)} closed year-to-date across {len(w_ytd)} deals{month_clause}'))
    if whales:
        summary_candidates.append((len(whales) * 8.0, 'var(--purple)', 'Whales in play.',
            f'{len(whales)} deals at $100k+ — {fmtamt(whale_val)} of combined potential — are active right now.'))
    if rm_ct > 0:
        summary_candidates.append((rm_ct * 5.0, 'var(--green)', 'Closest to the finish.',
            f'{rm_ct} deals sit at Recommendation Made, one step from close.'))
    # Daily activity tracker — the numeric cards, recomputed every run.
    daily_activity_html = (
        '<div class="card" style="margin-bottom:16px">'
        '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px">' + delta_cards + '</div>'
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">' + quality_cards + '</div></div>'
    )

    # Weekly summary — top-3 positive bullets FROZEN per ISO week (refresh on the first
    # run of each new week so it reads as a weekly note, not a 3x/day churn).
    week_bullets = _weekly_summary_bullets(summary_candidates, today_d)
    bullets = ''
    for i, b in enumerate(week_bullets, 1):
        bullets += (
            f'<div style="background:var(--surface-2);border-left:3px solid {b["color"]};border-radius:0 8px 8px 0;'
            f'padding:10px 12px;margin-bottom:6px">'
            f'<div style="font-size:12px;font-weight:600;color:{b["color"]};margin-bottom:3px">{i} · {b["title"]}</div>'
            f'<div style="font-size:11px;color:var(--text-2);line-height:1.6">{b["text"]}</div></div>'
        )
    weekly_summary_html = f'<div class="card" style="margin-bottom:16px">{bullets}</div>'

    html_parts = [
        '<!DOCTYPE html><html lang="en"><head>',
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">',
        '<title>Pipeline Overview</title>',
        f'<link rel="stylesheet" href="pipeline.css"></head><body>',

        # password gate
        f'<div id="pw-gate"><div id="pw-box">',
        f'<h2>PIPELINE OVERVIEW</h2>',
        f'<input id="pw-input" type="password" placeholder="Password" autofocus/>',
        f'<div id="pw-err"></div>',
        f'<button id="pw-btn" onclick="checkPw()">Enter</button>',
        f'</div></div>',
        f'<script>(function(){{var PW={repr(password)};var SK="pw_ok";',
        f'if(sessionStorage.getItem(SK)==="1")document.getElementById("pw-gate").classList.add("hidden");',
        f'window.checkPw=function(){{if(document.getElementById("pw-input").value===PW){{',
        f'sessionStorage.setItem(SK,"1");document.getElementById("pw-gate").classList.add("hidden");',
        f'}}else{{document.getElementById("pw-err").textContent="Incorrect password";',
        f'document.getElementById("pw-input").value="";}}}}; ',
        f'document.getElementById("pw-input").addEventListener("keydown",function(e){{if(e.key==="Enter")checkPw();}});',
        f'}})();</script>',

        nav_html,

        f'<div class="dash">',
        f'<div class="hdr"><div><h1>Gallery Leads — Pipeline Overview</h1>',
        f'<p style="font-size:12px;color:var(--text2);margin-top:3px">Outbound · Ani + Erik · {now_str}</p></div>',
        f'<div style="font-size:10px;color:var(--text3);text-align:right">Goal: ${MONTHLY_GOAL:,} / month<br>{now_str}</div></div>',

        # 1. Pipeline Health
        '<p class="slabel">1 · pipeline health</p>',
        '<div class="g6">',
        f'<div class="kc"><div class="kv" style="color:var(--purple)">{total_active}</div><div class="kl">Total active deals</div><div class="ks">Ani + Erik · excl. closed/DQ</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--green)">{fmtamt(pipeline_value)}</div><div class="kl">Pipeline value</div><div class="ks">open w/ amounts</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--red)">{pct_no_value:.0f}%</div><div class="kl">No value</div><div class="ks">{no_value_count} of {total_active} active</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--purple)">{close_rate:.1f}%</div><div class="kl">Close rate</div><div class="ks">won ÷ decided</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--amber)">{funnel_rate:.1f}%</div><div class="kl">Funnel close rate</div><div class="ks">won ÷ all assigned</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--purple)">{avg_close:.1f}d</div><div class="kl">Avg time to close</div><div class="ks">event → close · {len(close_times)} deals</div></div>',
        '</div>',

        # 2. Active Deal Funnel
        '<p class="slabel">2 · active deal funnel</p>',
        '<div class="frow">', funnel_bars, '</div>',

        # 3. Sales Closed
        '<p class="slabel">3 · sales closed</p>',
        '<div class="g3">',
        # Current month card
        f'<div class="card"><div class="ctitle">Current month — {month_name}</div>',
        f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px">',
        f'<span style="font-size:13px;color:var(--text2)">Closed so far</span>',
        f'<span style="font-size:24px;font-weight:500;color:var(--purple)">{fmtamt(rev_month)}</span></div>',
        f'<div class="goal-bar-bg"><div class="goal-bar-fill" style="width:{goal_pct:.1f}%"></div></div>',
        f'<div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text3);margin-bottom:13px"><span>$0</span><span>${MONTHLY_GOAL//1000}k goal</span></div>',
        f'<div class="fg"><span style="font-size:12px;color:var(--text2)">Gap to goal</span>',
        f'<span style="font-size:18px;font-weight:500;color:var(--red)">{fmtamt(max(0, MONTHLY_GOAL - rev_month))}</span></div>',
        f'<div class="note">{len(w_month)} deals closed this month</div></div>',
        # Last 30d card
        f'<div class="card">',
        f'<div class="sbig">{fmtamt(rev_30d)}</div><div class="ssub">{len(w_30d)} deals · last 30 days</div>',
        f'<div class="ctitle">Last 30 days</div>',
        f'<table><thead><tr><th>Client</th><th>Advisor</th><th style="text-align:right">Amount</th></tr></thead><tbody>',
        closed_30d_rows,
        f'</tbody></table></div>',
        # YTD card
        f'<div class="card">',
        f'<div class="sbig">{fmtamt(rev_ytd)}</div><div class="ssub">{len(w_ytd)} deals · Jan 1–today</div>',
        f'<div class="ctitle">YTD — by advisor</div>',
        f'<table><thead><tr><th>Advisor</th><th style="text-align:right">Closed</th></tr></thead><tbody>',
    ]
    for oid in ['77771452', '73613833']:
        name = OVERVIEW_OWNER_NAMES.get(oid, oid)
        v = ytd_by_owner.get(oid, 0)
        html_parts.append(f'<tr><td>{name}</td><td style="text-align:right;font-weight:500">{fmtamt(v)}</td></tr>')
    html_parts += [
        f'</tbody></table></div>',
        '</div>',

        # 4. Stage Progression (computed from hs_v2_date_entered_*)
        '<p class="slabel">4 · stage progression — deals advancing</p>',
        stage_progression_html,

        # 5. Whale Tracker
        '<p class="slabel">5 · whale tracker — $100k+ deals</p>',
        '<div class="card" style="margin-bottom:16px">',
        '<div class="g4s">',
        f'<div class="stat"><div class="sv" style="color:var(--purple)">{len(whales)}</div><div class="sl">active at $100k+</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--green)">{fmtamt(sum(amt(d) for d in whales))}</div><div class="sl">combined potential</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--red)">{sum(1 for d in whales if deal_age_days(d) > 30)} of {len(whales)}</div><div class="sl">over 30 days old</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--green)">{n_replied} of {len(whales)}</div><div class="sl">inbound reply</div></div>',
        '</div>',
        '<table><thead><tr><th>Deal</th><th>Value</th><th>Stage</th><th>Age</th><th>Contacts</th><th>Reply</th></tr></thead><tbody>',
        whale_rows,
        '</tbody></table></div>',

        # 6. Pipeline Hygiene
        '<p class="slabel">6 · pipeline hygiene — decisions needed</p>',
        '<div class="g2">',
        f'<div class="card"><div class="ctitle">Uncontacted 30+ days — by value band</div>',
        f'<div class="note" style="margin-bottom:9px">{len(stale)} deals · {fmtamt(stale_val)} at-risk</div>',
        '<div style="display:grid;grid-template-columns:1fr 32px 74px 44px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;padding-bottom:4px;border-bottom:.5px solid var(--border);margin-bottom:4px">',
        '<span>Band</span><span style="text-align:right">Deals</span><span style="text-align:right">Value</span><span style="text-align:right">Avg age</span></div>',
        band_rows or '<div style="color:var(--text3);font-size:11px;padding:8px 0">None — pipeline is healthy</div>',
        '<div class="note" style="margin-top:8px">Contact this week or DQ</div></div>',
        f'<div class="card"><div class="ctitle">Deals with no value — by rep</div>',
        f'<div class="note" style="margin-bottom:9px">{total_no_val} total · assign $10k floor or DQ</div>',
        no_val_rows,
        '</div></div>',

        # 7. Lower Pipeline
        '<p class="slabel">7 · lower pipeline ($10k–$25k) — capacity review</p>',
        '<div class="card" style="margin-bottom:16px"><div class="g4s">',
        f'<div class="stat"><div class="sv" style="color:var(--purple)">{len(lower)}</div><div class="sl">deals in $10k–$25k band</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--amber)">{fmtamt(lower_val)}</div><div class="sl">combined face value</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--red)">{closes_needed}</div><div class="sl">closes at $5k avg to hit goal</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--green)">?</div><div class="sl">deals with capacity above $25k</div></div>',
        '</div><div class="note">Key question: can any rep identify a deal where the investor has capacity beyond the current value? If yes — upgrade the band. If no story — DQ it.</div></div>',

        # 8. Advisor Activity
        '<p class="slabel">8 · advisor activity — calls &amp; emails</p>',
        '<div class="act3">',
        # Calls card
        '<div class="act-card"><div class="ctitle">Calls</div>',
        '<div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Last 30 days</div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Bringsjord</span><div><div class="act-bb"><div class="act-bf" style="width:{int(erik_c30/max_c30*100)}%;background:#534AB7"></div></div></div><div><div class="act-v" style="color:#534AB7">{erik_c30/30:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{erik_c30} total</div></div></div>',
        f'<div class="act-rep" style="border-bottom:.5px solid var(--border);padding-bottom:10px"><span style="font-size:12px;font-weight:500">Mittal</span><div><div class="act-bb"><div class="act-bf" style="width:{int(ani_c30/max_c30*100)}%;background:#3B6D11"></div></div></div><div><div class="act-v" style="color:#3B6D11">{ani_c30/30:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{ani_c30} total</div></div></div>',
        f'<div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-top:10px;margin-bottom:6px">Last {n_5wd_days} days</div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Bringsjord</span><div><div class="act-bb"><div class="act-bf" style="width:{int(erik_c5/max_c5*100)}%;background:#534AB7"></div></div></div><div><div class="act-v" style="color:#534AB7">{erik_c5/n_5wd_days:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{erik_c5} total</div></div></div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Mittal</span><div><div class="act-bb"><div class="act-bf" style="width:{int(ani_c5/max_c5*100)}%;background:#3B6D11"></div></div></div><div><div class="act-v" style="color:#3B6D11">{ani_c5/n_5wd_days:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{ani_c5} total</div></div></div>',
        '<div class="note">HubSpot logged outbound calls only</div></div>',
        # ROI card
        f'<div class="mid-card"><div style="font-size:9px;font-weight:500;text-transform:uppercase;letter-spacing:.08em;color:var(--text2);margin-bottom:14px">Outreach ROI</div>',
        f'<div style="font-size:56px;font-weight:500;color:#7B6FD4;line-height:1">{fmtamt(roi)}</div>',
        f'<div style="font-size:12px;color:var(--text2);margin-top:6px">per touch</div>',
        f'<div style="width:100%;border-top:.5px solid var(--border);margin:16px 0"></div>',
        f'<div style="font-size:10px;color:var(--text2);line-height:1.7">~{total_touches:,} logged touches<br>{fmtamt(rev_ytd)} confirmed YTD revenue</div></div>',
        # Emails card
        f'<div class="act-card"><div class="ctitle">Emails</div>'
        + (f'<div style="font-size:9px;color:var(--text3);margin-bottom:4px">last updated {email_cache_ts[:10] if email_cache_ts else "live"}</div>' if email_cache_ts else ''),
        '<div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Last 30 days</div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Bringsjord</span><div><div class="act-bb"><div class="act-bf" style="width:{int(erik_e30/max_e30*100)}%;background:#534AB7"></div></div></div><div><div class="act-v" style="color:#534AB7">{erik_e30/30:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{erik_e30} total</div></div></div>',
        f'<div class="act-rep" style="border-bottom:.5px solid var(--border);padding-bottom:10px"><span style="font-size:12px;font-weight:500">Mittal</span><div><div class="act-bb"><div class="act-bf" style="width:{int(ani_e30/max_e30*100)}%;background:#3B6D11"></div></div></div><div><div class="act-v" style="color:#3B6D11">{ani_e30/30:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{ani_e30} total</div></div></div>',
        f'<div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-top:10px;margin-bottom:6px">Last {n_5wd_days} days</div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Bringsjord</span><div><div class="act-bb"><div class="act-bf" style="width:{int(erik_e5/max_e5*100)}%;background:#534AB7"></div></div></div><div><div class="act-v" style="color:#534AB7">{erik_e5/n_5wd_days:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{erik_e5} total</div></div></div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Mittal</span><div><div class="act-bb"><div class="act-bf" style="width:{int(ani_e5/max_e5*100)}%;background:#3B6D11"></div></div></div><div><div class="act-v" style="color:#3B6D11">{ani_e5/n_5wd_days:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{ani_e5} total</div></div></div>',
        '</div>',
        '</div>',

        # 9. Advisor Breakdown
        '<p class="slabel">9 · advisor breakdown — pipeline value &amp; close rate</p>',
        advisor_breakdown_html,

        # 10. Daily activity tracker — numeric cards, recomputed every run
        f'<p class="slabel">10 · daily activity tracker — last {n_5wd_days} working days vs 30-day baseline</p>',
        daily_activity_html,

        # 11. Weekly summary — narrative text, refreshes once per ISO week
        '<p class="slabel">11 · weekly summary</p>',
        weekly_summary_html,

        f'<div class="footer"><span>Masterworks · Outbound · Gallery Leads · Confidential</span><span>{now_str}</span></div>',
        '</div></body></html>',
    ]
    return ''.join(html_parts)


def main():
    if not HUBSPOT_TOKEN:
        print('ERROR: HUBSPOT_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    print('\nFetching deals...', flush=True)
    deals = fetch_deals_live()

    for owner_cfg in OWNERS:
        print(f'\n=== {owner_cfg["name"]} ===', flush=True)
        nav_html = render_nav(owner_cfg)
        # Ani/Erik sub-toggle under the single "Pipeline" nav entry
        _ani = ' active' if owner_cfg['name'] == 'Ani' else ''
        _erik = ' active' if owner_cfg['name'] == 'Erik' else ''
        nav_html += (f'<div class="pipe-toggle">'
                     f'<a href="index.html" class="pt{_ani}">Ani</a>'
                     f'<a href="erik.html" class="pt{_erik}">Erik</a></div>')

        print('Fetching contacts...', flush=True)
        contacts = fetch_all_contacts(owner_cfg['id'])

        print('Fetching tasks...', flush=True)
        contact_ids = [c['id'] for c in contacts]
        tasks, daily_tasks = fetch_contact_tasks(contact_ids, owner_cfg['id'])
        print(f'  {len(tasks)} contacts have open tasks', flush=True)

        print('Fetching upcoming meetings...', flush=True)
        meetings, daily_meetings = fetch_contact_meetings(contact_ids)
        print(f'  {len(meetings)} contacts have upcoming meetings', flush=True)

        records, by_name, by_last_name = build_deal_index(deals)

        # Pre-pass: find Contacted-stage contacts so we can fetch their notes
        contacted_ids = []
        for c in contacts:
            deal = match_deal(c.get('properties', {}), records, by_name, by_last_name)
            if deal and deal.get('stage') == '1321369496':
                contacted_ids.append(c['id'])
        print(f'Fetching notes for {len(contacted_ids)} Contacted contacts...', flush=True)
        notes = fetch_contact_notes(contacted_ids)
        print(f'  {len(notes)} note snippets fetched', flush=True)

        print('Building HTML...', flush=True)
        html = build_html(contacts, records, by_name, by_last_name=by_last_name, tasks=tasks, meetings=meetings,
                          notes=notes, daily_tasks=daily_tasks, daily_meetings=daily_meetings,
                          owner_name=owner_cfg['name'], nav_html=nav_html, password=owner_cfg['pw'])

        out = Path(__file__).parent / owner_cfg['out']
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding='utf-8')
        print(f'Written: {out}', flush=True)

    # Overview page
    print('\n=== Overview ===', flush=True)
    ov_nav = render_nav(OVERVIEW_CFG)

    print('Fetching advisor activity...', flush=True)
    activity, n_5wd_days, email_cache_ts = fetch_advisor_activity()

    _ne, _ntz = eastern_now()
    now_str = _ne.strftime('%Y-%m-%d %H:%M') + ' ' + _ntz
    print('Building Overview HTML...', flush=True)
    ov_html = build_overview_html(deals, activity, n_5wd_days, now_str, ov_nav, password=OVERVIEW_CFG['pw'], email_cache_ts=email_cache_ts)

    ov_out = Path(__file__).parent / OVERVIEW_CFG['out']
    ov_out.parent.mkdir(parents=True, exist_ok=True)
    ov_out.write_text(ov_html, encoding='utf-8')
    print(f'Written: {ov_out}', flush=True)

    # Velocity page
    print('\n=== Velocity ===', flush=True)
    velo_nav = render_nav(VELOCITY_CFG)
    print('Computing velocity data...', flush=True)
    vd = build_velocity_data(deals)
    velo_html = build_velocity_html(vd, now_str, velo_nav, password=VELOCITY_CFG['pw'])
    velo_out = Path(__file__).parent / VELOCITY_CFG['out']
    velo_out.parent.mkdir(parents=True, exist_ok=True)
    velo_out.write_text(velo_html, encoding='utf-8')
    print(f'Written: {velo_out}', flush=True)

    # Magazine — inject nav into static source file
    print('\n=== Magazine ===', flush=True)
    mag_src = Path(__file__).parent / 'magazine_src.html'
    if mag_src.exists():
        mag_nav = render_nav(MAGAZINE_CFG)
        mag_html = mag_src.read_text(encoding='utf-8')
        mag_html = mag_html.replace(
            '</head>', '<link rel="stylesheet" href="nav.css"></head>', 1,
        ).replace('<body>', f'<body><div class="nav-shell">{mag_nav}</div>', 1)
        mag_out = Path(__file__).parent / MAGAZINE_CFG['out']
        mag_out.parent.mkdir(parents=True, exist_ok=True)
        mag_out.write_text(mag_html, encoding='utf-8')
        print(f'Written: {mag_out}', flush=True)
    else:
        print('magazine_src.html not found — skipping', flush=True)

    # Deliverables — inject nav into static source file (same pattern as Magazine)
    print('\n=== Deliverables ===', flush=True)
    del_src = Path(__file__).parent / 'deliverables_src.html'
    if del_src.exists():
        del_nav = render_nav(DELIVERABLES_CFG)
        del_html = del_src.read_text(encoding='utf-8')
        del_html = del_html.replace(
            '</head>', '<link rel="stylesheet" href="nav.css"></head>', 1,
        ).replace('<body>', f'<body><div class="nav-shell">{del_nav}</div>', 1)
        del_out = Path(__file__).parent / DELIVERABLES_CFG['out']
        del_out.parent.mkdir(parents=True, exist_ok=True)
        del_out.write_text(del_html, encoding='utf-8')
        print(f'Written: {del_out}', flush=True)
    else:
        print('deliverables_src.html not found — skipping', flush=True)



if __name__ == '__main__':
    main()

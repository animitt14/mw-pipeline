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
from datetime import datetime, timezone, timedelta, date as date_type
from html import escape
from pathlib import Path

HUBSPOT_TOKEN = os.environ.get('HUBSPOT_API_KEY', '').strip().replace('﻿', '')
PORTAL_ID = '5454671'
HEADERS = {'Authorization': f'Bearer {HUBSPOT_TOKEN}', 'Content-Type': 'application/json'}
SEARCH_URL = 'https://api.hubapi.com/crm/v3/objects/contacts/search'

DEAL_STAGES = {
    '1321369495': 'Event Attended',
    '1339121714': 'Advisor Assigned',
    '1321369496': 'Active Relationship',
    '1363474599': 'Long Term Relationship',
    '1321369497': 'Meeting Scheduled',
    '1321369500': 'Nurture',
    '1321369502': 'Recommendation Made',
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
OVERVIEW_CFG = {'name': 'Overview', 'out': 'docs/overview.html', 'pw': 'banksy'}
ALL_PAGES = OWNERS + [OVERVIEW_CFG]

OVERVIEW_OWNER_IDS = {'77771452', '73613833'}
OVERVIEW_OWNER_NAMES = {'77771452': 'Mittal', '73613833': 'Bringsjord'}
MONTHLY_GOAL = 700_000
CLOSED_WON_STAGE  = '1321369499'
CLOSED_LOST_STAGE = '1321369501'
DQ_STAGE          = '1341309466'
TERMINAL_STAGES   = {CLOSED_WON_STAGE, CLOSED_LOST_STAGE, DQ_STAGE, '1363474966', '1363467915'}


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


def fetch_deals_live():
    """Fetch all Gallery Leads deals live from HubSpot API."""
    url = 'https://api.hubapi.com/crm/v3/objects/deals/search'
    deals = []
    after = None
    while True:
        body = {
            'filterGroups': [{'filters': [
                {'propertyName': 'pipeline', 'operator': 'EQ', 'value': GALLERY_LEADS_PIPELINE}
            ]}],
            'properties': ['dealname', 'dealstage', 'amount', 'num_contacted_notes',
                           'hubspot_owner_id', 'closedate', 'createdate', 'notes_last_updated'],
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


def fetch_advisor_activity():
    """Fetch call and email counts for Ani + Erik, last 30 days."""
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

    for obj_type, key in [('calls', 'calls'), ('emails', 'emails')]:
        url = f'https://api.hubapi.com/crm/v3/objects/{obj_type}/search'
        after = None
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
                        if int(float(ts_val)) >= ts_5wd:
                            counts[owner][f'{key}_5wd'] += 1
                    except (ValueError, TypeError):
                        pass
            after = data.get('paging', {}).get('next', {}).get('after')
            if not after:
                break
            time.sleep(0.1)

    print(f'  Activity: Ani calls={counts["77771452"]["calls_30"]} emails={counts["77771452"]["emails_30"]} '
          f'| Erik calls={counts["73613833"]["calls_30"]} emails={counts["73613833"]["emails_30"]}', flush=True)
    return counts, n_5wd_days


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
        return dt.strftime('%#m/%#d/%y') if sys.platform == 'win32' else dt.strftime('%-m/%-d/%y')
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
    s = dt.strftime('%#m/%#d/%y') if sys.platform == 'win32' else dt.strftime('%-m/%-d/%y')
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
        return f'${float(amount_str):,.0f}'
    except Exception:
        return amount_str


FUNNEL_STAGES = [
    ('1339121714', 'Advisor Assigned'),
    ('1321369496', 'Active Rel.'),
    ('1363474599', 'Long Term Rel.'),
    ('1321369497', 'Mtg Scheduled'),
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


def build_html(contacts, records, by_name, by_last_name=None, tasks=None, meetings=None, notes=None,
               daily_tasks=None, daily_meetings=None, owner_name='Ani', nav_html='', password='anisha'):
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime('%B %-d, %Y %H:%M UTC') if sys.platform != 'win32' \
        else now_dt.strftime('%B %#d, %Y %H:%M UTC')
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
            _status, _status_order = 'Active', 1
        else:
            _status, _status_order = 'Dormant', 2
        row_data[-1]['status'] = _status
        row_data[-1]['status_order'] = _status_order

    # Save pre-filter rows for whale tracker (includes Collector etc.)
    all_row_data = row_data[:]

    # Remove disqualified and closed won
    row_data = [r for r in row_data if r['stage_id'] not in TERMINAL_STAGES]

    # Default sort: stage priority order, then amount descending within group
    row_data.sort(key=lambda r: (r['status_order'], STAGE_SORT_ORDER.get(r['stage_id'], 9), r['meeting_ms'] if (r['status_order'] == 0 and r['meeting_ms'] > 0) else 0, -r['amount_val']))

    # --- Summary stats ---
    ms_7d = 7 * 24 * 3600 * 1000
    stat_pipeline_val  = sum(r['amount_val'] for r in row_data if r['amount_val'] > 0)
    stat_mtg_count     = sum(1 for r in row_data if r['meeting_ms'] > 0)
    stat_tasks_week    = sum(1 for r in row_data if 0 < r['task_due_ms'] <= now_ms_ts + ms_7d)
    stat_active        = sum(1 for r in row_data if r['status'] in ('Active', 'Upcoming'))
    stat_dormant       = sum(1 for r in row_data if r['status'] == 'Dormant')
    stat_whales        = len([r for r in all_row_data if r['amount_val'] >= 50_000])

    def fmt_stat_val(n):
        if n >= 1_000_000: return f'${n/1_000_000:.1f}M'
        if n >= 1_000:     return f'${n/1_000:.0f}k'
        return f'${int(n):,}'

    def stat_card(value, label, sublabel='', accent=False):
        val_color = '#c9a96e' if accent else '#e8e8e8'
        return (f'<div class="stat-card">'
                f'<div class="stat-val" style="color:{val_color}">{value}</div>'
                f'<div class="stat-lbl">{label}</div>'
                + (f'<div class="stat-sub">{sublabel}</div>' if sublabel else '') +
                f'</div>')

    stats_html = (
        stat_card(fmt_stat_val(stat_pipeline_val), 'pipeline value', 'active deals w/ amounts', accent=True) +
        stat_card(str(stat_mtg_count), 'upcoming meetings') +
        stat_card(str(stat_tasks_week), 'tasks due this week') +
        stat_card(str(stat_active), 'active', f'{stat_dormant} dormant') +
        stat_card(str(stat_whales), 'whales', '$50k+')
    )

    # --- Today / Whale subsets ---
    today_start_ms = int(datetime.combine(today_date, datetime.min.time()).replace(tzinfo=timezone.utc).timestamp() * 1000)
    today_end_ms   = today_start_ms + 86_400_000
    today_rows = [r for r in row_data if
        (r['meeting_ms'] > 0 and today_start_ms <= r['meeting_ms'] < today_end_ms) or
        (r['task_due_ms'] > 0 and today_start_ms <= r['task_due_ms'] < today_end_ms)]
    today_rows.sort(key=lambda r: -r['amount_val'])

    whale_rows = sorted([r for r in all_row_data if r['amount_val'] >= 50_000], key=lambda r: -r['amount_val'])

    def mini_row(r):
        hs_badge = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        inv_badge = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        stage_cell = f'<span class="badge {r["stage_css"]}">{escape(r["stage_label"])}</span>' if r['stage_label'] else '—'
        amt_cell = escape(r['amount_fmt']) if r['amount_fmt'] else '—'
        mtg_title = escape(r['meeting_title']) if r['meeting_title'] else ''
        mtg_cell = f'<span title="{mtg_title}">{r["meeting_start"]}</span>' if r['meeting_start'] else '—'
        task_title = escape(r['task_subject']) if r['task_subject'] else ''
        task_cell = f'<span title="{task_title}">{r["task_due"]}</span>' if r['task_due'] else '—'
        return (f'<tr>'
                f'<td>{hs_badge}{r["name"]}{inv_badge}</td>'
                f'<td>{stage_cell}</td>'
                f'<td>{amt_cell}</td>'
                f'<td>{r["last_contact"]}</td>'
                f'<td>{mtg_cell}</td>'
                f'<td>{task_cell}</td>'
                f'</tr>')

    today_rows_html  = '\n'.join(mini_row(r) for r in today_rows)  if today_rows  else '<tr><td colspan="6" style="color:#555;text-align:center;padding:18px">No meetings or tasks due today</td></tr>'
    whale_rows_html  = '\n'.join(mini_row(r) for r in whale_rows)  if whale_rows  else '<tr><td colspan="6" style="color:#555;text-align:center;padding:18px">No deals at $50k+</td></tr>'

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
        cal_cards_html += f'''<div class="{card_class}">
  <div class="cal-day-label">{day_label}</div>
  <div class="cal-mtg">&#128197; {mtg_label}</div>
  <div class="cal-tasks-section">
    <div class="cal-row"><span class="cal-icon">&#9993;</span><span class="cal-num">{email_n}</span><span class="cal-lbl">email</span></div>
    <div class="cal-row"><span class="cal-icon">&#9990;</span><span class="cal-num">{call_n}</span><span class="cal-lbl">call</span></div>
    <div class="cal-row"><span class="cal-icon">&#183;</span><span class="cal-num">{other_n}</span><span class="cal-lbl">other</span></div>
  </div>
  <div class="cal-cap"><span class="cal-bar">{bar_str}</span><span class="cal-cap-label">{free_h:.1f}h open</span></div>
</div>'''

    # CSS funnel HTML (built in Python so braces don't need escaping in the f-string)
    max_cnt = max(funnel_counts) if any(funnel_counts) else 1
    F_COLORS = ['#c9c96d', '#6daacc', '#9d9ddd', '#cc8a6d', '#6dcccc', '#4dc94d']
    funnel_html = '\n'.join(
        f'<div class="f-row"><div class="f-track">'
        f'<div class="f-bar" style="width:{max(c / max_cnt * 100, 8):.0f}%;background:{F_COLORS[i]}">'
        f'{c}</div></div><span class="f-lbl">{lbl}</span></div>'
        for i, (c, (_, lbl)) in enumerate(zip(funnel_counts, FUNNEL_STAGES))
    )

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
        status_css = {'Upcoming': 'status-upcoming', 'Active': 'status-active', 'Dormant': 'status-dormant'}.get(r['status'], '')
        status_cell = f'<span class="badge {status_css}">{r["status"]}</span>'
        contacted_today = False
        if r['contacted_raw']:
            try:
                ct = datetime.fromisoformat(r['contacted_raw'].replace('Z', '+00:00'))
                contacted_today = ct.date() == today_date
            except Exception:
                pass
        row_class = ' class="contacted-today"' if contacted_today else ''
        tc_parts = [r['title'][:28] if r['title'] else '', r['company'][:24] if r['company'] else '']
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
            f'      <td>{r["last_contact"]}</td>\n'
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
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #141414; color: #e8e8e8; margin: 0; padding: 24px 28px; }}
  h1 {{ font-size: 1.3rem; font-weight: 600; margin: 0 0 4px; color: #f0f0f0; }}
  .meta {{ font-size: 0.78rem; color: #888; margin-bottom: 14px; }}
  .meta span {{ margin-right: 14px; }}
  .charts-row {{ display: flex; gap: 20px; margin-bottom: 22px; align-items: stretch; }}
  .chart-box {{ background: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 6px; padding: 12px 16px; flex: 1; min-width: 0; max-width: 440px; }}
  .chart-box h3 {{ font-size: 0.7rem; font-weight: 500; color: #777; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 10px; }}
  .cal-section {{ display: flex; flex-direction: column; }}
  .cal-heading {{ font-size: 0.7rem; font-weight: 500; color: #777; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 6px; }}
  .cal-cards {{ display: flex; gap: 8px; flex: 1; }}
  .cal-card {{ background: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 6px; padding: 8px 12px; width: fit-content; display: flex; flex-direction: column; }}
  .cal-today {{ border-color: #c9a96e; }}
  .cal-day-label {{ font-size: 0.76rem; font-weight: 600; color: #ddd; margin-bottom: 5px; }}
  .cal-today .cal-day-label {{ color: #c9a96e; }}
  .cal-mtg {{ font-size: 0.72rem; color: #bbb; margin-bottom: 5px; }}
  .cal-tasks-section {{ margin-bottom: 6px; }}
  .cal-row {{ display: flex; align-items: center; font-size: 0.71rem; color: #aaa; gap: 4px; line-height: 1.5; }}
  .cal-icon {{ width: 1.1em; flex-shrink: 0; }}
  .cal-lbl {{ color: #aaa; }}
  .cal-num {{ min-width: 1.4em; font-variant-numeric: tabular-nums; color: #ddd; font-weight: 600; }}
  .cal-cap {{ display: flex; align-items: center; gap: 6px; margin-top: 3px; }}
  .cal-bar {{ font-size: 0.6rem; color: #6daacc; letter-spacing: -1px; }}
  .cal-today .cal-bar {{ color: #c9a96e; }}
  .cal-cap-label {{ font-size: 0.7rem; color: #999; }}
  .f-row {{ display: flex; align-items: center; gap: 8px; margin: 2px 0; }}
  .f-track {{ flex: 1; display: flex; justify-content: center; }}
  .f-bar {{ height: 20px; display: flex; align-items: center; justify-content: center; font-size: 0.68rem; font-weight: 700; color: #111; border-radius: 2px; min-width: 22px; }}
  .f-lbl {{ font-size: 0.69rem; color: #aaa; white-space: nowrap; width: 90px; }}
  .controls {{ display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }}
  .controls select, .controls button {{
    background: #1e1e1e; color: #bbb; border: 1px solid #3a3a3a;
    padding: 5px 10px; border-radius: 4px; font-size: 0.78rem; cursor: pointer;
  }}
  .controls button:hover, .controls select:hover {{ border-color: #c9a96e; color: #c9a96e; }}
  .controls .sort-hint {{ font-size: 0.72rem; color: #777; font-style: italic; }}
  .controls label {{ font-size: 0.78rem; color: #888; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; }}
  th {{
    text-align: left; padding: 9px 12px; background: #1a1a1a; color: #999;
    font-weight: 500; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em;
    border-bottom: 1px solid #333; white-space: nowrap; position: sticky; top: 0;
    cursor: pointer; user-select: none;
  }}
  th:last-child {{ cursor: default; }}
  th.sort-asc::after  {{ content: " ▲"; font-size: 0.6rem; color: #c9a96e; }}
  th.sort-desc::after {{ content: " ▼"; font-size: 0.6rem; color: #c9a96e; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #272727; vertical-align: middle; }}
  tr:hover td {{ background: #1c1c1c; }}
  tr.hidden {{ display: none; }}
  a {{ color: #c9a96e; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .hs-badge {{ font-size: 0.68rem; font-weight: 700; color: #777; border: 1px solid #3a3a3a; border-radius: 3px; padding: 1px 4px; margin-right: 5px; vertical-align: middle; }}
  .hs-badge:hover {{ color: #c9a96e !important; border-color: #c9a96e !important; text-decoration: none !important; }}
  .inv-badge {{ font-size: 0.65rem; font-weight: 700; color: #6dcccc; border: 1px solid #2a5050; border-radius: 3px; padding: 1px 4px; margin-right: 5px; vertical-align: middle; }}
  .note-toggle {{ cursor: pointer; color: #aaa; font-size: 0.75rem; margin-left: 5px; user-select: none; }}
  .note-toggle:hover {{ color: #eee; }}
  .note-snippet {{ font-size: 0.72rem; color: #aaa; font-style: italic; margin-top: 4px; line-height: 1.4; white-space: normal; }}
  td:nth-child(2) {{ max-width: 170px; }}
  .tc-col {{ max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: left !important; }}
  .tc-text {{ font-size: 0.78rem; color: #888; }}
  .li-inline {{ font-size: 0.68rem; font-weight: 600; color: #555; border: 1px solid #333; border-radius: 3px; padding: 1px 4px; margin-right: 4px; }}
  .li-inline:hover {{ color: #c9a96e !important; border-color: #c9a96e !important; text-decoration: none !important; }}
  .links a {{ font-size: 0.72rem; font-weight: 600; color: #999; border: 1px solid #3a3a3a; border-radius: 3px; padding: 1px 5px; }}
  .links a:hover {{ color: #c9a96e; border-color: #c9a96e; text-decoration: none; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 0.72rem; font-weight: 500; white-space: nowrap; }}
  .stage-event     {{ background: #1a3a1a; color: #7dd87d; }}
  .stage-advisor   {{ background: #363618; color: #d9d97d; }}
  .stage-active    {{ background: #182a3a; color: #7dbedd; }}
  .stage-longterm  {{ background: #1e2a3a; color: #7daedd; }}
  .stage-meeting   {{ background: #222236; color: #adadee; }}
  .stage-nurture   {{ background: #3a2418; color: #dd9a7d; }}
  .stage-rec       {{ background: #183030; color: #7ddcdc; }}
  .stage-won       {{ background: #162a16; color: #5dd95d; }}
  .stage-lost      {{ background: #2e1212; color: #dd6666; }}
  .stage-disq      {{ background: #252525; color: #777; }}
  th:nth-child(n+2) {{ text-align: center; }}
  td:nth-child(n+2) {{ text-align: center; }}
  th:nth-child(2), th:nth-child(3) {{ text-align: left; }}
  td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
  td:nth-child(5)  {{ font-variant-numeric: tabular-nums; color: #c9a96e; }}
  td:nth-child(10) {{ font-variant-numeric: tabular-nums; color: #aaa; }}
  td:nth-child(9)  {{ font-size: 0.78rem; color: #999; }}
  .status-upcoming {{ background: #222236; color: #adadee; }}
  .status-active    {{ background: #1a3a1a; color: #7dd87d; }}
  .status-dormant   {{ background: #282828; color: #777; }}
  tr.contacted-today td {{ opacity: 0.4; }}
  .nav {{ display: flex; gap: 6px; margin-bottom: 18px; }}
  .nav a {{ font-size: 0.78rem; padding: 4px 14px; border-radius: 4px; border: 1px solid #3a3a3a; color: #888; text-decoration: none; }}
  .nav a.active {{ border-color: #c9a96e; color: #c9a96e; }}
  .nav a:hover {{ border-color: #c9a96e; color: #c9a96e; }}
  .stat-row {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
  .stat-card {{ background: #1e1e1e; border: 1px solid #2e2e2e; border-radius: 6px; padding: 12px 18px; min-width: 120px; }}
  .stat-val {{ font-size: 1.45rem; font-weight: 700; line-height: 1; margin-bottom: 5px; }}
  .stat-lbl {{ font-size: 0.72rem; color: #888; text-transform: uppercase; letter-spacing: 0.04em; }}
  .stat-sub {{ font-size: 0.68rem; color: #555; margin-top: 3px; }}
  .section-header {{ font-size: 0.72rem; font-weight: 600; color: #888; text-transform: uppercase; letter-spacing: 0.06em; margin: 26px 0 8px; display: flex; align-items: center; gap: 10px; }}
  .section-header .section-count {{ background: #2a2a2a; color: #aaa; border-radius: 10px; padding: 1px 8px; font-size: 0.68rem; font-weight: 500; text-transform: none; letter-spacing: 0; }}
  .section-header .section-dot {{ width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }}
  .dot-today {{ background: #adadee; }}
  .dot-whale {{ background: #c9a96e; }}
  .dot-all   {{ background: #555; }}
  .mini-table {{ width: 100%; border-collapse: collapse; font-size: 0.83rem; margin-bottom: 6px; }}
  .mini-table th {{ text-align: left; padding: 7px 12px; background: #1a1a1a; color: #999; font-weight: 500; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid #333; white-space: nowrap; }}
  .mini-table td {{ padding: 7px 12px; border-bottom: 1px solid #222; vertical-align: middle; }}
  .mini-table tr:hover td {{ background: #1c1c1c; }}
  .mini-table th:nth-child(n+2) {{ text-align: center; }}
  .mini-table td:nth-child(n+2) {{ text-align: center; }}
  .mini-table td:nth-child(3) {{ font-variant-numeric: tabular-nums; color: #c9a96e; }}
  .whale-amt {{ color: #c9a96e; font-weight: 700; font-size: 0.88rem; }}
</style>
<style>
#pw-gate{{position:fixed;inset:0;background:#141414;display:flex;align-items:center;justify-content:center;z-index:9999}}
#pw-gate.hidden{{display:none}}
#pw-box{{background:#1e1e1e;border:1px solid #3a3a3a;border-radius:8px;padding:32px 40px;text-align:center;min-width:280px}}
#pw-box h2{{margin:0 0 20px;color:#e8e8e8;font-size:16px;font-weight:500;letter-spacing:.05em}}
#pw-input{{width:100%;padding:10px 14px;background:#111;border:1px solid #444;border-radius:5px;color:#e8e8e8;font-size:15px;outline:none;box-sizing:border-box}}
#pw-input:focus{{border-color:#c9a96e}}
#pw-err{{color:#e07070;font-size:13px;margin-top:10px;min-height:18px}}
#pw-btn{{margin-top:14px;width:100%;padding:10px;background:#c9a96e;border:none;border-radius:5px;color:#111;font-size:14px;font-weight:600;cursor:pointer}}
#pw-btn:hover{{background:#d4b87a}}
</style>
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
  if(sessionStorage.getItem(SK)==='1')document.getElementById('pw-gate').classList.add('hidden');
  window.checkPw=function(){{
    if(document.getElementById('pw-input').value===PW){{
      sessionStorage.setItem(SK,'1');
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
<h1>Pipeline &mdash; {owner_name}</h1>
<div class="meta">
  <span>{count} contacts</span>
  <span>Updated {now}</span>
</div>
<div class="stat-row">{stats_html}</div>
<div class="charts-row">
  <div class="chart-box">
    <h3>Active Deals by Stage</h3>
    <div class="funnel">{funnel_html}</div>
  </div>
  <div class="cal-section">
    <h3 class="cal-heading">Next 3 Days</h3>
    <div class="cal-cards">{cal_cards_html}</div>
  </div>
</div>
<div class="section-header"><span class="section-dot dot-today"></span>Today<span class="section-count">{len(today_rows)}</span></div>
<table class="mini-table">
  <thead><tr><th>Name</th><th>Stage</th><th>Amount</th><th>Last Contacted</th><th>Meeting</th><th>Task Due</th></tr></thead>
  <tbody>{today_rows_html}</tbody>
</table>

<div class="section-header" style="margin-top:22px"><span class="section-dot dot-whale"></span>Whale Tracker &mdash; $50k+<span class="section-count">{len(whale_rows)}</span></div>
<table class="mini-table">
  <thead><tr><th>Name</th><th>Stage</th><th>Amount</th><th>Last Contacted</th><th>Meeting</th><th>Task Due</th></tr></thead>
  <tbody>{whale_rows_html}</tbody>
</table>

<div class="section-header" style="margin-top:22px"><span class="section-dot dot-all"></span>All Contacts<span class="section-count">{count}</span></div>
<div class="controls">
  <button id="btn-default" onclick="resetSort()">Default Sort</button>
  <button id="btn-dormant" onclick="toggleDormant()">Hide dormant ({stat_dormant})</button>
  <span class="sort-hint">deal stage &rarr; amount</span>
  <label for="stage-filter">Stage:</label>
  <select id="stage-filter" onchange="filterStage(this.value)">
    <option value="">All Stages</option>
{stage_options}
  </select>
  <span id="visible-count" style="color:#555;font-size:0.78rem;"></span>
</div>
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
  var hideDormant = false;

  function applyFilters() {{
    var rows = document.querySelectorAll('#pipeline-body tr');
    rows.forEach(function(r) {{
      var stageHide   = stageFilter !== '' && r.dataset.stageLabel !== stageFilter;
      var dormantHide = hideDormant && r.dataset.status === 'Dormant';
      r.classList.toggle('hidden', stageHide || dormantHide);
    }});
    updateCount();
  }}

  window.filterStage = function(val) {{
    stageFilter = val;
    applyFilters();
  }};

  window.toggleDormant = function() {{
    hideDormant = !hideDormant;
    var dormantCount = document.querySelectorAll('#pipeline-body tr[data-status="Dormant"]').length;
    document.getElementById('btn-dormant').textContent =
      (hideDormant ? 'Show' : 'Hide') + ' dormant (' + dormantCount + ')';
    applyFilters();
  }};

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


_OVERVIEW_CSS = '''
:root{--bg:#141414;--surface:#1e1e1e;--surface2:#242424;--border:#3a3a3a;--border2:#4a4a4a;
--text:#e8e8e8;--text2:#aaa;--text3:#777;--purple:#adadee;--green:#7dd87d;
--red:#dd6666;--amber:#c9a96e;}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px;font-size:13px;}
.dash{max-width:1100px;margin:0 auto;}
.hdr{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid var(--border);padding-bottom:12px;margin-bottom:20px;}
.hdr h1{font-size:20px;font-weight:600;color:#f0f0f0;}
.slabel{font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--text2);border-bottom:1px solid var(--border);padding-bottom:4px;margin:0 0 10px;}
.g6{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-bottom:18px;}
.kc{background:var(--surface);border-radius:6px;padding:12px;border:1px solid var(--border);border-top:2px solid var(--amber);}
.kv{font-size:22px;font-weight:500;line-height:1.1;}
.kl{font-size:11px;color:var(--text2);margin-top:4px;}
.ks{font-size:10px;color:var(--text3);font-style:italic;margin-top:2px;}
.frow{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-bottom:18px;}
.fc{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:12px 14px;display:flex;align-items:center;gap:10px;}
.fc-bw{flex:1;}
.fc-lbl{font-size:11px;color:var(--text2);margin-bottom:5px;}
.fc-bg{height:4px;background:var(--border);border-radius:2px;}
.fc-fill{height:4px;border-radius:2px;}
.fc-nums{text-align:right;white-space:nowrap;}
.fc-pct{font-size:17px;font-weight:500;line-height:1;}
.fc-cnt{font-size:10px;color:var(--text3);margin-top:2px;}
.g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:16px;}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;}
.card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:14px;}
.ctitle{font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em;margin-bottom:9px;}
.sbig{font-size:24px;font-weight:500;color:var(--amber);text-align:center;}
.ssub{font-size:10px;color:var(--text3);text-align:center;margin-top:3px;border-bottom:1px solid var(--border);padding-bottom:9px;margin-bottom:11px;}
table{width:100%;border-collapse:collapse;font-size:11px;}
th{font-size:10px;color:var(--text2);text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);background:var(--surface2);font-weight:500;text-transform:uppercase;letter-spacing:.04em;}
td{padding:6px 8px;border-bottom:1px solid #272727;color:var(--text);}
tr:last-child td{border-bottom:none;}
tr:nth-child(even) td{background:var(--surface2);}
tr:hover td{background:#1c1c1c;}
.badge{display:inline-block;font-size:9px;font-weight:600;padding:2px 6px;border-radius:3px;}
.bp{background:#222236;color:#adadee;}
.ba{background:#363618;color:#d9d97d;}
.br{background:#2e1212;color:#dd6666;}
.bg{background:#162a16;color:#7dd87d;}
.fg{background:var(--surface2);border-radius:4px;padding:9px 11px;display:flex;justify-content:space-between;align-items:center;margin:7px 0;border:1px solid var(--border);}
.goal-bar-bg{height:7px;background:var(--border);border-radius:4px;margin-bottom:4px;}
.goal-bar-fill{height:7px;border-radius:4px;background:var(--amber);}
.g4s{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-bottom:12px;}
.stat{background:var(--surface2);border-radius:4px;padding:9px 6px;text-align:center;border:1px solid var(--border);}
.sv{font-size:18px;font-weight:500;}
.sl{font-size:10px;color:var(--text2);margin-top:3px;line-height:1.3;}
.brow{display:grid;grid-template-columns:1fr 32px 74px 44px;align-items:center;padding:5px 0;border-bottom:1px solid #272727;font-size:11px;}
.brow:last-child{border-bottom:none;}
.or{display:flex;align-items:center;gap:9px;padding:6px 0;border-bottom:1px solid #272727;font-size:11px;}
.or:last-child{border-bottom:none;}
.mb{height:3px;border-radius:2px;margin-top:3px;}
.note{font-size:10px;color:var(--text3);font-style:italic;margin-top:6px;line-height:1.5;}
.stale-note{font-size:10px;color:var(--amber);font-style:italic;margin-bottom:8px;padding:5px 8px;background:#2a2010;border-radius:4px;border-left:2px solid var(--amber);}
.sr{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #272727;font-size:11px;gap:9px;}
.sr:last-child{border-bottom:none;}
.sbar{height:5px;border-radius:3px;margin-top:3px;}
.act3{display:grid;grid-template-columns:1fr 150px 1fr;gap:12px;margin-bottom:16px;}
.act-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:13px;}
.act-rep{display:grid;grid-template-columns:80px 1fr 70px;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid #272727;}
.act-rep:last-of-type{border-bottom:none;}
.act-bb{height:6px;background:var(--border);border-radius:3px;}
.act-bf{height:6px;border-radius:3px;}
.act-v{font-size:16px;font-weight:500;text-align:right;line-height:1;}
.act-s{font-size:10px;color:var(--text2);margin-top:2px;text-align:right;}
.mid-card{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:16px 12px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;}
.footer{margin-top:16px;padding-top:10px;border-top:1px solid var(--border);display:flex;justify-content:space-between;font-size:10px;color:var(--text3);font-style:italic;}
.nav{display:flex;gap:6px;margin-bottom:20px;}
.nav a{padding:4px 14px;font-size:0.78rem;border-radius:4px;border:1px solid var(--border);color:#888;text-decoration:none;}
.nav a.active{border-color:var(--amber);color:var(--amber);}
.nav a:hover{border-color:var(--amber);color:var(--amber);}
#pw-gate{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;justify-content:center;z-index:999;}
#pw-gate.hidden{display:none;}
#pw-box{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:32px 40px;text-align:center;min-width:280px;}
#pw-box h2{margin:0 0 20px;color:var(--text);font-size:16px;font-weight:500;letter-spacing:.05em;}
#pw-input{width:100%;padding:10px 14px;background:#111;border:1px solid #444;border-radius:5px;color:var(--text);font-size:15px;outline:none;box-sizing:border-box;}
#pw-input:focus{border-color:var(--amber);}
#pw-btn{margin-top:14px;width:100%;padding:10px;background:var(--amber);border:none;border-radius:5px;color:#111;font-size:14px;font-weight:600;cursor:pointer;}
#pw-btn:hover{background:#d4b87a;}
#pw-err{color:var(--red);font-size:13px;margin-top:10px;min-height:18px;}
'''

_STATIC_STAGE_PROGRESSION = '''
<div class="stale-note">Last updated: May 13, 2026 — stage progression history is not available via the HubSpot API.</div>
<div class="g2">
  <div class="card">
    <div class="ctitle">Daily advances by advisor</div>
    <div style="display:flex;gap:14px;margin-bottom:10px;flex-wrap:wrap;">
      <div style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text2)"><div style="width:10px;height:10px;border-radius:2px;background:#534AB7"></div>Bringsjord</div>
      <div style="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--text2)"><div style="width:10px;height:10px;border-radius:2px;background:#3B6D11"></div>Mittal</div>
    </div>
    <div style="display:grid;grid-template-columns:76px repeat(10,minmax(0,1fr));gap:3px;align-items:center;">
      <div></div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">Apr 28</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">Apr 30</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 1</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 4</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 5</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 6</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 7</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 8</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 11</div>
      <div style="font-size:9px;color:var(--text3);text-align:center;padding-bottom:5px;border-bottom:.5px solid var(--border)">May 12 ⚠</div>
      <div style="font-size:11px;font-weight:500;padding:3px 0;color:var(--text)">Bringsjord</div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:10%;background:#534AB7">1</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="height:0%"></div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:10%;background:#534AB7">1</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="height:0%"></div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="height:0%"></div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="height:0%"></div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:18%;background:#534AB7">3</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:10%;background:#534AB7">1</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:20%;background:#534AB7">4</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:98%;background:#534AB7">92</div></div>
      <div style="font-size:11px;font-weight:500;padding:3px 0;color:var(--text)">Mittal</div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:14%;background:#3B6D11">2</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:18%;background:#3B6D11">3</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:18%;background:#3B6D11">3</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:20%;background:#3B6D11">4</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:25%;background:#3B6D11">6</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:14%;background:#3B6D11">2</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:31%;background:#3B6D11">9</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:20%;background:#3B6D11">4</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"><div style="border-radius:3px 3px 0 0;width:100%;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:600;color:rgba(255,255,255,.9);min-height:3px;height:20%;background:#3B6D11">4</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:center;height:34px;padding:1px"></div>
    </div>
    <div class="note">Deals moved into a new forward stage · excl. new deal creation · ⚠ May 12 includes a bulk CRM stage update</div>
  </div>
  <div class="card">
    <div class="ctitle" style="margin-bottom:11px">Last 5 working days (May 6–12)</div>
    <div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px">Advances by advisor</div>
    <div style="display:grid;grid-template-columns:76px 1fr 26px 28px;gap:5px;align-items:center;padding:5px 0;border-bottom:.5px solid var(--border);font-size:11px;font-size:10px;color:var(--text3)"><span>Advisor</span><span></span><span style="text-align:right">Wk</span><span style="text-align:right">Prev</span></div>
    <div style="display:grid;grid-template-columns:76px 1fr 26px 28px;gap:5px;align-items:center;padding:5px 0;border-bottom:.5px solid var(--border);font-size:11px"><span style="font-weight:500">Mittal</span><div><div style="height:5px;background:var(--green);border-radius:3px;width:100%"></div></div><span style="text-align:right;font-weight:500;color:var(--green)">25</span><span style="text-align:right;color:var(--text3)">—</span></div>
    <div style="display:grid;grid-template-columns:76px 1fr 26px 28px;gap:5px;align-items:center;padding:5px 0;border-bottom:.5px solid var(--border);font-size:11px"><span style="font-weight:500">Bringsjord</span><div><div style="height:5px;background:var(--purple);border-radius:3px;width:100%"></div></div><span style="text-align:right;font-weight:500;color:var(--purple)">100</span><span style="text-align:right;color:var(--text3)">—</span></div>
    <div style="margin-top:14px;font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:7px">Where deals moved to</div>
    <div class="sr"><div style="flex:1"><span>Attem. to Contact</span><div class="sbar" style="width:100%;background:var(--amber)"></div></div><span style="font-weight:500;white-space:nowrap">106</span></div>
    <div class="sr"><div style="flex:1"><span>Contacted</span><div class="sbar" style="width:16%;background:var(--purple)"></div></div><span style="font-weight:500;white-space:nowrap">17</span></div>
    <div class="sr"><div style="flex:1"><span>Meeting Scheduled</span><div class="sbar" style="width:7%;background:var(--purple)"></div></div><span style="font-weight:500;white-space:nowrap">7</span></div>
    <div class="sr"><div style="flex:1"><span>Nurture</span><div class="sbar" style="width:4%;background:#888"></div></div><span style="font-weight:500;white-space:nowrap">4</span></div>
    <div class="sr"><div style="flex:1"><span>Rec. Made</span><div class="sbar" style="width:2%;background:var(--green)"></div></div><span style="font-weight:500;white-space:nowrap">2</span></div>
    <div style="margin-top:11px;padding-top:9px;border-top:.5px solid var(--border);display:flex;justify-content:space-between;align-items:center">
      <div><div style="font-size:12px;color:var(--text2)">Total advances (last 5 days)</div><div style="font-size:10px;color:var(--text3);font-style:italic;margin-top:2px">Includes May 12 bulk stage update (92 deals)</div></div>
      <span style="font-size:24px;font-weight:500;color:var(--purple)">135</span>
    </div>
  </div>
</div>
'''


def build_overview_html(deals, activity, n_5wd_days, now_str, nav_html, password='banksy'):
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
    close_times = []
    for d in won:
        p = d['properties']
        cd, cr = pd(p.get('closedate')), pd(p.get('createdate'))
        if cd and cr and cd > cr:
            close_times.append((cd - cr).days)
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
    whale_rows = ''
    for d in whales:
        p = d['properties']
        cls, lbl = STAGE_BADGE.get(p.get('dealstage', ''), ('bp', p.get('dealstage', '')))
        age = deal_age_days(d)
        nc  = p.get('num_contacted_notes') or '0'
        owner = OVERVIEW_OWNER_NAMES.get(p.get('hubspot_owner_id', ''), '')
        whale_rows += (f'<tr><td><strong>{escape(deal_name(d))}</strong>'
                       f'<small style="color:var(--text3);margin-left:5px">{owner}</small></td>'
                       f'<td>{fmtamt(amt(d))}</td>'
                       f'<td><span class="badge {cls}">{lbl}</span></td>'
                       f'<td>{age}d</td><td>{nc}</td>'
                       f'<td style="color:var(--text3)">—</td></tr>')

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

    html_parts = [
        '<!DOCTYPE html><html lang="en"><head>',
        '<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">',
        '<title>Pipeline Overview</title>',
        f'<style>{_OVERVIEW_CSS}</style></head><body>',

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
        f'<div class="kc"><div class="kv" style="color:var(--purple)">{total_active}</div><div class="kl">Total active deals</div><div class="ks">excl. closed/DQ</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--green)">{fmtamt(pipeline_value)}</div><div class="kl">Pipeline value</div><div class="ks">open w/ amounts</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--red)">{pct_no_value:.0f}%</div><div class="kl">No value</div><div class="ks">{no_value_count} of {total_active} active</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--purple)">{close_rate:.1f}%</div><div class="kl">Close rate</div><div class="ks">won ÷ decided</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--amber)">{funnel_rate:.1f}%</div><div class="kl">Funnel close rate</div><div class="ks">won ÷ all assigned</div></div>',
        f'<div class="kc"><div class="kv" style="color:var(--purple)">{avg_close:.1f}d</div><div class="kl">Avg time to close</div><div class="ks">create → close · {len(close_times)} deals</div></div>',
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

        # 4. Stage Progression (static)
        '<p class="slabel">4 · stage progression — deals advancing</p>',
        _STATIC_STAGE_PROGRESSION,

        # 5. Whale Tracker
        '<p class="slabel">5 · whale tracker — $100k+ deals</p>',
        '<div class="card" style="margin-bottom:16px">',
        '<div class="g4s">',
        f'<div class="stat"><div class="sv" style="color:var(--purple)">{len(whales)}</div><div class="sl">active at $100k+</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--green)">{fmtamt(sum(amt(d) for d in whales))}</div><div class="sl">combined potential</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--red)">{sum(1 for d in whales if deal_age_days(d) > 30)} of {len(whales)}</div><div class="sl">over 30 days old</div></div>',
        f'<div class="stat"><div class="sv" style="color:var(--amber)">—</div><div class="sl">inbound reply</div></div>',
        '</div>',
        '<div class="stale-note">Reply column: not available via HubSpot API — last updated May 13, 2026.</div>',
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
        '<div class="act-card"><div class="ctitle">Emails</div>',
        '<div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Last 30 days</div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Bringsjord</span><div><div class="act-bb"><div class="act-bf" style="width:{int(erik_e30/max_e30*100)}%;background:#534AB7"></div></div></div><div><div class="act-v" style="color:#534AB7">{erik_e30/30:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{erik_e30} total</div></div></div>',
        f'<div class="act-rep" style="border-bottom:.5px solid var(--border);padding-bottom:10px"><span style="font-size:12px;font-weight:500">Mittal</span><div><div class="act-bb"><div class="act-bf" style="width:{int(ani_e30/max_e30*100)}%;background:#3B6D11"></div></div></div><div><div class="act-v" style="color:#3B6D11">{ani_e30/30:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{ani_e30} total</div></div></div>',
        f'<div style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:.04em;margin-top:10px;margin-bottom:6px">Last {n_5wd_days} days</div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Bringsjord</span><div><div class="act-bb"><div class="act-bf" style="width:{int(erik_e5/max_e5*100)}%;background:#534AB7"></div></div></div><div><div class="act-v" style="color:#534AB7">{erik_e5/n_5wd_days:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{erik_e5} total</div></div></div>',
        f'<div class="act-rep"><span style="font-size:12px;font-weight:500">Mittal</span><div><div class="act-bb"><div class="act-bf" style="width:{int(ani_e5/max_e5*100)}%;background:#3B6D11"></div></div></div><div><div class="act-v" style="color:#3B6D11">{ani_e5/n_5wd_days:.1f}<span style="font-size:10px;font-weight:400">/day</span></div><div class="act-s">{ani_e5} total</div></div></div>',
        '</div>',
        '</div>',

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
        nav_html = '<div class="nav">' + ''.join(
            f'<a href="{Path(o["out"]).name}" class="active">{o["name"]}</a>'
            if o is owner_cfg else
            f'<a href="{Path(o["out"]).name}">{o["name"]}</a>'
            for o in ALL_PAGES
        ) + '</div>'

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
    ov_nav = '<div class="nav">' + ''.join(
        f'<a href="{Path(o["out"]).name}" class="active">{o["name"]}</a>'
        if o is OVERVIEW_CFG else
        f'<a href="{Path(o["out"]).name}">{o["name"]}</a>'
        for o in ALL_PAGES
    ) + '</div>'

    print('Fetching advisor activity...', flush=True)
    activity, n_5wd_days = fetch_advisor_activity()

    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    print('Building Overview HTML...', flush=True)
    ov_html = build_overview_html(deals, activity, n_5wd_days, now_str, ov_nav, password=OVERVIEW_CFG['pw'])

    ov_out = Path(__file__).parent / OVERVIEW_CFG['out']
    ov_out.parent.mkdir(parents=True, exist_ok=True)
    ov_out.write_text(ov_html, encoding='utf-8')
    print(f'Written: {ov_out}', flush=True)


if __name__ == '__main__':
    main()

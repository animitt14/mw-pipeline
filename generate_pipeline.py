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
        sub_stat(str(stat_tasks_week),   'Tasks this week', '&#128203;') +   # clipboard
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

    def mini_row(r):
        hs_badge = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        inv_badge = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        stage_cell = f'<span class="badge {r["stage_css"]}">{escape(r["stage_label"])}</span>' if r['stage_label'] else '—'
        amt_cell = escape(r['amount_fmt']) if r['amount_fmt'] else '—'
        mtg_title = escape(r['meeting_title']) if r['meeting_title'] else ''
        mtg_cell = f'<span title="{mtg_title}">{r["meeting_start"]}</span>' if r['meeting_start'] else '—'
        task_title = escape(r['task_subject']) if r['task_subject'] else ''
        task_cell = f'<span title="{task_title}">{r["task_due"]}</span>' if r['task_due'] else '—'
        cls = heat_cls(r.get('days_since'))
        cls_attr = f' class="{cls}"' if cls else ''
        return (f'<tr{cls_attr}>'
                f'<td>{hs_badge}{r["name"]}{inv_badge}</td>'
                f'<td>{stage_cell}</td>'
                f'<td>{amt_cell}</td>'
                f'<td>{r["last_contact"]}</td>'
                f'<td>{mtg_cell}</td>'
                f'<td>{task_cell}</td>'
                f'</tr>')

    def whale_row(r):
        hs_badge = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        inv_badge = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        stage_cell = f'<span class="badge {r["stage_css"]}">{escape(r["stage_label"])}</span>' if r['stage_label'] else '—'
        amt_cell = escape(r['amount_fmt']) if r['amount_fmt'] else '—'
        # Next action — same logic as the whale tiles
        if r['meeting_ms'] > 0 and r['meeting_ms'] >= now_ms_ts:
            mtitle = (r['meeting_title'] or 'Meeting').strip()
            next_cell = (
                f'<span class="next-strong" title="{escape(mtitle)}">{escape(mtitle[:32])}</span>'
                f'<span class="next-meta"> &middot; {escape(r["meeting_start"])}</span>'
            )
        elif r['task_due_ms'] > 0:
            tsubj = (r['task_subject'] or 'Task').strip()
            next_cell = (
                f'<span class="next-strong" title="{escape(tsubj)}">{escape(tsubj[:32])}</span>'
                f'<span class="next-meta"> &middot; due {escape(r["task_due"])}</span>'
            )
        else:
            next_cell = '<span class="next-prompt">Book follow-up</span>'
        cls = heat_cls(r.get('days_since'))
        cls_attr = f' class="{cls}"' if cls else ''
        return (f'<tr{cls_attr}>'
                f'<td>{hs_badge}{r["name"]}{inv_badge}</td>'
                f'<td>{stage_cell}</td>'
                f'<td>{amt_cell}</td>'
                f'<td>{r["last_contact"] or "—"}</td>'
                f'<td class="next-cell">{next_cell}</td>'
                f'</tr>')

    def today_row(r):
        hs_badge = f'<a href="{escape(r["hs_url"])}" target="_blank" class="hs-badge">HS</a>'
        inv_badge = '<span class="inv-badge">INV</span>' if r['prior_invested'] else ''
        stage_cell = f'<span class="badge {r["stage_css"]}">{escape(r["stage_label"])}</span>' if r['stage_label'] else '—'
        amt_cell = escape(r['amount_fmt']) if r['amount_fmt'] else '—'
        has_mtg_today = r['meeting_ms'] > 0 and today_start_ms <= r['meeting_ms'] < today_end_ms
        has_task_today = r['task_due_ms'] > 0 and today_start_ms <= r['task_due_ms'] < today_end_ms
        # Meeting takes priority when both fall on the same day
        if has_mtg_today:
            mtg_title = escape(r['meeting_title']) if r['meeting_title'] else 'Meeting'
            activity_cell = (
                f'<span class="act act-mtg" title="{mtg_title}">'
                f'<span class="act-kind">meeting</span></span>'
            )
        elif has_task_today:
            task_title = escape(r['task_subject']) if r['task_subject'] else ''
            detail = task_title[:60] if task_title else ''
            activity_cell = (
                f'<span class="act act-task" title="{task_title}">'
                f'<span class="act-kind">task</span>{detail}</span>'
            )
        else:
            activity_cell = '<span class="act-empty">&mdash;</span>'
        cls = heat_cls(r.get('days_since'))
        cls_attr = f' class="{cls}"' if cls else ''
        return (f'<tr{cls_attr}>'
                f'<td>{hs_badge}{r["name"]}{inv_badge}</td>'
                f'<td>{stage_cell}</td>'
                f'<td>{amt_cell}</td>'
                f'<td class="act-cell">{activity_cell}</td>'
                f'</tr>')

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
                f'<td>{r["last_contact"] or "—"}</td>'
                f'<td>{task_cell}</td>'
                f'</tr>')

    # Whale table excludes the top 3 already shown as tiles
    whale_table_rows = whale_rows[3:]
    whale_rows_html      = '\n'.join(whale_row(r) for r in whale_table_rows) if whale_table_rows else '<tr><td colspan="5" style="color:var(--text-3);text-align:center;padding:18px">All whales above are in the tiles</td></tr>'
    cold_rows_html       = '\n'.join(cold_row(r) for r in cold_rows)         if cold_rows        else '<tr><td colspan="6" style="color:var(--text-3);text-align:center;padding:18px">No deals are slipping</td></tr>'

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
        if r['last_contact']: meta_parts.append(f'last {escape(r["last_contact"])}')
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

    # Stacked horizontal funnel — colors mirror the .stage-* badge text colors
    total_funnel = sum(funnel_counts) or 1
    # Order matches FUNNEL_STAGES: Advisor, Active, LongTerm, Mtg, Nurture, Rec Made
    SF_COLORS = ['#854d0e', '#1d4ed8', '#4338ca', '#5b21b6', '#9a3412', '#115e59']
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
        _classes = []
        if contacted_today:
            _classes.append('contacted-today')
        else:
            _heat = heat_cls(r.get('days_since'))
            if _heat: _classes.append(_heat)
        row_class = f' class="{" ".join(_classes)}"' if _classes else ''
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
    <div class="funnel-eyebrow">Active Deals by Stage</div>
    <div class="sf-track">{stacked_funnel_html}</div>
    <div class="sf-legend">{stacked_funnel_legend_html}</div>
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
    <thead><tr><th>Name</th><th>Days Cold</th><th>Stage</th><th>Amount</th><th>Last Contacted</th><th>Task Due</th></tr></thead>
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

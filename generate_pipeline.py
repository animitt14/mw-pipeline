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
from datetime import datetime, timezone
from html import escape
from pathlib import Path

HUBSPOT_TOKEN = os.environ.get('HUBSPOT_API_KEY', '').strip()
PORTAL_ID = '5454671'
HEADERS = {'Authorization': f'Bearer {HUBSPOT_TOKEN}', 'Content-Type': 'application/json'}
SEARCH_URL = 'https://api.hubapi.com/crm/v3/objects/contacts/search'

DEAL_STAGES = {
    '1321369495': 'Event Attended',
    '1339121714': 'Attempted',
    '1321369496': 'Contacted',
    '1321369497': 'Meeting Scheduled',
    '1321369500': 'Nurture',
    '1321369502': 'Recommendation Made',
    '1321369499': 'Closed Won',
    '1321369501': 'Closed Lost',
    '1341309466': 'Disqualified',
}

STAGE_SORT_ORDER = {
    '1321369502': 0,  # Recommendation Made
    '1321369500': 1,  # Nurture
    '1321369497': 2,  # Meeting Scheduled
    '1321369496': 3,  # Contacted
    '1339121714': 4,  # Attempted
    '1321369495': 5,  # Event Attended
    '1321369499': 6,  # Closed Won
    '1321369501': 7,  # Closed Lost
    '1341309466': 8,  # Disqualified
    '':            9,  # No deal
}

STAGE_CSS = {
    '1321369495': 'stage-event',
    '1339121714': 'stage-attempted',
    '1321369496': 'stage-contacted',
    '1321369497': 'stage-meeting',
    '1321369500': 'stage-nurture',
    '1321369502': 'stage-rec',
    '1321369499': 'stage-won',
    '1321369501': 'stage-lost',
    '1341309466': 'stage-disq',
}

GALLERY_LEADS_PIPELINE = '880355706'
ANI_OWNER_ID = '77771452'

OWNERS = [
    {'name': 'Ani',  'id': '77771452', 'out': 'docs/index.html',    'cache': 'pipeline_deal_cache.json'},
    {'name': 'Erik', 'id': '73613833', 'out': 'docs/erik.html',     'cache': 'pipeline_deal_cache_erik.json'},
]


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
             'company', 'industry', 'total_purchased___reserved', 'haspurchased']

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


def load_deals_from_cache(cache_name='pipeline_deal_cache.json'):
    """Load deals from cache file (populated via MCP each session)."""
    cache_path = Path(__file__).parent / cache_name
    if not cache_path.exists():
        print('WARNING: pipeline_deal_cache.json not found — no deal data', flush=True)
        return []
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    deals = data.get('results', [])
    fetched = data.get('fetched_at', 'unknown')
    print(f'Loaded {len(deals)} deals from cache (fetched {fetched})', flush=True)
    return deals


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
    ('1339121714', 'Attempted'),
    ('1321369496', 'Contacted'),
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
               daily_tasks=None, daily_meetings=None, owner_name='Ani', nav_html=''):
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
        if stage_id in ('1339121714', '1321369501'):  # Attempted + Closed Lost always dormant
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

    # Remove disqualified and closed won
    row_data = [r for r in row_data if r['stage_id'] not in ('1341309466', '1321369499')]

    # Default sort: stage priority order, then amount descending within group
    row_data.sort(key=lambda r: (r['status_order'], STAGE_SORT_ORDER.get(r['stage_id'], 9), r['meeting_ms'] if (r['status_order'] == 0 and r['meeting_ms'] > 0) else 0, -r['amount_val']))

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
        rows.append(
            f'    <tr{row_class} data-default-order="{i}" data-stage-order="{STAGE_SORT_ORDER.get(r["stage_id"], 9)}"'
            f' data-amount="{r["amount_val"]}" data-rsvp="{escape(r["rsvp_raw"])}"'
            f' data-contacted="{escape(r["contacted_raw"])}" data-times="{r["times_val"]}"'
            f' data-task-ms="{r["task_due_ms"]}" data-meeting-ms="{r["meeting_ms"]}"'
            f' data-status-order="{r["status_order"]}" data-stage-label="{escape(r["stage_label"])}">\n'
            f'      <td>{hs_badge}{r["name"]}{inv_badge}{note_html}</td>\n'
            f'      <td>{status_cell}</td>\n'
            f'      <td>{stage_cell}</td>\n'
            f'      <td>{escape(r["amount_fmt"])}</td>\n'
            f'      <td>{r["rsvp_date"]}</td>\n'
            f'      <td>{r["last_contact"]}</td>\n'
            f'      <td>{mtg_cell}</td>\n'
            f'      <td>{task_cell}</td>\n'
            f'      <td>{escape(r["times_contacted"])}</td>\n'
            f'      <td class="links">{li_link}</td>\n'
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
  td:first-child {{ max-width: 170px; }}
  .links a {{ font-size: 0.72rem; font-weight: 600; color: #999; border: 1px solid #3a3a3a; border-radius: 3px; padding: 1px 5px; }}
  .links a:hover {{ color: #c9a96e; border-color: #c9a96e; text-decoration: none; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 0.72rem; font-weight: 500; white-space: nowrap; }}
  .stage-event     {{ background: #1a3a1a; color: #7dd87d; }}
  .stage-attempted {{ background: #363618; color: #d9d97d; }}
  .stage-contacted {{ background: #182a3a; color: #7dbedd; }}
  .stage-meeting   {{ background: #222236; color: #adadee; }}
  .stage-nurture   {{ background: #3a2418; color: #dd9a7d; }}
  .stage-rec       {{ background: #183030; color: #7ddcdc; }}
  .stage-won       {{ background: #162a16; color: #5dd95d; }}
  .stage-lost      {{ background: #2e1212; color: #dd6666; }}
  .stage-disq      {{ background: #252525; color: #777; }}
  th:nth-child(n+2) {{ text-align: center; }}
  td:nth-child(n+2) {{ text-align: center; }}
  td:nth-child(4)  {{ font-variant-numeric: tabular-nums; color: #c9a96e; }}
  td:nth-child(9)  {{ font-variant-numeric: tabular-nums; color: #aaa; }}
  td:nth-child(8)  {{ font-size: 0.78rem; color: #999; }}
  .status-upcoming {{ background: #222236; color: #adadee; }}
  .status-active    {{ background: #1a3a1a; color: #7dd87d; }}
  .status-dormant   {{ background: #282828; color: #777; }}
  tr.contacted-today td {{ opacity: 0.4; }}
  .nav {{ display: flex; gap: 6px; margin-bottom: 18px; }}
  .nav a {{ font-size: 0.78rem; padding: 4px 14px; border-radius: 4px; border: 1px solid #3a3a3a; color: #888; text-decoration: none; }}
  .nav a.active {{ border-color: #c9a96e; color: #c9a96e; }}
  .nav a:hover {{ border-color: #c9a96e; color: #c9a96e; }}
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
  var PW='anisha';
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
<div class="controls">
  <button id="btn-default" onclick="resetSort()">Default Sort</button>
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
      <th onclick="sortTable(0,'text')">Name</th>
      <th onclick="sortTable(8,'number')">Status</th>
      <th onclick="sortTable(1,'stage')">Deal Stage</th>
      <th onclick="sortTable(2,'number')">Deal Amount</th>
      <th onclick="sortTable(3,'date')">Date Attended</th>
      <th onclick="sortTable(4,'date')">Last Contacted</th>
      <th onclick="sortTable(7,'date')">Upcoming Mtg</th>
      <th onclick="sortTable(6,'number')">Task</th>
      <th onclick="sortTable(5,'number')"># Contacted</th>
      <th>LI</th>
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

  window.filterStage = function(val) {{
    var rows = document.querySelectorAll('#pipeline-body tr');
    rows.forEach(function(r) {{
      r.classList.toggle('hidden', val !== '' && r.dataset.stageLabel !== val);
    }});
    updateCount();
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


def main():
    if not HUBSPOT_TOKEN:
        print('ERROR: HUBSPOT_API_KEY not set', file=sys.stderr)
        sys.exit(1)

    for owner_cfg in OWNERS:
        print(f'\n=== {owner_cfg["name"]} ===', flush=True)
        nav_html = '<div class="nav">' + ''.join(
            f'<a href="{Path(o["out"]).name}" class="active">{o["name"]}</a>'
            if o is owner_cfg else
            f'<a href="{Path(o["out"]).name}">{o["name"]}</a>'
            for o in OWNERS
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

        print('Loading deals from cache...', flush=True)
        deals = load_deals_from_cache(owner_cfg['cache'])
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
                          owner_name=owner_cfg['name'], nav_html=nav_html)

        out = Path(__file__).parent / owner_cfg['out']
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding='utf-8')
        print(f'Written: {out}', flush=True)


if __name__ == '__main__':
    main()

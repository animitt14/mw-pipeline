"""
Score all contacts in GL Advisor Assigned and render an HTML table.
"""
import json, os, re, requests, sys, time
from pathlib import Path
from html import escape
from datetime import datetime, timezone, timedelta

# Pull render_nav + SCORED_CFG from generate_pipeline so the nav is never hardcoded here.
sys.path.insert(0, str(Path(__file__).parent))
from generate_pipeline import render_nav, SCORED_CFG

# Dashboard timestamps display in New York wall-clock time (DST-aware: EST winter, EDT summer).
def eastern_now():
    u = datetime.now(timezone.utc)
    mar8 = datetime(u.year, 3, 8, tzinfo=timezone.utc)
    dst_start = (mar8 + timedelta(days=(6 - mar8.weekday()) % 7)).replace(hour=7)
    nov1 = datetime(u.year, 11, 1, tzinfo=timezone.utc)
    dst_end = (nov1 + timedelta(days=(6 - nov1.weekday()) % 7)).replace(hour=6)
    off, name = (-4, 'EDT') if dst_start <= u < dst_end else (-5, 'EST')
    return u.astimezone(timezone(timedelta(hours=off))), name

# Single source of truth for the scored page (CI runs THIS file from the repo root).
# Always writes the deployed copy under docs/. Token comes from env (CI) or .env (local).
_OUT = Path(__file__).parent / 'docs' / 'advisor_assigned_scored.html'
TOKEN = os.environ.get('HUBSPOT_API_KEY', '').strip().replace('﻿', '')
if not TOKEN:
    _env_file = Path(r'C:\Users\Anisha Mittal\masterworks-events\.env').read_text()
    TOKEN = next(l.split('=', 1)[1].strip() for l in _env_file.splitlines() if 'HUBSPOT_API_KEY' in l)
    TOKEN = TOKEN.replace('﻿', '').strip('"').strip("'")
HEADERS = {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}
PORTAL = '5454671'

OWNER_NAMES = {'77771452':'Ani','73613833':'Erik','202057506':'Linna','35397207':'DelPozzo','1433036370':'Blake'}

# ── Scoring constants (from build_scored_html.py) ────────────────────────────
FINANCE_DOMAINS = {'jpmorgan.com','gs.com','goldmansachs.com','ubs.com','nb.com','pimco.com','kkr.com','virtu.com','blackrock.com','morganstanley.com','citadel.com','tdsecurities.com','ml.com','alliancebernstein.com','fticonsulting.com','stblaw.com','sullcrom.com','troutman.com','beckerglynn.com','dorflaw.com','willkie.com'}
HIGH_TITLE_TERMS = ['managing director','managing member','general partner','founding partner','managing partner','senior partner','fund manager','portfolio manager','chief executive','chief financial','chief operating','chief technology','chief investment','chief information','ceo','cfo','cto','coo','cio','head of']
_PARTNER_EXCL = {'account','channel','strategic','implementation','solutions','solution','business','technology','alliance','referral','reseller'}
SMALL_BIZ = ['restaurant','cafe','coffee shop','juice','smoothie','bakery','pizza','deli','bagel','sandwich','burger','sushi','ramen','steakhouse','bistro','tavern','eatery','food truck','catering','ice cream','dessert','pastry','wine bar','cocktail bar','speakeasy','salon','barbershop','barber shop','nail salon','nail studio','hair salon','beauty salon','day spa','lash studio','brow bar','massage','med spa','medspa','boutique','flower shop','florist','dry clean','laundromat','car wash','auto repair','auto body','pet grooming','dog grooming','gym','fitness studio','fitness center','yoga studio','pilates','crossfit','nonprofit','non-profit','foundation','charity','charities','ngo','social services','community organization']
WEALTH_ADV = ['wealth advisor','wealth management advisor','wealth management','wealth manager','private banker','private client','private wealth','financial advisor','financial planner','investment advisor','personal banking advisor']
TARGET_WEALTH = ['lpl financial','raymond james','jp morgan','jpmorgan','morgan stanley']
DOWNGRADE = ['real estate agent','realtor','re agent','re broker','art broker','art dealer','fine art broker','nft','crypto','web3','intern','assistant','paralegal','associate professor','clergy','pastor','personal trainer','fitness trainer','fitness instructor','fitness coach','yoga instructor','pilates instructor','gym owner','fitness management','property manager','property management','asset manager','building manager','data analyst','business analyst','research analyst','marketing analyst','financial analyst','credit analyst','junior analyst']
SERVICE = ['freelancer','freelance consultant','freelance designer','freelance writer','freelance photographer','freelance videographer','freelance developer','self-employed','independent contractor','sole proprietor','solopreneur','independent consultant','independent advisor','life coach','business coach','executive coach','career coach','health coach','wellness coach','dating coach','mindset coach','leadership coach','performance coach','personal chef','private chef']
CREATORS = ['content creator','influencer','youtuber','tiktoker','tiktok creator','blogger','podcaster','vlogger','brand ambassador','social media creator','social media influencer']
FINANCE_COS = ['goldman sachs','morgan stanley','jp morgan','jpmorgan','blackrock','blackstone','kkr','kohlberg kravis','carlyle group','apollo global','citadel','bridgewater','two sigma','renaissance technologies','pimco','vanguard','fidelity investments','merrill lynch','ubs','credit suisse','deutsche bank','barclays','wells fargo','citigroup','citibank','bank of america','neuberger berman','alliancebernstein','td securities','scotiabank','royal bank of canada','rbc','cibc','bmo','bank of montreal','lazard','evercore','jefferies','raymond james','point72','millennium management','tiger global','coatue','andreessen horowitz','sequoia capital','general atlantic','warburg pincus','bain capital','cerberus capital','oaktree capital','silver lake','skadden','kirkland & ellis','weil gotshal','latham & watkins','sullivan & cromwell','debevoise & plimpton','simpson thacher','cleary gottlieb','paul weiss','cravath','white & case','proskauer','sidley austin','davis polk','waterfall asset','schonfeld','sculptor capital','glenview capital','canyon partners','king street capital','anchorage capital','baupost','elliot management','paul singer']

def edom(email): return email.split('@')[-1].lower() if email and '@' in email else ''
def is_small_biz(co): return any(t in co.lower() for t in SMALL_BIZ)
def is_target_wealth(co): return any(f in (co or '').lower() for f in TARGET_WEALTH) or bool(re.search(r'\blpl\b',(co or '').lower()))
def has_high_title(title):
    tl = title.lower().strip(); t = ' ' + tl + ' '
    for term in HIGH_TITLE_TERMS:
        if (' '+term+' ') in t: return True
    if re.search(r'\bchief\b.+\bofficer\b', tl): return True
    if ' president ' in t and 'vice president' not in tl: return True
    if tl == 'partner' or tl.endswith(' partner'):
        prefix = tl[:-len(' partner')].strip() if ' partner' in tl else ''
        if prefix not in _PARTNER_EXCL: return True
    if tl == 'principal' or tl.startswith('principal ') or ' principal ' in t:
        if not any(tc in tl for tc in ['engineer','software','developer','analyst','scientist','researcher','architect']): return True
    return False
def is_physician(title, email, company):
    HOSP_DOM = {'northwell.edu','nyulangone.org','mountsinai.org'}
    if any(t in title for t in ['physician','surgeon','doctor','cardiologist','radiologist','psychiatrist','dermatologist','neurologist','anesthesiologist','ophthalmologist','dentist','medical director']): return True
    if edom(email) in HOSP_DOM: return True
    if any(t in company for t in ['medical center','health system','northwell','nyu langone','mount sinai']): return True
    return any(w.startswith('hospital') and w not in ('hospitality','hospitalier') for w in company.replace(',',' ').split())

NW_LOW_VALS  = {'less than $1 million','$1 million to $2.5 million','less than $50k','$50k to $100k','$100k to $250k','$50,000 - $150,000','$150,000 - $500,000','$50k-$200k','$150k-$500k','under $50k','under $150k'}
NW_MED_VALS  = {'$250k to $1 million','$500k to $1 million','$500,000 - $1,000,000','$500k-$1m'}

def nw_cap(p):
    nw = (p.get('net_worth') or '').lower().strip()
    ia = (p.get('total_investable_assets') or '').lower().strip()
    val = nw or ia
    if not val: return None
    # Hard cap at 2 for very low NW
    if any(v in val for v in ['less than $50','$50k','$50,000','under $50','$50 - $','$50k-','50k to 100','50,000 - 150']): return 2
    # Cap at 3 for $150K-$500K range
    if any(v in val for v in ['$150','$200','$250','$300','$400','$500k','$500,000','500k to','500,000 -','150,000','200,000','250,000','300,000','400,000']): return 3
    return None

def score_contact(p):
    title   = (p.get('jobtitle') or '').lower()
    company = (p.get('company')  or '').lower()
    email   = (p.get('email')    or '').lower()
    lc      = (p.get('lifecyclestage') or '').lower()
    call    = (p.get('call_completed') or '').lower()
    ob_call = (p.get('ob_call_completed') or '').lower()
    flags = []
    if lc == 'customer' or call == 'order completed': flags.append('invested')
    if lc == 'opportunity': flags.append('opportunity')
    if call == 'not interested' or ob_call == 'not interested': flags.append('not_interested')
    if call == 'no show' or ob_call == 'no show': flags.append('no_show')
    combined = title + ' ' + company
    no_data  = not title.strip() and not company.strip()
    rsvp  = (p.get('outbound_rsvp_to_event') or '')[:10]
    stage = (p.get('hs_v2_date_entered_current_stage') or '')[:10]
    if rsvp and stage and stage >= rsvp and 'opportunity' in flags: flags.remove('opportunity')
    if 'invested' in flags or 'opportunity' in flags: return 5, flags
    if 'not_interested' in flags: return 1, flags
    is_wa = any(t in combined for t in WEALTH_ADV)
    if is_wa:
        if is_target_wealth(company): flags.append('target_wealth_firm'); return 5, flags
        return 1, flags
    if any(t in combined for t in ['real estate agent','realtor','re agent','re broker']): return 2, flags
    if any(t in combined for t in ['art dealer','fine art broker','art broker','art advisor','art adviser','art consultant','gallery']) or 'fine art' in company: return 2, flags
    if any(t in title for t in ['music producer','music programming','music curation','music curator','music supervisor','film-maker','filmmaker','screenwriter','cinematographer']): return 2, flags
    if any(t in combined for t in SERVICE): return 2, flags
    if any(t in combined for t in CREATORS): return 2, flags
    if not company.strip() and edom(email) not in FINANCE_DOMAINS:
        if any(t in title for t in ['consultant','coach','advisor','adviser']):
            if not any(fc in title for fc in ['management consulting','strategy']): return 2, flags
    has_dg = any(t in combined for t in DOWNGRADE)
    if 'broker' in combined and not any(fc in company for fc in FINANCE_COS): has_dg = True
    if has_dg: return 1 if 'no_show' in flags else 2, flags
    if 'no_show' in flags: return 2, flags
    if no_data: return 2, flags
    _at_fin = any(fc in company for fc in FINANCE_COS)
    if edom(email) in FINANCE_DOMAINS: sc = 5
    elif is_physician(title, email, company): sc = 5
    elif has_high_title(title): sc = 5
    else:
        re_co  = any(t in company for t in ['real estate','realty','extell','related companies','tishman','sl green','brookfield'])
        re_ti  = any(t in title   for t in ['vp','svp','evp','director','executive','president','ceo','coo','chief'])
        med_hi = any(t in title   for t in ['vice president','vp','director','senior director','svp','evp','avp','senior manager','senior vice','associate director'])
        if _at_fin: sc = 4
        elif re_co and re_ti: sc = 4
        elif med_hi: sc = 4
        else: sc = 3
    if sc > 3 and is_small_biz(company) and 'invested' not in flags and 'opportunity' not in flags: sc = 3
    _is_founder = any(t in title for t in ['founder','co-founder','cofounder'])
    if _is_founder and 'invested' not in flags and 'opportunity' not in flags:
        _et = any(t in title for t in ['ceo','coo','cto','cfo','cio','cmo','cro','chief executive','chief operating','chief technology','chief financial','chief information','chief marketing','managing director','managing member','managing partner','general partner','president']) or bool(re.search(r'\bchief\b.+\bofficer\b',title))
        if not (edom(email) in FINANCE_DOMAINS or is_physician(title,email,company) or any(fc in company for fc in FINANCE_COS) or _et):
            sc = min(sc, 2)
    if sc == 5 and 'invested' not in flags and 'opportunity' not in flags:
        if not (edom(email) in FINANCE_DOMAINS or is_physician(title,email,company) or any(fc in company for fc in FINANCE_COS)): sc = 4
    # NW cap overrides everything except invested/opportunity
    if 'invested' not in flags and 'opportunity' not in flags:
        cap = nw_cap(p)
        if cap is not None: sc = min(sc, cap)
    return sc, flags

# ── Fetch live deal IDs from HubSpot ─────────────────────────────────────────
print('Fetching live deals from Advisor Assigned...')
deal_ids = []
after = None
while True:
    body = {
        'filterGroups': [{'filters': [
            {'propertyName': 'pipeline',   'operator': 'EQ', 'value': '880355706'},
            {'propertyName': 'dealstage',  'operator': 'EQ', 'value': '1339121714'},
        ]}],
        'properties': ['dealname'],
        'limit': 200,
    }
    if after: body['after'] = after
    r = requests.post('https://api.hubapi.com/crm/v3/objects/deals/search', headers=HEADERS, json=body)
    r.raise_for_status()
    data = r.json()
    deal_ids.extend(str(d['id']) for d in data['results'])
    after = data.get('paging', {}).get('next', {}).get('after')
    if not after: break
    time.sleep(0.2)
print(f'Deals: {len(deal_ids)}')

# ── Get deal->contact associations ───────────────────────────────────────────
print('Fetching associations...')
deal_to_contact = {}
for i in range(0, len(deal_ids), 100):
    batch = deal_ids[i:i+100]
    r = requests.post('https://api.hubapi.com/crm/v4/associations/deal/contact/batch/read',
                      headers=HEADERS, json={'inputs':[{'id':d} for d in batch]})
    r.raise_for_status()
    for item in r.json().get('results',[]):
        did = str(item['from']['id'])
        cids = [str(a['toObjectId']) for a in item.get('to',[])]
        if cids: deal_to_contact[did] = cids[0]
    time.sleep(0.15)
print(f'  {len(deal_to_contact)} deals matched to contacts')

# ── Also get deal owner + createdate ─────────────────────────────────────────
print('Fetching deal owners...')
deal_owners = {}
deal_created = {}
deal_called = {}
for i in range(0, len(deal_ids), 100):
    batch = deal_ids[i:i+100]
    r = requests.post('https://api.hubapi.com/crm/v3/objects/deals/batch/read',
                      headers=HEADERS, json={'inputs':[{'id':d} for d in batch],'properties':['hubspot_owner_id','createdate','num_contacted_notes']})
    r.raise_for_status()
    for d in r.json().get('results',[]):
        did = str(d['id'])
        deal_owners[did]   = d['properties'].get('hubspot_owner_id','')
        deal_created[did]  = (d['properties'].get('createdate') or '')[:10]
        deal_called[did]   = d['properties'].get('num_contacted_notes') or ''
    time.sleep(0.15)

# ── Fetch contact properties ──────────────────────────────────────────────────
print('Fetching contacts...')
all_cids = list(set(deal_to_contact.values()))
PROPS = ['firstname','lastname','email','jobtitle','company','lifecyclestage','call_completed','ob_call_completed','state','outbound_rsvp_to_event','hs_v2_date_entered_current_stage','net_worth','total_investable_assets']
contact_data = {}
for i in range(0, len(all_cids), 100):
    batch = all_cids[i:i+100]
    r = requests.post('https://api.hubapi.com/crm/v3/objects/contacts/batch/read',
                      headers=HEADERS, json={'inputs':[{'id':c} for c in batch],'properties':PROPS})
    r.raise_for_status()
    for c in r.json()['results']:
        contact_data[c['id']] = c['properties']
    time.sleep(0.15)
print(f'  {len(contact_data)} contacts fetched')

# ── Score and build rows ──────────────────────────────────────────────────────
rows = []
seen_contacts = set()
for did, cid in deal_to_contact.items():
    if cid in seen_contacts: continue
    seen_contacts.add(cid)
    cp = contact_data.get(cid, {})
    sc, flags = score_contact(cp)
    first = cp.get('firstname',''); last = cp.get('lastname','')
    name  = f'{first} {last}'.strip() or cp.get('email','') or cid
    owner_id = deal_owners.get(did,'')
    owner = OWNER_NAMES.get(owner_id, owner_id) or ''
    created = deal_created.get(did, '')
    old_deal = bool(created and created < '2026-04-10')
    PERSONAL_DOMS = {'gmail.com','yahoo.com','hotmail.com','outlook.com','icloud.com','me.com','aol.com','msn.com','live.com','mac.com','protonmail.com','proton.me'}
    _is_founder = any(t in (cp.get('jobtitle') or '').lower() for t in ['founder','co-founder','cofounder'])
    _personal_email = edom(cp.get('email','')) in PERSONAL_DOMS
    _no_firm = not any(fc in (cp.get('company') or '').lower() for fc in FINANCE_COS)
    needs_review = sc == 4 and _is_founder and _personal_email and _no_firm

    rows.append({
        'name': name, 'cid': cid,
        'hs_url': f'https://app.hubspot.com/contacts/{PORTAL}/record/0-1/{cid}',
        'score': sc, 'flags': flags, 'owner': owner,
        'jobtitle': cp.get('jobtitle','') or '',
        'company':  cp.get('company','')  or '',
        'review': needs_review,
        'created': created, 'old_deal': old_deal,
        'num_called': deal_called.get(did, ''),
    })

rows.sort(key=lambda r: (-r['score'], r['name']))
print(f'  {len(rows)} contacts scored')

# ── Render HTML ───────────────────────────────────────────────────────────────
SCORE_COLORS = {5:('#fff','#1e3a8a'),4:('#fff','#1d4ed8'),3:('#1e40af','#bfdbfe'),2:('#374151','#e5e7eb'),1:('#6b7280','#f3f4f6')}
SCORE_LABELS = {5:'5 High',4:'4 Med-High',3:'3 Medium',2:'2 Low-Med',1:'1 Low'}

def badge(sc):
    fg,bg = SCORE_COLORS[sc]
    return f'<span style="background:{bg};color:{fg};padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600">{SCORE_LABELS[sc]}</span>'

score_counts = {}
for r in rows: score_counts[r['score']] = score_counts.get(r['score'],0)+1
owner_counts = {}
for r in rows: owner_counts[r['owner']] = owner_counts.get(r['owner'],0)+1

review_count = sum(1 for r in rows if r['review'])
old_count    = sum(1 for r in rows if r['old_deal'])

tr_rows = ''
for r in rows:
    sc = r['score']
    rev_badge  = '<span style="background:#7c3aed;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;margin-left:5px">REVIEW</span>' if r['review'] else ''
    date_cell  = f'<span style="color:#c2410c;font-size:12px;font-weight:700">{r["created"]}</span>' if r['old_deal'] else f'<span style="color:#374151;font-size:12px">{r["created"]}</span>'
    hs_btn     = f'<a href="{escape(r["hs_url"])}" target="_blank" style="background:#ff7a59;color:#fff;padding:2px 9px;border-radius:4px;font-size:11px;font-weight:600;text-decoration:none;white-space:nowrap">HS</a>'
    called_val = r['num_called']
    called_cell = f'<span style="font-weight:600;color:#111827">{called_val}</span>' if called_val else '<span style="color:#9ca3af">—</span>'
    tr_rows += f'''<tr data-score="{sc}" data-owner="{escape(r['owner'])}" data-review="{'1' if r['review'] else '0'}" data-old="{'1' if r['old_deal'] else '0'}" data-name="{escape(r['name'].lower())}" data-title="{escape((r['jobtitle'] or '').lower())}" data-company="{escape((r['company'] or '').lower())}" data-created="{r['created']}" data-called="{called_val or 0}">
  <td style="font-weight:500;color:#111827">{escape(r['name'])}</td>
  <td>{badge(sc)}{rev_badge}</td>
  <td style="color:#374151;font-size:12px">{escape(r['jobtitle'])}</td>
  <td style="color:#374151;font-size:12px">{escape(r['company'])}</td>
  <td style="color:#6b7280;font-size:12px">{escape(r['owner'])}</td>
  <td>{date_cell}</td>
  <td style="text-align:center">{called_cell}</td>
  <td>{hs_btn}</td>
</tr>'''

_now_e, _now_tz = eastern_now()
now = _now_e.strftime('%B %d, %Y %H:%M') + ' ' + _now_tz
owners_sorted = sorted(owner_counts.keys())

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>GL Advisor Assigned — Scored</title>
<link rel="stylesheet" href="pipeline.css">
<style>
*{{box-sizing:border-box}}
.meta{{color:var(--text-3);font-size:12px;margin-bottom:16px}}
.filters{{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}}
.filter-btn{{background:var(--surface-2);border:1px solid var(--border);color:var(--text-2);padding:5px 14px;border-radius:5px;cursor:pointer;font-size:12px;font-weight:500;font-family:inherit}}
.filter-btn:hover{{border-color:var(--border-2)}}
.filter-btn.active{{background:var(--accent);border-color:var(--accent);color:#fff}}
.sep{{color:var(--text-3);font-size:12px;font-weight:500}}
.summary{{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}}
.sum-card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:8px 16px;font-size:12px;color:var(--text-2);font-weight:500}}
.sum-card b{{font-size:18px;display:block;color:var(--text);font-weight:700}}
.scored-table{{width:100%;border-collapse:collapse;font-size:13px;background:var(--surface);border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(14,20,34,.08)}}
.scored-table th{{background:var(--surface-2);color:var(--accent-2);font-weight:600;padding:10px 12px;text-align:left;border-bottom:2px solid var(--accent);white-space:nowrap;cursor:pointer;font-size:12px;text-transform:uppercase;letter-spacing:.04em;user-select:none}}
.scored-table th:hover{{color:var(--accent)}}
.scored-table th.sort-asc::after{{content:' ▲';font-size:10px}}
.scored-table th.sort-desc::after{{content:' ▼';font-size:10px}}
.scored-table td{{padding:8px 12px;border-bottom:1px solid var(--border);vertical-align:middle}}
.scored-table tr:hover td{{background:var(--surface-2)}}
.hidden{{display:none}}
</style>
</head>
<body>
<div id="pw-gate"><div id="pw-box"><h2>MASTERWORKS PIPELINE</h2><input id="pw-input" type="password" placeholder="Password" autofocus /><div id="pw-err"></div><button id="pw-btn" onclick="checkPw()">Enter</button></div></div>
<script>
(function(){{
  var PW='banksy',SK='pw_ok';
  if(localStorage.getItem(SK)==='1')document.getElementById('pw-gate').classList.add('hidden');
  window.checkPw=function(){{
    if(document.getElementById('pw-input').value===PW){{localStorage.setItem(SK,'1');document.getElementById('pw-gate').classList.add('hidden');}}
    else{{document.getElementById('pw-err').textContent='Incorrect password';document.getElementById('pw-input').value='';}}
  }};
  document.getElementById('pw-input').addEventListener('keydown',function(e){{if(e.key==='Enter')checkPw();}});
}})();
</script>
{render_nav(SCORED_CFG)}
<h1>GL Advisor Assigned — All Contacts Scored</h1>
<div class="meta">Generated {now} &nbsp;·&nbsp; {len(rows)} contacts</div>

<div class="summary">
  <div class="sum-card"><b>{len(rows)}</b>Total</div>
  {''.join(f'<div class="sum-card"><b>{score_counts.get(s,0)}</b>Score {s}</div>' for s in [5,4,3,2,1])}
  {''.join(f'<div class="sum-card"><b>{owner_counts[o]}</b>{o}</div>' for o in owners_sorted)}
  <div class="sum-card"><b>{review_count}</b>Needs Review</div>
  <div class="sum-card" style="border-color:#c2410c"><b style="color:#c2410c">{old_count}</b>Pre-Apr 10</div>
</div>

<div class="filters">
  <span class="sep">Score:</span>
  <button class="filter-btn active" onclick="setFilter('score','all')">All</button>
  <button class="filter-btn" onclick="setFilter('score','5')">5</button>
  <button class="filter-btn" onclick="setFilter('score','4')">4</button>
  <button class="filter-btn" onclick="setFilter('score','3')">3</button>
  <button class="filter-btn" onclick="setFilter('score','2')">2</button>
  <button class="filter-btn" onclick="setFilter('score','1')">1</button>
  <span class="sep" style="margin-left:8px">Owner:</span>
  <button class="filter-btn active" onclick="setFilter('owner','all')">All</button>
  {''.join(f'<button class="filter-btn" onclick="setFilter(\'owner\',\'{o}\')">{o}</button>' for o in owners_sorted)}
  <span class="sep" style="margin-left:8px">Flag:</span>
  <button class="filter-btn" onclick="setFilter('review','1')">Review ({review_count})</button>
  <button class="filter-btn" onclick="setFilter('old','1')" style="border-color:#c2410c;color:#c2410c">Pre-Apr 10 ({old_count})</button>
</div>

<table id="tbl" class="scored-table">
<thead><tr>
  <th onclick="sortBy('name')">Name</th>
  <th onclick="sortBy('score')">Score</th>
  <th onclick="sortBy('title')">Title</th>
  <th onclick="sortBy('company')">Company</th>
  <th onclick="sortBy('owner')">Owner</th>
  <th onclick="sortBy('created')">Deal Created</th>
  <th onclick="sortBy('called')" style="text-align:center">Called</th>
  <th>Link</th>
</tr></thead>
<tbody>{tr_rows}</tbody>
</table>

<script>
let scoreF='all', ownerF='all', reviewF='all', oldF='all';
let sortCol=null, sortDir=1;
const NUMERIC = new Set(['score','called']);

function applyVisibility() {{
  document.querySelectorAll('#tbl tbody tr').forEach(r => {{
    const show=(scoreF==='all'||r.dataset.score===scoreF)
      &&(ownerF==='all'||r.dataset.owner===ownerF)
      &&(reviewF==='all'||r.dataset.review==='1')
      &&(oldF==='all'||r.dataset.old==='1');
    r.classList.toggle('hidden',!show);
  }});
}}

function setFilter(type, val) {{
  if(type==='score') scoreF=val;
  else if(type==='owner') ownerF=val;
  else if(type==='review') reviewF = reviewF==='1'?'all':'1';
  else if(type==='old') oldF = oldF==='1'?'all':'1';
  document.querySelectorAll('.filter-btn').forEach(b => {{
    const s=b.getAttribute('onclick')||'', t=b.textContent;
    if(s.includes("'score'")) b.classList.toggle('active', scoreF==='all'?t==='All':t===scoreF);
    if(s.includes("'owner'")) b.classList.toggle('active', ownerF==='all'?t==='All':t.startsWith(ownerF));
    if(s.includes("'review'")) b.classList.toggle('active', reviewF==='1');
    if(s.includes("'old'"))    b.classList.toggle('active', oldF==='1');
  }});
  applyVisibility();
}}

function sortBy(col) {{
  if(sortCol===col) sortDir*=-1; else {{ sortCol=col; sortDir=col==='called'||col==='score'?-1:1; }}
  const tbody = document.querySelector('#tbl tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a,b) => {{
    let av=a.dataset[col]||'', bv=b.dataset[col]||'';
    if(NUMERIC.has(col)) return ((parseFloat(av)||0)-(parseFloat(bv)||0))*sortDir;
    return av.localeCompare(bv)*sortDir;
  }});
  rows.forEach(r => tbody.appendChild(r));
  document.querySelectorAll('#tbl thead th').forEach(th => {{
    th.classList.remove('sort-asc','sort-desc');
    if(th.getAttribute('onclick')===`sortBy('${{col}}')`) th.classList.add(sortDir===1?'sort-asc':'sort-desc');
  }});
  applyVisibility();
}}
</script>
</body>
</html>'''

_OUT.write_text(html, encoding='utf-8')
print(f'Saved: {_OUT}')

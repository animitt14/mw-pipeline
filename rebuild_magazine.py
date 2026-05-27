from pathlib import Path

MAGAZINE_CFG = {'name': 'Magazine', 'out': 'docs/magazine.html'}
OWNERS = [
    {'name': 'Ani', 'out': 'docs/index.html'},
    {'name': 'Erik', 'out': 'docs/erik.html'},
]
OVERVIEW_CFG = {'name': 'Overview',     'out': 'docs/overview.html'}
SCORED_CFG   = {'name': 'Adv Assigned', 'out': 'docs/advisor_assigned_scored.html'}
ALL_PAGES = OWNERS + [OVERVIEW_CFG, SCORED_CFG, MAGAZINE_CFG]

mag_src = Path(__file__).parent / 'magazine_src.html'
mag_nav = '<div class="nav">' + ''.join(
    f'<a href="{Path(o["out"]).name}" class="active">{o["name"]}</a>'
    if o["name"] == "Magazine" else
    f'<a href="{Path(o["out"]).name}">{o["name"]}</a>'
    for o in ALL_PAGES
) + '</div>'

nav_css = (
    '<style>'
    '.nav{display:flex;gap:4px;margin-bottom:18px;}'
    '.nav a{font-size:.76rem;padding:5px 14px;border-radius:5px;background:#dfe2e7;color:#4a5363;text-decoration:none;}'
    '.nav a.active,.nav a:hover{background:rgba(30,42,64,.10);color:#11203a;}'
    '@media print{.nav{display:none!important}}'
    '</style>'
)

mag_html = mag_src.read_text(encoding='utf-8')
mag_html = mag_html.replace(
    '</head>', f'{nav_css}</head>', 1,
).replace('<body>', f'<body>{mag_nav}', 1)

mag_out = Path(__file__).parent / MAGAZINE_CFG['out']
mag_out.parent.mkdir(parents=True, exist_ok=True)
mag_out.write_text(mag_html, encoding='utf-8')
print(f'Written: {mag_out} ({len(mag_html)} bytes)')

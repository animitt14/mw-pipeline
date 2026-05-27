from pathlib import Path

MAGAZINE_CFG = {'name': 'Magazine', 'out': 'docs/magazine.html'}
OWNERS = [
    {'name': 'Ani', 'out': 'docs/index.html'},
    {'name': 'Erik', 'out': 'docs/erik.html'},
]
OVERVIEW_CFG = {'name': 'Overview',     'out': 'docs/overview.html'}
SCORED_CFG   = {'name': 'Adv Assigned', 'out': 'docs/advisor_assigned_scored.html'}
ALL_PAGES = OWNERS + [OVERVIEW_CFG, SCORED_CFG, MAGAZINE_CFG]


def render_nav(active_name):
    return '<div class="nav">' + ''.join(
        f'<a href="{Path(o["out"]).name}" class="active">{o["name"]}</a>'
        if o["name"] == active_name else
        f'<a href="{Path(o["out"]).name}">{o["name"]}</a>'
        for o in ALL_PAGES
    ) + '</div>'


mag_src = Path(__file__).parent / 'magazine_src.html'
mag_nav = render_nav('Magazine')

mag_html = mag_src.read_text(encoding='utf-8')
mag_html = mag_html.replace(
    '</head>', '<link rel="stylesheet" href="nav.css"></head>', 1,
).replace('<body>', f'<body><div class="nav-shell">{mag_nav}</div>', 1)

mag_out = Path(__file__).parent / MAGAZINE_CFG['out']
mag_out.parent.mkdir(parents=True, exist_ok=True)
mag_out.write_text(mag_html, encoding='utf-8')
print(f'Written: {mag_out} ({len(mag_html)} bytes)')

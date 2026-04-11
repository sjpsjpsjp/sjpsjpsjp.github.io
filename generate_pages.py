#!/usr/bin/env python3
"""
generate_pages.py — Build research.html, downloads.html, pruitt_bib.bib,
and update the bio and honors in index.html from spcv.tex.

Run this script (python generate_pages.py) whenever spcv.tex is updated.
It reads paper entries from spcv.tex — titles, authors, venues, and the
\webid / \webshort / \webfull / \webtags / \weblinks / \webnotes commands —
and writes fresh output files.
"""

import json
import re, subprocess, sys
from pathlib import Path

TEX_FILE        = Path('spcv.tex')
HTML_FILE       = Path('research.html')
BIB_FILE        = Path('pruitt_bib.bib')
INDEX_FILE      = Path('index.html')
DOWNLOADS_FILE  = Path('downloads.html')
MAINTAINED_FILE = Path('maintained.json')

JOURNAL_ABBREVS = {
    'Journal of Finance':                          'JF',
    'Journal of Financial Economics':              'JFE',
    'American Economic Review':                    'AER',
    'American Economic Review: Insights':          'AERI',
    'Journal of Econometrics':                     'JoE',
    'Journal of Financial and Quantitative Analysis': 'JFQA',
    'Journal of Money, Credit and Banking':        'JMCB',
    'American Economic Journal: Macroeconomics':   'AEJMacro',
    'Critical Finance Review':                     'CFR',
    'Quantitative Economics':                      'QE',
}

def short_cite(p):
    """Return a short citation string for the dropdown, e.g. 'JF \'23' or 'R&R JFE'."""
    if p['rr_journal']:
        abbrev = JOURNAL_ABBREVS.get(p['rr_journal'], p['rr_journal'])
        return f'R&amp;R {abbrev}'
    if p['venue'].get('journal'):
        abbrev = JOURNAL_ABBREVS.get(p['venue']['journal'], p['venue']['journal'])
        date = p['venue'].get('date', '')
        year_m = re.search(r'\b(\d{4})\b', date)
        if year_m:
            yr = year_m.group(1)[-2:]
            return f"{abbrev} \u2019{yr}"
        return abbrev
    return ''

TAG_LABELS = {
    'asset-pricing':   'asset pricing',
    'debt':            'debt',
    'econometrics':    'econometrics',
    'equity':          'equity',
    'esg':             'ESG',
    'factor-models':   'factor models',
    'labor':           'labor',
    'macroeconomics':  'macroeconomics',
    'monetary-policy': 'monetary policy',
    'state-space':     'state space',
    'text-data':       'text data',
}

# ─────────────────────────────────────────────────────────────────────────────
# BRACE-BALANCED EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_braced(text, start):
    """Return (content, end_pos) for the {...} block starting at index `start`."""
    assert text[start] == '{', f"Expected '{{' at pos {start}, got {text[start]!r}"
    depth, i = 0, start
    while i < len(text):
        if text[i] == '\\':
            i += 2; continue          # skip escaped char
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start+1:i], i+1
        i += 1
    raise ValueError(f"Unmatched brace at {start}")

def all_command_args(text, command):
    """Return list of all brace-balanced arguments to \\command{...}."""
    pat = re.compile(r'\\' + re.escape(command) + r'\s*\{')
    results, pos = [], 0
    while True:
        m = pat.search(text, pos)
        if not m: break
        try:
            arg, end = extract_braced(text, m.end()-1)
            results.append(arg); pos = end
        except (ValueError, AssertionError):
            pos = m.end(); continue
    return results

def first_command_arg(text, command):
    args = all_command_args(text, command)
    return args[0].strip() if args else ''

def strip_command(text, command):
    """Remove all occurrences of \\command{...} from text."""
    while True:
        pat = re.compile(r'\\' + re.escape(command) + r'\s*\{')
        m = pat.search(text)
        if not m: break
        try:
            _, end = extract_braced(text, m.end()-1)
            text = text[:m.start()] + text[end:]
        except (ValueError, AssertionError):
            break
    return text

# ─────────────────────────────────────────────────────────────────────────────
# MACRO RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

def parse_macros(tex):
    """Parse \\newcommand{\\name}{value} definitions → dict."""
    macros = {}
    for m in re.finditer(r'\\newcommand\s*\{\\([a-zA-Z]+)\}\s*\{([^}]+)\}', tex):
        macros[m.group(1)] = m.group(2)
    return macros

def resolve_url(raw, macros):
    """If raw is \\macroname, return the URL from macros; else return raw."""
    raw = raw.strip()
    if raw.startswith('\\'):
        cmd = raw.lstrip('\\')
        return macros.get(cmd, raw)
    return raw

# ─────────────────────────────────────────────────────────────────────────────
# LATEX → HTML
# ─────────────────────────────────────────────────────────────────────────────

def process_hrefs(text, macros):
    """Convert \\href{url}{label} to <a href="url">label</a>."""
    result, i = [], 0
    pat = re.compile(r'\\href\s*\{')
    while i < len(text):
        m = pat.match(text, i)
        if m:
            try:
                url_raw, j = extract_braced(text, m.end()-1)
                url = resolve_url(url_raw, macros)
                while j < len(text) and text[j] in ' \t\n': j += 1
                if j < len(text) and text[j] == '{':
                    label_raw, j2 = extract_braced(text, j)
                    label = latex_to_html(label_raw, macros)
                    result.append(f'<a href="{url}">{label}</a>')
                    i = j2; continue
            except (ValueError, AssertionError, IndexError):
                pass
        result.append(text[i]); i += 1
    return ''.join(result)

def process_wrapped(text, cmd, open_tag, close_tag, macros):
    """Convert \\cmd{content} to open_tag + content + close_tag."""
    result, i = [], 0
    pat = re.compile(r'\\' + re.escape(cmd) + r'\s*\{')
    while i < len(text):
        m = pat.match(text, i)
        if m:
            try:
                content, j = extract_braced(text, m.end()-1)
                result.append(f'{open_tag}{latex_to_html(content, macros)}{close_tag}')
                i = j; continue
            except (ValueError, AssertionError):
                pass
        result.append(text[i]); i += 1
    return ''.join(result)

def latex_to_html(text, macros=None):
    if macros is None: macros = {}
    text = process_hrefs(text, macros)
    text = process_wrapped(text, 'emph',   '<em>',     '</em>',     macros)
    text = process_wrapped(text, 'textbf', '<strong>', '</strong>', macros)
    text = process_wrapped(text, 'textit', '<em>',     '</em>',     macros)
    text = text.replace(r'\&', '&amp;').replace(r'\%', '%').replace(r'\$', '$').replace(r'\#', '#')
    text = re.sub(r"``(.*?)''", r'"\1"', text, flags=re.DOTALL)
    text = re.sub(r'\\[a-zA-Z]+(?:\s*\{[^}]*\})?', '', text)  # remaining commands
    text = re.sub(r'[ \t]+', ' ', text).strip()
    return text

def clean_abstract(text):
    """Minimal LaTeX cleanup for abstract text (no HTML tags needed)."""
    text = text.replace('---', '\u2014').replace('--', '\u2013')
    text = text.replace(r'\&', '&amp;').replace(r'\%', '%').replace(r'\$', '$')
    text = re.sub(r"``(.*?)''", r'"\1"', text, flags=re.DOTALL)
    text = re.sub(r'\\[a-zA-Z]+(?:\*?\{[^}]*\})?', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def note_to_html(raw, macros):
    if not raw: return ''
    text = process_hrefs(raw, macros)
    text = re.sub(r'\\[a-zA-Z]+(?:\{[^}]*\})?', '', text)
    return re.sub(r'\s+', ' ', text).strip()

# ─────────────────────────────────────────────────────────────────────────────
# ITEM PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_title_url(item, macros):
    """Return (title_text, title_url) from the first \\href or ``Title''."""
    m = re.search(r'\\href\s*\{', item)
    if m:
        try:
            url_raw, j = extract_braced(item, m.end()-1)
            url = resolve_url(url_raw, macros)
            while j < len(item) and item[j] in ' \t\n': j += 1
            if item[j] == '{':
                label_raw, _ = extract_braced(item, j)
                title = re.sub(r"^``|''$", '', label_raw.strip()).strip('"').strip()
                return title, url
        except (ValueError, AssertionError, IndexError):
            pass
    m = re.search(r"``(.+?)''", item, re.DOTALL)
    if m:
        return m.group(1).strip(), ''
    return '', ''

def parse_authors(item, macros):
    """Return 'with <a>Name</a> and Name' HTML, or '' if solo."""
    m = re.search(r'\(with\s+(.*?)\)', item, re.DOTALL)
    if not m: return ''
    raw = m.group(1)
    # expand macro URLs then process hrefs
    for name, url in macros.items():
        raw = raw.replace('\\' + name, url)
    html = process_hrefs(raw, macros)
    return 'with ' + re.sub(r'\s+', ' ', html).strip()

def parse_venue(item):
    """For published/other papers. Returns dict: journal, date, year, volume, number, pages, lead."""
    v = {'journal': '', 'date': '', 'year': '', 'volume': '', 'number': '', 'pages': '', 'lead': False}
    m = re.search(r'\\textbf\s*\{\\emph\s*\{([^}]+)\}\s*\}', item)
    if m:
        v['journal'] = m.group(1)
        after = item[m.end():]
        dm = re.match(r'\s*,\s*([A-Za-z\u2013\-]+\.?\s+\d{4}[^\\]*)', after)
        if dm:
            date_str = re.sub(r'\s+', ' ', dm.group(1).strip().rstrip('.').rstrip(','))
            v['date'] = date_str
            ym = re.search(r'\b(\d{4})\b', date_str)
            if ym:
                v['year'] = ym.group(1)
            # volume(number): pages  e.g. "78(4): 1967-2008" or "44(2-3): 341-365"
            vm = re.search(r'(\d+)\(([^)]+)\):\s*([\d\u2013\-]+)', date_str)
            if vm:
                v['volume'] = vm.group(1)
                v['number'] = vm.group(2)
                v['pages']  = vm.group(3).replace('\u2013', '-')
    v['lead'] = bool(re.search(r'\\textit\s*\{lead article\}', item, re.IGNORECASE))
    return v

def parse_rr(item):
    """Return R&R journal name or ''."""
    m = re.search(r'R\\&R[,\s]*\\emph\s*\{([^}]+)\}', item)
    if m: return m.group(1)
    m = re.search(r'R&R[,\s]*\\emph\s*\{([^}]+)\}', item)
    if m: return m.group(1)
    return ''

def parse_awards(item, macros):
    """Return list of {url, label} for award lines."""
    awards = []
    pat = re.compile(r'\\href\s*\{([^}]*)\}\s*\{((?:Winner|Awarded|[12]\d{3})[^}]*)\}')
    for m in pat.finditer(item):
        url = resolve_url(m.group(1), macros)
        label = m.group(2).strip()
        if url and label:
            awards.append({'url': url, 'label': label})
    return awards

def parse_item(item_text, macros, section_type):
    p = {
        'id': '', 'title': '', 'title_url': '', 'authors': '',
        'authors_bib': '', 'institution': '',
        'venue': {}, 'rr_journal': '', 'awards': [],
        'short': '', 'full': '', 'tags': [], 'links': [], 'notes': '',
        'section': section_type,
    }

    # Extract web metadata
    p['id']          = first_command_arg(item_text, 'webid')
    p['short']       = clean_abstract(first_command_arg(item_text, 'webshort'))
    p['full']        = clean_abstract(first_command_arg(item_text, 'webfull'))
    p['notes']       = note_to_html(first_command_arg(item_text, 'webnotes'), macros)
    p['authors_bib'] = first_command_arg(item_text, 'webauthors')
    p['institution'] = first_command_arg(item_text, 'webinstitution')

    tags_raw = first_command_arg(item_text, 'webtags')
    p['tags'] = [t.strip() for t in tags_raw.split(',') if t.strip()]

    links_raw = first_command_arg(item_text, 'weblinks')
    if links_raw:
        for part in links_raw.split('|'):
            if '::' in part:
                label, url = part.split('::', 1)
                p['links'].append({'label': label.strip(), 'url': url.strip()})

    # Strip all web commands before further parsing
    clean = item_text
    for cmd in ('webid','webshort','webfull','webtags','weblinks','webnotes',
                'webauthors','webinstitution'):
        clean = strip_command(clean, cmd)

    p['title'], p['title_url'] = parse_title_url(clean, macros)

    # For non-published sections, never link the title — all links come from \weblinks
    if p['section'] not in ('published', 'other'):
        p['title_url'] = ''

    p['authors']    = parse_authors(clean, macros)
    p['rr_journal'] = parse_rr(clean)
    p['awards']     = parse_awards(clean, macros)

    if section_type in ('published', 'other'):
        p['venue'] = parse_venue(clean)

    return p

# ─────────────────────────────────────────────────────────────────────────────
# SECTION SPLITTING
# ─────────────────────────────────────────────────────────────────────────────

def get_section(tex, keywords):
    """Return the text of the section whose header contains all keywords."""
    section_starts = [m.start() for m in re.finditer(r'\\section\b', tex)]
    for i, start in enumerate(section_starts):
        nxt = section_starts[i+1] if i+1 < len(section_starts) else len(tex)
        header_end = min(tex.find(r'\begin{', start), nxt) if r'\begin{' in tex[start:nxt] else nxt
        header = tex[start:header_end]
        if all(kw.lower() in header.lower() for kw in keywords):
            list_start = tex.find(r'\begin{', start)
            return tex[list_start:nxt] if list_start != -1 else ''
    return ''

def split_items(text):
    positions = [m.start() for m in re.finditer(r'\\item\b', text)]
    if not positions: return []
    return [text[positions[i]: positions[i+1] if i+1 < len(positions) else len(text)]
            for i in range(len(positions))]

def parse_section(tex, keywords, section_type, macros):
    text = get_section(tex, keywords)
    if not text:
        print(f'Warning: section not found: {keywords}', file=sys.stderr)
        return []
    papers = []
    for item in split_items(text):
        p = parse_item(item, macros, section_type)
        if p['id']:
            papers.append(p)
    return papers

# ─────────────────────────────────────────────────────────────────────────────
# BIB ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

def make_bib_key(p):
    """Derive BibTeX key: lastnames_year (lowercase, underscores)."""
    authors = p.get('authors_bib', '')
    if authors:
        lasts = []
        for name in [a.strip() for a in authors.split(' and ')]:
            last = name.split(',')[0].strip() if ',' in name else name.split()[-1]
            lasts.append(re.sub(r'[^a-zA-Z]', '', last).lower())
        key = '_'.join(lasts)
    else:
        key = p['id'].replace('-', '_')
    year = p['venue'].get('year', '')
    if not year and p['section'] in ('working', 'older'):
        ym = re.search(r'-(\d{4})$', p['id'])
        if ym:
            year = ym.group(1)
    if year:
        key += f'_{year}'
    return key

def bib_entry(p, key=None):
    """Return a complete BibTeX entry string for paper p."""
    if key is None:
        key = make_bib_key(p)
    is_article = p['section'] in ('published', 'other')
    entry_type = 'article' if is_article else 'techreport'

    fields = []
    fields.append(('title',  p['title']))
    if p['authors_bib']:
        fields.append(('author', p['authors_bib']))
    if is_article:
        if p['venue'].get('journal'):
            fields.append(('journal', p['venue']['journal']))
        if p['venue'].get('year'):
            fields.append(('year',    p['venue']['year']))
        if p['venue'].get('volume'):
            fields.append(('volume',  p['venue']['volume']))
        if p['venue'].get('number'):
            fields.append(('number',  p['venue']['number']))
        if p['venue'].get('pages'):
            fields.append(('pages',   p['venue']['pages']))
    else:
        if p['institution']:
            fields.append(('institution', p['institution']))
        year = p['venue'].get('year', '')
        if not year:
            ym = re.search(r'-(\d{4})$', p['id'])
            if ym:
                year = ym.group(1)
        if year:
            fields.append(('year', year))

    body = '\n'.join(f'{k} = {{{v}}},' for k, v in fields)
    return f'@{entry_type}{{{key},\n{body}\n}}\n'

def generate_bib(all_papers):
    """Return complete .bib file contents, with duplicate-key deduplication."""
    header = '% Generated by generate_research.py — do not edit by hand\n\n'
    # Assign keys, fall back to webid if two papers share the same derived key
    keys = [make_bib_key(p) for p in all_papers]
    seen = {}
    for i, (k, p) in enumerate(zip(keys, all_papers)):
        if k in seen:
            # First collision: rename the earlier entry to webid-based key
            earlier_i = seen[k]
            keys[earlier_i] = all_papers[earlier_i]['id'].replace('-', '_')
            keys[i] = p['id'].replace('-', '_')
            print(f'  Note: key conflict on "{k}" — '
                  f'using webid keys "{keys[earlier_i]}" and "{keys[i]}"',
                  file=sys.stderr)
        else:
            seen[k] = i
    return header + '\n'.join(bib_entry(p, k) for p, k in zip(all_papers, keys))

# ─────────────────────────────────────────────────────────────────────────────
# HTML ASSEMBLY
# ─────────────────────────────────────────────────────────────────────────────

def paper_html(p):
    L = []
    tags_str = ' '.join(p['tags'])
    L.append(f'        <div class="paper" id="{p["id"]}" data-topics="{tags_str}">')

    # Title
    L.append('            <div class="paper-title">')
    if p['title_url'] and p['section'] in ('published', 'other'):
        L.append(f'                <a href="{p["title_url"]}">{p["title"]}</a>')
    else:
        L.append(f'                {p["title"]}')
    if p['rr_journal']:
        L.append('                <span class="rr-badge">R&amp;R</span>')
    L.append('            </div>')

    # Venue + Authors (combined onto one line when both present)
    if p['venue'].get('journal'):
        lead = '&nbsp;<span class="lead">lead article</span>' if p['venue'].get('lead') else ''
        date = p['venue'].get('date','')
        date_str = f', {date}' if date else ''
        jname = p["venue"]["journal"]
        if p.get('title_url'):
            jname_html = f'<a href="{p["title_url"]}">{jname}</a>'
        else:
            jname_html = jname
        venue_inner = f'<span class="journal">{jname_html}</span>{date_str}{lead}'
        if p['authors']:
            L.append('            <div class="paper-meta">')
            L.append(f'                {venue_inner}<span class="meta-sep">&nbsp;&middot;&nbsp;</span><span class="paper-authors-inline">{p["authors"]}</span>')
            L.append('            </div>')
        else:
            L.append('            <div class="paper-venue">')
            L.append(f'                {venue_inner}')
            L.append('            </div>')
    elif p['rr_journal']:
        rr_inner = f'<em>{p["rr_journal"]}</em>'
        if p['authors']:
            L.append('            <div class="paper-meta" style="font-size:0.88em;color:var(--maroon);margin-bottom:6px;">')
            L.append(f'                {rr_inner}<span class="meta-sep">&nbsp;&middot;&nbsp;</span><span class="paper-authors-inline" style="color:var(--text-muted);font-style:normal;">{p["authors"]}</span>')
            L.append('            </div>')
        else:
            L.append('            <div class="paper-venue" style="font-size:0.88em;color:var(--maroon);margin-bottom:6px;">')
            L.append(f'                {rr_inner}')
            L.append('            </div>')
    elif p['authors']:
        L.append(f'            <div class="paper-authors">{p["authors"]}</div>')

    # Links
    if p['links']:
        L.append('            <div class="paper-links">')
        for lk in p['links']:
            L.append(f'                <a href="{lk["url"]}">{lk["label"]}</a>')
        L.append('            </div>')

    # Abstract
    if p['short']:
        if p['full']:
            L.append('            <div class="abstract-toggle" onclick="toggleAbstract(this)">')
            L.append('                <span class="abstract-label">Sentence abstract <span class="abstract-arrow">&#9658;</span></span>')
            L.append(f'                <p class="abstract-sentence">{p["short"]}</p>')
            L.append(f'                <p class="abstract-full" style="display:none">{p["full"]}</p>')
            L.append('            </div>')
        else:
            L.append('            <div class="abstract-toggle no-expand">')
            L.append('                <span class="abstract-label">Sentence abstract</span>')
            L.append(f'                <p class="abstract-sentence">{p["short"]}</p>')
            L.append('            </div>')

    # Awards
    if p['awards']:
        L.append('            <div class="paper-award">')
        L.append('<br>\n'.join(f'                <a href="{a["url"]}">{a["label"]}</a>' for a in p['awards']))
        L.append('            </div>')

    # Notes
    if p['notes']:
        L.append('            <div class="paper-note">')
        L.append(f'                {p["notes"]}')
        L.append('            </div>')

    # Tags
    if p['tags']:
        L.append('            <div class="paper-tags">')
        for tag in p['tags']:
            L.append(f'                <span class="tag">{TAG_LABELS.get(tag, tag)}</span>')
        L.append('            </div>')

    L.append('        </div>')
    L.append('')
    return '\n'.join(L)

def filter_bar_html(all_papers):
    all_tags = sorted({t for p in all_papers for t in p['tags']})
    L = ['    <div class="filter-bar">',
         '        <div class="filter-bar-label">Filter by topic</div>',
         "        <button class=\"filter-btn active\" data-filter=\"all\" onclick=\"filterPapers('all', this)\">All</button>"]
    for tag in all_tags:
        label = TAG_LABELS.get(tag, tag)
        L.append(f"        <button class=\"filter-btn\" data-filter=\"{tag}\" onclick=\"filterPapers('{tag}', this)\">{label}</button>")
    L.append('    </div>')
    return '\n'.join(L)

def dropdown_html(working, published, other, older, prefix=''):
    """Build the nav dropdown HTML.  prefix should be 'research.html' when this
    dropdown appears on a page other than research.html itself."""
    L = ['                    <a href="research.html" class="dropdown-top-link">\u2191 Research</a>']
    def section(label, papers):
        L.append(f'                    <div class="dropdown-section-label">{label}</div>')
        for p in papers:
            cite = short_cite(p)
            cite_html = f' <span style="font-size:0.8em;color:#8b2332;">{cite}</span>' if cite else ''
            L.append(f'                    <a href="{prefix}#{p["id"]}">{p["title"]}{cite_html}</a>')
    section('Working Papers', working)
    section('Published Papers', published)
    section('Other Articles', other)
    section('Older Working Papers', older)
    return '\n'.join(L)

def section_html(heading, anchor, papers):
    L = [f'    <section class="paper-section" id="section-{anchor}">',
         f'        <h2 class="section-heading">{heading}</h2>',
         '']
    for p in papers:
        L.append(paper_html(p))
    L.append('    </section>')
    return '\n'.join(L)

# ─────────────────────────────────────────────────────────────────────────────
# FULL PAGE TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────

PAGE_TEMPLATE = '''\
<!DOCTYPE html>
<!-- Generated by generate_research.py — do not edit by hand -->
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Research \u2013 Seth Pruitt</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
    <link rel="icon" href="favicon.ico">
</head>
<body>

<header>
    <div class="header-inner">
        <div class="site-name"><a href="index.html">Seth Pruitt</a></div>
        <nav>
            <a href="index.html" class="nav-link">About</a>

            <div class="nav-dropdown">
                <button class="nav-dropdown-toggle active" id="research-toggle" onclick="toggleDropdown()">
                    Research <span class="arrow">\u25be</span>
                </button>
                <div class="dropdown-menu" id="research-dropdown">
{DROPDOWN}
                </div>
            </div>

            <a href="downloads.html" class="nav-link">Downloads</a>
        </nav>
    </div>
</header>

<div class="canyon-banner"></div>

<div class="page">

    <p style="font-size:0.9em;margin-bottom:8px;font-weight:500;">
        <a href="pruitt_bib.bib">.bib file</a>
    </p>

    <!-- ======================================================
         FILTER BAR
         ====================================================== -->

{FILTER_BAR}

    <!-- ======================================================
         WORKING PAPERS
         ====================================================== -->

{WORKING}

    <!-- ======================================================
         PUBLISHED PAPERS
         ====================================================== -->

{PUBLISHED}

    <!-- ======================================================
         OTHER ARTICLES
         ====================================================== -->

{OTHER}

    <!-- ======================================================
         OLDER WORKING PAPERS
         ====================================================== -->

{OLDER}

</div><!-- .page -->

<footer>economics research by Seth Pruitt</footer>

<script>
/* ---- Dropdown ---- */
function toggleDropdown() {
    const toggle = document.getElementById(\'research-toggle\');
    const menu   = document.getElementById(\'research-dropdown\');
    toggle.classList.toggle(\'open\');
    menu.classList.toggle(\'open\');
}

// Close dropdown when clicking a link inside it
document.getElementById(\'research-dropdown\').addEventListener(\'click\', function(e) {
    if (e.target.tagName === \'A\') {
        document.getElementById(\'research-toggle\').classList.remove(\'open\');
        document.getElementById(\'research-dropdown\').classList.remove(\'open\');
    }
});

// Close dropdown when clicking outside
document.addEventListener(\'click\', function(e) {
    const dropdown = document.querySelector(\'.nav-dropdown\');
    if (!dropdown.contains(e.target)) {
        document.getElementById(\'research-toggle\').classList.remove(\'open\');
        document.getElementById(\'research-dropdown\').classList.remove(\'open\');
    }
});

/* ---- Topic filter ---- */
function filterPapers(topic, btn) {
    document.querySelectorAll(\'.filter-btn\').forEach(b => b.classList.remove(\'active\'));
    btn.classList.add(\'active\');
    const papers = document.querySelectorAll(\'.paper\');
    if (topic === \'all\') {
        papers.forEach(p => p.style.display = \'\');
    } else {
        papers.forEach(function(p) {
            const topics = p.getAttribute(\'data-topics\') || \'\';
            p.style.display = topics.split(\' \').indexOf(topic) !== -1 ? \'\' : \'none\';
        });
    }
    document.querySelectorAll(\'.paper-section\').forEach(function(section) {
        const visible = Array.from(section.querySelectorAll(\'.paper\')).some(p => p.style.display !== \'none\');
        section.style.display = visible ? \'\' : \'none\';
    });
}

/* ---- Abstract toggle ---- */
function toggleAbstract(el) {
    var label    = el.querySelector(\'.abstract-label\');
    var sentence = el.querySelector(\'.abstract-sentence\');
    var full     = el.querySelector(\'.abstract-full\');
    var arrow    = el.querySelector(\'.abstract-arrow\');
    var expanded = full.style.display !== \'none\';
    if (expanded) {
        label.childNodes[0].textContent = \'Sentence abstract \';
        arrow.innerHTML = \'&#9658;\';
        sentence.style.display = \'\';
        full.style.display = \'none\';
    } else {
        label.childNodes[0].textContent = \'Full abstract \';
        arrow.innerHTML = \'&#9660;\';
        sentence.style.display = \'none\';
        full.style.display = \'\';
    }
}
</script>

</body>
</html>
'''

# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOADS PAGE
# ─────────────────────────────────────────────────────────────────────────────

# ── Maintained software cards ────────────────────────────────────────────────

def maintained_card_html(item):
    """Render one maintained-software card from a maintained.json entry."""
    name     = item.get('name', '')
    desc     = item.get('description', '')
    language = item.get('language', '')
    github   = item.get('github', '')
    install  = item.get('install', '')

    badge = (f'<span class="dl-lang-badge">{language}</span>' if language else '')
    footer_parts = []
    if github:
        footer_parts.append(f'<a href="{github}" class="dl-pill">GitHub</a>')
    if install:
        footer_parts.append(f'<code class="dl-install">{install}</code>')
    footer = '\n            '.join(footer_parts)

    slug = re.sub(r'\s+', '-', name.strip().lower())
    return (
        f'        <div class="dl-maintained-card" id="maintained-{slug}">\n'
        f'            <div class="dl-maintained-name">{name}{(" " + badge) if badge else ""}</div>\n'
        f'            <div class="dl-maintained-desc">{desc}</div>\n'
        '            <div class="dl-maintained-footer">\n'
        f'                {footer}\n'
        '            </div>\n'
        '        </div>'
    )

def maintained_section_html():
    """Read maintained.json and return the maintained grid HTML, or '' if empty."""
    if not MAINTAINED_FILE.exists():
        return ''
    items = json.loads(MAINTAINED_FILE.read_text(encoding='utf-8'))
    if not items:
        return ''
    cards = '\n'.join(maintained_card_html(item) for item in items)
    return (
        '    <h2 class="dl-section-heading">Maintained Software</h2>\n'
        '    <p class="dl-intro">Code I actively develop and maintain.</p>\n'
        '    <div class="dl-maintained-grid">\n'
        f'{cards}\n'
        '    </div>\n'
    )

# ── Archive table ─────────────────────────────────────────────────────────────

# Link labels that are NOT substantive archives (preprint/interview/draft links)
NON_ARCHIVE_LABELS = {'ssrn', 'draft', 'paper', 'faculti interview', 'software'}

def archive_links(p):
    """Return only the links that represent substantive download archives."""
    return [lk for lk in p['links']
            if lk['label'].lower() not in NON_ARCHIVE_LABELS]

def dl_authors_display(p):
    """Format 'Last1, Last2, and Last3 (year)' from webauthors + venue year."""
    authors_bib = p.get('authors_bib', '')
    lasts = []
    if authors_bib:
        for name in [a.strip() for a in authors_bib.split(' and ')]:
            last = name.split(',')[0].strip() if ',' in name else name.split()[-1]
            lasts.append(last)
    if len(lasts) == 1:
        author_str = lasts[0]
    elif len(lasts) == 2:
        author_str = f'{lasts[0]} and {lasts[1]}'
    elif lasts:
        author_str = ', '.join(lasts[:-1]) + f', and {lasts[-1]}'
    else:
        author_str = p.get('title', '')

    year = p['venue'].get('year', '')
    if not year:
        ym = re.search(r'(\d{4})', p.get('id', ''))
        if ym:
            year = ym.group(1)
    return f'{author_str} ({year})' if year else author_str

def dl_row_html(p):
    """Render one archive table row for a paper with substantive download links."""
    links = archive_links(p)
    if not links:
        return ''

    display   = dl_authors_display(p)
    journal   = p['venue'].get('journal', '')
    if not journal and p['section'] == 'working':
        journal = 'working paper'

    paper_href = f'research.html#{p["id"]}' if p.get('id') else ''
    if paper_href:
        left_name = f'<a href="{paper_href}" class="dl-paper-link">{display}</a>'
    else:
        left_name = f'<span class="dl-paper-link">{display}</span>'

    journal_html = (f'<span class="dl-journal"><em>{journal}</em></span>'
                    if journal else '')

    pills = '\n                '.join(
        f'<a href="{lk["url"]}" class="dl-pill">{lk["label"]}</a>'
        for lk in links
    )

    L = ['        <div class="dl-row">',
         '            <div class="dl-paper-cell">',
         f'                {left_name}']
    if journal_html:
        L.append(f'                {journal_html}')
    L += ['            </div>',
          '            <div class="dl-links-cell">',
          f'                {pills}',
          '            </div>',
          '        </div>']
    return '\n'.join(L)

def _dl_sort_key(p):
    """Sort published first (by year desc), then working, then other, then older."""
    sec_order = {'published': 0, 'other': 1, 'working': 2, 'older': 3}
    year = p['venue'].get('year', '')
    if not year:
        ym = re.search(r'(\d{4})', p.get('id', ''))
        if ym:
            year = ym.group(1)
    yr = -int(year) if year else 0
    return (sec_order.get(p['section'], 4), yr)

DOWNLOADS_TEMPLATE = '''\
<!DOCTYPE html>
<!-- Generated by generate_research.py \u2014 do not edit by hand -->
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Downloads \u2013 Seth Pruitt</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
    <link rel="icon" href="favicon.ico">
</head>
<body>

<header>
    <div class="header-inner">
        <div class="site-name"><a href="index.html">Seth Pruitt</a></div>
        <nav>
            <a href="index.html" class="nav-link">About</a>

            <div class="nav-dropdown">
                <button class="nav-dropdown-toggle" id="research-toggle" onclick="toggleDropdown()">
                    Research <span class="arrow">\u25be</span>
                </button>
                <div class="dropdown-menu" id="research-dropdown">
{DROPDOWN}
                </div>
            </div>

            <a href="downloads.html" class="nav-link active">Downloads</a>
        </nav>
    </div>
</header>

<div class="canyon-banner"></div>

<div class="page">

{MAINTAINED}
    <h2 class="dl-section-heading">Replication Archives</h2>
    <p class="dl-intro">Code and data for published and working papers, as released alongside each paper.</p>

    <div class="dl-archive-table">
{ARCHIVE_ROWS}
    </div>

</div><!-- .page -->

<footer>economics research by Seth Pruitt</footer>

<script>
function toggleDropdown() {
    const toggle = document.getElementById(\'research-toggle\');
    const menu   = document.getElementById(\'research-dropdown\');
    toggle.classList.toggle(\'open\');
    menu.classList.toggle(\'open\');
}
document.addEventListener(\'click\', function(e) {
    const dropdown = document.querySelector(\'.nav-dropdown\');
    if (dropdown && !dropdown.contains(e.target)) {
        document.getElementById(\'research-toggle\').classList.remove(\'open\');
        document.getElementById(\'research-dropdown\').classList.remove(\'open\');
    }
});
</script>

</body>
</html>
'''

def generate_downloads(all_papers, working, published, other, older):
    """Generate downloads.html from papers that have substantive archive links."""
    papers_with_dl = sorted(
        [p for p in all_papers if archive_links(p)],
        key=_dl_sort_key
    )
    rows = '\n'.join(dl_row_html(p) for p in papers_with_dl if dl_row_html(p))
    # Use research.html prefix so dropdown links navigate correctly from downloads.html
    dl_dropdown = dropdown_html(working, published, other, older, prefix='research.html')
    html = DOWNLOADS_TEMPLATE
    html = html.replace('{DROPDOWN}',     dl_dropdown)
    html = html.replace('{MAINTAINED}',   maintained_section_html())
    html = html.replace('{ARCHIVE_ROWS}', rows)
    return html

# ─────────────────────────────────────────────────────────────────────────────
# BIO / INDEX.HTML
# ─────────────────────────────────────────────────────────────────────────────

def parse_bio(tex, macros):
    """Extract the \\section Bio paragraph and convert to an HTML <p>."""
    m = re.search(r'\\section\s*\{[^}]*Bio[^}]*\}(.*?)(?=\\section|\\end\{resume\})',
                  tex, re.DOTALL)
    if not m:
        return ''
    raw = m.group(1).strip()
    # Collapse whitespace / newlines into single spaces
    raw = re.sub(r'\s+', ' ', raw)
    html = latex_to_html(raw, macros)
    # Wrap journals after "such as" in "the" if missing — normalise leading "the"
    # (the tex has no "the" before journals; index.html had "such as the")
    html = re.sub(r'such as (<em>)', r'such as the \1', html)
    return f'            <p>{html}</p>'

def update_index_dropdown(working, published, other, older):
    """Replace the <!-- dropdown-start/end --> block in index.html with generated dropdown."""
    if not INDEX_FILE.exists():
        print(f"Warning: {INDEX_FILE} not found — skipping dropdown update.", file=sys.stderr)
        return
    # Use research.html prefix so links navigate correctly from index.html
    inner = dropdown_html(working, published, other, older, prefix='research.html')
    new_block = f'                    <!-- dropdown-start -->\n{inner}\n                    <!-- dropdown-end -->'
    index = INDEX_FILE.read_text(encoding='utf-8')
    index_new, n = re.subn(
        r'                    <!-- dropdown-start -->.*?<!-- dropdown-end -->',
        new_block,
        index,
        flags=re.DOTALL,
    )
    if n == 0:
        print("Warning: dropdown markers not found in index.html — no update made.", file=sys.stderr)
        return
    if index_new == index:
        print(f"Dropdown in {INDEX_FILE} already up to date.")
    else:
        INDEX_FILE.write_text(index_new, encoding='utf-8')
        print(f"Updated dropdown in {INDEX_FILE}")

def update_index_bio(bio_html):
    """Replace the <!-- bio-start/end --> block in index.html with new bio_html."""
    if not INDEX_FILE.exists():
        print(f"Warning: {INDEX_FILE} not found — skipping bio update.", file=sys.stderr)
        return
    index = INDEX_FILE.read_text(encoding='utf-8')
    new_block = f'            <!-- bio-start -->\n{bio_html}\n            <!-- bio-end -->'
    index_new, n = re.subn(
        r'            <!-- bio-start -->.*?<!-- bio-end -->',
        new_block,
        index,
        flags=re.DOTALL,
    )
    if n == 0:
        print("Warning: bio markers not found in index.html — no update made.", file=sys.stderr)
        return
    if index_new == index:
        print(f"Bio in {INDEX_FILE} already up to date.")
    else:
        INDEX_FILE.write_text(index_new, encoding='utf-8')
        print(f"Updated bio in {INDEX_FILE}")

def parse_honors(tex):
    """Extract all \\section{Honors} entries.

    Returns a list of (url_or_None, text) tuples in document order.
    Linked entries get a URL; plain-text entries get url=None.
    LaTeX en-dashes (--) are converted to Unicode –.
    """
    m = re.search(
        r'\\section\s*\{[^}]*Honors[^}]*\}(.*?)(?=\\section|\\end\{resume\})',
        tex, re.DOTALL,
    )
    if not m:
        return []
    raw = m.group(1)
    # Split on LaTeX line-break \\
    entries = re.split(r'\\\\\s*', raw)
    results = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        # \webhide anywhere on the line → exclude from website
        if r'\webhide' in entry:
            continue
        hm = re.match(r'\\href\s*\{([^}]+)\}\s*\{([^}]+)\}', entry)
        if hm:
            url  = hm.group(1).strip()
            text = hm.group(2).strip()
        else:
            url  = None
            text = entry
        # Convert LaTeX en-dash
        text = text.replace('--', '\u2013')
        results.append((url, text))
    return results


def update_index_honors(honors):
    """Replace the <!-- honors-start/end --> block in index.html."""
    if not INDEX_FILE.exists():
        print(f"Warning: {INDEX_FILE} not found — skipping honors update.", file=sys.stderr)
        return
    lines = ['    <ul class="awards-list">']
    for url, text in honors:
        if url:
            lines.append(f'        <li><a href="{url}">{text}</a></li>')
        else:
            lines.append(f'        <li>{text}</li>')
    lines.append('    </ul>')
    inner = '\n'.join(lines)
    new_block = f'    <!-- honors-start -->\n{inner}\n    <!-- honors-end -->'

    index = INDEX_FILE.read_text(encoding='utf-8')
    index_new, n = re.subn(
        r'    <!-- honors-start -->.*?<!-- honors-end -->',
        new_block,
        index,
        flags=re.DOTALL,
    )
    if n == 0:
        print("Warning: honors markers not found in index.html — no update made.", file=sys.stderr)
        return
    if index_new == index:
        print(f"Honors in {INDEX_FILE} already up to date.")
    else:
        INDEX_FILE.write_text(index_new, encoding='utf-8')
        print(f"Updated honors in {INDEX_FILE}")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    tex = TEX_FILE.read_text(encoding='utf-8')
    macros = parse_macros(tex)

    working   = parse_section(tex, ['working', 'papers'],         'working',   macros)
    published = parse_section(tex, ['refereed', 'articles'],      'published',  macros)
    other     = parse_section(tex, ['other', 'articles'],         'other',      macros)
    older     = parse_section(tex, ['older', 'working', 'papers'],'older',      macros)

    print(f"Parsed: {len(working)} working, {len(published)} published, "
          f"{len(other)} other, {len(older)} older working papers")

    all_papers = working + published + other + older

    dropdown = dropdown_html(working, published, other, older)

    html = PAGE_TEMPLATE
    html = html.replace('{DROPDOWN}',    dropdown)
    html = html.replace('{FILTER_BAR}',  filter_bar_html(all_papers))
    html = html.replace('{WORKING}',     section_html('Working Papers',       'working',       working))
    html = html.replace('{PUBLISHED}',   section_html('Published Papers',     'published',     published))
    html = html.replace('{OTHER}',       section_html('Other Articles',       'other',         other))
    html = html.replace('{OLDER}',       section_html('Older Working Papers', 'older-working', older))

    HTML_FILE.write_text(html, encoding='utf-8')
    print(f"Wrote {HTML_FILE}  ({len(html):,} bytes)")

    dl_html = generate_downloads(all_papers, working, published, other, older)
    DOWNLOADS_FILE.write_text(dl_html, encoding='utf-8')
    print(f"Wrote {DOWNLOADS_FILE}  ({len(dl_html):,} bytes)")

    bib = generate_bib(all_papers)
    BIB_FILE.write_text(bib, encoding='utf-8')
    print(f"Wrote {BIB_FILE}   ({len(bib):,} bytes)")

    bio_html = parse_bio(tex, macros)
    if bio_html:
        update_index_bio(bio_html)
    else:
        print("Warning: Bio section not found in spcv.tex.", file=sys.stderr)

    honors = parse_honors(tex)
    if honors:
        update_index_honors(honors)
    else:
        print("Warning: Honors section not found in spcv.tex.", file=sys.stderr)

    update_index_dropdown(working, published, other, older)

    compile_pdf()


def compile_pdf():
    """Compile spcv.tex → spcv.pdf using pdflatex (run twice for references)."""
    if not TEX_FILE.exists():
        print(f"Warning: {TEX_FILE} not found — skipping PDF compile.", file=sys.stderr)
        return
    cmd = ['pdflatex', '-interaction=nonstopmode', str(TEX_FILE)]
    cwd = TEX_FILE.parent.resolve()
    for run in range(2):
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if result.returncode != 0:
            log_tail = '\n'.join(result.stdout.splitlines()[-20:])
            print(f"pdflatex error (run {run+1}):\n{log_tail}", file=sys.stderr)
            return
    pdf = cwd / 'spcv.pdf'
    print(f"Compiled {pdf}  ({pdf.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()

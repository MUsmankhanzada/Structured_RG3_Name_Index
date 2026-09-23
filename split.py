import csv
import re
import difflib

INPUT = "personenverzeichnis_raw_final_sorted.csv"
OUTPUT = "personenverzeichnis_split.csv"
INPUT_COLUMN = "raw_entry"  # change if your column is named differently

SIMILARITY_THRESHOLD = 0.4  # tweak if needed

# ---------------------------------------------------------------------------
# Office / role and institution abbreviation lists.
# Multi-word entries (e.g. "vic. gen.", "mag. gen.") are matched as a whole
# phrase and take priority over shorter matches at the same position.
# ---------------------------------------------------------------------------
PERSON_OR_ROLE_PREFIXES = (
    "ep.", "aep.", "vic.", "vic. gen.", "chorep.", "chorepisc.", "offic.",
    "presb.", "subdiacon.", "cler.", "fr.", "scolar.", "provincia", "dioc.",
    "legatus", "nuntius", "collector", "subcollect.", "armig.", "mil.",
    "baron.", "domic.", "opid.", "laici", "civit.", "castella", "rex",
    "regina", "regnum", "plebanus", "pleban.", "marchio", "capellani",
    "flumen", "dux", "ducissa", "comes", "comites", "comitissa", "com.",
    "com. palat.", "dominium", "conv.", "provinc. concilium", "concilium",
    "doctor", "monach.", "curia aepisc.", "camerar.", "abbat.", "abb.",
    "alt.", "advocatus", "advoc.", "archidiacon.", "archidiac.", "burgravi",
    "burgravius", "portus",
    # --- added after the first data audit ---
    "pp.", "card.", "diac.", "diac. card.", "presb. card.", "card. presb.",
    "tit.", "patr.", "prior", "priorat.", "prep.", "prepos.", "dec.",
    "decan.", "custos", "custod.", "scholast.", "scolast.", "thesaurar.",
    "can.", "capit.", "natus", "nuncup.", "el.", "resp.", "quondam",
    # --- added after the second data audit ---
    "mag.", "mag. gen.", "ord.", "priorissa", "duxissa", "ducis",
    "princeps", "lantgr.", "bar.", "imp.", "scol.", "precept.",
    "thesaur.", "not.", "not. ap. sed.", "ap. sed.",
    # --- added after the third data audit ---
    "archipresb.", "castellanus", "heres",
)

INSTITUTION_PREFIXES = (
    "eccl.", "par. eccl.", "(par.) eccl.", "capel.", "nova capel.", "mon.",
    "dom.", "hosp.", "parochia", "studium", "abbatia", "dominus terre",
)

# 'al.' / 'dict.' / 'sive' / 'seu' introduce an ALIAS, not an office. They
# stack ("al. dict.") and can be followed by a bracketed variant.
ALIAS_MARKERS = {"al.", "al", "dict.", "dictus", "dicta", "sive", "seu"}

# Markers introducing a RELATIONSHIP to another person (widow/child/wife of).
# These must be detected BEFORE the office scan, because the relation text
# itself often contains office words ("Vlrici bar. de Haenow uxor").
# 'relict*'/'relect*' are matched by prefix: OCR yields relictæ/relictä/
# relictā/relecta/…
RELATION_MARKERS = {"natus", "nata", "uxor", "filius", "filia"}
RELATION_PREFIXES = ("relict", "relect")

# Generational suffixes: part of the personal name, never a family name.
NAME_SUFFIXES = {"iunior", "junior", "senior", "iun.", "sen."}

ROMAN_RE = re.compile(r'^[IVXLCDM]+\.?$')

# Particles that may appear bracketed as an optional part of a family name.
NOBILIARY_PARTICLES = {"de", "von", "van", "di", "del", "della"}

# OCR/spelling normalisations applied per token.
TOKEN_FIXES = {"dc": "de"}


def _sorted_components(prefixes):
    comps = [p.strip().split(' ') for p in prefixes]
    comps.sort(key=lambda c: (-len(c), -sum(len(x) for x in c)))
    return comps


INSTITUTION_COMPONENTS = _sorted_components(INSTITUTION_PREFIXES)
ROLE_OR_INSTITUTION_COMPONENTS = _sorted_components(
    PERSON_OR_ROLE_PREFIXES + INSTITUTION_PREFIXES
)


# ---------------------------------------------------------------------------
# Pre-cleaning
# ---------------------------------------------------------------------------

MOJIBAKE_MARKERS = ('Ã', 'â€', 'Â', 'Å', 'Ä', 'Ð', 'Ñ')


def fix_mojibake(text):
    """Repair UTF-8 bytes that were decoded as cp1252/latin-1
    ('DumplÃ¶s' -> 'Dumplös', '385â€”402' -> '385—402')."""
    if not isinstance(text, str):
        return text
    for _ in range(3):
        if not any(mark in text for mark in MOJIBAKE_MARKERS):
            break
        repaired = None
        for enc in ('cp1252', 'latin1'):
            try:
                candidate = text.encode(enc).decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            if candidate != text:
                repaired = candidate
            break
        if repaired is None:
            break
        text = repaired
    return text


# Words the OCR split with a stray space. These cannot be repaired by a general
# rule, because structurally identical strings are genuinely multi-word here
# ('zu der golden luffe', 'de villa Eschwilre', 'in Pomerio', 'van den Weghe'),
# so each join is listed explicitly. 'Sle sie' is corroborated by six correctly
# spelled 'Slesie' entries elsewhere in the index.
OCR_SPACE_JOINS = {
    "Sle sie": "Slesie",
    "Appsp erg": "Appsperg",
    "Dorn dorff": "Dorndorff",
    "Cy lia": "Cylia",
    "Wolfilsteyn er": "Wolfilsteyner",
    "Reyng hersuliet": "Reynghersuliet",
}

_OCR_SPACE_RE = re.compile(
    '|'.join(re.escape(k) for k in sorted(OCR_SPACE_JOINS, key=len, reverse=True))
)


def fix_ocr_spaces(text):
    """Rejoin the listed OCR space-breaks ('Sle sie' -> 'Slesie')."""
    return _OCR_SPACE_RE.sub(lambda m: OCR_SPACE_JOINS[m.group(0)], text)


# Two index entries occasionally arrive glued into one line, when a pageless
# cross-reference is followed by the next headword:
#   'Liebhardus, Liphardus v. Luphardus. Linka Strociurn 93.'
# = 'Liebhardus, Liphardus v. Luphardus.' + 'Linka Strociurn 93.'
# The split is deliberately narrow: the cross-reference part must carry no
# digits, its target must be a single ordinary capitalised word, and the
# remainder must itself start with an ordinary capitalised word and end in a
# page run. That leaves 'Angelus Corario v. Gregorius XII. pp.' and
# 'Petrus de Luna v. Benedictus XIII. pp. 312.' untouched, because 'pp.' is
# not an ordinary capitalised word.
_MERGED_ENTRY = re.compile(
    r'^(?P<ref>[^\d]+?\sv\.\s+[A-ZÄÖÜ][a-zäöüß]+)\.\s+'
    r'(?P<next>[A-ZÄÖÜ][a-zäöüß]{2,}.*?\d[\d\s.—–-]*)\.?$'
)


def split_merged_entries(line):
    """Return the one or two index entries contained in a raw line."""
    m = _MERGED_ENTRY.match(line.strip())
    if m:
        first = m.group('ref').strip().rstrip('.') + '.'
        second = m.group('next').strip().rstrip('.') + '.'
        return [first, second]
    return [line]


def fix_unbalanced_brackets(text):
    """
    Repair OCR bracket damage so the tokenizer still sees a closed group:
      'Schaczel (Scheczel 123 164.'        -> 'Schaczel (Scheczel) 123 164.'
      'de Blaetzhem (Blatzhem, (Blaeczheim)' -> '... (Blatzhem, Blaeczheim)'
    A stray '(' inside an already-open group is dropped; an unclosed group is
    closed before the trailing page numbers (or at the end of the line).
    """
    if text.count('(') == text.count(')'):
        return text

    out, depth = [], 0
    for ch in text:
        if ch == '(':
            if depth > 0:
                continue          # stray '(' inside an open group -> drop it
            depth += 1
        elif ch == ')':
            if depth == 0:
                continue          # stray ')' with nothing open -> drop it
            depth -= 1
        out.append(ch)
    text = ''.join(out)

    if depth > 0:                 # still open: close before the page run
        m = re.search(r'\s+(?=\d[\d\s.—–-]*\.?\s*$)', text)
        if m:
            text = text[:m.start()] + ')' + text[m.start():]
        else:
            text = text.rstrip('. ') + ')'
    return text


def fix_ocr_linebreaks(text):
    """Rejoin words split across a printed line break ('Con-stancianen.' ->
    'Constancianen.'). Only lowercase-to-lowercase, so suffix variants like
    '(-berg, -berch)' are untouched."""
    return re.sub(r'(?<=[a-zäöüß])-\s*(?=[a-zäöüß])', '', text)


# A bracket glued to a letter with no space is editorial, not a name variant.
# Lowercase content = optional letters INSIDE one word ('Deys(t)', 'Je(ronimus)',
# 'Hil(le)manni'). Uppercase content = a real variant that merely lost its space
# ('Diick(Deyk)').
_INWORD_BRACKET = re.compile(
    r'([A-Za-zäöüßÄÖÜ]*)\(([a-zäöüß]+)\)([A-Za-zäöüß]*)'
)
_GLUED_VARIANT = re.compile(r'(?<=[A-Za-zäöüß])\((?=[A-ZÄÖÜ])')

# Sentinels stand in for in-word parentheses while the text is tokenized, so
# the bracket-splitting regex ignores them. They are turned back into real
# parentheses as each word token is built.
_LP, _RP = '\x01', '\x02'


def fix_inword_brackets(text):
    """
    Protect editorial in-word brackets so the tokenizer does not shatter the
    name. The bracketed letters are part of the name (an editorial expansion
    of an abbreviated source form) and the reading is kept EXACTLY as printed:

        'Je(ronimus) Rutili'    -> firstname 'Je(ronimus)'
        'Johannes Hil(le)manni' -> familyname 'Hil(le)manni'
        'Fridericus Deys(t)'    -> familyname 'Deys(t)'

    Nothing is added to the variant columns: the contracted spelling is not an
    attested alternative name, just the abbreviation the brackets expand.

    A glued bracket whose content is capitalised is a genuine variant that
    merely lost its space, so a space is inserted instead ('Diick(Deyk)').
    """
    text = _GLUED_VARIANT.sub(' (', text)

    def repl(m):
        head, inner, tail = m.group(1), m.group(2), m.group(3)
        if not head and not tail:          # free-standing bracket: leave alone
            return m.group(0)
        return f'{head}{_LP}{inner}{_RP}{tail}'

    return _INWORD_BRACKET.sub(repl, text)


def restore_inword_brackets(text):
    """Turn the in-word sentinels back into literal parentheses."""
    return text.replace(_LP, '(').replace(_RP, ')')


def split_cf_clause(text):
    """Split off a trailing '; cf. X' cross-reference clause."""
    m = re.search(r'[;,]?\s*\bcf\.\s*(.+)$', text, flags=re.IGNORECASE)
    if m:
        return text[:m.start()].strip(), m.group(1).strip().rstrip('.').strip()
    return text, ''


# A page token: a number, optionally a range ('304—307').
_NUM = r'\d+(?:\s*[—–-]\s*\d+)?'
# A run of page tokens, separated by whitespace and/or a period
# ('13 23 269', '58. 345', '385—402').
_PAGERUN = re.compile(
    r'((?:' + _NUM + r')(?:\s*\.?\s+(?:' + _NUM + r'))*)\s*\.?\s*$'
)


def extract_pages(text):
    """Pull the trailing page/column run off the end. Handles dash ranges and
    period-separated runs ('Georgius Lichtenberg 127. 256.' -> '127 256')."""
    m = _PAGERUN.search(text)
    if m:
        pages = m.group(1)
        pages = re.sub(r'\s*([—–-])\s*', r'\1', pages)   # tighten ranges
        pages = re.sub(r'\.\s*', ' ', pages)             # drop inner periods
        pages = re.sub(r'\s+', ' ', pages).strip()
        return pages, text[:m.start()].strip()
    return '', text.rstrip('.').strip()


# ---------------------------------------------------------------------------
# Tokenizing
# ---------------------------------------------------------------------------

def build_tokens(text):
    """('word', w) / ('bracket', content) tokens."""
    segments = [s for s in re.split(r'(\([^)]*\))', text) if s]
    tokens = []
    for seg in segments:
        if seg.startswith('(') and seg.endswith(')'):
            content = seg[1:-1].strip()
            if not content:
                continue
            # A bracket holding only a nobiliary particle is an optional part
            # of the family name, not a variant: 'Bertoldus (de) Cellis' ->
            # familyname '(de) Cellis'. Emit it as a word so it survives
            # verbatim and still marks the family-name boundary.
            if content.lower() in NOBILIARY_PARTICLES:
                tokens.append(('word', f'({content})'))
            else:
                tokens.append(('bracket', content))
        else:
            for w in seg.split():
                # ',' ';' ':' are clause separators, not part of the name
                # ('... de Murssen; aep. Colon. 152 347.')
                w = restore_inword_brackets(w.rstrip(',;:'))
                if w:
                    tokens.append(('word', TOKEN_FIXES.get(w.lower(), w)))
    return tokens


def token_matches(tk, comp):
    ttype, val = tk
    comp = comp.strip()
    if comp.startswith('(') and comp.endswith(')'):
        return ttype == 'bracket' and val.strip().lower() == comp[1:-1].strip().lower()
    if ttype != 'word':
        return False
    # Office abbreviations are always printed lowercase in this index. A
    # capitalised form is a surname, not an office: 'Johannes Rex 18.',
    # 'Hermannus Ducis 131.', 'Marcus Wenceslai dict. Dux 365.'
    if val[:1].isupper():
        return False
    return val.lower() == comp.lower()


def find_prefix_boundary(tokens, sorted_components, start=0):
    n = len(tokens)
    for i in range(start, n):
        for comps in sorted_components:
            plen = len(comps)
            if i + plen > n:
                continue
            if all(token_matches(tokens[i + k], comps[k]) for k in range(plen)):
                return i, plen
    return None, 0


def tokens_to_text(tokens):
    return ' '.join(val if t == 'word' else f'({val})' for t, val in tokens).strip()


# ---------------------------------------------------------------------------
# Relation / alias extraction
# ---------------------------------------------------------------------------

def extract_relation(tokens):
    """
    Pull out a 'relicta/natus/uxor/filius ...' relationship clause.
    Runs BEFORE the office scan, since the clause often contains office words.

      'Agnes relictæ Herbordi Kuykens de Tremonia' ->
          name=['Agnes'], relation='relictæ Herbordi Kuykens de Tremonia'
      'Elisabet Vlrici bar. de Haenow uxor' ->
          name=['Elisabet'], relation='Vlrici bar. de Haenow uxor'
    """
    idx = None
    for i, (ttype, val) in enumerate(tokens):
        if ttype != 'word':
            continue
        low = val.lower().rstrip('.')
        if low in RELATION_MARKERS or low.startswith(RELATION_PREFIXES):
            idx = i
            break
    if idx is None or idx == 0:
        return tokens, ''

    # marker at the very end -> the whole middle is the relation clause
    if idx == len(tokens) - 1:
        return tokens[:1], tokens_to_text(tokens[1:])
    return tokens[:idx], tokens_to_text(tokens[idx:])


def extract_alias(tokens):
    """
    Pull out alias segments introduced by 'al.' / 'dict.' / 'sive'.
    Markers stack, brackets stay attached to their alias, and a following
    'de ...' is handed back as part of the name (territorial family name).

      'Albertus Beyer al. Steneken'            -> name='Albertus Beyer', alias='Steneken'
      'Andreas dict. Kelermaister'             -> name='Andreas', alias='Kelermaister'
      'Bertoldus dict. Diues (Rikem) al. Schomaker'
                                               -> alias='Diues (Rikem); Schomaker'
      'Nicolaus Henrici al. dict. Schilchin (Schibchin) de Cruczenaco'
                                               -> name='Nicolaus Henrici de Cruczenaco'
    """
    first = None
    for i, (ttype, val) in enumerate(tokens):
        if ttype == 'word' and val.lower() in ALIAS_MARKERS:
            first = i
            break
    if first is None:
        return tokens, ''

    head = tokens[:first]
    tail = []
    aliases, current = [], []

    i = first
    while i < len(tokens):
        ttype, val = tokens[i]
        if ttype == 'word' and val.lower() in ALIAS_MARKERS:
            if current:
                aliases.append(current)
                current = []
            i += 1
            continue
        if ttype == 'word' and val.lower() == 'de' and current:
            # 'de ...' AFTER alias content is the territorial family name
            # ('al. Vockenlander de Kytzpuhel'). Directly after the marker it
            # belongs to the alias itself ('al de Gisenheim').
            aliases.append(current)
            current = []
            tail = tokens[i:]
            break
        current.append(tokens[i])
        i += 1
    if current:
        aliases.append(current)

    return head + tail, '; '.join(tokens_to_text(a) for a in aliases)


# ---------------------------------------------------------------------------
# Name splitting
# ---------------------------------------------------------------------------

def similarity(a, b):
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def is_variant(bracket_content, comparison_targets):
    variants = [v.strip() for v in bracket_content.split(',') if v.strip()]
    if not variants:
        variants = [bracket_content.strip()]
    for v in variants:
        if v.startswith('-'):       # suffix variant, e.g. '-berg'
            return True
        for target in comparison_targets:
            if target and similarity(v, target) >= SIMILARITY_THRESHOLD:
                return True
    return False


def strip_leading_de(s):
    s = s.strip()
    return s[3:].strip() if s.lower().startswith('de ') else s


def classify_bracket(content, target, firstname_words, familyname_words,
                     firstname_variants, familyname_variants, zusatz):
    target_words = firstname_words if target == 'firstname' else familyname_words
    last_word = target_words[-1] if target_words else ''
    comparison_targets = [last_word, strip_leading_de(' '.join(target_words))]

    if content.strip().lower()[:2] == 'de':
        zusatz.append(content)
        return
    if is_variant(content, comparison_targets):
        (firstname_variants if target == 'firstname' else familyname_variants).append(content)
    else:
        zusatz.append(content)


def split_name_tokens(tokens):
    """Bracket-priority / de-based split over the NAME part only. Regnal
    numerals ('Gregorius XII.') stay with the firstname."""
    de_token_idx = first_bracket_idx = None
    for i, (ttype, val) in enumerate(tokens):
        if ttype == 'word' and val.strip('()').lower() == 'de' and de_token_idx is None:
            de_token_idx = i
        if ttype == 'bracket' and first_bracket_idx is None:
            first_bracket_idx = i

    use_bracket_priority = (
        first_bracket_idx is not None and de_token_idx is not None
        and first_bracket_idx < de_token_idx
    )

    firstname_words, familyname_words = [], []
    firstname_variants, familyname_variants, zusatz = [], [], []

    if use_bracket_priority:
        for i, (ttype, val) in enumerate(tokens):
            if i < first_bracket_idx:
                firstname_words.append(val)
            elif i == first_bracket_idx:
                classify_bracket(val, 'firstname', firstname_words, familyname_words,
                                 firstname_variants, familyname_variants, zusatz)
            else:
                if ttype == 'word':
                    familyname_words.append(val)
                else:
                    classify_bracket(val, 'familyname', firstname_words, familyname_words,
                                     firstname_variants, familyname_variants, zusatz)
    else:
        plain_words = [val for (t, val) in tokens if t == 'word']

        de_index = None
        for i, w in enumerate(plain_words):
            if w.strip('()').lower() == 'de':
                de_index = i
                break
        if de_index is None and len(plain_words) >= 2:
            de_index = 1
            # regnal numerals and generational suffixes stay with the forename
            while de_index < len(plain_words) and (
                ROMAN_RE.match(plain_words[de_index])
                or plain_words[de_index].lower() in NAME_SUFFIXES
            ):
                de_index += 1
            if de_index >= len(plain_words):
                de_index = None

        word_counter, last_target = 0, None
        for ttype, val in tokens:
            if ttype == 'bracket':
                classify_bracket(val, last_target or 'firstname',
                                 firstname_words, familyname_words,
                                 firstname_variants, familyname_variants, zusatz)
            else:
                if de_index is None or word_counter < de_index:
                    firstname_words.append(val)
                    last_target = 'firstname'
                else:
                    familyname_words.append(val)
                    last_target = 'familyname'
                word_counter += 1

    # A second 'de' group inside the family name is a place of origin, not part
    # of the surname: 'de Baldinotis de Pistorio' -> familyname 'de Baldinotis',
    # zusatz 'de Pistorio'. Index 0 is the surname's own particle, so the scan
    # starts at 1. An in-word bracket ('de Wetze(de)') is not a separate token
    # and is therefore untouched.
    for i in range(1, len(familyname_words)):
        if familyname_words[i].strip('()').lower() == 'de':
            zusatz.append(' '.join(familyname_words[i:]))
            familyname_words = familyname_words[:i]
            break

    return (
        ' '.join(firstname_words).strip(),
        '; '.join(firstname_variants).strip(),
        ' '.join(familyname_words).strip(),
        '; '.join(familyname_variants).strip(),
        '; '.join(zusatz).strip(),
    )


def split_role_block(role_tokens):
    """Split the office/institution block; stray brackets become Zusatz."""
    if not role_tokens:
        return '', '', []
    extra_zusatz = [val for (t, val) in role_tokens
                    if t == 'bracket' and val.lower().rstrip('.') not in ('el', 'par')]
    kept = [tk for tk in role_tokens
            if tk[0] == 'word' or tk[1].lower().rstrip('.') in ('el', 'par')]

    inst_idx, _ = find_prefix_boundary(kept, INSTITUTION_COMPONENTS)
    if inst_idx is None:
        return tokens_to_text(kept), '', extra_zusatz
    if inst_idx == 0:
        return '', tokens_to_text(kept), extra_zusatz
    return tokens_to_text(kept[:inst_idx]), tokens_to_text(kept[inst_idx:]), extra_zusatz


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------

def parse_entry(line):
    line = fix_mojibake(line).strip()
    if not line:
        return None
    raw_original = line

    text = fix_ocr_spaces(line)
    text = fix_unbalanced_brackets(text)
    text = fix_ocr_linebreaks(text)
    text = fix_inword_brackets(text)

    # A. '; cf. X' cross-reference (before pages: pages may precede it)
    text, cf_reference = split_cf_clause(text)

    # B. pages (dash ranges + period-separated runs)
    pages, rest = extract_pages(text)

    # C. comma-listed alternate forenames in pure cross-reference entries
    m = re.match(r'^([A-ZÄÖÜ][\wäöüß]*),\s*([A-ZÄÖÜ][\wäöüß]*)\s+(?:v\.|s\.)\s+(.+)$', rest)
    if m and not pages:
        return {
            'raw_entry': raw_original,
            'firstname': m.group(1), 'firstname_variant': m.group(2),
            'familyname': '', 'familyname_variant': '', 'alias': '',
            'relation': '', 'zusatz': '', 'office': '', 'institution': '',
            'reference': m.group(3).strip().rstrip('.').strip(),
            'cf_reference': cf_reference, 'pages': pages,
        }

    tokens = build_tokens(rest)

    # D. reference separator: 'v.' always; 's.' only when the entry has no
    #    digits at all (otherwise 's.' is 'sancti', as in 'mon. s. Lamberti')
    allow_s = not pages and not re.search(r'\d', rest)
    v_idx = None
    for i, (ttype, val) in enumerate(tokens):
        if ttype == 'word' and (val == 'v.' or (allow_s and val == 's.')):
            v_idx = i
            break
    if v_idx is not None:
        pre_v_tokens, reference_tokens = tokens[:v_idx], tokens[v_idx + 1:]
    else:
        pre_v_tokens, reference_tokens = tokens, []
    reference = tokens_to_text(reference_tokens).rstrip('.').strip()

    # E. relation clause (BEFORE the office scan)
    pre_v_tokens, relation = extract_relation(pre_v_tokens)

    # F. office / institution boundary.
    #    The scan starts at token 1: this index is alphabetised by forename, so
    #    the headword always occupies position 0. A role word there is the name
    #    itself ("Legatus de Werberge 89." files under Le-, between "Laurentius
    #    Weys" and "Leo de Reiis"), not an office.
    boundary_idx, _ = find_prefix_boundary(
        pre_v_tokens, ROLE_OR_INSTITUTION_COMPONENTS, start=1
    )
    if boundary_idx is not None:
        name_tokens, role_tokens = pre_v_tokens[:boundary_idx], pre_v_tokens[boundary_idx:]
    else:
        name_tokens, role_tokens = pre_v_tokens, []
    office, institution, role_zusatz = split_role_block(role_tokens)

    # G. alias segments ('al.' / 'dict.' / 'sive')
    name_tokens, alias = extract_alias(name_tokens)

    # H. name part
    firstname, fn_var, familyname, fam_var, zusatz_str = split_name_tokens(name_tokens)

    if role_zusatz:
        zusatz_str = '; '.join([z for z in [zusatz_str] if z] + role_zusatz)

    return {
        'raw_entry': raw_original,
        'firstname': firstname,
        'firstname_variant': fn_var,
        'familyname': familyname,
        'familyname_variant': fam_var,
        'alias': alias,
        'relation': relation,
        'zusatz': zusatz_str,
        'office': office,
        'institution': institution,
        'reference': reference,
        'cf_reference': cf_reference,
        'pages': pages,
    }


def main():
    rows = []
    with open(INPUT, 'r', encoding='utf-8-sig') as f:
        sample = f.read(2048)
        f.seek(0)
        has_header = INPUT_COLUMN in sample.splitlines()[0] if sample else False
        if has_header:
            sources = (row[INPUT_COLUMN] for row in csv.DictReader(f))
        else:
            sources = (line for line in f)

        for src in sources:
            # one raw line can hold two glued index entries
            for entry in split_merged_entries(fix_mojibake(str(src)).strip()):
                parsed = parse_entry(entry)
                if parsed:
                    rows.append(parsed)

    fieldnames = ['raw_entry', 'firstname', 'firstname_variant',
                  'familyname', 'familyname_variant', 'alias', 'relation',
                  'zusatz', 'office', 'institution', 'reference',
                  'cf_reference', 'pages']

    with open(OUTPUT, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == '__main__':
    main()
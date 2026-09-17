import csv
import re
import difflib

INPUT = "personenverzeichnis_raw_final_sorted.csv"
OUTPUT = "personenverzeichnis_split.csv"
INPUT_COLUMN = "raw_entry"  # change if your column is named differently

SIMILARITY_THRESHOLD = 0.4  # tweak if needed


def similarity(a, b):
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def is_variant(bracket_content, comparison_targets):
    """
    bracket_content may contain multiple comma-separated names.
    Checks each variant, in order, against each comparison target (last word,
    full name). As soon as ONE variant matches ONE target above threshold,
    the whole bracket is treated as a name variant (short-circuit).
    """
    variants = [v.strip() for v in bracket_content.split(',') if v.strip()]
    if not variants:
        variants = [bracket_content.strip()]

    for v in variants:
        for target in comparison_targets:
            if not target:
                continue
            if similarity(v, target) >= SIMILARITY_THRESHOLD:
                return True
    return False


def strip_leading_de(s):
    s = s.strip()
    if s.lower().startswith('de '):
        return s[3:].strip()
    return s


def classify_bracket(content, target, firstname_words, familyname_words,
                      firstname_variants, familyname_variants, zusatz):
    """
    Decide whether `content` (bracket text) is a variant of `target`
    ('firstname' or 'familyname') or a Zusatz, and append it to the
    right list. Uses the words accumulated so far for that target.
    """
    target_words = firstname_words if target == 'firstname' else familyname_words
    last_word = target_words[-1] if target_words else ''
    full_name_no_de = strip_leading_de(' '.join(target_words))
    comparison_targets = [last_word, full_name_no_de]

    # Auto-Zusatz rule: bracket content starting with "de" is never a name variant
    if content.strip().lower()[:2] == 'de':
        zusatz.append(content)
        return

    if is_variant(content, comparison_targets):
        if target == 'firstname':
            firstname_variants.append(content)
        else:
            familyname_variants.append(content)
    else:
        zusatz.append(content)


def build_tokens(text):
    """Turn a plain-text (bracket-containing) string into an ordered list of
    ('word', w) / ('bracket', content) tokens."""
    segments = re.split(r'(\([^)]*\))', text)
    segments = [s for s in segments if s]

    tokens = []
    for seg in segments:
        if seg.startswith('(') and seg.endswith(')'):
            content = seg[1:-1].strip()
            if content:
                tokens.append(('bracket', content))
        else:
            for w in seg.split():
                tokens.append(('word', w))
    return tokens


def split_name_tokens(tokens):
    """
    Runs the bracket-priority / de-based split (and bracket variant/Zusatz
    classification) over a token stream that represents ONLY the name part
    of an entry (reference part, if any, must already be removed).
    Returns firstname, firstname_variant, familyname, familyname_variant, zusatz.
    """
    de_token_idx = None
    first_bracket_idx = None
    for i, (ttype, val) in enumerate(tokens):
        if ttype == 'word' and val == 'de' and de_token_idx is None:
            de_token_idx = i
        if ttype == 'bracket' and first_bracket_idx is None:
            first_bracket_idx = i

    # Bracket-boundary rule takes priority over the 'de' rule: if a bracket
    # appears BEFORE the standalone 'de' token, firstname ends where that
    # bracket starts, and familyname starts right after it closes.
    use_bracket_priority = (
        first_bracket_idx is not None and
        de_token_idx is not None and
        first_bracket_idx < de_token_idx
    )

    firstname_words = []
    familyname_words = []
    firstname_variants = []
    familyname_variants = []
    zusatz = []

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
            if w == 'de':
                de_index = i
                break
        if de_index is None and len(plain_words) >= 2:
            de_index = 1  # fallback split point

        word_counter = 0
        last_target = None

        for ttype, val in tokens:
            if ttype == 'bracket':
                target = last_target if last_target is not None else 'firstname'
                classify_bracket(val, target, firstname_words, familyname_words,
                                  firstname_variants, familyname_variants, zusatz)
            else:
                # de_index is None -> everything is firstname (single-word case)
                if de_index is None or word_counter < de_index:
                    firstname_words.append(val)
                    last_target = 'firstname'
                else:
                    familyname_words.append(val)
                    last_target = 'familyname'
                word_counter += 1

    firstname = ' '.join(firstname_words).strip()
    familyname = ' '.join(familyname_words).strip()
    firstname_variant = '; '.join(firstname_variants).strip()
    familyname_variant = '; '.join(familyname_variants).strip()
    zusatz_str = '; '.join(zusatz).strip()

    return firstname, firstname_variant, familyname, familyname_variant, zusatz_str


def parse_entry(line):
    line = line.strip()
    if not line:
        return None

    # 1. Extract trailing page numbers from the whole raw line
    m = re.search(r'(\d+(?:\s+\d+)*)\s*\.?\s*$', line)
    if m:
        pages = m.group(1).strip()
        rest = line[:m.start()].strip()
    else:
        pages = ''
        rest = line.rstrip('.').strip()

    # 2. Tokenize the remainder (name [+ optional reference])
    tokens = build_tokens(rest)

    # 3. Reference rule: a standalone "v." token separates the name part
    #    (before it) from a reference part (after it), e.g.
    #    "Anton v. Tiboldus" -> name = Anton, reference = Tiboldus
    #    "Elsa de Villanders v. Elizabeth" -> name = Elsa de Villanders, reference = Elizabeth
    v_idx = None
    for i, (ttype, val) in enumerate(tokens):
        if ttype == 'word' and val == 'v.':
            v_idx = i
            break

    if v_idx is not None:
        name_tokens = tokens[:v_idx]
        reference_tokens = tokens[v_idx + 1:]
    else:
        name_tokens = tokens
        reference_tokens = []

    reference_parts = []
    for ttype, val in reference_tokens:
        if ttype == 'word':
            reference_parts.append(val)
        else:
            reference_parts.append(f'({val})')
    reference = ' '.join(reference_parts).strip()

    # 4. Run the normal name-splitting logic on the name part only
    firstname, firstname_variant, familyname, familyname_variant, zusatz_str = \
        split_name_tokens(name_tokens)

    return {
        'raw_entry': line,
        'firstname': firstname,
        'firstname_variant': firstname_variant,
        'familyname': familyname,
        'familyname_variant': familyname_variant,
        'zusatz': zusatz_str,
        'reference': reference,
        'pages': pages,
    }


def main():
    rows = []

    with open(INPUT, 'r', encoding='utf-8') as f:
        sample = f.read(2048)
        f.seek(0)
        has_header = INPUT_COLUMN in sample.splitlines()[0] if sample else False

        if has_header:
            reader = csv.DictReader(f)
            for row in reader:
                parsed = parse_entry(row[INPUT_COLUMN])
                if parsed:
                    rows.append(parsed)
        else:
            for line in f:
                parsed = parse_entry(line)
                if parsed:
                    rows.append(parsed)

    fieldnames = ['raw_entry', 'firstname', 'firstname_variant',
                  'familyname', 'familyname_variant', 'zusatz', 'reference', 'pages']

    with open(OUTPUT, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == '__main__':
    main()
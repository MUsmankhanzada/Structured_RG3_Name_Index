# Structured_RG3_Name_Inde# Repertorium Germanicum — Personenverzeichnis Pipeline

Turns scanned pages of the *Repertorium Germanicum* person index into a
structured CSV.

Three stages, run in order:

| # | Script | In | Out |
|---|---|---|---|
| 1 | `grab.py` | page images | `personenverzeichnis_raw_final.csv` |
| 2 | `preprocess.py` | raw CSV | `personenverzeichnis_raw_final_sorted.csv` |
| 3 | `split_names.py` | sorted CSV | `personenverzeichnis_split.csv` |

Current run: **3,709 entries**, 13 columns.

```bash
python grab.py
python preprocess.py
python split_names.py
```

---

## Setup

```bash
pip install openai opencv-python pandas python-dotenv
```

### Configuration

`grab.py` reads its settings from a `.env` file in the same directory:

```
ACADEMIC_CLOUD_API_KEY=your_key_here
ACADEMIC_CLOUD_BASE_URL=https://chat-ai.academiccloud.de/v1
MODEL_NAME=qwen3-omni-30b-a3b-instruct
```

Only the key is required; the other two fall back to the defaults above.
`OPENAI_API_KEY` is accepted as an alternative key name.

Keep secrets and bulky intermediates out of version control:

```gitignore
.env
*.csv
```

---

## Stage 1 — `grab.py`

Reads two-column scans, sends each column to a vision model, and returns
transcribed index entries.

**What it does**

- Finds images matching `He 6481 (3_0264` … `3_0306` in the working directory.
- Crops running headers/footers (top 5%, bottom 4%), splits the page down the
  middle into left and right columns.
- Downscales to 1200px wide, JPEG q85, base64-encodes.
- Sends each column with a prompt instructing the model to merge indented
  continuation lines into one entry per person and preserve spelling,
  punctuation and abbreviations exactly.
- Runs 4 columns in parallel, retries with backoff on rate limits.

**Checkpointing.** Every completed column is written to
`personenverzeichnis_raw_checkpoint.csv` immediately. Re-running skips
already-processed columns, so an interrupted run resumes where it stopped.
Columns that legitimately return zero entries are also recorded, so they
aren't retried forever.

**Tuning**

```python
START_PAGE    = 264
END_PAGE      = 306
MAX_WORKERS   = 4     # 6 if the server tolerates it
REQUEST_DELAY = 0.5
MAX_RETRIES   = 5
```

**Output** — one column, `raw_entry`:

```csv
raw_entry
Abelinus Vanderlinden 37.
Abraham Abrahe de Nouacuria 43 243 309.
Adam Nicolai de Banchow (Banchcow, Bankow) 37 289.
```

---

## Stage 2 — `preprocess.py`

Cleans and orders the transcription before parsing. Three operations:

1. **Drop section headers.** Single letters printed to mark alphabetical
   divisions — `A`, `B.`, `[C]`, `- D -` — are detected by stripping
   whitespace and styling punctuation and checking for one remaining letter.
2. **Deduplicate.** Identical `raw_entry` rows are removed. Columns can
   overlap slightly at page boundaries, and a retried column can be recorded
   twice.
3. **Sort.** Case-insensitive (`casefold`) alphabetical sort.

It falls back to line-based parsing if standard CSV parsing hits unquoted
commas in historical aliases.

Progress is printed, including which headers were dropped and how many
duplicates were removed.

> **The sort determines the final row order**, which is why stage 3 output
> appears alphabetised. It is a plain string sort, so ASCII ordering applies:
> `(` (0x28) precedes digits (0x3x), which precede letters (0x6x). Within a
> forename group you therefore see bracketed-variant entries, then the bare
> forename, then surnames:
>
> ```
> Agnes (Vogt de Beringen) 320.
> Agnes abbat. mon. in Kirchen 184.
> Antonius (de Challant) diac. card. … 52 179 300.
> Antonius 241.
> Antonius Camerawer 53.
> ```
>
> This is expected. Do not treat it as a sorting fault.

---

## Stage 3 — `split_names.py`

Set at the top of the file if your filenames differ:

```python
INPUT        = "personenverzeichnis_raw_final_sorted.csv"
OUTPUT       = "personenverzeichnis_split.csv"
INPUT_COLUMN = "raw_entry"
```

### Output columns

| Column | Meaning |
|---|---|
| `raw_entry` | source line, verbatim (mojibake repaired) |
| `firstname` | forename, incl. regnal numerals and *iunior/senior* |
| `firstname_variant` | alternative spelling of the forename |
| `familyname` | surname incl. its nobiliary particle |
| `familyname_variant` | alternative spelling of the surname |
| `alias` | *al.* / *dict.* / *sive* / *seu* name |
| `relation` | *relicta / natus / uxor / filius* clause |
| `zusatz` | other bracketed additions; place of origin |
| `office` | clerical or noble office |
| `institution` | church, monastery, hospital, house |
| `reference` | *v.* (or *s.*) cross-reference target |
| `cf_reference` | *; cf. X* cross-reference target |
| `pages` | column numbers of the printed index |

---

## Parsing rules, by example

### Basic split

```
Abelinus Vanderlinden 37.
  firstname   Abelinus
  familyname  Vanderlinden
  pages       37
```

### `de` marks the family name

Everything before a standalone `de` is the forename; `de` and everything after
is the surname.

```
Abraham Abrahe de Nouacuria 43 243 309.
  firstname   Abraham Abrahe
  familyname  de Nouacuria
  pages       43 243 309
```

### Bracketed variants

A bracket following a name holds alternative spellings of *that* name.

```
Adam Nicolai de Banchow (Banchcow, Bankow) 37 289.
  firstname           Adam Nicolai
  familyname          de Banchow
  familyname_variant  Banchcow, Bankow
```

Several brackets each attach to the nearest preceding element:

```
Goswinus (Goyswinus) Eychlo (Eichloe) 134 236.
  firstname           Goswinus
  firstname_variant   Goyswinus
  familyname          Eychlo
  familyname_variant  Eichloe
```

### Variant or Zusatz?

Bracket content is compared with the name it follows (`SIMILARITY_THRESHOLD`,
default `0.40`). Similar → variant. Unrelated → `zusatz`. Content starting
with `de` always goes to `zusatz`.

```
Gotfridus Canawe (Conowe) 71 156.      → familyname_variant  Conowe
Agnes (Heyncze de Brega) 311.          → zusatz              Heyncze de Brega
Affra (de Weyspruch) 110.              → zusatz              de Weyspruch
```

With several items in one bracket, the first match makes the whole bracket a
variant.

### A bracket outranks `de`

If a bracket appears *before* the standalone `de`, the forename ends where the
bracket opens.

```
Aldigerius (Aldigherius, Aldericus) Francisci de Florena 46 52 95 …
  firstname          Aldigerius
  firstname_variant  Aldigherius, Aldericus
  familyname         Francisci de Florena
```

### Offices and institutions

The first known role or institution abbreviation ends the name. Within that
block, the first institution term separates office from institution.

```
Erhardus (Gerardus) abb. mon. s. Lamberti in Sewn 110.
  firstname          Erhardus
  firstname_variant  Gerardus
  office             abb.
  institution        mon. s. Lamberti in Sewn
```

```
Dobrogostius ep. Poznan. 102.          → office  ep. Poznan.
Clara abbat. mon. Nussien. 77.         → office  abbat.   institution  mon. Nussien.
```

Office terms match **lowercase only**, so capitalised forms are surnames:

```
Johannes Rex 18.        → familyname  Rex      (not an office)
Hermannus Ducis 131.    → familyname  Ducis
```

Popes keep their numeral with the forename:

```
Gregorius XII. (Angelus, Errorius) pp. 9 12 13 …
  firstname          Gregorius XII.
  firstname_variant  Angelus, Errorius
  office             pp.
```

### Alias — `al.` / `dict.` / `sive` / `seu`

Markers stack, and a `de …` *after* alias content returns to the surname.

```
Albertus Beyer al. Steneken 40.
  firstname  Albertus   familyname  Beyer   alias  Steneken

Andreas dict. Kelermaister 48.
  firstname  Andreas    familyname  —       alias  Kelermaister

Nicolaus Henrici al. dict. Schilchin (Schibchin) de Cruczenaco 287 397.
  firstname  Nicolaus Henrici
  familyname de Cruczenaco
  alias      Schilchin (Schibchin)

Johannes Wintheri de Rudensheim al de Gisenheim 238 255.
  familyname de Rudensheim
  alias      de Gisenheim          ← de directly after the marker stays in the alias
```

### Relation clauses

Detected **before** the office scan, because the clause itself contains office
words. Works with the marker leading or trailing.

```
Agnes relictæ Herbordi Kuykens de Tremonia 38.
  firstname  Agnes    relation  relictæ Herbordi Kuykens de Tremonia

Elisabet Vlrici bar. de Haenow uxor 107.
  firstname  Elisabet  relation  Vlrici bar. de Haenow uxor
```

### Cross-references

```
Anton v. Tiboldus.                     → reference  Tiboldus
Elsa de Villanders v. Elizabeth.       → familyname de Villanders, reference Elizabeth
Venslaus, Vinceslaus s. Wenceslaus.    → firstname_variant Vinceslaus, reference Wenceslaus
Clemens de Alsentz 78; cf. Nicolaus Frederici de A.
                                       → pages 78, cf_reference Nicolaus Frederici de A
```

`s.` counts as *siehe* only when the entry contains **no digits** — otherwise
it is *sancti* (`mon. s. Lamberti`).

### Place of origin

A second `de` group is an origin, not part of the surname.

```
Antonius de Baldinotis de Pistorio 59.
  familyname  de Baldinotis
  zusatz      de Pistorio
```

### Editorial in-word brackets

A bracket glued to a word with no space marks letters *inside* one word. It is
preserved exactly as printed and produces **no** variant.

```
Je(ronimus) Rutili 186.            → firstname   Je(ronimus)
Johannes Hil(le)manni 143 216.     → familyname  Hil(le)manni
Fridericus Deys(t) 12 118 205.     → familyname  Deys(t)
```

Both mechanisms can occur together:

```
Wenc(z)eslaus (Vinceslaus) Thyen (Thiem, Thien) 30 88 210 …
  firstname           Wenc(z)eslaus      ← glued, kept verbatim
  firstname_variant   Vinceslaus         ← spaced, a real variant
  familyname          Thyen
  familyname_variant  Thiem, Thien
```

An *uppercase* glued bracket is a variant that lost its space, so a space is
inserted: `Diick(Deyk)` → `Diick (Deyk)`.

A bracketed particle stays with the surname:

```
Bertoldus (de) Cellis 65 254.      → familyname  (de) Cellis
```

### Pages

Dash ranges and period-separated runs are both handled.

```
Johannes XXIII. pp. 39 52 … 385—402.      → 39 52 … 385—402
Georgius Lichtenberg 127. 256.            → 127 256
```

---

## OCR repairs

Applied before parsing, in this order.

| Repair | Example |
|---|---|
| Mojibake (cp1252→UTF-8) | `DumplÃ¶s` → `Dumplös`, `385â€”402` → `385—402` |
| Page corrections | `300 33359 388` → `300 333 359 388` |
| Space breaks | `Sle sie` → `Slesie`, `Dorn dorff` → `Dorndorff` |
| Unbalanced brackets | `(Scheczel 123` → `(Scheczel) 123` |
| Hyphen line breaks | `Con-stancianen.` → `Constancianen.` |
| Token typo | `dc` → `de` |
| Merged entries | one line split back into two |

**Why cp1252 and not latin-1.** `â€”` contains `€`, which latin-1 cannot
encode — the round-trip raises and the em-dash is never repaired. cp1252 maps
it to `0x80`, giving `E2 80 94` → `—`. Repair runs only when marker characters
are present and only if the round-trip succeeds, so correct text containing
real `ö`, `ü` or `—` is never touched.

> `grab.py` also has a `fix_mojibake_str` using latin-1. It handles the common
> `Ã¶` cases but silently fails on em-dashes, which is why `split_names.py`
> repairs again with cp1252.

**Why lookup tables, not rules.** Space breaks cannot be fixed generally,
because structurally identical strings are genuinely multi-word here:
`zu der golden luffe`, `de villa Eschwilre`, `in Pomerio`, `van den Weghe`.
Each join is listed explicitly in `OCR_SPACE_JOINS`. Same for `PAGE_FIXES`.
Before adding a key, confirm the substring occurs exactly **once** in the
corpus.

---

## Validation

`split_names.py` has been checked against these invariants:

| Check | Result |
|---|---|
| Token loss (reconstruction audit) | 0 |
| Empty firstname | 0 |
| Rows with no pages and no reference | 0 |
| Trailing `,;:` in name columns | 0 |
| Capitalised office blocks | 0 |
| Mojibake survivors | 0 |
| Duplicate raw entries | 0 |
| Malformed page strings | 0 |
| Unbalanced brackets in output | 0 |
| Page numbers above 402 | 0 |

**The reconstruction audit is the most valuable test.** It checks that every
word of every raw entry appears somewhere in the output columns. Keep it as a
standing regression test — any new rule that silently eats text shows up
immediately.

```python
import pandas as pd, re
df = pd.read_csv("personenverzeichnis_split.csv", encoding="utf-8-sig").fillna("")

def norm(s):
    return set(re.sub(r"[()\[\],;.?]", " ", str(s).lower()).split())

EXPECTED = {"al", "dict", "sive", "seu", "dc", "cf", "v", "s"}  # consumed markers

for _, r in df.iterrows():
    out = set().union(*(norm(r[c]) for c in df.columns[1:]))
    lost = {w for w in norm(r["raw_entry"]) - out
            if not w.isdigit() and w not in EXPECTED and "-" not in w}
    lost = {w for w in lost if not any(w in o for o in out)}
    if lost:
        print(r["raw_entry"], lost)
```

### Page runs are not reliably ascending

3,677 of 3,682 ascend, which makes it a good *flag* for OCR damage — it is how
all five page corrections were found. But
`Sigismundus rex Roman. et Vngarie … 238 243 151 258 …` is correct as printed.
Treat a descending run as something to check, never to auto-correct.

### Do not use alphabetical order as an automated test

It produced 222, then 84, then 65 false positives before yielding one genuine
hit — all caused by not accounting for the ASCII sort described in stage 2.
Useful manually, unusable as a gate.

---

## Known open items

- **Row order.** `Linka Strociurn 93.` sits one row too high. Stage 3 splits it
  out of a merged line but inserts it in place; stage 2's sort has already run,
  so it is never re-ordered. Content is correct.
- **Stacked offices.** `Henricus com. de Hoya, ep. (el.) Verden.` yields one
  merged office and no surname. Genuinely ambiguous; left as-is.
- **Borderline variant.** `Balduinus (Baldwinus) de Diick(Deyk)` — `Deyk`
  scores below `0.40` against `Diick` and lands in `zusatz`.

---

## Extending to other volumes

Lookup tables and vocabularies are deliberately explicit:

```python
PAGE_FIXES                # page-run corrections
OCR_SPACE_JOINS           # space-break joins
TOKEN_FIXES               # token typos (currently dc -> de)

PERSON_OR_ROLE_PREFIXES   # offices and titles
INSTITUTION_PREFIXES      # churches, monasteries, hospitals
ALIAS_MARKERS             # al. dict. dictus dicta sive seu
RELATION_MARKERS          # natus nata uxor filius filia
RELATION_PREFIXES         # relict- relect-
NAME_SUFFIXES             # iunior junior senior iun. sen.
NOBILIARY_PARTICLES       # de von van di del della
```

Offices match lowercase only, so adding a term that also occurs as a surname
is safe — but re-check that assumption if a new volume prints offices
differently.

---

## Files

```
grab.py                                    stage 1 — transcription
preprocess.py                              stage 2 — clean, dedupe, sort
split_names.py                             stage 3 — parsing
PARSING_LOG.txt                            step-by-step development log
.env                                       API key and endpoint settings

personenverzeichnis_raw_checkpoint.csv     resumable progress (stage 1)
personenverzeichnis_raw_final.csv          stage 1 output
personenverzeichnis_raw_final_sorted.csv   stage 2 output
personenverzeichnis_split.csv              final structured output
```

`PARSING_LOG.txt` records every rule in the order it was added, with the
observation that triggered it — including one reversal, where contracted forms
were briefly written to the variant columns before being removed as invented
data.
import re
import pandas as pd

INPUT_FILE = "personenverzeichnis_raw_final.csv"
OUTPUT_FILE = "personenverzeichnis_raw_final_sorted.csv"


def is_single_letter_header(text: str) -> bool:
    """Detects if an entry is just a single section header letter

    (e.g., 'A', 'A.', '- B -', '[C]').
    """
    # Remove whitespace and common section styling punctuation
    stripped = re.sub(r"[\s\.\-\[\]\(\):_]", "", text)
    return len(stripped) == 1 and stripped.isalpha()


def clean_and_sort_csv(input_path, output_path):
    # Try standard CSV parsing first; fallback to line-based parsing
    # if unquoted commas exist in historical name aliases
    try:
        df = pd.read_csv(input_path, dtype=str)
    except pd.errors.ParserError:
        df = pd.read_csv(
            input_path, sep=r"\r?\n", engine="python", header=0, dtype=str
        )
        df.columns = ["raw_entry"]

    target_col = "raw_entry" if "raw_entry" in df.columns else df.columns[0]

    # Clean whitespace and drop empty rows
    df[target_col] = df[target_col].astype(str).str.strip()
    df = df[df[target_col] != ""]
    initial_count = len(df)

    # 1. Filter out single alphabet headers (A, B., [C], etc.)
    letter_mask = df[target_col].apply(is_single_letter_header)
    removed_letters = df[letter_mask][target_col].tolist()
    df = df[~letter_mask].copy()

    # 2. Remove duplicate person rows
    before_dedup = len(df)
    df = df.drop_duplicates(subset=[target_col]).copy()
    duplicates_removed = before_dedup - len(df)

    # 3. Sort alphabetically (case-insensitive)
    df = df.sort_values(
        by=target_col, key=lambda col: col.str.casefold(), ascending=True
    ).reset_index(drop=True)

    # Save the cleaned CSV
    df.to_csv(output_path, index=False, encoding="utf-8")

    print(f"Total rows read:           {initial_count}")
    print(f"Alphabet headers dropped:  {len(removed_letters)}")
    if removed_letters:
        print(f"  -> Examples dropped:     {removed_letters[:5]}")
    print(f"Duplicate rows removed:    {duplicates_removed}")
    print(f"Final sorted rows:         {len(df)}")
    print(f"Saved cleaned CSV to:      {output_path}")


if __name__ == "__main__":
    clean_and_sort_csv(INPUT_FILE, OUTPUT_FILE)
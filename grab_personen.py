import base64
import json
import os
import re
import time

import cv2
import pandas as pd

from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI, APIStatusError, RateLimitError


# ============================================================
# API CONFIGURATION
# ============================================================

# Set your API key as an environment variable:
#
# Windows CMD:
#     set ACADEMIC_CLOUD_API_KEY=YOUR_KEY
#
# PowerShell:
#     $env:ACADEMIC_CLOUD_API_KEY="YOUR_KEY"

API_KEY = "c4230084c776d861e5125d19088be3ec"

BASE_URL = "https://chat-ai.academiccloud.de/v1"

MODEL = "qwen3-omni-30b-a3b-instruct"


# ============================================================
# IMAGE RANGE
# ============================================================

START_PAGE = 264
END_PAGE = 306


# ============================================================
# FILES
# ============================================================

CHECKPOINT_CSV = "personenverzeichnis_raw_checkpoint.csv"
FINAL_CSV = "personenverzeichnis_raw_final.csv"


# ============================================================
# SPEED CONFIGURATION
# ============================================================

# Number of simultaneous API requests.
#
# 4 is a good balance between speed and avoiding rate limits.
#
# If the server handles it well, you can try 6 later.
MAX_WORKERS = 4

# Small delay before each request.
REQUEST_DELAY = 0.5

# Maximum retries for failed requests.
MAX_RETRIES = 5


# ============================================================
# CLIENT
# ============================================================

if not API_KEY:
    raise ValueError(
        "API key not found.\n\n"
        "Set the ACADEMIC_CLOUD_API_KEY environment variable."
    )


client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    timeout=120.0
)


# ============================================================
# MOJIBAKE REPAIR
# ============================================================

def fix_mojibake_str(val):

    if isinstance(val, str):

        try:
            return val.encode("latin1").decode("utf-8")

        except (
            UnicodeEncodeError,
            UnicodeDecodeError
        ):
            return val

    return val


# ============================================================
# IMAGE ENCODING
# ============================================================

def encode_and_resize(
    img_array,
    max_width=1200,
    jpeg_quality=85
):

    h, w = img_array.shape[:2]

    if w > max_width:

        scale = max_width / float(w)

        img_array = cv2.resize(
            img_array,
            (
                int(w * scale),
                int(h * scale)
            ),
            interpolation=cv2.INTER_AREA
        )

    encode_param = [
        int(cv2.IMWRITE_JPEG_QUALITY),
        jpeg_quality
    ]

    success, buffer = cv2.imencode(
        ".jpg",
        img_array,
        encode_param
    )

    if not success:
        raise RuntimeError(
            "Could not encode image."
        )

    return base64.b64encode(
        buffer
    ).decode("utf-8")


# ============================================================
# SPLIT PAGE
# ============================================================

def split_columns(image_path):

    img = cv2.imread(image_path)

    if img is None:
        raise FileNotFoundError(
            f"Could not read image: {image_path}"
        )

    h, w, _ = img.shape

    # Remove running header/footer
    cropped = img[
        int(h * 0.05):
        int(h * 0.96),
        :
    ]

    mid = w // 2

    left_col = cropped[:, :mid]

    right_col = cropped[:, mid:]

    return left_col, right_col


# ============================================================
# FIND IMAGES
# ============================================================

def get_image_files(
    start_num=START_PAGE,
    end_num=END_PAGE
):

    valid_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".tif",
        ".tiff"
    )

    pattern = re.compile(
        r"He\s*6481\s*\(\s*3[_-]0*(\d+)",
        re.IGNORECASE
    )

    matched = []

    for fname in os.listdir("."):

        if not fname.lower().endswith(
            valid_extensions
        ):
            continue

        match = pattern.search(fname)

        if match:

            num = int(match.group(1))

            if start_num <= num <= end_num:

                matched.append(
                    (num, fname)
                )

    matched.sort(
        key=lambda x: x[0]
    )

    return [
        fname
        for _, fname in matched
    ]


# ============================================================
# EXTRACTION PROMPT
# ============================================================

PERSON_PROMPT = r"""
Transcribe this historical Personenverzeichnis column into a
JSON list of person-index entries.

IMPORTANT: This is a HISTORICAL PERSON INDEX.

Rules:

1. Every PERSON NAME that starts at the normal left margin
   begins a separate record.

2. Indented continuation lines below a person belong to that
   same person and MUST be merged into the same string.

3. A person's entry can span several physical lines.

4. Combine all physical lines belonging to one person using
   exactly ONE SPACE between them.

5. Preserve the text exactly as printed.

6. Preserve ALL punctuation:
   periods, commas, semicolons, colons, parentheses,
   brackets, hyphens, etc.

7. Preserve all page numbers exactly.

8. Preserve Latin abbreviations exactly as printed.

9. Preserve German characters:
   Ä Ö Ü ä ö ü ß

10. Historical spelling must NOT be modernized or corrected.

11. Cross-references such as:
    "Betta v. Berta."
    are single entries.

12. Do NOT create separate entries from indented continuation
    lines.

13. Ignore running headers and footers.

14. Ignore page numbers printed at the bottom.

15. Ignore alphabetic section markers such as:
    "C K"

16. Do not invent missing text.

17. Each person must be returned as exactly ONE string.

Return ONLY valid JSON in this format:

{
    "entries": [
        "Person entry 1",
        "Person entry 2",
        "Person entry 3"
    ]
}
"""


# ============================================================
# EXTRACT ONE COLUMN
# ============================================================

def extract_column(
    column_img,
    col_tag
):

    # Encode image once
    b64_img = encode_and_resize(
        column_img
    )

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            # Small throttle
            time.sleep(
                REQUEST_DELAY
            )

            response = client.chat.completions.create(

                model=MODEL,

                messages=[
                    {
                        "role": "user",
                        "content": [

                            {
                                "type": "text",
                                "text": PERSON_PROMPT
                            },

                            {
                                "type": "image_url",
                                "image_url": {
                                    "url":
                                    f"data:image/jpeg;base64,{b64_img}"
                                }
                            }

                        ]
                    }
                ],

                temperature=0.0
            )

            raw_text = (
                response
                .choices[0]
                .message
                .content
                .strip()
            )

            # ------------------------------------------------
            # Remove markdown fences
            # ------------------------------------------------

            if "```json" in raw_text:

                raw_text = (
                    raw_text
                    .split(
                        "```json",
                        1
                    )[1]
                    .split(
                        "```",
                        1
                    )[0]
                    .strip()
                )

            elif "```" in raw_text:

                raw_text = (
                    raw_text
                    .split(
                        "```",
                        1
                    )[1]
                    .split(
                        "```",
                        1
                    )[0]
                    .strip()
                )

            # ------------------------------------------------
            # Parse JSON
            # ------------------------------------------------

            data = json.loads(
                raw_text
            )

            entries = data.get(
                "entries",
                []
            )

            if not isinstance(
                entries,
                list
            ):
                raise ValueError(
                    "'entries' is not a list."
                )

            # ------------------------------------------------
            # Clean entries
            # ------------------------------------------------

            cleaned = []

            for entry in entries:

                if not isinstance(
                    entry,
                    str
                ):
                    continue

                entry = entry.strip()

                # Remove accidental surrounding quotes
                entry = entry.strip('"').strip()

                if entry:
                    cleaned.append(entry)

            return {
                "success": True,
                "image": col_tag[0],
                "column": col_tag[1],
                "entries": cleaned,
                "error": None
            }

        except RateLimitError as e:

            wait_time = 15 * attempt

            print(
                f"[RATE LIMIT] "
                f"{col_tag[0]} [{col_tag[1]}] "
                f"waiting {wait_time}s..."
            )

            time.sleep(
                wait_time
            )

        except (
            APIStatusError,
            Exception
        ) as e:

            if attempt >= MAX_RETRIES:

                print(
                    f"[FAILED] "
                    f"{col_tag[0]} "
                    f"[{col_tag[1]}]"
                )

                return {
                    "success": False,
                    "image": col_tag[0],
                    "column": col_tag[1],
                    "entries": [],
                    "error": str(e)
                }

            wait_time = 3 * attempt

            print(
                f"[ERROR] "
                f"{col_tag[0]} "
                f"[{col_tag[1]}] "
                f"attempt {attempt}/{MAX_RETRIES} "
                f"-> retrying in {wait_time}s"
            )

            time.sleep(
                wait_time
            )

    return {
        "success": False,
        "image": col_tag[0],
        "column": col_tag[1],
        "entries": [],
        "error": "Maximum retries exceeded"
    }


# ============================================================
# LOAD CHECKPOINT
# ============================================================

def load_checkpoint():

    if not os.path.exists(
        CHECKPOINT_CSV
    ):

        return pd.DataFrame(
            columns=[
                "source_image",
                "column",
                "raw_entry"
            ]
        )

    try:

        df = pd.read_csv(
            CHECKPOINT_CSV,
            encoding="utf-8-sig"
        )

        required = {
            "source_image",
            "column",
            "raw_entry"
        }

        if not required.issubset(
            df.columns
        ):

            print(
                "Checkpoint format invalid."
            )

            return pd.DataFrame(
                columns=[
                    "source_image",
                    "column",
                    "raw_entry"
                ]
            )

        return df

    except Exception as e:

        print(
            f"Could not read checkpoint: {e}"
        )

        return pd.DataFrame(
            columns=[
                "source_image",
                "column",
                "raw_entry"
            ]
        )


# ============================================================
# CHECK IF ALREADY PROCESSED
# ============================================================

def already_processed(
    checkpoint_df,
    image_name,
    column_name
):

    if checkpoint_df.empty:
        return False

    mask = (
        (checkpoint_df["source_image"] == image_name)
        &
        (checkpoint_df["column"] == column_name)
    )

    return mask.any()


# ============================================================
# SAVE CHECKPOINT
# ============================================================

def save_checkpoint(
    checkpoint_df
):

    checkpoint_df.to_csv(
        CHECKPOINT_CSV,
        index=False,
        encoding="utf-8-sig"
    )


# ============================================================
# ADD RESULTS
# ============================================================

def add_results(
    checkpoint_df,
    image_name,
    column_name,
    entries
):

    new_rows = pd.DataFrame(
        {
            "source_image":
                [image_name] * len(entries),

            "column":
                [column_name] * len(entries),

            "raw_entry":
                entries
        }
    )

    return pd.concat(
        [
            checkpoint_df,
            new_rows
        ],
        ignore_index=True
    )


# ============================================================
# BUILD FINAL CSV
# ============================================================

def build_final(
    checkpoint_df
):

    if checkpoint_df.empty:

        return pd.DataFrame(
            columns=["raw_entry"]
        )

    final_df = checkpoint_df[
        ["raw_entry"]
    ].copy()

    # Mojibake repair
    final_df = final_df.map(
        fix_mojibake_str
    )

    # Remove surrounding quotes
    final_df["raw_entry"] = (
        final_df["raw_entry"]
        .astype(str)
        .str.strip()
        .str.strip('"')
        .str.strip()
    )

    # Remove empty rows
    final_df = final_df[
        final_df["raw_entry"].notna()
        &
        (final_df["raw_entry"] != "")
        &
        (final_df["raw_entry"] != "nan")
    ]

    final_df.reset_index(
        drop=True,
        inplace=True
    )

    return final_df


# ============================================================
# SAVE FINAL
# ============================================================

def save_final(
    checkpoint_df
):

    final_df = build_final(
        checkpoint_df
    )

    final_df.to_csv(
        FINAL_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    return final_df


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print("=" * 70)
    print("PERSONENVERZEICHNIS EXTRACTION")
    print("=" * 70)

    print(
        f"Pages: 3_{START_PAGE:04d} "
        f"→ 3_{END_PAGE:04d}"
    )

    print(
        f"Model: {MODEL}"
    )

    print(
        f"Parallel workers: {MAX_WORKERS}"
    )

    # --------------------------------------------------------
    # Find images
    # --------------------------------------------------------

    images = get_image_files(
        START_PAGE,
        END_PAGE
    )

    print(
        f"\nFound {len(images)} images."
    )

    expected = (
        END_PAGE - START_PAGE + 1
    )

    if len(images) != expected:

        print(
            f"WARNING: Expected {expected} "
            f"images but found {len(images)}."
        )

    if not images:

        print(
            "No images found."
        )

        return

    # --------------------------------------------------------
    # Load checkpoint
    # --------------------------------------------------------

    checkpoint_df = load_checkpoint()

    print(
        f"Checkpoint entries: "
        f"{len(checkpoint_df)}"
    )

    # --------------------------------------------------------
    # Prepare tasks
    # --------------------------------------------------------

    tasks = []

    skipped = 0

    for img_path in images:

        try:

            left_col, right_col = split_columns(
                img_path
            )

        except Exception as e:

            print(
                f"Could not read {img_path}: {e}"
            )

            continue

        # ----------------------------------------------------
        # LEFT
        # ----------------------------------------------------

        if already_processed(
            checkpoint_df,
            img_path,
            "left"
        ):

            skipped += 1

        else:

            tasks.append(
                (
                    img_path,
                    "left",
                    left_col
                )
            )

        # ----------------------------------------------------
        # RIGHT
        # ----------------------------------------------------

        if already_processed(
            checkpoint_df,
            img_path,
            "right"
        ):

            skipped += 1

        else:

            tasks.append(
                (
                    img_path,
                    "right",
                    right_col
                )
            )

    print(
        f"\nAlready processed: "
        f"{skipped} columns"
    )

    print(
        f"Remaining columns: "
        f"{len(tasks)}"
    )

    if not tasks:

        print(
            "\nEverything is already processed."
        )

        final_df = save_final(
            checkpoint_df
        )

        print(
            f"Final entries: "
            f"{len(final_df)}"
        )

        return

    print(
        f"\nStarting {len(tasks)} API calls "
        f"using {MAX_WORKERS} workers..."
    )

    # ========================================================
    # PARALLEL PROCESSING
    # ========================================================

    completed = 0

    failed = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        future_to_task = {}

        for (
            image_name,
            column_name,
            column_img
        ) in tasks:

            future = executor.submit(
                extract_column,
                column_img,
                (
                    image_name,
                    column_name
                )
            )

            future_to_task[
                future
            ] = (
                image_name,
                column_name
            )

        # ----------------------------------------------------
        # Collect results as they finish
        # ----------------------------------------------------

        for future in as_completed(
            future_to_task
        ):

            image_name, column_name = (
                future_to_task[future]
            )

            try:

                result = future.result()

            except Exception as e:

                print(
                    f"\n[WORKER ERROR] "
                    f"{image_name} "
                    f"[{column_name}]: {e}"
                )

                failed += 1

                continue

            # ------------------------------------------------
            # Successful extraction
            # ------------------------------------------------

            if result["success"]:

                entries = result["entries"]

                if entries:

                    checkpoint_df = add_results(
                        checkpoint_df,
                        image_name,
                        column_name,
                        entries
                    )

                # ------------------------------------------------
                # IMPORTANT:
                # We save a marker even if the column has zero
                # entries, otherwise an empty column will be
                # processed again every time.
                # ------------------------------------------------

                else:

                    checkpoint_df = add_results(
                        checkpoint_df,
                        image_name,
                        column_name,
                        []
                    )

                # Save checkpoint after every completed column
                save_checkpoint(
                    checkpoint_df
                )

                completed += 1

                print(
                    f"\n[{completed}/{len(tasks)}] "
                    f"✓ {image_name} "
                    f"[{column_name}] "
                    f"→ {len(entries)} entries"
                )

            else:

                failed += 1

                print(
                    f"\n[FAILED] "
                    f"{image_name} "
                    f"[{column_name}]"
                )

                print(
                    f"Reason: "
                    f"{result['error']}"
                )

    # ========================================================
    # FINAL EXPORT
    # ========================================================

    print(
        "\n" + "=" * 70
    )

    print(
        "CREATING FINAL CSV"
    )

    print(
        "=" * 70
    )

    final_df = save_final(
        checkpoint_df
    )

    elapsed = (
        time.time() - start_time
    )

    minutes = elapsed / 60

    print(
        f"\nCompleted columns: "
        f"{completed}"
    )

    print(
        f"Failed columns: "
        f"{failed}"
    )

    print(
        f"Total final entries: "
        f"{len(final_df)}"
    )

    print(
        f"Final columns: "
        f"{list(final_df.columns)}"
    )

    print(
        f"Time elapsed: "
        f"{minutes:.2f} minutes"
    )

    print(
        f"\nFinal CSV:"
    )

    print(
        os.path.abspath(
            FINAL_CSV
        )
    )

    print(
        f"\nCheckpoint CSV:"
    )

    print(
        os.path.abspath(
            CHECKPOINT_CSV
        )
    )

    # --------------------------------------------------------
    # Preview
    # --------------------------------------------------------

    print(
        "\nFirst 15 entries:"
    )

    print(
        final_df
        .head(15)
        .to_string(index=False)
    )

    print(
        "\n" + "=" * 70
    )

    print(
        "FINISHED"
    )

    print(
        "=" * 70
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
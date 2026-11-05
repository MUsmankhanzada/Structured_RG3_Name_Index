"""Build an XML proposal from personenverzeichnis_split.csv."""

import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
from itertools import groupby
from pathlib import Path

INPUT_DEFAULT = "personenverzeichnis_split.csv"
OUTPUT_DEFAULT = "personenverzeichnis.xml"
FIELDS = (
    "raw_entry",
    "firstname",
    "firstname_variant",
    "familyname",
    "familyname_variant",
    "alias",
    "relation",
    "zusatz",
    "office",
    "institution",
    "reference",
    "cf_reference",
    "pages",
)


def index_char(row):
    value = row.get("firstname", "") or row.get("raw_entry", "")
    match = re.search(r"[^\W\d_]", value, re.UNICODE)
    return match.group(0).casefold() if match else "?"


def read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = [field for field in FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        return list(reader)


def build_person(row, person_id):
    person = ET.Element("person", {"id": str(person_id)})
    for field in FIELDS:
        value = (row.get(field) or "").strip()
        if value:
            ET.SubElement(person, field).text = value
    return person


def build_tree(rows):
    root = ET.Element("personenverzeichnis", {"volume": "3"})
    numbered_rows = enumerate(rows, start=1)

    for char, section in groupby(numbered_rows, key=lambda item: index_char(item[1])):
        index = ET.SubElement(root, "index", {"type": "personen", "char": char})
        for person_id, row in section:
            index.append(build_person(row, person_id))

    return ET.ElementTree(root)


def verify_xml(path, rows):
    root = ET.parse(path).getroot()
    if root.tag != "personenverzeichnis":
        return [f"Unexpected root element: {root.tag}"]

    people = root.findall("./index/person")
    if len(people) != len(rows):
        return [f"Expected {len(rows)} people, found {len(people)}"]

    problems = []
    for expected_id, (person, row) in enumerate(zip(people, rows), start=1):
        if person.get("id") != str(expected_id):
            problems.append(
                f"Row {expected_id}: expected id {expected_id}, got {person.get('id')!r}"
            )
        for field in FIELDS:
            expected = (row.get(field) or "").strip()
            actual = person.findtext(field, default="")
            if actual != expected:
                problems.append(f"Person {expected_id}: {field} did not round-trip")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default=INPUT_DEFAULT)
    parser.add_argument("-o", "--output", default=OUTPUT_DEFAULT)
    parser.add_argument("--preview", nargs="?", const=5, type=int)
    args = parser.parse_args()

    try:
        rows = read_rows(args.input)
        selected_rows = rows[:max(0, args.preview)] if args.preview is not None else rows
        tree = build_tree(selected_rows)
        if args.preview is not None:
            ET.indent(tree, space="\t")
            print(ET.tostring(tree.getroot(), encoding="unicode"))
            return

        ET.indent(tree, space="\t")
        tree.write(args.output, encoding="utf-8", xml_declaration=True)
        problems = verify_xml(args.output, rows)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            raise SystemExit(1)
        print(f"CSV rows         : {len(rows)}")
        print(f"<person> written : {len(rows)}")
        print(f"IDs              : 1-{len(rows)} in CSV order")
        print("Checks           : all passed")
        print(f"Wrote {args.output}")
    except (OSError, ValueError, csv.Error, ET.ParseError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
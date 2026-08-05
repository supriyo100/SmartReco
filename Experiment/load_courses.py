"""app/db/load_courses.py — merges seed/courses/*.json (one file per course,
your manual curation workflow) into the flat list app/db/seed.py embeds and
writes to SQL + Chroma. Run validate_seed.py first; this trusts its output.
"""
import glob
import json


def load_all(dirpath: str = "seed/courses") -> list[dict]:
    products = []
    for path in sorted(glob.glob(f"{dirpath}/*.json")):
        products.append(json.loads(open(path).read()))
    return products
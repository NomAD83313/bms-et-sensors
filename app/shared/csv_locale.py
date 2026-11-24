"""Delimiter and decimal separator conventions shared by CSV exports."""

CSV_DELIMITERS = {
    "comma": ",",
    ",": ",",
    "semicolon": ";",
    "semi": ";",
    ";": ";",
    "tab": "\t",
    "tsv": "\t",
    "\\t": "\t",
}


def resolve_csv_delimiter(args) -> str:
    value = str(args.get("csv_delimiter", args.get("delimiter", "comma")) or "comma").strip().lower()
    return CSV_DELIMITERS.get(value, ",")


def resolve_csv_decimal_separator(delimiter: str) -> str:
    return "," if delimiter == ";" else "."

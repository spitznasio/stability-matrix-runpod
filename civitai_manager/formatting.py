import re


def format_commercial_use(value) -> str:
    """CivitAI's API sometimes serializes this as a raw Postgres array literal
    (e.g. "{RentCivit,Image,Rent}") instead of a JSON list or single string.
    """
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        items = [v for v in value[1:-1].split(",") if v]
    elif isinstance(value, list):
        items = [str(v) for v in value]
    elif value:
        items = [str(value)]
    else:
        items = []

    # Split CamelCase enum values ("RentCivit" -> "Rent Civit") for readability.
    readable = [re.sub(r"(?<!^)(?=[A-Z])", " ", item).strip() for item in items]
    return ", ".join(readable) if readable else "Unknown"

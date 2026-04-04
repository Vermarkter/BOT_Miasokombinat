import re


def normalize_phone(phone: str) -> str:
    candidate = phone.strip()
    candidate = re.sub(r"[()\-\s]", "", candidate)
    return candidate


def is_valid_phone(phone: str) -> bool:
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    return bool(re.fullmatch(r"\+?\d{10,15}", normalized))

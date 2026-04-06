import re


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""

    if digits.startswith("00380") and len(digits) >= 14:
        digits = digits[2:]

    if digits.startswith("380") and len(digits) == 12:
        return f"+{digits}"

    if digits.startswith("80") and len(digits) == 11:
        return f"+3{digits}"

    if digits.startswith("0") and len(digits) == 10:
        return f"+38{digits}"

    if len(digits) == 9:
        return f"+380{digits}"

    return ""


def is_valid_phone(phone: str) -> bool:
    normalized = normalize_phone(phone)
    return bool(re.fullmatch(r"\+380\d{9}", normalized))

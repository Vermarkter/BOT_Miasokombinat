import logging

logger = logging.getLogger(__name__)


class OneCService:
    def check_auth(self, phone: str, code: str) -> bool:
        masked_phone = f"***{phone[-4:]}" if len(phone) >= 4 else "***"
        logger.info("1C auth check started for phone=%s", masked_phone)

        is_authorized = code == "1234"
        if is_authorized:
            logger.info("1C auth check success for phone=%s", masked_phone)
        else:
            logger.warning("1C auth check failed for phone=%s", masked_phone)

        return is_authorized

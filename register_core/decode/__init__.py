"""OTP decode layer (pull mail + parse code; does not allocate addresses)."""

from register_core.decode.base import OtpDecoder
from register_core.decode.registry import get_otp_decoder, list_otp_decoders

__all__ = [
    "OtpDecoder",
    "get_otp_decoder",
    "list_otp_decoders",
]

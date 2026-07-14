"""Minimal gRPC-web/protobuf codec for accounts.x.ai AuthManagement calls."""

from __future__ import annotations

import struct
from typing import Any

WT_VARINT = 0
WT_FIXED64 = 1
WT_LEN = 2
WT_FIXED32 = 5


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field_no: int, wire_type: int) -> bytes:
    return encode_varint((field_no << 3) | wire_type)


def encode_string(field_no: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return _tag(field_no, WT_LEN) + encode_varint(len(raw)) + raw


def frame_request(message: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(message)) + message


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def decode_message(data: bytes) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    i = 0
    n = len(data)
    while i < n:
        tag, i = _read_varint(data, i)
        field_no = tag >> 3
        wt = tag & 0x07
        if wt == WT_VARINT:
            val, i = _read_varint(data, i)
            fields.append({"field": field_no, "type": "varint", "value": val})
        elif wt == WT_FIXED64:
            chunk = data[i : i + 8]
            i += 8
            fields.append({"field": field_no, "type": "fixed64", "hex": chunk.hex()})
        elif wt == WT_LEN:
            ln, i = _read_varint(data, i)
            chunk = data[i : i + ln]
            i += ln
            try:
                s = chunk.decode("utf-8")
                if s.isprintable():
                    fields.append({"field": field_no, "type": "string", "value": s})
                    continue
            except UnicodeDecodeError:
                pass
            fields.append({"field": field_no, "type": "bytes", "hex": chunk.hex(), "len": ln})
        elif wt == WT_FIXED32:
            chunk = data[i : i + 4]
            i += 4
            fields.append({"field": field_no, "type": "fixed32", "hex": chunk.hex()})
        else:
            raise ValueError(f"unsupported wire type {wt} at offset {i}")
    return fields


def parse_response(body: bytes) -> dict[str, Any]:
    messages: list[list[dict[str, Any]]] = []
    trailers: dict[str, str] = {}
    i = 0
    n = len(body)
    while i + 5 <= n:
        flag = body[i]
        length = struct.unpack(">I", body[i + 1 : i + 5])[0]
        payload = body[i + 5 : i + 5 + length]
        i += 5 + length
        if flag & 0x80:
            for line in payload.decode("utf-8", "replace").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
        else:
            messages.append(decode_message(payload))
    grpc_status = int(trailers["grpc-status"]) if "grpc-status" in trailers else None
    return {"messages": messages, "trailers": trailers, "grpc_status": grpc_status}

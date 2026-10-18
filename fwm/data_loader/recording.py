"""
Reader of FESVR device traffic recordings (the folder written by +dev-traffic-record).

The format is defined in riscv-fesvr/fesvr/device_traffic_format.h, and documented in riscv-isa-sim's
docs/fesvr-checkpoint-and-traffic-replay.md, section 9. All integers are little-endian and all structures are packed.
The .packets, .index and .sha256 files are gzip-compressed by default; plain files are read as well. A recording also
holds its provenance (recording_provenance.json, see fwm.data_loader.provenance), which fwm requires.
"""
import gzip
import hashlib
import os
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, List, Optional, Tuple

from fwm.data_loader.provenance import RecordingProvenance, ProvenanceError, read_provenance

TARGET_SPEC_FILE = "target_spec.data"
BOOT_ELF_FILE = "boot.elf"

PACKETS_FILE_MAGIC = 0x38837587
INDEX_FILE_MAGIC = 0x38834639
SHA256_FILE_MAGIC = 0x38833478

# Packet types, and the sizes of their fixed parts (4-byte header + payload)
PACKET_COMMAND_BEGIN = 0
PACKET_PHYSICAL_MEMORY_ACCESS = 1
PACKET_COMMAND_END = 128
_HEADER = struct.Struct("<i")
_CMD_BEGIN_PAYLOAD = struct.Struct("<BBQ")  # device, cmd, payload
_MEM_ACCESS_PAYLOAD = struct.Struct("<QIB")  # physical address, length, is_write; then the data
_CMD_END_PAYLOAD = struct.Struct("<BQqI")  # responded, response value, htif exit code, CRC32

_TARGET_SPEC = struct.Struct("<II32s")  # memory size in MB, number of HARTs, SHA256 of the boot ELF

_GZIP_MAGIC = b"\x1f\x8b"
_CHUNK = 1 << 20


class RecordingFormatError(ValueError):
    pass


@dataclass
class TargetSpec:
    mem_mb: int
    num_harts: int
    boot_elf_sha256: str


@dataclass
class HartRecording:
    hart_id: int
    # SHA256 of the HART's .packets stream, as recorded in its .sha256 file (and verified against the stream)
    recording_sha256: str
    num_commands: int
    # The FESVR exit code recorded in the HART's last COMMAND_END, 0 if it has no command
    last_exit_code: int


@dataclass
class Recording:
    target_spec: TargetSpec
    harts: List[HartRecording]
    # The program's exit status, or None if the run never exited (a truncated recording)
    exit_status: Optional[int]
    # How it was made (recording_provenance.json), and the file's text
    provenance: RecordingProvenance
    provenance_text: str


def _open(path: str) -> BinaryIO:
    """Open a recording file, gzip-compressed or plain."""
    with open(path, "rb") as f:
        magic = f.read(len(_GZIP_MAGIC))
    if magic == _GZIP_MAGIC:
        return gzip.open(path, "rb")  # type: ignore[return-value]
    return open(path, "rb")


def hart_file(recording_dir: str, hart_id: int, ext: str) -> str:
    return os.path.join(recording_dir, f"device_traffic_hart{hart_id}.{ext}")


def read_target_spec(recording_dir: str) -> TargetSpec:
    path = os.path.join(recording_dir, TARGET_SPEC_FILE)
    if not os.path.isfile(path):
        raise RecordingFormatError(f"{recording_dir} is not a device traffic recording: it has no {TARGET_SPEC_FILE}")
    with open(path, "rb") as f:
        data = f.read()
    if len(data) != _TARGET_SPEC.size:
        raise RecordingFormatError(f"{path} is {len(data)} bytes, expected {_TARGET_SPEC.size}")
    mem_mb, num_harts, elf_sha256 = _TARGET_SPEC.unpack(data)
    if num_harts == 0:
        raise RecordingFormatError(f"{path} records a system with no HART")
    if elf_sha256 == bytes(32):
        raise RecordingFormatError(f"{recording_dir} is a recording of a run that loaded no boot ELF")
    return TargetSpec(mem_mb=mem_mb, num_harts=num_harts, boot_elf_sha256=elf_sha256.hex())


def check_boot_elf(recording_dir: str, target_spec: TargetSpec) -> None:
    path = os.path.join(recording_dir, BOOT_ELF_FILE)
    if not os.path.isfile(path):
        raise RecordingFormatError(f"{recording_dir} has no {BOOT_ELF_FILE}; record it again with the current spike")
    with open(path, "rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    if digest != target_spec.boot_elf_sha256:
        raise RecordingFormatError(
            f"{path} has SHA256 {digest}, but the recording was made with a boot ELF of SHA256 "
            f"{target_spec.boot_elf_sha256}")


def _check_magic(data: bytes, magic: int, path: str) -> None:
    if len(data) < 4 or struct.unpack_from("<I", data)[0] != magic:
        raise RecordingFormatError(f"{path} is not a device traffic recording file: bad magic number")


def read_recording_sha256(recording_dir: str, hart_id: int) -> str:
    path = hart_file(recording_dir, hart_id, "sha256")
    with _open(path) as f:
        data = f.read()
    _check_magic(data, SHA256_FILE_MAGIC, path)
    if len(data) != 4 + 32:
        raise RecordingFormatError(f"{path} has no SHA256: the recording was never finalized")
    return data[4:].hex()


def read_index(recording_dir: str, hart_id: int) -> Tuple[int, Optional[int]]:
    """
    Read a HART's .index file.

    :return: The number of commands, and the offset of the last command in the .packets stream (None if no command).
    """
    path = hart_file(recording_dir, hart_id, "index")
    with _open(path) as f:
        _check_magic(f.read(4), INDEX_FILE_MAGIC, path)
        size = 0
        head = last = b""
        while chunk := f.read(_CHUNK):
            if len(head) < 8:
                head = (head + chunk)[:8]
            last = (last + chunk)[-8:]
            size += len(chunk)
    if size % 8 != 0:
        raise RecordingFormatError(f"{path} is truncated")
    num_commands = size // 8
    if num_commands == 0:
        return 0, None
    if struct.unpack("<Q", head)[0] != 4:
        raise RecordingFormatError(f"{path} doesn't start at the first packet")
    return num_commands, struct.unpack("<Q", last)[0]


def _parse_last_command(tail: bytes, path: str) -> int:
    """
    Parse the last command of a .packets stream: a COMMAND_BEGIN, any PHYSICAL_MEMORY_ACCESS, then a COMMAND_END that
    ends the stream.

    :return: The FESVR exit code recorded in the COMMAND_END.
    """
    def bad(why: str) -> RecordingFormatError:
        return RecordingFormatError(f"{path}: malformed last command: {why}")

    pos = 0

    def packet_type() -> int:
        nonlocal pos
        if pos + _HEADER.size > len(tail):
            raise bad("unexpected end of stream")
        (t,) = _HEADER.unpack_from(tail, pos)
        pos += _HEADER.size
        return t

    if packet_type() != PACKET_COMMAND_BEGIN:
        raise bad("the index doesn't point to a COMMAND_BEGIN packet")
    pos += _CMD_BEGIN_PAYLOAD.size
    while True:
        t = packet_type()
        if t == PACKET_PHYSICAL_MEMORY_ACCESS:
            if pos + _MEM_ACCESS_PAYLOAD.size > len(tail):
                raise bad("unexpected end of stream")
            _, length, _ = _MEM_ACCESS_PAYLOAD.unpack_from(tail, pos)
            pos += _MEM_ACCESS_PAYLOAD.size + length
        elif t == PACKET_COMMAND_END:
            if pos + _CMD_END_PAYLOAD.size != len(tail):
                raise bad("COMMAND_END is not the end of the stream")
            _, _, exit_code, _ = _CMD_END_PAYLOAD.unpack_from(tail, pos)
            return exit_code
        else:
            raise bad(f"unexpected packet type {t}")


def scan_packets(recording_dir: str, hart_id: int, last_offset: Optional[int]) -> Tuple[str, int]:
    """
    Read a HART's .packets stream in a single pass.

    :param last_offset: The offset of the last command (from the .index file), or None if there is no command.
    :return: The SHA256 of the whole (decompressed) stream, and the FESVR exit code of its last command (0 if none).
    """
    path = hart_file(recording_dir, hart_id, "packets")
    sha256 = hashlib.sha256()
    tail = bytearray()
    pos = 0
    with _open(path) as f:
        while chunk := f.read(_CHUNK):
            if pos == 0:
                _check_magic(chunk, PACKETS_FILE_MAGIC, path)
            sha256.update(chunk)
            if last_offset is not None and pos + len(chunk) > last_offset:
                tail += chunk[max(0, last_offset - pos):]
            pos += len(chunk)
    if pos == 0:
        _check_magic(b"", PACKETS_FILE_MAGIC, path)
    if last_offset is None:
        if pos != 4:
            raise RecordingFormatError(f"{path} holds packets, but its index lists no command")
        return sha256.hexdigest(), 0
    return sha256.hexdigest(), _parse_last_command(bytes(tail), path)


def read_recording(recording_dir: str) -> Recording:
    """
    Read and check a recording: its target spec, its boot ELF, its provenance, and each HART's files. Each HART's
    .packets stream is read in full to check it against its SHA256 (the replay itself never recomputes it).

    :raise RecordingFormatError: The folder is not a complete, consistent recording.
    """
    try:
        return _read_recording(recording_dir)
    except (zlib.error, EOFError, gzip.BadGzipFile) as e:
        raise RecordingFormatError(f"a file of {recording_dir} is corrupt: {e}")


def _read_recording(recording_dir: str) -> Recording:
    target_spec = read_target_spec(recording_dir)
    check_boot_elf(recording_dir, target_spec)
    try:
        provenance, provenance_text = read_provenance(recording_dir)
    except ProvenanceError as e:
        raise RecordingFormatError(str(e))

    harts = []
    for hart_id in range(target_spec.num_harts):
        for ext in ("packets", "index", "sha256"):
            if not os.path.isfile(hart_file(recording_dir, hart_id, ext)):
                raise RecordingFormatError(
                    f"{recording_dir} has no {os.path.basename(hart_file(recording_dir, hart_id, ext))}")
        recorded_sha256 = read_recording_sha256(recording_dir, hart_id)
        num_commands, last_offset = read_index(recording_dir, hart_id)
        actual_sha256, last_exit_code = scan_packets(recording_dir, hart_id, last_offset)
        if actual_sha256 != recorded_sha256:
            raise RecordingFormatError(
                f"the packets of HART {hart_id} in {recording_dir} have SHA256 {actual_sha256}, but the recording says "
                f"{recorded_sha256}")
        harts.append(HartRecording(hart_id=hart_id, recording_sha256=recorded_sha256, num_commands=num_commands,
                                   last_exit_code=last_exit_code))

    # sys_exit sets the FESVR exit code to (code << 1 | 1), which is never 0, and the run stops right after that
    # command. A run stopped earlier (e.g. by -e) leaves 0 in every last command. The HARTs after the exiting one are
    # still serviced in that round and record the final code, so the last HART's is the one. Like spike's process, keep
    # the low 8 bits of the status (exit(-1) is 255).
    exit_codes = [h.last_exit_code for h in harts if h.last_exit_code != 0]
    exit_status = (exit_codes[-1] >> 1) & 0xff if exit_codes else None
    return Recording(target_spec=target_spec, harts=harts, exit_status=exit_status, provenance=provenance,
                     provenance_text=provenance_text)

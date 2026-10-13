"""
Reader of FESVR checkpoint files (written by +create-checkpoint).

The format is defined in riscv-fesvr/fesvr/checkpoint_format.h and hart_state_format.h, and documented in
riscv-isa-sim's docs/fesvr-checkpoint-and-traffic-replay.md, section 9. The digest follows riscv-isa-sim's
scripts/checkpoint-digest.py, and produces the same text.
"""
import hashlib
import struct
import zlib
from dataclasses import dataclass
from typing import List, Tuple

CHECKPOINT_MAGIC = 0x78642578
CHECKPOINT_FORMAT_VERSION = 1
HART_STATE_STORAGE_MAX = 8192
HART_STATE_MAGIC = b"RVHSTATE"
HART_STATE_VERSION = 1

_HEADER = struct.Struct("<IIII32s")  # magic, version, number of HARTs, memory size in MB, boot ELF SHA256
_HART_RECORD = struct.Struct(f"<32sQI{HART_STATE_STORAGE_MAX}s")  # recording SHA256, skip amount, state size, state
_HART_STATE_HEADER = struct.Struct("<8sII")  # magic, version, number of entries
_HART_STATE_ENTRY = struct.Struct("<IIQ")  # id, reserved, value

# Hart state entry ids
CSR_COUNT = 0x506
CSR_NAMES = {
    0x001: "fflags", 0x002: "frm", 0x500: "sup0", 0x501: "sup1", 0x502: "epc", 0x503: "badvaddr",
    0x504: "ptbr", 0x506: "count", 0x507: "compare", 0x508: "evec", 0x509: "cause", 0x50a: "status",
    0x51e: "tohost", 0x51f: "fromhost",
}

_CHUNK = 1 << 20


class CheckpointFormatError(ValueError):
    pass


@dataclass
class CheckpointHartInfo:
    hart_id: int
    recording_sha256: str
    # Number of commands already serviced: the replay resumes at this command index
    cmd_index: int
    state_version: int
    # The state's entries, (id, reserved, value), in file order
    state_entries: List[Tuple[int, int, int]]

    @property
    def csr_count(self) -> int:
        """The HART's CSR_COUNT: its retired-instruction count where the checkpoint was taken."""
        counts = [value for entry_id, _, value in self.state_entries if entry_id == CSR_COUNT]
        if len(counts) != 1:
            raise CheckpointFormatError(f"the state of HART {self.hart_id} has {len(counts)} CSR_COUNT entries")
        return counts[0]


@dataclass
class CheckpointHeader:
    format_version: int
    num_harts: int
    mem_mb: int
    boot_elf_sha256: str
    harts: List[CheckpointHartInfo]

    @property
    def size(self) -> int:
        return _HEADER.size + self.num_harts * _HART_RECORD.size


def _read_exact(f, n: int, what: str) -> bytes:
    data = f.read(n)
    if len(data) != n:
        raise CheckpointFormatError(f"truncated {what}")
    return data


def _decode_hart_state(hart_id: int, state: bytes) -> Tuple[int, List[Tuple[int, int, int]]]:
    if len(state) < _HART_STATE_HEADER.size or state[:8] != HART_STATE_MAGIC:
        raise CheckpointFormatError(f'the state of HART {hart_id} is not in the portable format (no "RVHSTATE" magic)')
    _, version, count = _HART_STATE_HEADER.unpack_from(state)
    if version != HART_STATE_VERSION:
        raise CheckpointFormatError(
            f"the state of HART {hart_id} is in hart state format version {version}, fwm reads version "
            f"{HART_STATE_VERSION}")
    if len(state) != _HART_STATE_HEADER.size + _HART_STATE_ENTRY.size * count:
        raise CheckpointFormatError(
            f"the state of HART {hart_id} is {len(state)} bytes, which doesn't match its {count} entries")
    entries = [_HART_STATE_ENTRY.unpack_from(state, _HART_STATE_HEADER.size + _HART_STATE_ENTRY.size * i)
               for i in range(count)]
    return version, entries


def _read_header(f) -> CheckpointHeader:
    magic, version, num_harts, mem_mb, elf_sha256 = _HEADER.unpack(_read_exact(f, _HEADER.size, "header"))
    if magic != CHECKPOINT_MAGIC:
        raise CheckpointFormatError("not a checkpoint: bad magic number")
    if version != CHECKPOINT_FORMAT_VERSION:
        raise CheckpointFormatError(
            f"checkpoint format version {version}, fwm reads version {CHECKPOINT_FORMAT_VERSION}")
    harts = []
    for hart_id in range(num_harts):
        recording_sha256, cmd_index, state_size, storage = _HART_RECORD.unpack(
            _read_exact(f, _HART_RECORD.size, f"record of HART {hart_id}"))
        if state_size > HART_STATE_STORAGE_MAX:
            raise CheckpointFormatError(f"the state of HART {hart_id} is {state_size} bytes, more than its storage")
        state_version, entries = _decode_hart_state(hart_id, storage[:state_size])
        harts.append(CheckpointHartInfo(hart_id=hart_id, recording_sha256=recording_sha256.hex(), cmd_index=cmd_index,
                                        state_version=state_version, state_entries=entries))
    return CheckpointHeader(format_version=version, num_harts=num_harts, mem_mb=mem_mb,
                            boot_elf_sha256=elf_sha256.hex(), harts=harts)


def read_checkpoint_header(path: str) -> CheckpointHeader:
    """
    Read and check a checkpoint's header, including every HART's state.

    :raise CheckpointFormatError: The file is not a checkpoint fwm can read.
    """
    with open(path, "rb") as f:
        return _read_header(f)


def _entry_name(entry_id: int) -> str:
    if 0x1000 <= entry_id < 0x1020:
        return "x%d" % (entry_id - 0x1000)
    if 0x1100 <= entry_id < 0x1120:
        return "f%d" % (entry_id - 0x1100)
    if entry_id == 0x2000:
        return "pc"
    if entry_id == 0x2001:
        return "load_reservation"
    if entry_id < 0x1000:
        return CSR_NAMES.get(entry_id, "csr 0x%03x" % entry_id)
    return "id 0x%x" % entry_id


def checkpoint_digest_lines(path: str) -> List[str]:
    """
    The host-independent digest of a checkpoint, as printed by riscv-isa-sim's scripts/checkpoint-digest.py: the
    header fields, every HART's decoded state and the SHA256 of the decompressed memory. Two checkpoints of the same
    point of a run have the same digest, even when their zlib streams differ.

    The memory image must decompress to exactly the memory size, with nothing after the zlib stream.

    :raise CheckpointFormatError: The file is not a well-formed checkpoint.
    """
    with open(path, "rb") as f:
        header = _read_header(f)
        lines = [
            "format version %d" % header.format_version,
            "harts %d" % header.num_harts,
            "memory %d MB" % header.mem_mb,
            "boot ELF SHA256 %s" % header.boot_elf_sha256,
        ]
        for hart in header.harts:
            lines.append("hart %d recording SHA256 %s" % (hart.hart_id, hart.recording_sha256))
            lines.append("hart %d commands serviced %d" % (hart.hart_id, hart.cmd_index))
            lines.append("hart %d state:" % hart.hart_id)
            lines.extend("  %-18s 0x%016x" % (_entry_name(entry_id), value)
                         for entry_id, _, value in sorted(hart.state_entries))

        inflater = zlib.decompressobj()
        memory_sha256 = hashlib.sha256()
        memory_size = 0
        try:
            while chunk := f.read(_CHUNK):
                data = inflater.decompress(chunk)
                memory_sha256.update(data)
                memory_size += len(data)
                if inflater.eof:
                    break
        except zlib.error as e:
            raise CheckpointFormatError(f"corrupt memory image: {e}")
        if not inflater.eof:
            raise CheckpointFormatError("truncated memory image")
        if inflater.unused_data or f.read(1):
            raise CheckpointFormatError("unexpected data after the memory image")
        if memory_size != header.mem_mb << 20:
            raise CheckpointFormatError(
                f"the memory image is {memory_size} bytes, but the checkpoint is of a {header.mem_mb} MB system")
        lines.append("memory image %d bytes, SHA256 %s" % (memory_size, memory_sha256.hexdigest()))
    return lines


def checkpoint_content_sha256(path: str) -> str:
    """
    The SHA256 of a checkpoint's digest text: equal to `scripts/checkpoint-digest.py <path> | sha256sum`.
    """
    text = "\n".join(checkpoint_digest_lines(path)) + "\n"
    return hashlib.sha256(text.encode()).hexdigest()

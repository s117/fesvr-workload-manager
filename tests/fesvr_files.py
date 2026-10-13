"""
Writers of synthetic FESVR recordings and checkpoints for tests, in the on-disk formats of
riscv-fesvr/fesvr/device_traffic_format.h and checkpoint_format.h.
"""
import gzip
import hashlib
import os
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

PACKETS_FILE_MAGIC = 0x38837587
INDEX_FILE_MAGIC = 0x38834639
SHA256_FILE_MAGIC = 0x38833478
CHECKPOINT_MAGIC = 0x78642578

DEFAULT_BOOT_ELF = b"\x7fELF fake proxy kernel"


@dataclass
class Command:
    device: int = 0
    cmd: int = 0
    payload: int = 0x1000
    # (physical address, is_write, data)
    accesses: List[Tuple[int, bool, bytes]] = field(default_factory=lambda: [(0x1000, False, b"\0" * 64)])
    responded: bool = True
    response: int = 1
    # The FESVR exit code after the command: (status << 1 | 1) for an exit command, 0 otherwise
    exit_code: int = 0


def exit_command(status: int) -> Command:
    return Command(exit_code=status << 1 | 1)


def encode_packets(commands: Sequence[Command]) -> Tuple[bytes, List[int]]:
    """
    :return: The .packets stream and the offset of each command.
    """
    data = bytearray(struct.pack("<I", PACKETS_FILE_MAGIC))
    offsets = []
    crc = 0
    for c in commands:
        offsets.append(len(data))
        data += struct.pack("<iBBQ", 0, c.device, c.cmd, c.payload)
        for address, is_write, access_data in c.accesses:
            data += struct.pack("<iQIB", 1, address, len(access_data), int(is_write)) + access_data
        data += struct.pack("<iBQq", 128, int(c.responded), c.response, c.exit_code)
        crc = zlib.crc32(bytes(data[4:]), 0)  # cumulative, from just after the magic up to this field
        data += struct.pack("<I", crc)
    return bytes(data), offsets


def write_recording(rec_dir: str, mem_mb: int = 256, harts: Optional[Sequence[Sequence[Command]]] = None,
                    boot_elf: bytes = DEFAULT_BOOT_ELF, compress: bool = True) -> None:
    """
    Write a recording folder. By default, one HART whose program runs two commands and exits with status 0.
    """
    if harts is None:
        harts = [[Command(), exit_command(0)]]
    os.makedirs(rec_dir, exist_ok=True)

    def write(name: str, data: bytes, may_compress: bool = True) -> None:
        path = os.path.join(rec_dir, name)
        if compress and may_compress:
            with gzip.open(path, "wb") as f:
                f.write(data)
        else:
            with open(path, "wb") as f:
                f.write(data)

    write("target_spec.data", struct.pack("<II32s", mem_mb, len(harts), hashlib.sha256(boot_elf).digest()), False)
    write("boot.elf", boot_elf, False)
    for hart_id, commands in enumerate(harts):
        packets, offsets = encode_packets(commands)
        write(f"device_traffic_hart{hart_id}.packets", packets)
        write(f"device_traffic_hart{hart_id}.index",
              struct.pack("<I", INDEX_FILE_MAGIC) + b"".join(struct.pack("<Q", o) for o in offsets))
        write(f"device_traffic_hart{hart_id}.sha256",
              struct.pack("<I", SHA256_FILE_MAGIC) + hashlib.sha256(packets).digest())


def recording_sha256(commands: Sequence[Command]) -> str:
    return hashlib.sha256(encode_packets(commands)[0]).hexdigest()


# The 80 entries spike writes: 14 CSRs, x0-x31, f0-f31, pc and the load reservation
CSR_IDS = [0x001, 0x002, 0x500, 0x501, 0x502, 0x503, 0x504, 0x506, 0x507, 0x508, 0x509, 0x50a, 0x51e, 0x51f]


def default_hart_state_entries(count: int = 1000, pc: int = 0x10000) -> List[Tuple[int, int]]:
    entries = [(csr, count if csr == 0x506 else csr) for csr in CSR_IDS]
    entries += [(0x1000 + i, 0 if i == 0 else i * 3) for i in range(32)]
    entries += [(0x1100 + i, i * 5) for i in range(32)]
    entries += [(0x2000, pc), (0x2001, (1 << 64) - 1)]
    return entries


def encode_hart_state(entries: Sequence[Tuple[int, int]], version: int = 1, magic: bytes = b"RVHSTATE") -> bytes:
    return magic + struct.pack("<II", version, len(entries)) + b"".join(
        struct.pack("<IIQ", entry_id, 0, value) for entry_id, value in entries)


@dataclass
class CheckpointHart:
    recording_sha256: str
    cmd_index: int = 1
    state: bytes = field(default_factory=lambda: encode_hart_state(default_hart_state_entries()))


def write_checkpoint(path: str, harts: Sequence[CheckpointHart], mem_mb: int = 1,
                     boot_elf: bytes = DEFAULT_BOOT_ELF, version: int = 1, memory: Optional[bytes] = None,
                     trailing: bytes = b"") -> None:
    """
    Write a checkpoint file. The memory defaults to a recognizable pattern of mem_mb MB.
    """
    if memory is None:
        memory = (bytes(range(256)) * 4096 * mem_mb)[:mem_mb << 20]
    header = struct.pack("<IIII32s", CHECKPOINT_MAGIC, version, len(harts), mem_mb, hashlib.sha256(boot_elf).digest())
    for hart in harts:
        header += struct.pack("<32sQI8192s", bytes.fromhex(hart.recording_sha256), hart.cmd_index, len(hart.state),
                              hart.state)
    with open(path, "wb") as f:
        f.write(header + zlib.compress(memory, 9) + trailing)

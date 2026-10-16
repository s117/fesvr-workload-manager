import gzip
import hashlib
import os
import struct

import pytest

from fwm.data_loader.recording import read_recording, RecordingFormatError, hart_file
from tests.fesvr_files import write_recording, Command, exit_command, recording_sha256, DEFAULT_BOOT_ELF


@pytest.mark.parametrize("compress", [True, False])
def test_complete_recording(tmp_path, compress):
    commands = [Command(), Command(accesses=[(0x2000, True, b"x" * 100), (0x3000, False, b"y")]), exit_command(0)]
    write_recording(str(tmp_path), mem_mb=512, harts=[commands], compress=compress)

    rec = read_recording(str(tmp_path))
    assert rec.target_spec.mem_mb == 512
    assert rec.target_spec.num_harts == 1
    assert len(rec.harts) == 1
    assert rec.harts[0].hart_id == 0
    assert rec.harts[0].num_commands == 3
    assert rec.harts[0].recording_sha256 == recording_sha256(commands)
    assert rec.exit_status == 0


def test_exit_status(tmp_path):
    write_recording(str(tmp_path), harts=[[Command(), exit_command(3)]])
    assert read_recording(str(tmp_path)).exit_status == 3


def test_truncated_recording(tmp_path):
    # a run stopped by -e: the program never exited, so no command carries an exit code
    write_recording(str(tmp_path), harts=[[Command(), Command()]])
    rec = read_recording(str(tmp_path))
    assert rec.exit_status is None
    assert rec.harts[0].num_commands == 2


def test_last_command_without_memory_access(tmp_path):
    write_recording(str(tmp_path), harts=[[Command(), Command(accesses=[], exit_code=5 << 1 | 1)]])
    assert read_recording(str(tmp_path)).exit_status == 5


def test_empty_recording(tmp_path):
    write_recording(str(tmp_path), harts=[[]])
    rec = read_recording(str(tmp_path))
    assert rec.harts[0].num_commands == 0
    assert rec.exit_status is None


def test_multi_hart_recording(tmp_path):
    hart0 = [Command(), exit_command(0)]
    hart1 = [Command(payload=0x2000)]
    write_recording(str(tmp_path), harts=[hart0, hart1])
    rec = read_recording(str(tmp_path))
    assert rec.target_spec.num_harts == 2
    assert [h.recording_sha256 for h in rec.harts] == [recording_sha256(hart0), recording_sha256(hart1)]
    assert [h.num_commands for h in rec.harts] == [2, 1]
    assert rec.exit_status == 0


def test_not_a_recording(tmp_path):
    with pytest.raises(RecordingFormatError, match="target_spec.data"):
        read_recording(str(tmp_path))


def test_target_spec_bad_size(tmp_path):
    write_recording(str(tmp_path))
    with open(tmp_path / "target_spec.data", "ab") as f:
        f.write(b"\0")
    with pytest.raises(RecordingFormatError, match="41 bytes"):
        read_recording(str(tmp_path))


def test_no_boot_elf_loaded(tmp_path):
    write_recording(str(tmp_path))
    with open(tmp_path / "target_spec.data", "wb") as f:
        f.write(struct.pack("<II32s", 256, 1, bytes(32)))
    with pytest.raises(RecordingFormatError, match="loaded no boot ELF"):
        read_recording(str(tmp_path))


def test_boot_elf_missing(tmp_path):
    write_recording(str(tmp_path))
    os.remove(tmp_path / "boot.elf")
    with pytest.raises(RecordingFormatError, match="has no boot.elf"):
        read_recording(str(tmp_path))


def test_boot_elf_tampered(tmp_path):
    write_recording(str(tmp_path))
    with open(tmp_path / "boot.elf", "wb") as f:
        f.write(DEFAULT_BOOT_ELF + b"!")
    with pytest.raises(RecordingFormatError, match="boot.elf has SHA256"):
        read_recording(str(tmp_path))


@pytest.mark.parametrize("ext", ["packets", "index", "sha256"])
def test_hart_file_missing(tmp_path, ext):
    write_recording(str(tmp_path))
    os.remove(hart_file(str(tmp_path), 0, ext))
    with pytest.raises(RecordingFormatError, match=f"device_traffic_hart0.{ext}"):
        read_recording(str(tmp_path))


@pytest.mark.parametrize("ext", ["packets", "index", "sha256"])
def test_bad_magic(tmp_path, ext):
    write_recording(str(tmp_path))
    path = hart_file(str(tmp_path), 0, ext)
    with gzip.open(path, "rb") as f:
        data = f.read()
    with gzip.open(path, "wb") as f:
        f.write(b"\0\0\0\0" + data[4:])
    with pytest.raises(RecordingFormatError, match="bad magic number"):
        read_recording(str(tmp_path))


def test_packets_tampered(tmp_path):
    write_recording(str(tmp_path), compress=False)
    path = hart_file(str(tmp_path), 0, "packets")
    with open(path, "r+b") as f:
        f.seek(20)
        f.write(b"\xff")
    with pytest.raises(RecordingFormatError, match="have SHA256"):
        read_recording(str(tmp_path))


def test_not_finalized(tmp_path):
    # a recording whose SHA256 couldn't be written has only the magic in its .sha256 file
    write_recording(str(tmp_path), compress=False)
    with open(hart_file(str(tmp_path), 0, "sha256"), "r+b") as f:
        f.truncate(4)
    with pytest.raises(RecordingFormatError, match="never finalized"):
        read_recording(str(tmp_path))


def test_index_truncated(tmp_path):
    write_recording(str(tmp_path), compress=False)
    with open(hart_file(str(tmp_path), 0, "index"), "r+b") as f:
        f.truncate(4 + 8 + 3)
    with pytest.raises(RecordingFormatError, match="truncated"):
        read_recording(str(tmp_path))


def test_packets_truncated(tmp_path):
    write_recording(str(tmp_path), compress=False)
    path = hart_file(str(tmp_path), 0, "packets")
    size = os.path.getsize(path)
    with open(path, "r+b") as f:
        f.truncate(size - 10)
    # the SHA256 file still holds the digest of the full stream
    with pytest.raises(RecordingFormatError):
        read_recording(str(tmp_path))


def test_corrupt_gzip(tmp_path):
    write_recording(str(tmp_path), harts=[[Command(), exit_command(0)]], compress=True)
    packets = tmp_path / "device_traffic_hart0.packets"
    data = bytearray(packets.read_bytes())
    data[len(data) // 2] ^= 0xff
    packets.write_bytes(bytes(data))
    with pytest.raises(RecordingFormatError):
        read_recording(str(tmp_path))


@pytest.mark.parametrize("status, expected", [(0, 0), (3, 3), (-1, 255), (256, 0)])
def test_exit_status_like_spike(tmp_path, status, expected):
    # spike's process exits with the low 8 bits of the program's status
    write_recording(str(tmp_path), harts=[[Command(), exit_command(status)]])
    assert read_recording(str(tmp_path)).exit_status == expected


def write_raw_hart(rec_dir, packets: bytes, offsets):
    """Replace HART 0's files with this .packets stream and index, with a matching SHA256, uncompressed."""
    for ext, data in (("packets", packets),
                      ("index", struct.pack("<I", 0x38834639) + b"".join(struct.pack("<Q", o) for o in offsets)),
                      ("sha256", struct.pack("<I", 0x38833478) + hashlib.sha256(packets).digest())):
        with open(hart_file(str(rec_dir), 0, ext), "wb") as f:
            f.write(data)


MAGIC = struct.pack("<I", 0x38837587)
BEGIN = struct.pack("<iBBQ", 0, 0, 0, 0x1000)  # COMMAND_BEGIN
ACCESS = struct.pack("<iQIB", 1, 0x1000, 4, 0) + b"data"  # PHYSICAL_MEMORY_ACCESS of 4 bytes
END = struct.pack("<iBQqI", 128, 1, 1, 0, 0)  # COMMAND_END


@pytest.mark.parametrize("packets, offsets, match", [
    (MAGIC + BEGIN + END, [8], "doesn't start at the first packet"),
    (MAGIC + BEGIN + ACCESS + END, [4, 4 + len(BEGIN)], "doesn't point to a COMMAND_BEGIN"),
    (MAGIC + BEGIN + b"\0\0", [4], "unexpected end of stream"),
    (MAGIC + BEGIN + ACCESS[:7], [4], "unexpected end of stream"),
    (MAGIC + BEGIN + struct.pack("<i", 7) + b"\0" * 8, [4], "unexpected packet type 7"),
    (MAGIC + BEGIN + END + b"\0", [4], "COMMAND_END is not the end of the stream"),
    (MAGIC + BEGIN + END, [], "holds packets, but its index lists no command"),
    (b"", [], "bad magic number"),
])
def test_malformed_packets(tmp_path, packets, offsets, match):
    write_recording(str(tmp_path), compress=False)
    write_raw_hart(tmp_path, packets, offsets)
    with pytest.raises(RecordingFormatError, match=match):
        read_recording(str(tmp_path))


def test_no_hart(tmp_path):
    write_recording(str(tmp_path))
    with open(tmp_path / "target_spec.data", "r+b") as f:
        f.seek(4)
        f.write(struct.pack("<I", 0))
    with pytest.raises(RecordingFormatError, match="records a system with no HART"):
        read_recording(str(tmp_path))

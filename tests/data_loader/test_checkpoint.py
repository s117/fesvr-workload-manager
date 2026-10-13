import hashlib
import os
import subprocess
import sys
import zlib

import pytest

from fwm.data_loader.checkpoint import (
    read_checkpoint_header, checkpoint_digest_lines, checkpoint_content_sha256, CheckpointFormatError, CSR_COUNT
)
from tests.fesvr_files import (
    write_checkpoint, CheckpointHart, encode_hart_state, default_hart_state_entries, DEFAULT_BOOT_ELF
)

REC_SHA = "ab" * 32

# riscv-isa-sim's source tree, to cross-check against its scripts/checkpoint-digest.py
SPIKE_SRC = os.environ.get("FWM_TEST_SPIKE_SRC")


def test_read_header(tmp_path):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA, cmd_index=42,
                                           state=encode_hart_state(default_hart_state_entries(count=123456)))],
                     mem_mb=2)
    header = read_checkpoint_header(path)
    assert header.format_version == 1
    assert header.num_harts == 1
    assert header.mem_mb == 2
    assert header.boot_elf_sha256 == hashlib.sha256(DEFAULT_BOOT_ELF).hexdigest()
    assert header.size == 8284
    hart = header.harts[0]
    assert hart.hart_id == 0
    assert hart.recording_sha256 == REC_SHA
    assert hart.cmd_index == 42
    assert hart.state_version == 1
    assert len(hart.state_entries) == 80
    assert hart.csr_count == 123456


def test_multi_hart_header(tmp_path):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA, cmd_index=1), CheckpointHart("cd" * 32, cmd_index=2)])
    header = read_checkpoint_header(path)
    assert [(h.hart_id, h.recording_sha256, h.cmd_index) for h in header.harts] == [(0, REC_SHA, 1),
                                                                                     (1, "cd" * 32, 2)]


def test_no_csr_count(tmp_path):
    path = str(tmp_path / "ckpt")
    entries = [e for e in default_hart_state_entries() if e[0] != CSR_COUNT]
    write_checkpoint(path, [CheckpointHart(REC_SHA, state=encode_hart_state(entries))])
    with pytest.raises(CheckpointFormatError, match="CSR_COUNT"):
        _ = read_checkpoint_header(path).harts[0].csr_count


@pytest.mark.parametrize("kwargs, match", [
    ({"version": 2}, "checkpoint format version 2"),
])
def test_bad_header(tmp_path, kwargs, match):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA)], **kwargs)
    with pytest.raises(CheckpointFormatError, match=match):
        read_checkpoint_header(path)


def test_bad_magic(tmp_path):
    path = tmp_path / "ckpt"
    path.write_bytes(b"\0" * 9000)
    with pytest.raises(CheckpointFormatError, match="bad magic"):
        read_checkpoint_header(str(path))


def test_truncated_header(tmp_path):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA)])
    with open(path, "r+b") as f:
        f.truncate(1000)
    with pytest.raises(CheckpointFormatError, match="truncated record of HART 0"):
        read_checkpoint_header(path)


@pytest.mark.parametrize("state, match", [
    (encode_hart_state(default_hart_state_entries(), magic=b"NOTSTATE"), "not in the portable format"),
    (encode_hart_state(default_hart_state_entries(), version=2), "hart state format version 2"),
    (encode_hart_state(default_hart_state_entries())[:-8], "doesn't match its 80 entries"),
])
def test_bad_hart_state(tmp_path, state, match):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA, state=state)])
    with pytest.raises(CheckpointFormatError, match=match):
        read_checkpoint_header(path)


def test_digest_lines(tmp_path):
    path = str(tmp_path / "ckpt")
    memory = bytes(1 << 20)
    write_checkpoint(path, [CheckpointHart(REC_SHA, cmd_index=7)], memory=memory)
    lines = checkpoint_digest_lines(path)
    assert lines[:4] == ["format version 1", "harts 1", "memory 1 MB",
                         f"boot ELF SHA256 {hashlib.sha256(DEFAULT_BOOT_ELF).hexdigest()}"]
    assert lines[4:7] == [f"hart 0 recording SHA256 {REC_SHA}", "hart 0 commands serviced 7", "hart 0 state:"]
    assert "  count              0x00000000000003e8" in lines
    assert "  pc                 0x0000000000010000" in lines
    assert lines[-1] == f"memory image {1 << 20} bytes, SHA256 {hashlib.sha256(memory).hexdigest()}"


def test_digest_independent_of_compression(tmp_path):
    # the same memory compressed differently gives the same digest
    path_a, path_b = str(tmp_path / "a"), str(tmp_path / "b")
    write_checkpoint(path_a, [CheckpointHart(REC_SHA)])
    write_checkpoint(path_b, [CheckpointHart(REC_SHA)])
    with open(path_b, "rb") as f:
        data = f.read()
    header_size = 8284
    memory = zlib.decompress(data[header_size:])
    with open(path_b, "wb") as f:
        f.write(data[:header_size] + zlib.compress(memory, 1))
    assert open(path_a, "rb").read() != open(path_b, "rb").read()
    assert checkpoint_content_sha256(path_a) == checkpoint_content_sha256(path_b)


@pytest.mark.parametrize("kwargs, match", [
    ({"trailing": b"x"}, "unexpected data after the memory image"),
    ({"memory": bytes(1000)}, "is of a 1 MB system"),
])
def test_bad_memory_image(tmp_path, kwargs, match):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA)], **kwargs)
    with pytest.raises(CheckpointFormatError, match=match):
        checkpoint_digest_lines(path)


def test_truncated_memory_image(tmp_path):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA)])
    with open(path, "r+b") as f:
        f.truncate(os.path.getsize(path) - 10)
    with pytest.raises(CheckpointFormatError, match="truncated memory image"):
        checkpoint_digest_lines(path)


@pytest.mark.skipif(not SPIKE_SRC, reason="set FWM_TEST_SPIKE_SRC to riscv-isa-sim's source tree")
def test_content_sha256_matches_digest_script(tmp_path):
    path = str(tmp_path / "ckpt")
    write_checkpoint(path, [CheckpointHart(REC_SHA, cmd_index=3)], mem_mb=2)
    assert SPIKE_SRC
    output = subprocess.run([sys.executable, os.path.join(SPIKE_SRC, "scripts", "checkpoint-digest.py"), path],
                            check=True, capture_output=True).stdout
    assert checkpoint_content_sha256(path) == hashlib.sha256(output).hexdigest()

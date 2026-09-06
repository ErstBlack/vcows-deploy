"""The one `qemu-img` call, against a faked `subprocess.run`.

What is worth testing here is the argv and what a failure says, and neither needs
qemu-img: the real conversion is measured by the smoke chunk, against a file it
then imports. So `subprocess.run` is replaced and the whole command line is
asserted -- a subformat handed to the wrong import path fails at the far end,
mid-upload, which is the one thing a unit test can still catch here.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from orchestrator.backends.vsphere import convert


@pytest.fixture
def recorded(monkeypatch):
    """`subprocess.run` on the module under test, recording its whole call."""
    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(convert.subprocess, "run", fake_run)
    return calls


def failing(returncode: int = 1, stderr: bytes | None = b""):
    """A `subprocess.run` that refused, the way `check=True` reports it."""

    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(returncode, argv, b"", stderr)

    return fake_run


def test_the_command_line_is_the_whole_of_what_it_does(recorded, tmp_path):
    dest = tmp_path / "golden.vmdk"
    assert convert.to_vmdk("/images/golden.qcow2", dest, "streamOptimized") == dest
    assert recorded[0]["argv"] == [
        "qemu-img",
        "convert",
        "-O",
        "vmdk",
        "-o",
        "subformat=streamOptimized",
        "/images/golden.qcow2",
        str(dest),
    ]


def test_it_refuses_a_conversion_that_failed_rather_than_returning_a_path(
    monkeypatch, tmp_path
):
    """`check=True` is what makes the failure visible at all: without it,
    `prepare` hands `create` the path of a file qemu-img never finished, and the
    upload is what fails."""
    monkeypatch.setattr(convert.subprocess, "run", failing())
    with pytest.raises(convert.ConversionError):
        convert.to_vmdk("/images/golden.qcow2", tmp_path / "g.vmdk", "monolithicFlat")


def test_the_capture_is_what_puts_qemu_imgs_own_sentence_in_the_error(
    monkeypatch, tmp_path
):
    """A `CalledProcessError` renders as its exit status and nothing else, so
    the captured stderr has to be lifted into the message by hand. It is the
    only place the reason -- a full filesystem, an unreadable image -- appears.
    """
    monkeypatch.setattr(
        convert.subprocess,
        "run",
        failing(1, b"qemu-img: error while writing sector 0: No space left on device"),
    )
    with pytest.raises(convert.ConversionError) as bad:
        convert.to_vmdk("/images/golden.qcow2", tmp_path / "g.vmdk", "monolithicFlat")
    assert "No space left on device" in str(bad.value)
    assert "/images/golden.qcow2" in str(bad.value)
    assert "monolithicFlat" in str(bad.value)


def test_a_failure_that_wrote_nothing_to_stderr_still_names_the_image(
    monkeypatch, tmp_path
):
    """`capture_output` yields None for a stream the fake did not set, and a
    message built by concatenating that is a TypeError instead of an error."""
    monkeypatch.setattr(convert.subprocess, "run", failing(127, None))
    with pytest.raises(convert.ConversionError, match="exit 127"):
        convert.to_vmdk("/images/golden.qcow2", tmp_path / "g.vmdk", "streamOptimized")


def test_the_two_import_paths_take_the_two_subformats():
    """An `ImportVApp` lease reads `streamOptimized` and nothing else; a
    datastore PUT wants the descriptor-plus-extent pair. The mapping lives beside
    the call for that reason."""
    assert convert.SUBFORMAT == {
        "ovf": "streamOptimized",
        "datastore": "monolithicFlat",
    }


def test_it_says_what_it_is_converting_before_it_goes_quiet(recorded, caplog, tmp_path):
    """A multi-GB conversion is the longest silence in a deploy that has not
    reached the network yet."""
    with caplog.at_level(logging.INFO):
        convert.to_vmdk("/images/golden.qcow2", tmp_path / "g.vmdk", "monolithicFlat")
    assert "/images/golden.qcow2" in caplog.text
    assert "monolithicFlat" in caplog.text


def test_the_output_is_captured_rather_than_written_through(recorded, tmp_path):
    """qemu-img's progress would otherwise land in the middle of the run's own
    output, and the stderr is wanted in the exception rather than on the
    terminal."""
    convert.to_vmdk("/images/golden.qcow2", tmp_path / "g.vmdk", "streamOptimized")
    assert recorded[0]["capture_output"] is True
    assert recorded[0]["check"] is True


def test_every_argument_reaches_argv_as_a_string(recorded, tmp_path):
    """`prepare` hands it a `Path` and the config hands it a `str`; subprocess
    takes both, and a test asserting on argv would not notice which arrived."""
    convert.to_vmdk(Path("/images/golden.qcow2"), tmp_path / "g.vmdk", "monolithicFlat")
    assert all(isinstance(arg, str) for arg in recorded[0]["argv"])

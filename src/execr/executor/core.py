from __future__ import annotations

import os
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import cast

from wasmtime import Config, Engine, Func, Linker, Memory, Module, Store, WasiConfig


@dataclass
class RuntimeConfig:
    use_fuel: bool = False
    fuel: int = 400_000_000
    mount_fs: str = field(default_factory=mkdtemp)
    stdio: str = field(default_factory=mkdtemp)


@dataclass
class Result:
    stdout: str = ""
    stderr: str = ""
    fuel_consumed: int | None = None
    memory: int = 0
    data_len: int = 0


@cache
def binary() -> bytes:
    return files("execr").joinpath("files").joinpath("python-3.12.0.wasm").read_bytes()


def _execute(
    code: str,
    stdin: str = "",
    args: Sequence[str] | None = None,
    cfg: RuntimeConfig | None = None,
):
    if cfg is None:
        cfg = RuntimeConfig()

    engine_config = Config()
    engine_config.consume_fuel = cfg.use_fuel
    engine_config.cache = True

    linker = Linker(Engine(engine_config))
    linker.define_wasi()

    module = Module(linker.engine, binary())

    wasi_config = WasiConfig()
    wasi_config.preopen_dir(cfg.mount_fs, "/")
    if "EXECR_PYTHONPATH" in os.environ:
        wasi_config.preopen_dir(os.environ["EXECR_PYTHONPATH"], "/__pypackages__")
        wasi_config.env = [("PYTHONPATH", "/__pypackages__")]

    stdio = Path(cfg.stdio)
    code_file_name = f"{uuid.uuid4()}.py"
    code_file = Path(cfg.mount_fs) / code_file_name
    stdin_file = stdio / "stdin.txt"
    stdout_file = stdio / "stdout.txt"
    stderr_file = stdio / "stderr.txt"

    code_file.write_text(code, encoding="utf-8")
    stdin_file.write_text(stdin, encoding="utf-8")

    argv = ["python", code_file_name]
    if args:
        argv.extend(args)

    wasi_config.argv = argv
    wasi_config.stdin_file = str(stdin_file)
    wasi_config.stdout_file = str(stdout_file)
    wasi_config.stderr_file = str(stderr_file)

    store = Store(linker.engine)
    store.set_wasi(wasi_config)
    instance = linker.instantiate(store, module)

    if cfg.use_fuel:
        store.set_fuel(cfg.fuel)

    start = instance.exports(store)["_start"]
    start = cast(Func, start)
    memory = instance.exports(store)["memory"]
    memory = cast(Memory, memory)

    with suppress(Exception):
        start(store)

    out = stdout_file.read_text(encoding="utf-8")
    err = stderr_file.read_text(encoding="utf-8")
    fuel_consumed = cfg.fuel - store.get_fuel() if cfg.use_fuel else None

    return Result(
        stdout=out,
        stderr=err,
        fuel_consumed=fuel_consumed,
        memory=memory.size(store),
        data_len=memory.data_len(store),
    )


def execute(  # noqa: PLR0913
    code: str,
    stdin: str = "",
    args: Sequence[str] | None = None,
    use_fuel: bool = False,  # noqa: FBT001, FBT002
    fuel: int = 400_000_000,
    mount_fs: str | None = None,
    stdio: str | None = None,
):
    with TemporaryDirectory(prefix="execr") as tmp:
        mount_fs_path = Path(tmp, "mount_fs") if mount_fs is None else Path(mount_fs)
        stdio_path = Path(tmp, "stdio") if stdio is None else Path(stdio)
        mount_fs_path.mkdir(exist_ok=True)
        stdio_path.mkdir(exist_ok=True)

        cfg = RuntimeConfig(
            use_fuel=use_fuel,
            fuel=fuel,
            mount_fs=str(mount_fs_path),
            stdio=str(stdio_path),
        )
        return _execute(code, stdin, args, cfg)

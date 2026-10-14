import subprocess
from logging import getLogger

log = getLogger(__name__)

DEFAULT_EXECUTE_PARAMS = dict(
    stdin=False,
    stdout=True,
    stderr=False,
)


def get_params(program: str, *args, **kwargs) -> tuple[list[str], dict[str, bool]]:
    opts = [program]
    args = [str(p) for p in args]
    exec_kwargs = {k: kwargs.pop(k, v) for k, v in DEFAULT_EXECUTE_PARAMS.items()}

    for key, value in kwargs.items():
        key = key.replace("_", "-")
        prefix = "-" if len(key) == 1 else "--"
        if value is True:
            opts.append(f"{prefix}{key}")
        elif value is not False and value is not None:
            opts.extend([f"{prefix}{key}", str(value)])

    return opts + args, exec_kwargs


def sys_execute(program: str, *args, **kwargs) -> subprocess.CompletedProcess:
    cmd, exec_kwargs = get_params(program, *args, **kwargs)
    try:
        log.debug("Eseguo comando: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            shell=False,
            check=False,
        )
        log.debug("Return code: %s", result.returncode)
        if result.stdout:
            log.debug("STDOUT:\n%s", result.stdout)
        if result.stderr:
            log.error("STDERR:\n%s", result.stderr)
        return result
    except Exception:
        log.exception("Errore durante l'esecuzione del comando")
        raise

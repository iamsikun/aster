import yaml, pathlib

def load_config(path: str | None = None) -> dict:
    p = pathlib.Path(path or "config.yaml")
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

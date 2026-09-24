#!/usr/bin/env python3
"""Baixa e extrai o modelo leve de portugues do Vosk (vosk-model-small-pt-0.3).

Uso:
    python download_model.py

O download e idempotente: se a pasta do modelo ja existir, nada e feito.
"""

import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

MODEL_NAME = "vosk-model-small-pt-0.3"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
MODELS_DIR = Path(__file__).resolve().parent / "models"
MODEL_DIR = MODELS_DIR / MODEL_NAME
ZIP_PATH = MODELS_DIR / f"{MODEL_NAME}.zip"

# O Vosk tem dois layouts de modelo: o plano (modelos "small" antigos, como o
# pt-0.3) e o de subpastas (modelos maiores/mais novos). Aceitamos os dois.
FLAT_LAYOUT = ("final.mdl", "mfcc.conf")
NESTED_LAYOUT = ("am", "conf", "graph")


def is_model_valid(path: Path) -> bool:
    if not path.is_dir():
        return False
    flat = all((path / name).exists() for name in FLAT_LAYOUT)
    nested = all((path / name).is_dir() for name in NESTED_LAYOUT)
    return flat or nested


_last_pct = -1


def _progress(block_num: int, block_size: int, total_size: int) -> None:
    """Redesenha a barra so quando o percentual muda, para nao inundar o log."""
    global _last_pct
    if total_size <= 0:
        return
    downloaded = min(block_num * block_size, total_size)
    pct = downloaded * 100 // total_size
    if pct == _last_pct:
        return
    _last_pct = pct
    bar = "#" * (pct // 2) + "." * (50 - pct // 2)
    sys.stdout.write(
        f"\r  [{bar}] {pct:3d}%  ({downloaded / 1e6:.1f}/{total_size / 1e6:.1f} MB)"
    )
    sys.stdout.flush()


def download() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Baixando {MODEL_URL}")
    try:
        urllib.request.urlretrieve(MODEL_URL, ZIP_PATH, _progress)
    except urllib.error.URLError as exc:
        ZIP_PATH.unlink(missing_ok=True)
        sys.exit(
            f"\nERRO: falha no download ({exc}).\n"
            f"Baixe manualmente em {MODEL_URL} e extraia em {MODELS_DIR}/"
        )
    print()


def extract() -> None:
    print(f"Extraindo em {MODELS_DIR}/ ...")
    try:
        with zipfile.ZipFile(ZIP_PATH) as zf:
            zf.extractall(MODELS_DIR)
    except zipfile.BadZipFile:
        ZIP_PATH.unlink(missing_ok=True)
        sys.exit(
            "ERRO: o arquivo baixado esta corrompido. Rode o script de novo."
        )
    finally:
        ZIP_PATH.unlink(missing_ok=True)


def main() -> None:
    if is_model_valid(MODEL_DIR):
        print(f"Modelo ja presente em {MODEL_DIR} - nada a fazer.")
        return

    # Pasta existe mas esta incompleta (download interrompido, por exemplo).
    if MODEL_DIR.exists():
        print(f"Pasta {MODEL_DIR} esta incompleta; removendo para rebaixar.")
        shutil.rmtree(MODEL_DIR)

    download()
    extract()

    if not is_model_valid(MODEL_DIR):
        sys.exit(
            f"ERRO: extracao terminou mas {MODEL_DIR} nao parece um modelo Vosk "
            f"(esperado {', '.join(FLAT_LAYOUT)} ou as pastas "
            f"{', '.join(NESTED_LAYOUT)})."
        )

    print(f"\nPronto! Modelo disponivel em {MODEL_DIR}")
    print('Confira se "model_path" no config.json aponta para essa pasta.')


if __name__ == "__main__":
    main()

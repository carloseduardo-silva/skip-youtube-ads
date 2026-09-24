#!/usr/bin/env python3
"""Captura a posicao do mouse para preencher o modo "click" do config.json.

Uso:
    python get_coords.py            # conta 5s e captura onde o cursor estiver
    python get_coords.py --wait 10  # mais tempo para posicionar o cursor

Deixe um anuncio rodando no YouTube, posicione o cursor sobre o botao
"Pular anuncios" e espere a contagem terminar.
"""

import argparse
import sys
import time

try:
    import pyautogui
except Exception as exc:  # ImportError ou falha do backend grafico
    sys.exit(
        f"ERRO: nao foi possivel carregar o pyautogui ({exc}).\n"
        "Ative a venv e rode: pip install -r requirements.txt"
    )

# Sem isso, levar o cursor a um canto da tela derruba o script com FailSafeException.
pyautogui.FAILSAFE = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wait",
        type=float,
        default=5.0,
        help="segundos ate a captura (padrao: 5)",
    )
    return parser.parse_args()


def countdown(seconds: float) -> None:
    """Mostra a posicao do cursor ao vivo ate o tempo acabar."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        x, y = pyautogui.position()
        sys.stdout.write(f"\r  x={x:<6} y={y:<6}   capturando em {remaining:4.1f}s ")
        sys.stdout.flush()
        time.sleep(0.1)
    sys.stdout.write("\r" + " " * 60 + "\r")


def main() -> None:
    args = parse_args()
    width, height = pyautogui.size()

    print(f"Tela: {width}x{height} (pontos logicos, a mesma unidade usada no clique)")
    print("Posicione o cursor sobre o botao 'Pular anuncios'...\n")

    try:
        countdown(args.wait)
    except KeyboardInterrupt:
        sys.exit("\nCancelado.")

    x, y = pyautogui.position()
    print(f"Coordenada capturada: x={x}, y={y}\n")
    print("Cole este trecho no config.json:\n")
    print('  "action_mode": "click",')
    print('  "click": {')
    print(f'    "x": {x},')
    print(f'    "y": {y},')
    print('    "restore_mouse": true')
    print("  }")
    print(
        "\nDica: o botao muda de lugar quando a janela muda de tamanho. "
        "Recalibre se mudar o layout do navegador."
    )


if __name__ == "__main__":
    main()

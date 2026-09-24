#!/usr/bin/env python3
"""Pula anuncios do YouTube por comando de voz, 100% offline (macOS).

Fluxo: microfone -> Vosk (modelo local) -> acao no sistema (clique ou teclas).

Uso:
    python main.py                  # usa config.json ao lado deste arquivo
    python main.py --test-action    # dispara a acao uma vez e sai (sem microfone)
    python main.py --list-devices   # lista os dispositivos de entrada
    python main.py --verbose        # mostra os parciais do reconhecedor
"""

import argparse
import json
import logging
import os
import queue
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"

log = logging.getLogger("skip-ads")


# --------------------------------------------------------------------------- #
# Dependencias externas
# --------------------------------------------------------------------------- #

def import_dependencies():
    """Importa as libs pesadas com uma mensagem util se faltarem."""
    try:
        import sounddevice as sd
        import vosk
        import pyautogui
    except ImportError as exc:
        sys.exit(
            f"ERRO: dependencia ausente ({exc.name}).\n"
            "Ative a venv e rode: pip install -r requirements.txt"
        )
    except Exception as exc:
        # pyautogui pode falhar ao carregar o backend grafico do macOS.
        sys.exit(f"ERRO ao carregar as dependencias: {exc}")
    return sd, vosk, pyautogui


# --------------------------------------------------------------------------- #
# Permissoes do macOS
# --------------------------------------------------------------------------- #

# No macOS a permissao vale para o app que lancou o processo, nao para o "python".
_BUNDLE_NAMES = {
    "com.microsoft.VSCode": "Visual Studio Code",
    "com.visualstudio.code.oss": "VS Code (OSS)",
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "dev.warp.Warp-Stable": "Warp",
    "co.zeit.hyper": "Hyper",
    "com.jetbrains.pycharm": "PyCharm",
}


def host_app_name() -> str:
    """Nome do app que precisa das permissoes (o que lancou este processo)."""
    bundle_id = os.environ.get("__CFBundleIdentifier")
    if not bundle_id:
        return "o app que roda este script"
    return _BUNDLE_NAMES.get(bundle_id, bundle_id)


def check_accessibility() -> Optional[bool]:
    """A permissao de Acessibilidade esta concedida?

    True/False conforme o preflight do CoreGraphics; None se nao der para saber.
    Usamos o *preflight* de proposito: o CGRequestPostEventAccess abriria o
    dialogo do sistema como efeito colateral de um processo que so queria ouvir
    o microfone.
    """
    try:
        import Quartz
        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception as exc:
        log.debug("nao foi possivel checar a Acessibilidade: %s", exc)
        return None


def warn_if_no_accessibility() -> Optional[bool]:
    """Avisa (sem derrubar nada) quando falta Acessibilidade.

    Devolve o mesmo tri-estado de check_accessibility(): True concedida,
    False negada, None indeterminado.
    """
    granted = check_accessibility()
    if granted is not False:
        return granted

    app = host_app_name()
    log.warning(
        "SEM permissao de Acessibilidade: o clique/teclas nao vao funcionar.\n"
        "    Ajustes do Sistema > Privacidade e Seguranca > Acessibilidade > '+' "
        "> adicione %s\n"
        "    Depois encerre %s por completo (Cmd+Q) e abra de novo - a permissao "
        "so vale para um processo novo.",
        app, app,
    )
    return False





# --------------------------------------------------------------------------- #
# Configuracao
# --------------------------------------------------------------------------- #

class ConfigError(Exception):
    """Configuracao invalida a ponto de impedir a execucao."""


@dataclass
class Config:
    model_path: str = "models/vosk-model-small-pt-0.3"
    # "skip" nao existe no vocabulario do modelo de portugues (veja o README).
    commands: List[str] = field(default_factory=lambda: ["pular", "pula"])
    sample_rate: int = 16000
    block_size: int = 2000
    input_device: Optional[Union[int, str]] = None
    cooldown_seconds: float = 2.0
    action_mode: str = "click"
    click: Dict[str, Any] = field(
        default_factory=lambda: {"x": 0, "y": 0, "restore_mouse": True}
    )
    keys: List[List[str]] = field(default_factory=lambda: [["tab"], ["enter"]])
    failsafe: bool = False
    verbose_partials: bool = False


def load_config(path: Path) -> Config:
    """Le o config.json por cima dos defaults.

    Arquivo ausente ou chave faltando nao e fatal: cai no default e avisa.
    """
    cfg = Config()

    if not path.is_file():
        log.warning("config.json nao encontrado em %s - usando os defaults.", path)
        return cfg

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("config.json invalido (%s) - usando os defaults.", exc)
        return cfg

    if not isinstance(raw, dict):
        log.warning("config.json nao e um objeto JSON - usando os defaults.")
        return cfg

    known = set(cfg.__dataclass_fields__)
    for key, value in raw.items():
        if key in known:
            setattr(cfg, key, value)
        else:
            log.warning("Chave desconhecida no config.json ignorada: %r", key)

    return cfg


def validate_config(cfg: Config, pyautogui) -> None:
    """Falhas que so aparecem na hora de agir viram erro aqui, na partida."""
    model_dir = Path(cfg.model_path)
    if not model_dir.is_absolute():
        model_dir = BASE_DIR / model_dir
    if not model_dir.is_dir():
        raise ConfigError(
            f"modelo nao encontrado em {model_dir}\n"
            "Rode: python download_model.py"
        )
    cfg.model_path = str(model_dir)

    cfg.commands = [str(c).strip().lower() for c in cfg.commands if str(c).strip()]
    if not cfg.commands:
        raise ConfigError('a lista "commands" esta vazia no config.json')

    if cfg.action_mode not in ("click", "keys"):
        raise ConfigError(
            f'action_mode invalido: {cfg.action_mode!r} (use "click" ou "keys")'
        )

    if cfg.action_mode == "click":
        try:
            x, y = int(cfg.click["x"]), int(cfg.click["y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f'bloco "click" invalido no config.json ({exc})')

        if (x, y) == (0, 0):
            raise ConfigError(
                "as coordenadas do clique ainda estao em (0, 0).\n"
                "Rode: python get_coords.py  e cole o resultado no config.json"
            )

        try:
            width, height = pyautogui.size()
        except Exception as exc:  # sem acesso a tela
            raise ConfigError(f"nao foi possivel ler o tamanho da tela ({exc})")

        if not (0 <= x < width and 0 <= y < height):
            raise ConfigError(
                f"coordenada ({x}, {y}) esta fora da tela de {width}x{height}.\n"
                "Recalibre com: python get_coords.py"
            )
        cfg.click["x"], cfg.click["y"] = x, y

    if cfg.action_mode == "keys":
        if not cfg.keys or not all(isinstance(combo, list) and combo for combo in cfg.keys):
            raise ConfigError(
                '"keys" deve ser uma lista de combinacoes, ex.: [["tab"], ["enter"]]'
            )

    if cfg.block_size < 500:
        log.warning(
            "block_size=%d e pequeno demais e piora o reconhecimento; usando 500.",
            cfg.block_size,
        )
        cfg.block_size = 500


# --------------------------------------------------------------------------- #
# Captura de audio
# --------------------------------------------------------------------------- #

class AudioError(Exception):
    """Falha de microfone/stream que impede a escuta."""


class AudioSource:
    """Stream de entrada do PortAudio exposto como um gerador de blocos.

    O callback do PortAudio roda em uma thread de tempo real: ele apenas
    enfileira os bytes. Todo o trabalho pesado (Vosk) fica na thread principal,
    senao o callback atrasa e o driver comeca a descartar audio.
    """

    # Poucos blocos na fila: se o consumidor atrasar, e melhor descartar audio
    # velho do que acumular e responder ao comando com segundos de atraso.
    MAX_QUEUED_BLOCKS = 16

    def __init__(self, sd, samplerate: int, blocksize: int, device=None):
        self._sd = sd
        self._samplerate = samplerate
        self._blocksize = blocksize
        self._device = device
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=self.MAX_QUEUED_BLOCKS)
        self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("status do stream de audio: %s", status)
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            # Consumidor atrasado: descarta o bloco mais antigo e guarda o novo.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(bytes(indata))
            except (queue.Empty, queue.Full):
                pass

    def __enter__(self) -> "AudioSource":
        try:
            self._sd.check_input_settings(
                device=self._device,
                channels=1,
                dtype="int16",
                samplerate=self._samplerate,
            )
        except Exception as exc:
            raise AudioError(
                f"o dispositivo de entrada nao aceita {self._samplerate} Hz mono "
                f"int16 ({exc}).\n"
                "Ajuste 'sample_rate' no config.json (tente 44100 ou 48000) ou "
                "escolha outro 'input_device' (veja: python main.py --list-devices)."
            )

        try:
            self._stream = self._sd.RawInputStream(
                samplerate=self._samplerate,
                blocksize=self._blocksize,
                device=self._device,
                dtype="int16",
                channels=1,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            raise AudioError(
                f"nao foi possivel abrir o microfone ({exc}).\n"
                "Verifique se o Terminal tem permissao em Ajustes do Sistema > "
                "Privacidade e Seguranca > Microfone, e se outro app nao esta "
                "usando o dispositivo com exclusividade."
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as close_exc:
                log.debug("erro ao fechar o stream: %s", close_exc)

    def blocks(self) -> Iterator[bytes]:
        """Entrega blocos de audio; avisa (uma vez) se o microfone emudecer."""
        warned_silent = False
        while True:
            try:
                block = self._queue.get(timeout=5.0)
            except queue.Empty:
                if not warned_silent:
                    log.warning(
                        "nenhum audio ha 5s. Confira a permissao do microfone em "
                        "Ajustes do Sistema > Privacidade e Seguranca > Microfone."
                    )
                    warned_silent = True
                continue
            warned_silent = False
            yield block


# --------------------------------------------------------------------------- #
# Reconhecimento
# --------------------------------------------------------------------------- #

class CommandRecognizer:
    """Vosk com gramatica fechada nos comandos-alvo.

    Restringir o vocabulario deixa o decoder mais rapido e derruba o
    falso-positivo: qualquer outra fala cai em "[unk]".
    """

    def __init__(self, vosk_mod, model_path: str, sample_rate: int,
                 commands: List[str], verbose_partials: bool = False):
        vosk_mod.SetLogLevel(0 if verbose_partials else -1)

        try:
            model = vosk_mod.Model(model_path)
        except Exception as exc:
            raise ConfigError(
                f"nao foi possivel carregar o modelo em {model_path} ({exc}).\n"
                "Rode: python download_model.py"
            )

        commands = self._filter_by_vocabulary(model, commands)

        grammar = json.dumps(list(commands) + ["[unk]"], ensure_ascii=False)
        self._rec = vosk_mod.KaldiRecognizer(model, sample_rate, grammar)
        self._commands = list(commands)
        self._verbose = verbose_partials
        self._last_partial = ""

    @staticmethod
    def _filter_by_vocabulary(model, commands: List[str]) -> List[str]:
        """Remove comandos que o modelo nao conhece.

        Palavra fora do vocabulario nunca seria reconhecida e ainda faz o Vosk
        cuspir um warning na partida. Melhor avisar de forma clara aqui.
        """
        usable, unknown = [], []
        for cmd in commands:
            missing = [w for w in cmd.split() if model.vosk_model_find_word(w) < 0]
            if missing:
                unknown.extend(missing)
            else:
                usable.append(cmd)

        if unknown:
            log.warning(
                "ignorando palavra(s) fora do vocabulario do modelo: %s "
                "(o modelo e de portugues; termos em outro idioma nao sao "
                "reconhecidos)",
                ", ".join(repr(w) for w in unknown),
            )
        if not usable:
            raise ConfigError(
                "nenhum comando do config.json existe no vocabulario do modelo."
            )
        return usable

    @property
    def commands(self) -> List[str]:
        """Comandos efetivamente ativos (ja filtrados pelo vocabulario)."""
        return list(self._commands)

    def _match(self, text: str) -> Optional[str]:
        text = text.strip().lower()
        if not text:
            return None
        words = set(text.split())
        for cmd in self._commands:
            # Comparacao por palavra inteira: "pulava" nao pode casar com "pula".
            if (cmd in text) if " " in cmd else (cmd in words):
                return cmd
        return None

    def feed(self, block: bytes) -> Optional[str]:
        """Consome um bloco e devolve o comando reconhecido, se houver.

        Le tambem o resultado parcial: esperar o fim do enunciado custaria
        centenas de milissegundos, que e justamente o que queremos evitar.
        """
        if self._rec.AcceptWaveform(block):
            text = json.loads(self._rec.Result()).get("text", "")
            self._last_partial = ""
        else:
            text = json.loads(self._rec.PartialResult()).get("partial", "")
            if self._verbose and text and text != self._last_partial:
                log.debug("parcial: %s", text)
                self._last_partial = text

        command = self._match(text)
        if command:
            # Zera o estado para o mesmo trecho de fala nao disparar de novo.
            self._rec.Reset()
            self._last_partial = ""
        return command


# --------------------------------------------------------------------------- #
# Acao no sistema
# --------------------------------------------------------------------------- #

class Actuator:
    """Executa a acao de pular, respeitando um cooldown."""

    def __init__(self, pyautogui_mod, cfg: Config):
        self._gui = pyautogui_mod
        self._cfg = cfg
        # -inf, e nao 0: time.monotonic() comeca perto de zero no macOS, entao
        # zerar aqui faria o cooldown engolir o primeiro comando logo apos a
        # partida do script.
        self._last_fired = float("-inf")
        self._permission_warned = False

        # PAUSE padrao do pyautogui e 100ms entre acoes - latencia gratuita que
        # nao queremos aqui.
        self._gui.PAUSE = 0
        self._gui.FAILSAFE = bool(cfg.failsafe)

    def trigger(self, command: str) -> bool:
        now = time.monotonic()
        elapsed = now - self._last_fired
        if elapsed < self._cfg.cooldown_seconds:
            log.debug("'%s' ignorado (cooldown, faltam %.1fs)",
                      command, self._cfg.cooldown_seconds - elapsed)
            return False

        self._last_fired = now
        try:
            if self._cfg.action_mode == "click":
                self._click()
            else:
                self._press_keys()
        except Exception as exc:
            self._warn_permission(exc)
            return False

        log.info("comando '%s' -> acao disparada (%s)", command, self._cfg.action_mode)
        return True

    def _click(self) -> None:
        x, y = self._cfg.click["x"], self._cfg.click["y"]
        restore = self._cfg.click.get("restore_mouse", True)
        origin = self._gui.position() if restore else None
        self._gui.click(x, y)
        if origin is not None:
            self._gui.moveTo(origin[0], origin[1])

    def _press_keys(self) -> None:
        for combo in self._cfg.keys:
            self._gui.hotkey(*combo)

    def _warn_permission(self, exc: Exception) -> None:
        """Nao derruba a escuta: so explica o motivo mais provavel, uma vez."""
        if self._permission_warned:
            log.error("falha ao executar a acao: %s", exc)
            return
        self._permission_warned = True
        app = host_app_name()
        log.error(
            "falha ao executar a acao: %s\n"
            "    No macOS isso quase sempre e permissao: Ajustes do Sistema > "
            "Privacidade e Seguranca > Acessibilidade > '+' > adicione %s, "
            "depois encerre %s com Cmd+Q e abra de novo.",
            exc, app, app,
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="caminho do config.json")
    parser.add_argument("--list-devices", action="store_true",
                        help="lista os dispositivos de audio e sai")
    parser.add_argument("--test-action", action="store_true",
                        help="dispara a acao configurada uma vez e sai "
                             "(nao abre o microfone nem carrega o modelo)")
    parser.add_argument("--verbose", action="store_true",
                        help="mostra os parciais do reconhecedor")
    return parser.parse_args()


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d  %(message)s",
        datefmt="%H:%M:%S",
    )


def list_devices(sd) -> None:
    print(sd.query_devices())
    try:
        default_in = sd.default.device[0]
        print(f"\nDispositivo de entrada padrao: {default_in}")
    except Exception:
        pass
    print('\nUse o indice ou o nome em "input_device" no config.json.')


def has_input_device(sd) -> bool:
    try:
        return any(d["max_input_channels"] > 0 for d in sd.query_devices())
    except Exception as exc:
        log.debug("falha ao consultar dispositivos: %s", exc)
        return True  # deixa o erro real aparecer na abertura do stream


def describe_device(sd, device) -> str:
    try:
        info = sd.query_devices(device, "input")
        return f"{info['name']}"
    except Exception:
        return str(device if device is not None else "padrao do sistema")


def test_action(cfg: Config, pyautogui_mod, accessible: Optional[bool]) -> int:
    """Dispara a acao uma vez, para conferir permissao e coordenada.

    Nao abre o microfone nem carrega o modelo: e o caminho rapido para separar
    "nao entendeu a voz" de "nao conseguiu agir".
    """
    # Sem Acessibilidade o pyautogui nao levanta erro: ele simplesmente nao
    # posta o evento. Se tentassemos assim mesmo, o teste diria "funcionou"
    # sem nada ter acontecido - pior que nao testar.
    if accessible is False:
        log.error("teste abortado: sem a permissao de Acessibilidade a acao "
                  "seria descartada em silencio pelo macOS.")
        return 1

    if cfg.action_mode == "click":
        alvo = f"clicar em ({cfg.click['x']}, {cfg.click['y']})"
        dica = ("Se o cursor nao clicou onde voce esperava, recalibre com: "
                "python get_coords.py")
    else:
        alvo = "enviar " + " + ".join("+".join(c) for c in cfg.keys)
        dica = "Confira se a janela em foco recebeu as teclas."

    log.info("Teste de acao: vou %s.", alvo)
    log.info("Deixe a janela alvo na frente...")
    for restante in (3, 2, 1):
        log.info("  %d...", restante)
        time.sleep(1)

    if not Actuator(pyautogui_mod, cfg).trigger("--test-action"):
        log.error("A acao NAO foi executada.")
        return 1

    log.info("Acao executada. %s", dica)
    return 0


def run(cfg: Config, sd, vosk_mod, pyautogui_mod) -> int:
    recognizer = CommandRecognizer(
        vosk_mod, cfg.model_path, cfg.sample_rate, cfg.commands, cfg.verbose_partials
    )
    actuator = Actuator(pyautogui_mod, cfg)

    log.info("Microfone: %s", describe_device(sd, cfg.input_device))
    log.info("Acao: %s | comandos: %s",
             cfg.action_mode, ", ".join(recognizer.commands))
    log.info("Ouvindo... (Ctrl+C para sair)")

    with AudioSource(sd, cfg.sample_rate, cfg.block_size, cfg.input_device) as source:
        for block in source.blocks():
            command = recognizer.feed(block)
            if command:
                actuator.trigger(command)
    return 0


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    sd, vosk_mod, pyautogui_mod = import_dependencies()

    if args.list_devices:
        list_devices(sd)
        return 0

    cfg = load_config(args.config)
    if args.verbose:
        cfg.verbose_partials = True

    # Antes da validacao do config de proposito: a permissao independe do
    # config, e e o diagnostico mais util quando algo nao funciona.
    accessible = warn_if_no_accessibility()

    try:
        validate_config(cfg, pyautogui_mod)
    except ConfigError as exc:
        log.error("configuracao invalida: %s", exc)
        return 1

    if args.test_action:
        return test_action(cfg, pyautogui_mod, accessible)

    if not has_input_device(sd):
        log.error("nenhum dispositivo de entrada encontrado.")
        list_devices(sd)
        return 1

    try:
        return run(cfg, sd, vosk_mod, pyautogui_mod)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1
    except AudioError as exc:
        log.error("problema de audio: %s", exc)
        return 1
    except KeyboardInterrupt:
        log.info("Encerrado.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

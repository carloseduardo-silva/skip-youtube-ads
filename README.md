# skip-youtube-ads

Pule os anúncios do YouTube falando **"pular"**. Roda 100% offline no macOS: o
reconhecimento de voz acontece localmente com [Vosk](https://alphacephei.com/vosk/),
sem nenhuma requisição de rede no caminho do comando.

```
microfone ──▶ Vosk (modelo local, gramática fechada) ──▶ clique / teclas
```

---

## Setup

### 1. Dependências

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Não é preciso `brew install portaudio`: as wheels do `sounddevice` já trazem o
PortAudio embutido.

### 2. Modelo de voz em português

```bash
python download_model.py
```

O script baixa o `vosk-model-small-pt-0.3` (~40 MB) e extrai em
`models/vosk-model-small-pt-0.3/`. É idempotente — rodar de novo não baixa outra vez.

<details>
<summary>Download manual (se preferir)</summary>

1. Baixe https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip
2. Extraia dentro de `models/`, de forma que exista
   `models/vosk-model-small-pt-0.3/` contendo `final.mdl`, `mfcc.conf`,
   `HCLr.fst`, `Gr.fst` e a pasta `ivector/`.
3. Confira se `"model_path"` no `config.json` aponta para essa pasta.

</details>

### 3. Permissões do macOS (obrigatório)

Este é o passo que mais trava, e quase sempre pelo mesmo motivo: **a permissão não
é do Python, é do aplicativo de onde você roda o Python.** Se você executa pelo
terminal integrado do VS Code, quem precisa aparecer nas listas é o **Visual Studio
Code** — não o Terminal.app, não o Python.

Em **Ajustes do Sistema → Privacidade e Segurança**, habilite esse app nas duas listas:

| Permissão | Para quê | Sem ela |
|---|---|---|
| **Microfone** | capturar áudio | o stream abre mas só entrega silêncio |
| **Acessibilidade** | simular clique/teclas | reconhece a voz, mas nada acontece na tela |

Em **Acessibilidade** o app normalmente não aparece sozinho: use o botão **`+`** e
escolha o aplicativo (ex.: `/Applications/Visual Studio Code.app`).

> **Depois de conceder, encerre o app por completo (⌘Q) e abra de novo.** O macOS
> avalia a permissão quando o processo nasce; uma janela já aberta continua sem ela.

Para conferir se ficou tudo certo, sem precisar falar nada:

```bash
python main.py --test-action
```

Ele dispara a ação configurada uma vez e diz se funcionou. O `main.py` também avisa
sozinho na partida quando a Acessibilidade está faltando.

### 4. Calibrar o clique

O YouTube não tem atalho de teclado nativo para "Pular anúncios", então o modo
padrão é clique em coordenada fixa:

```bash
python get_coords.py
```

Deixe um anúncio rodando, posicione o cursor sobre o botão **Pular anúncios**,
espere a contagem e cole o resultado no `config.json`.

---

## Uso

```bash
source venv/bin/activate
python main.py
```

Fale **"pular"** (ou "pula"/"skip") e o botão é acionado.

```
21:04:12.331  Microfone: Microfone (MacBook Air)
21:04:12.331  Acao: click | comandos: pular, pula
21:04:12.332  Ouvindo... (Ctrl+C para sair)
21:04:19.884  comando 'pular' -> acao disparada (click)
```

Outros comandos:

```bash
python main.py --test-action    # dispara a ação uma vez (sem microfone) e sai
python main.py --list-devices   # lista os dispositivos de áudio
python main.py --verbose        # mostra os parciais do reconhecedor
python main.py --config outro.json
```

---

## Configuração (`config.json`)

| Chave | Descrição |
|---|---|
| `model_path` | pasta do modelo Vosk |
| `commands` | palavras que disparam a ação. Palavras fora do vocabulário do modelo são descartadas na partida, com aviso |
| `sample_rate` | 16000 é o nativo do modelo; mude só se o device não aceitar |
| `block_size` | amostras por bloco. 2000 = 125 ms. Menor = mais rápido, porém menos preciso |
| `input_device` | `null` = padrão do sistema; ou índice/nome de `--list-devices` |
| `cooldown_seconds` | tempo mínimo entre dois disparos |
| `action_mode` | `"click"` ou `"keys"` |
| `click` | `x`, `y` e `restore_mouse` (devolve o cursor ao lugar de origem) |
| `keys` | usado no modo `keys`: lista de combinações, ex. `[["tab"], ["enter"]]` |
| `failsafe` | `true` aborta se o cursor for para um canto da tela (proteção do pyautogui) |
| `verbose_partials` | loga os resultados parciais do reconhecedor |

### Modo `keys`

Se preferir teclas a coordenadas, troque para:

```json
"action_mode": "keys",
"keys": [["tab"], ["enter"]]
```

A sequência é enviada para a **janela em foco** — o navegador precisa estar na
frente e o player focado. É mais frágil que o clique, mas não quebra quando a
janela muda de tamanho.

---

## Como a latência é mantida baixa

- **Gramática fechada.** O reconhecedor só conhece as palavras de `commands` e
  `[unk]`. Vocabulário mínimo = decodificação mais rápida e quase nenhum
  falso-positivo com conversa normal.
- **Disparo no parcial.** A ação sai assim que a palavra aparece no resultado
  *parcial* do Vosk, sem esperar o fim do enunciado (o que custaria centenas de ms).
- **Callback só enfileira.** A thread de tempo real do PortAudio apenas empilha
  bytes; o Vosk roda na thread principal. Se o consumo atrasar, blocos antigos
  são descartados em vez de acumular.
- **`pyautogui.PAUSE = 0`.** Remove a pausa padrão de 100 ms entre ações.

---

## Troubleshooting

**Não reconhece nada / nenhum log aparece**
Rode `python main.py --verbose` e fale. Se nenhum parcial aparecer, é permissão
de microfone ou dispositivo errado — confira `--list-devices` e ajuste
`input_device`.

**Reconhece, mas nada acontece na tela**
Quase sempre é a permissão de Acessibilidade. Rode `python main.py --test-action`
para isolar: se ele falhar, é permissão (veja o passo 3 do setup); se ele funcionar
mas o anúncio não pular, é a coordenada — recalibre com `get_coords.py`.

**"o dispositivo de entrada nao aceita 16000 Hz"**
Mude `sample_rate` para `48000` no `config.json`. O Vosk aceita outras taxas —
só perde um pouco de precisão.

**Clica no lugar errado**
O botão "Pular anúncios" muda de posição conforme o tamanho da janela. Recalibre
com `get_coords.py` ou mantenha a janela num tamanho fixo (tela cheia ajuda).

**Dispara sozinho**
Aumente `cooldown_seconds` e reduza `commands` — palavras curtas como "pula"
casam com mais coisa. Deixar só `["pular"]` é o mais conservador.

**"skip" nunca funciona**
O `vosk-model-small-pt-0.3` não tem "skip" no vocabulário — palavra que o modelo
não conhece nunca é reconhecida. Por isso o default vem só com `["pular", "pula"]`.
Se você colocar uma palavra desconhecida no `config.json`, ela é descartada na
partida com um aviso:

```
ignorando palavra(s) fora do vocabulario do modelo: 'skip' (...)
```

Qualquer palavra do português serve como gatilho (`"próximo"`, `"anúncio"`, etc.).

---

## Licença

MIT — veja [LICENSE](LICENSE).

# Vast.ai — Transcrição paralela em 4 GPUs

## Objetivo

Executar a etapa de transcrição do `youtube-data-pipeline` em uma instância Vast.ai com 4 GPUs,
reaproveitando a base `videos.parquet` já descoberta localmente.

Fluxo:

```text
videos.parquet
    ↓
fila compartilhada de video_id
    ↓
GPU 0 ─┐
GPU 1 ─┼─> fallback de transcrição por vídeo
GPU 2 ─┤   youtube-transcript-api → yt-dlp subtitle → faster-whisper
GPU 3 ─┘
    ↓
checkpoint JSONL por GPU
    ↓
transcripts.parquet
transcript_segments.parquet
videos_with_transcripts.parquet
summary.json
```

## Por que existe um runner separado

O pipeline v0.2 foi desenhado primeiro para execução sequencial/local. Para uma máquina com várias GPUs,
o runner `scripts/vastai_transcribe_4gpu.py` usa uma fila compartilhada e um processo por GPU.

Cada processo carrega o modelo Faster Whisper uma única vez, consome vídeos da fila e grava um checkpoint
após cada vídeo. Isso evita recarregar o modelo a cada vídeo e evita escrita concorrente no mesmo Parquet.

## Requisitos da instância

Escolha uma instância Vast.ai com:

- 4 GPUs na mesma máquina;
- NVIDIA/CUDA 12;
- cuDNN 9;
- Python 3.11, 3.12 ou 3.13;
- pelo menos 50 GB de disco;
- conexão de rede confiável.

Para o modelo `small`, 8 GB de VRAM por GPU deixa folga. O runner usa `float16` por padrão.

## 1. Criar a instância

Na Vast.ai:

1. abra **Search**;
2. filtre para uma oferta com **4 GPUs**;
3. prefira uma oferta verificada/reliável e com boa banda;
4. escolha um template PyTorch/Jupyter com CUDA 12;
5. use Jupyter em modo direto quando disponível;
6. reserve pelo menos 50 GB de disco;
7. crie a instância;
8. em **Instances**, abra o Jupyter.

## 2. Abrir o Terminal do Jupyter

No JupyterLab:

```text
File → New → Terminal
```

Todos os comandos abaixo são executados nesse terminal Linux.

## 3. Clonar a branch GPU

```bash
cd /workspace
git clone -b feat/vastai-4gpu-transcripts https://github.com/CacoBruno/youtube-data-pipeline.git
cd youtube-data-pipeline
```

Confirme:

```bash
git branch --show-current
```

Esperado:

```text
feat/vastai-4gpu-transcripts
```

## 4. Confirmar as quatro GPUs

```bash
nvidia-smi -L
```

Devem aparecer quatro linhas, normalmente GPU 0, GPU 1, GPU 2 e GPU 3.

Também confirme via Python/PyTorch:

```bash
python - <<'PY'
import torch
print("CUDA disponível:", torch.cuda.is_available())
print("GPUs:", torch.cuda.device_count())
for i in range(torch.cuda.device_count()):
    print(i, torch.cuda.get_device_name(i))
PY
```

Critério de sucesso:

```text
CUDA disponível: True
GPUs: 4
```

Se aparecer menos de 4, não continue a rodada completa.

## 5. Instalar o projeto

Ainda em:

```text
/workspace/youtube-data-pipeline
```

rode:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Instale explicitamente as bibliotecas NVIDIA usadas pela versão atual do CTranslate2/Faster Whisper:

```bash
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
```

Configure o loader Linux:

```bash
export LD_LIBRARY_PATH="$(python - <<'PY'
import os
import nvidia.cublas.lib
import nvidia.cudnn.lib
print(
    os.path.dirname(nvidia.cublas.lib.__file__)
    + ":"
    + os.path.dirname(nvidia.cudnn.lib.__file__)
)
PY
):$LD_LIBRARY_PATH"
```

Valide:

```bash
python -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"
```

## 6. Rodar os testes do repositório

```bash
pytest -q
```

O teste novo `test_transcript_model_reuse.py` confirma que um modelo Whisper já carregado pode ser
reutilizado em vários vídeos.

Não continue para milhares de vídeos se os testes falharem.

## 7. Preparar a base de entrada

No computador local, o arquivo necessário é:

```text
data/lideres_opiniao_credito_2025_2026/processed/videos.parquet
```

Na instância Vast.ai, crie:

```bash
mkdir -p input/lideres_opiniao_credito_2025_2026
mkdir -p outputs/lideres_opiniao_credito_2025_2026
```

Faça upload de `videos.parquet` pelo Jupyter para:

```text
/workspace/youtube-data-pipeline/input/lideres_opiniao_credito_2025_2026/videos.parquet
```

Valide:

```bash
python - <<'PY'
import pandas as pd
p = "input/lideres_opiniao_credito_2025_2026/videos.parquet"
df = pd.read_parquet(p)
print("linhas:", len(df))
print("vídeos únicos:", df["video_id"].nunique())
print(df[["video_id", "channel_title"]].head())
PY
```

## 8. Pré-baixar o modelo uma única vez

```bash
python - <<'PY'
from faster_whisper import download_model
path = download_model("small")
print("modelo:", path)
PY
```

Isso evita que os quatro workers tentem iniciar o download do modelo simultaneamente.

## 9. Smoke test — 8 vídeos

```bash
python scripts/vastai_transcribe_4gpu.py \
  --config configs/lideres_opiniao_credito_2025_2026.yaml \
  --videos input/lideres_opiniao_credito_2025_2026/videos.parquet \
  --output-dir outputs/lideres_opiniao_credito_2025_2026 \
  --gpus 0,1,2,3 \
  --model small \
  --compute-type float16 \
  --limit 8
```

No terminal deverão aparecer mensagens semelhantes a:

```text
[GPU 0] modelo pronto
[GPU 1] modelo pronto
[GPU 2] modelo pronto
[GPU 3] modelo pronto
[GPU 0] #1 | VIDEO_ID | success | youtube_transcript_api_generated
[GPU 2] #1 | VIDEO_ID | success | faster_whisper_small
```

Abra outro Terminal do Jupyter e monitore:

```bash
watch -n 1 nvidia-smi
```

Vídeos resolvidos por legenda não usam GPU. As GPUs trabalham quando o fallback chega ao Faster Whisper.

## 10. Inspecionar o smoke test

```bash
cat outputs/lideres_opiniao_credito_2025_2026/summary.json
```

E:

```bash
python - <<'PY'
import pandas as pd
p = "outputs/lideres_opiniao_credito_2025_2026/transcripts.parquet"
df = pd.read_parquet(p)
print(df["transcript_status"].value_counts(dropna=False))
print(df["transcript_method"].value_counts(dropna=False))
print(df[["video_id", "transcript_status", "transcript_method", "transcript_language"]].head(20))
PY
```

Critério de sucesso:

- quatro workers iniciam;
- pelo menos um vídeo conclui sem erro estrutural;
- `transcripts.parquet` é criado;
- `videos_with_transcripts.parquet` é criado;
- falhas, se houver, aparecem com `transcript_status=failed` e erro registrado.

## 11. Rodada completa em tmux

Instale `tmux` caso necessário:

```bash
apt-get update && apt-get install -y tmux
```

Crie uma sessão:

```bash
tmux new -s youtube-transcripts
```

Dentro dela, reexporte o `LD_LIBRARY_PATH`:

```bash
export LD_LIBRARY_PATH="$(python - <<'PY'
import os
import nvidia.cublas.lib
import nvidia.cudnn.lib
print(
    os.path.dirname(nvidia.cublas.lib.__file__)
    + ":"
    + os.path.dirname(nvidia.cudnn.lib.__file__)
)
PY
):$LD_LIBRARY_PATH"
```

Agora rode sem `--limit`:

```bash
python scripts/vastai_transcribe_4gpu.py \
  --config configs/lideres_opiniao_credito_2025_2026.yaml \
  --videos input/lideres_opiniao_credito_2025_2026/videos.parquet \
  --output-dir outputs/lideres_opiniao_credito_2025_2026 \
  --gpus 0,1,2,3 \
  --model small \
  --compute-type float16
```

Para sair do tmux sem interromper:

```text
Ctrl+B
depois D
```

Para voltar:

```bash
tmux attach -t youtube-transcripts
```

## 12. Resume/checkpoint

O runner grava um arquivo JSONL por GPU em:

```text
outputs/lideres_opiniao_credito_2025_2026/checkpoints/
```

Cada linha corresponde a um vídeo finalizado.

Se a instância cair, rode exatamente o mesmo comando novamente. O runner:

- pula vídeos com `transcript_status=success`;
- tenta novamente os que falharam;
- processa os ainda ausentes;
- consolida novamente as saídas.

Não apague a pasta `checkpoints` durante a execução.

## 13. Outputs finais

```text
outputs/lideres_opiniao_credito_2025_2026/
├── checkpoints/
│   ├── gpu_0.jsonl
│   ├── gpu_1.jsonl
│   ├── gpu_2.jsonl
│   └── gpu_3.jsonl
├── transcripts.parquet
├── transcripts.csv
├── transcript_segments.parquet
├── transcript_segments.csv
├── videos_with_transcripts.parquet
├── videos_with_transcripts.csv
└── summary.json
```

Antes de substituir qualquer arquivo local, valide `summary.json`, taxa de sucesso e métodos utilizados.

## 14. Compactar para download

```bash
tar -czf lideres_opiniao_transcripts_vastai.tar.gz \
  outputs/lideres_opiniao_credito_2025_2026
```

Faça download desse arquivo pelo Jupyter.

## 15. O que não fazer nesta rodada

- não coletar comentários na Vast.ai;
- não refazer discovery;
- não alterar as queries;
- não sobrescrever o `videos.parquet` local antes da validação;
- não aumentar modelo/batch/complexidade antes do smoke test;
- não apagar checkpoints enquanto houver vídeos pendentes.

O objetivo aqui é apenas acelerar, de modo reproduzível e retomável, a transcrição do corpus já definido.

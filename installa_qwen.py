"""Reproducible isolated installation without modifying global Python."""
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / '.runtime'
PYTHON = RUNTIME / 'qwen' / 'Scripts' / 'python.exe'


def main():
    uv = RUNTIME / 'uv' / 'bin' / 'uv.exe'
    if not uv.is_file():
        raise RuntimeError('Installare uv in .runtime/uv prima di procedere.')
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(RUNTIME / 'python'),
               UV_CACHE_DIR=str(RUNTIME / 'cache'), UV_SYSTEM_CERTS='true')
    commands = [
        [str(uv), 'venv', str(RUNTIME / 'qwen'), '--python', '3.12', '--managed-python'],
        [str(uv), 'pip', 'install', '--python', str(PYTHON), 'torch', 'torchaudio',
         '--index-url', 'https://download.pytorch.org/whl/cu126'],
        [str(uv), 'pip', 'install', '--python', str(PYTHON), 'qwen-tts==0.1.1', 'truststore==0.10.5', 'espeakng-loader==0.2.4'],
    ]
    for command in commands:
        if command[1] == 'venv' and PYTHON.exists():
            continue
        subprocess.run(command, env=env, check=True)
    subprocess.run([str(PYTHON), '-c',
        'import torch; from qwen_tts import Qwen3TTSModel; '
        'print("CUDA:", torch.cuda.is_available()); '
        'assert torch.cuda.is_available(), "GPU CUDA non disponibile"; '
        'print(torch.cuda.get_device_name(0))'], env=env, check=True)


if __name__ == '__main__':
    main()

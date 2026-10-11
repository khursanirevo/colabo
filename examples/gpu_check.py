"""Smoke test: confirm the Colab GPU is visible and do a tiny matmul.

colabo up && colabo run python examples/gpu_check.py && cat outputs/gpu_check.txt
"""

import subprocess
import sys
import time
from pathlib import Path

smi = subprocess.run(
    ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
    check=False,
    capture_output=True,
    text=True,
)
print(smi.stdout.strip() or "nvidia-smi: no GPU visible")
try:
    import torch
except ImportError:
    sys.exit("torch not installed; run: colabo install torch")
print("torch", torch.__version__, "cuda:", torch.cuda.is_available())
x = torch.randn(4096, 4096, device="cuda")
torch.cuda.synchronize()
t = time.time()
for _ in range(10):
    x = x @ x / 4096
torch.cuda.synchronize()
print(f"10 matmuls: {time.time() - t:.3f}s")
Path("outputs").mkdir(exist_ok=True)
Path("outputs/gpu_check.txt").write_text(f"ok {torch.cuda.get_device_name(0)}\n")
print("wrote outputs/gpu_check.txt")

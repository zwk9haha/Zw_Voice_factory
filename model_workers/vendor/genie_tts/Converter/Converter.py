from .v2.Converter import convert as convert_v2
from .v2ProPlus.Converter import convert as convert_v2pp

import os


def convert(torch_ckpt_path: str, torch_pth_path: str, output_dir: str) -> None:
    if os.path.getsize(torch_pth_path) > 150 * 1024 * 1024:  # 大于 150 MB
        convert_v2pp(torch_ckpt_path, torch_pth_path, output_dir)
    else:
        convert_v2(torch_ckpt_path, torch_pth_path, output_dir)

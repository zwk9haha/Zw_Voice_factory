import torch
import onnx
import json
import os
from collections import OrderedDict

from ..load_state_dict import load_sovits_model


class PromptEncoderConverter:
    """
    一个转换器，用于从 PyTorch 模型创建：
    1. 一个用于分发的半精度 (fp16) .bin 权重文件。
    2. 一个与全精度 (fp32) 布局兼容的 ONNX 模型。
    3. 一个可以将 fp16 .bin 文件还原为 fp32 .bin 的工具函数。
    """

    def __init__(self,
                 torch_pth_path: str,
                 prompt_encoder_onnx_path: str,
                 key_list_file: str,
                 output_dir: str,
                 cache_dir: str,
                 ):
        self.torch_pth_path: str = torch_pth_path
        self.vits_onnx_path: str = prompt_encoder_onnx_path
        self.key_list_file: str = key_list_file
        self.output_dir: str = output_dir
        self.cache_dir: str = cache_dir
        # 定义输出文件路径
        self.fp16_bin_path: str = os.path.join(self.output_dir, "prompt_encoder_fp16.bin")
        self.index_table_path: str = os.path.join(self.cache_dir, "prompt_encoder_weights_index_fp32.json")
        self.relinked_fp32_onnx_path: str = os.path.join(self.output_dir, "prompt_encoder_fp32.onnx")
        self.reconstructed_fp32_bin_path: str = os.path.join(self.output_dir, "prompt_encoder_fp32.bin")

        # 确保输出目录存在
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

        if not os.path.exists(self.key_list_file):
            raise FileNotFoundError(f"错误: Key 列表文件未找到! 路径: {self.key_list_file}")

    def step1_create_fp16_bin_and_fp32_index(self):
        """
        (1) 创建一个半精度 (fp16) 的 .bin 文件，但生成一个
            描述全精度 (fp32) 布局的索引表。
        """
        # 加载 key 列表
        with open(self.key_list_file, 'r') as f:
            onnx_keys = [line.strip() for line in f.readlines()]

        # 加载 PyTorch 模型权重
        torch_state_dict = load_sovits_model(self.torch_pth_path)['weight']

        index_table = OrderedDict()
        # 这个偏移量将按照 fp32 的大小进行累加
        current_fp32_offset = 0

        with open(self.fp16_bin_path, 'wb') as f_bin:
            for onnx_key in onnx_keys:
                torch_key = onnx_key[len("vq_model."):] if onnx_key.startswith("vq_model.") else onnx_key

                torch_tensor = torch_state_dict.get(torch_key)
                if torch_tensor is None:
                    raise ValueError(f"❌ 严重错误: 在 PyTorch 权重中找不到 Key '{torch_key}'")

                # 转换为 fp16 并写入文件
                torch_tensor_fp16 = torch_tensor.to(torch.float16)
                numpy_array_fp16 = torch_tensor_fp16.cpu().numpy()
                tensor_bytes_fp16 = numpy_array_fp16.tobytes()
                f_bin.write(tensor_bytes_fp16)

                # 关键步骤：计算并记录 fp32 的长度和偏移量
                # 一个 fp32 = 4 字节, 一个 fp16 = 2 字节。所以 fp32 长度是 fp16 的两倍。
                tensor_length_fp32 = len(tensor_bytes_fp16) * 2

                index_table[onnx_key] = {
                    'offset': current_fp32_offset,
                    'length': tensor_length_fp32
                }

                # 偏移量也按照 fp32 的长度进行累加
                current_fp32_offset += tensor_length_fp32

        # 保存描述 fp32 布局的索引表
        with open(self.index_table_path, 'w') as f_json:
            json.dump(index_table, f_json, indent=4)  # type: ignore

    def step2_relink_onnx_for_fp32(self):
        """
        (2) 根据 fp32 索引表，修改 ONNX 模型，使其链接到一个
            未来的、全精度的 .bin 文件。
        """
        # 加载描述 fp32 布局的索引表
        with open(self.index_table_path, 'r') as f:
            index_table = json.load(f)

        # 加载 ONNX 模型结构
        model = onnx.load_model(self.vits_onnx_path, load_external_data=False)

        # 这个 ONNX 模型将要链接的 .bin 文件名
        reconstructed_bin_filename = os.path.basename(self.reconstructed_fp32_bin_path)

        for tensor in model.graph.initializer:
            if tensor.name in index_table:
                tensor.ClearField('raw_data')
                tensor.data_location = onnx.TensorProto.EXTERNAL
                info = index_table[tensor.name]

                del tensor.external_data[:]

                keys = ["location", "offset", "length"]
                values = [reconstructed_bin_filename, str(info['offset']), str(info['length'])]

                for k, v in zip(keys, values):
                    entry = tensor.external_data.add()
                    entry.key = k
                    entry.value = v

        # 保存修改后的、链接到 fp32 权重的 ONNX 模型
        onnx.save(model, self.relinked_fp32_onnx_path)

    def run_full_process(self):
        """
        按顺序执行核心的转换步骤 (1 和 2)。
        """
        self.step1_create_fp16_bin_and_fp32_index()
        self.step2_relink_onnx_for_fp32()
